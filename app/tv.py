"""Thin wrapper around the Frame TV art channel.

Everything here is limited to the art channel: no power or remote-key commands
are ever sent, and only the "My Photos" category is managed. The one exception
is pairing, which opens the remote-control channel once to be given a token.
"""
import re
import threading
from contextlib import contextmanager
from pathlib import Path

import websocket
from samsungtvws import exceptions
from samsungtvws.art import SamsungTVArt
from samsungtvws.connection import SamsungTVWSConnection
from samsungtvws.remote import REMOTE_ENDPOINT

from .config import TOKEN_FILE, Config

MY_PHOTOS = "MY-C0002"
_PHOTO_ID = re.compile(r"MY_F\d+")
# Longer than a normal request: someone has to pick up the remote.
PAIR_TIMEOUT = 60


class TVUnavailable(Exception):
    """The TV could not be reached or did not answer."""


class NotAPhoto(Exception):
    """The content id is not one of the user's own uploaded photos."""


def check_photo_id(content_id: str) -> str:
    if not _PHOTO_ID.fullmatch(content_id):
        raise NotAPhoto(content_id)
    return content_id


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
        with self._lock:
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
            thumbs = art.get_thumbnail_list(missing) if missing else {}
            return {
                "art_mode": art.get_artmode() == "on",
                "current": art.get_current().get("content_id"),
                "photos": photos,
                "thumbs": {Path(name).stem: bytes(data) for name, data in thumbs.items()},
            }

    def thumbnail(self, content_id: str) -> bytes | None:
        check_photo_id(content_id)
        with self._art() as art:
            thumbs = art.get_thumbnail_list([content_id])
        return bytes(next(iter(thumbs.values()))) if thumbs else None

    def upload(self, jpeg: bytes) -> str:
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
