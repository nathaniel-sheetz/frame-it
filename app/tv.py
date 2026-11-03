"""Thin wrapper around the Frame TV art channel.

Everything here is limited to the art channel: no power or remote-key commands
are ever sent, and only the "My Photos" category is managed. The one exception
is pairing, which opens the remote-control channel once to be given a token.
"""
import json
import logging
import random
import re
import threading
from contextlib import contextmanager
from socket import create_connection

import websocket
from samsungtvws import exceptions
from samsungtvws.art import SamsungTVArt
from samsungtvws.connection import SamsungTVWSConnection
from samsungtvws.helper import get_ssl_context
from samsungtvws.remote import REMOTE_ENDPOINT

from .config import TOKEN_FILE, Config

MY_PHOTOS = "MY-C0002"
_PHOTO_ID = re.compile(r"MY_F\d+")
# Longer than a normal request: someone has to pick up the remote.
PAIR_TIMEOUT = 60
THUMBNAIL_TRIES = 4

log = logging.getLogger(__name__)


class TVUnavailable(Exception):
    """The TV could not be reached or did not answer."""


class NotAPhoto(Exception):
    """The content id is not one of the user's own uploaded photos."""


def check_photo_id(content_id: str) -> str:
    if not _PHOTO_ID.fullmatch(content_id):
        raise NotAPhoto(content_id)
    return content_id


def _receive(sock, size: int) -> bytes:
    """Exactly `size` bytes, or ConnectionError if the TV hangs up first."""
    data = bytearray()
    while len(data) < size:
        packet = sock.recv(size - len(data))
        if not packet:
            raise ConnectionError("The TV closed the thumbnail transfer part way through")
        data.extend(packet)
    return bytes(data)


def _thumbnail_transfer(art, content_ids: list[str], timeout: float) -> dict[str, bytes]:
    """One try at the thumbnails, over the second connection the TV opens for them.

    This stands in for the library's get_thumbnail_list. That one has no timeout
    on the connection, and reads a connection the TV has closed forever, holding
    the lock that every other TV request waits on.
    """
    reply = art._send_art_request({
        "request": "get_thumbnail_list",
        "content_id_list": [{"content_id": content_id} for content_id in content_ids],
        "conn_info": {"d2d_mode": "socket", "connection_id": random.randrange(4 * 1024 * 1024 * 1024), "id": art.get_uuid()},
    })
    conn_info = json.loads(reply["conn_info"])
    sock = create_connection((conn_info["ip"], int(conn_info["port"])), timeout=timeout)
    try:
        if conn_info.get("secured", False):
            sock = get_ssl_context().wrap_socket(sock)
        thumbs = {}
        last, total = -1, 1
        while last + 1 < total:
            # TODO: cap the header and fileLength. A multi-gigabyte length is
            # read with only a per-recv timeout, so a slow sender holds this
            # lock and grows memory until the read finishes.
            header = json.loads(_receive(sock, int.from_bytes(_receive(sock, 4), "big")))
            last, total = int(header["num"]), int(header["total"])
            # TODO: fileID is the cache filename. A value with ".." is written
            # outside the thumbs directory. Keep only MY_F ids.
            thumbs[header["fileID"]] = _receive(sock, int(header["fileLength"]))
        return thumbs
    finally:
        sock.close()


def _thumbnails(art, content_ids: list[str], timeout: float) -> dict[str, bytes]:
    """Thumbnails by content id.

    The TV sends them over a second connection, and now and then drops it as it
    opens or part way through. The art channel itself is unharmed, so they are
    simply asked for again.
    """
    for tries_left in reversed(range(THUMBNAIL_TRIES)):
        try:
            return _thumbnail_transfer(art, content_ids, timeout)
        except TimeoutError:
            # A TV that stopped sending does not start again, and asking again holds up every other request.
            raise
        except OSError:
            if not tries_left:
                raise
        except (ValueError, KeyError, TypeError, OverflowError) as e:
            # A reply that cannot be read is the same drop. A null length or a
            # port outside 0-65535 is that kind of reply, not a server error.
            if not tries_left:
                raise ConnectionError("The TV cut the thumbnail transfer short") from e


class FrameTV:
    def __init__(self, cfg: Config):
        self._cfg = cfg
        # The TV handles one art request at a time, so serialise access.
        self._lock = threading.Lock()

    @contextmanager
    def _art(self):
        art = SamsungTVArt(
            self._cfg.tv_host,
            port=self._cfg.tv_port,
            token_file=str(TOKEN_FILE),
            timeout=self._cfg.tv_timeout,
            name=self._cfg.tv_name,
            # The library's default 1s pause is for remote key presses; art
            # requests wait for their own response.
            key_press_delay=0,
        )
        # A request stuck on the TV must not leave every later one waiting behind it.
        if not self._lock.acquire(timeout=self._cfg.tv_timeout):
            raise TVUnavailable("The TV is still busy with an earlier request. Try again in a moment.")
        try:
            try:
                if not TOKEN_FILE.exists():
                    self._pair()
                art.open()
                yield art
            except exceptions.UnauthorizedError as e:
                raise TVUnavailable("The TV refused the connection. Accept the prompt on the TV and try again.") from e
            except (OSError, exceptions.ConnectionFailure, exceptions.TimeoutError, websocket.WebSocketException) as e:
                raise TVUnavailable(f"Could not reach the TV at {self._cfg.tv_host} ({type(e).__name__}).") from e
            except exceptions.ResponseError as e:
                raise TVUnavailable(f"The TV rejected the request: {e}") from e
            finally:
                art.close()
        finally:
            # close() can raise. The lock still has to come back, or every later
            # request waits out tv_timeout and then reports the TV as busy.
            self._lock.release()

    def _pair(self):
        """Have the TV show its "Allow" prompt and save the token it hands back.

        The token only comes over the remote-control channel. The art channel
        shows the same prompt, but accepting it there never yields a token.
        Opening the channel is all this does; no key presses are sent.
        """
        remote = SamsungTVWSConnection(
            self._cfg.tv_host,
            endpoint=REMOTE_ENDPOINT,
            port=self._cfg.tv_port,
            token_file=str(TOKEN_FILE),
            timeout=PAIR_TIMEOUT,
            name=self._cfg.tv_name,
        )
        try:
            remote.open()
        except exceptions.UnauthorizedError:
            raise
        except exceptions.ConnectionFailure as e:
            # The TV answered, but not with a connection: its prompt went unanswered.
            raise TVUnavailable('The TV is waiting for you to allow this app. Choose "Allow" on the TV and try again.') from e
        finally:
            remote.close()

    def snapshot(self, cached_thumbs: set[str]) -> dict:
        """Art mode state, current artwork, the photo list, and any thumbnails not yet cached."""
        with self._art() as art:
            photos = art.available(MY_PHOTOS)
            missing = [p["content_id"] for p in photos if p["content_id"] not in cached_thumbs]
            try:
                thumbs = _thumbnails(art, missing, self._cfg.tv_timeout) if missing else {}
            except OSError:
                # The list is still worth having. Each missing thumbnail is asked for again on its own.
                log.warning("The TV did not send thumbnails for %s", ", ".join(missing))
                thumbs = {}
            return {
                "art_mode": art.get_artmode() == "on",
                "current": art.get_current().get("content_id"),
                "photos": photos,
                "thumbs": thumbs,
            }

    def now_showing(self) -> dict:
        """Art mode state and the current artwork, without the photo list. Cheap enough to ask often."""
        with self._art() as art:
            return {"art_mode": art.get_artmode() == "on", "current": art.get_current().get("content_id")}

    def photo_ids(self) -> set[str]:
        """The content ids of everything in My Photos."""
        with self._art() as art:
            return {p["content_id"] for p in art.available(MY_PHOTOS)}

    def thumbnail(self, content_id: str) -> bytes | None:
        check_photo_id(content_id)
        with self._art() as art:
            thumbs = _thumbnails(art, [content_id], self._cfg.tv_timeout)
        return next(iter(thumbs.values()), None)

    def upload(self, jpeg: bytes) -> str:
        # TODO: art.upload's bulk socket has no timeout. A TV that stops reading
        # holds this lock until the process restarts.
        with self._art() as art:
            return art.upload(jpeg, file_type="jpg", matte="none", portrait_matte="none")

    def select(self, content_id: str) -> bool:
        """Make this the current artwork. Returns whether it is on screen now.

        When someone is watching TV (art mode off) the picture is selected
        without being shown, so normal viewing is never interrupted.
        """
        check_photo_id(content_id)
        with self._art() as art:
            showing = art.get_artmode() == "on"
            art.select_image(content_id, show=showing)
            return showing

    def delete(self, content_id: str) -> bool:
        check_photo_id(content_id)
        with self._art() as art:
            return art.delete(content_id)
