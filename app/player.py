"""Runs the playlist: sends its pictures to the TV, then changes the picture when its time is up.

The TV's own slideshow is not used, since it keeps neither an order nor an
interval of your choosing. This app keeps the clock instead. Only time a
picture has spent on screen in Art Mode counts, so none is used up while the
TV is being watched or is off.
"""
import logging
import random
import threading
import time

from .tv import TVUnavailable

# How often the TV is asked what it is showing while a playlist runs.
TICK = 15
# How long the TV is left alone after it could not be reached or refused a picture.
RETRY = 60
# Between one picture sent and the next, so a press of Stop gets its turn.
SOON = 0.1

log = logging.getLogger(__name__)


class Refused(Exception):
    """The playlist cannot do what was asked as things stand."""


def _settled(ids: list[str], current: str | None, cycle: list[str] | None, rng) -> list[str]:
    """A shuffled order brought in line with the playlist as it is now.

    Pictures taken out of the playlist are dropped. New ones, which is all of
    them when there is no order yet, take a random place among those still to come.
    """
    cycle = [image_id for image_id in cycle or [] if image_id in ids]
    if current in ids and current not in cycle:
        cycle.insert(0, current)
    at = cycle.index(current) if current in cycle else -1
    for image_id in ids:
        if image_id not in cycle:
            cycle.insert(rng.randint(at + 1, len(cycle)), image_id)
    return cycle


def neighbour(ids: list[str], current: str | None, direction: int, cycle: list[str] | None, rng=random):
    """The picture one step from `current`, and the shuffled order to keep.

    `cycle` is None for a playlist played in order. A step back from the first
    picture of a shuffle has nowhere to go, and gives None.
    """
    if cycle is None:
        if current not in ids:
            return ids[0], None
        return ids[(ids.index(current) + direction) % len(ids)], None
    cycle = _settled(ids, current, cycle, rng)
    at = cycle.index(current) if current in cycle else -1
    if direction < 0:
        return (cycle[at - 1] if at > 0 else None), cycle
    if at + 1 < len(cycle):
        return cycle[at + 1], cycle
    # Every picture has had its turn. Shuffle again, without the same one twice running.
    cycle = rng.sample(ids, len(ids))
    if len(cycle) > 1 and cycle[0] == current:
        cycle.append(cycle.pop(0))
    return cycle[0], cycle


def upcoming(ids: list[str], current: str | None, cycle: list[str] | None) -> str | None:
    """The picture that comes next, where that is settled. After the last of a shuffle it is not."""
    order = ids if cycle is None else [image_id for image_id in cycle if image_id in ids]
    if len(ids) < 2 or current not in order:
        return None
    at = order.index(current) + 1
    if cycle is None:
        return order[at % len(order)]
    return order[at] if at < len(order) else None


class Player:
    def __init__(self, tv, library, playlists, clock=time.monotonic, rng=random):
        self.tv = tv
        self.library = library
        self.playlists = playlists
        self._clock = clock
        self._rng = rng
        # The clock and whoever is pressing buttons take turns.
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._closing = False
        # When the picture was last seen on screen, by this process's clock. None while it is not counting.
        self._seen = None
        # A picture the TV was showing instead, the last time it was asked.
        self._astray = None
        # Why the playlist is waiting, in words for the page.
        self._problem = None
        # Why the last playlist stopped by itself.
        self.ended = None

    def start(self) -> None:
        """Start the clock. It runs until the app stops."""
        threading.Thread(target=self._run, name="playlist", daemon=True).start()

    def close(self) -> None:
        self._closing = True
        self._wake.set()

    def _run(self) -> None:
        while not self._closing:
            self._wake.clear()
            try:
                delay = self.tick()
            except Exception:  # noqa: BLE001 - the clock has to outlive anything one turn runs into
                log.exception("The playlist clock stumbled")
                delay = RETRY
            self._wake.wait(delay)

    def tick(self) -> float:
        """One turn of the clock. Returns how long to wait before the next."""
        with self._lock:
            playing = self.playlists.playing()
            if not playing:
                return TICK
            playlist = self.playlists.get(playing["playlist_id"])
            if not playlist["pictures"]:
                self._end(playlist, "it has no pictures left")
                return TICK
            try:
                if playing["state"] == "sending":
                    delay = self._prepare(playlist, playing)
                else:
                    delay = self._watch(playlist, playing)
            except TVUnavailable as e:
                self._seen = None
                self._problem = str(e)
                return RETRY
            except OSError:
                log.exception("A playlist picture could not be read")
                self._seen = None
                self._problem = "A picture in the playlist could not be read from the library."
                return RETRY
            self._problem = None
            return delay

    def _prepare(self, playlist: dict, playing: dict) -> float:
        """Send one picture the TV does not have yet, or start playing once it has them all."""
        # Whatever was removed with the remote since the app last looked has to be sent again.
        self.library.reconcile(self.tv.photo_ids(), None, False)
        missing = next((p for p in self.playlists.get(playlist["id"])["pictures"] if not p["on_tv"]), None)
        if missing:
            self._send(missing["id"])
            return SOON
        self._step(playlist, playing, 1)
        return TICK

    def _watch(self, playlist: dict, playing: dict) -> float:
        seen = self.tv.now_showing()
        ids = [p["id"] for p in playlist["pictures"]]
        if seen["current"] != playing["content_id"]:
            self._seen = None
            # Acted on the second time it is seen: the TV can take a moment to report a change of picture.
            if self._astray == seen["current"]:
                self._astray = None
                self._follow(playlist, playing, seen["current"], seen["art_mode"], may_be_gone=True)
            else:
                self._astray = seen["current"]
            return TICK
        self._astray = None
        if playing["image_id"] not in ids:
            # Taken out of the playlist while it was up.
            self._step(playlist, playing, 1)
            return TICK
        if playing["paused"] or not seen["art_mode"]:
            self._seen = None
        else:
            now = self._clock()
            shown_for = playing["shown_for"] + (now - self._seen if self._seen is not None else 0)
            self._seen = now
            if shown_for >= playlist["interval"] and len(ids) > 1:
                self._step(playlist, playing, 1)
                return TICK
            self.playlists.note(shown_for=shown_for)
        # A picture added since the playlist started, or taken off the TV since, is sent as soon as it is missed.
        missing = next((p for p in playlist["pictures"] if not p["on_tv"]), None)
        if missing:
            self._send(missing["id"])
            return SOON
        return TICK

    def _follow(self, playlist: dict, playing: dict, content_id: str | None, showing: bool,
                may_be_gone: bool = False) -> None:
        """The TV has a picture the playlist did not put up. Carry on from it if it is one of the playlist's."""
        image_id = self.library.copy_of(content_id) if content_id else None
        if image_id in [p["id"] for p in playlist["pictures"]]:
            cycle = playing["cycle"]
            if cycle and image_id in cycle and playing["image_id"] in cycle:
                # It jumps the queue, so the rest of the shuffle still gets its turn.
                cycle = [queued for queued in cycle if queued != image_id]
                cycle.insert(cycle.index(playing["image_id"]) + 1, image_id)
            self.playlists.note(image_id=image_id, content_id=content_id, shown_for=0, cycle=cycle)
            self._seen = self._clock() if showing and not playing["paused"] else None
        elif may_be_gone and playing["content_id"] not in self.tv.photo_ids():
            # Its copy was removed from the TV, which is not the same as choosing something else.
            self.library.close_copy(playing["content_id"])
            self._step(playlist, playing, 1)
        else:
            self._end(playlist, "another picture was chosen")

    def _step(self, playlist: dict, playing: dict, direction: int) -> None:
        ids = [p["id"] for p in playlist["pictures"]]
        cycle = (playing["cycle"] or []) if playlist["shuffle"] else None
        image_id, cycle = neighbour(ids, playing["image_id"], direction, cycle, self._rng)
        if image_id is None:
            raise Refused("This is the first picture of the shuffle.")
        self._show(image_id, cycle)

    def _show(self, image_id: str, cycle: list[str] | None) -> None:
        """Put a playlist picture up, sending it first if the TV has lost its copy."""
        image = self.library.get(image_id)
        on_tv = self.tv.photo_ids()
        for content_id in image["tv"]:
            if content_id not in on_tv:
                self.library.close_copy(content_id)
        held = [content_id for content_id in image["tv"] if content_id in on_tv]
        content_id = held[-1] if held else self._send(image_id)
        showing = self.tv.select(content_id)
        self.playlists.note(state="playing", image_id=image_id, content_id=content_id, shown_for=0, cycle=cycle)
        if showing:
            self.library.shown(content_id)
        self._seen = self._clock() if showing else None
        self._astray = None

    def _send(self, image_id: str) -> str:
        image = self.library.get(image_id)
        content_id = self.tv.upload(self.library.path(image["file"]).read_bytes())
        if not content_id:
            raise TVUnavailable("The TV did not confirm the upload.")
        self.library.record_sent(image_id, content_id)
        return content_id

    def _end(self, playlist: dict, why: str) -> None:
        self.playlists.stop()
        self._seen = self._astray = self._problem = None
        self.ended = f"{playlist['name']} stopped because {why}."

    def _running(self) -> tuple[dict, dict]:
        """The playlist that is playing, and where it has got to."""
        playing = self.playlists.playing()
        if not playing:
            raise Refused("No playlist is playing.")
        if playing["state"] == "sending":
            raise Refused("The playlist's pictures are still being sent to the TV.")
        return self.playlists.get(playing["playlist_id"]), playing

    def play(self, playlist_id: int) -> None:
        """Run this playlist in place of any other. The clock sends its pictures, then starts it."""
        with self._lock:
            self.playlists.begin(playlist_id)
            self._seen = self._astray = self._problem = self.ended = None
        self._wake.set()

    def stop(self, remove: bool = False) -> dict:
        """Stop the playlist. With `remove`, also take its pictures off the TV. They stay in the library."""
        with self._lock:
            playing = self.playlists.stop()
            if not playing:
                raise Refused("No playlist is playing.")
            self._seen = self._astray = self._problem = self.ended = None
            removed = left = 0
            if remove:
                pictures = self.playlists.get(playing["playlist_id"])["pictures"]
                theirs = [content_id for p in pictures for content_id in self.library.get(p["id"])["tv"]]
                for done, content_id in enumerate(theirs):
                    try:
                        gone = self.tv.delete(content_id)
                    except TVUnavailable:
                        # The rest would only wait for the same answer.
                        left = len(theirs) - done
                        break
                    if gone:
                        self.library.close_copy(content_id)
                        removed += 1
                    else:
                        left += 1
            return {"removed": removed, "left": left}

    def pause(self, paused: bool) -> None:
        """Hold the picture that is up, or let the clock run again."""
        with self._lock:
            self._running()
            self.playlists.note(paused=paused)
            self._seen = None
        self._wake.set()

    def skip(self, direction: int) -> None:
        """Move on to the next picture now, or back to the one before."""
        with self._lock:
            self._step(*self._running(), direction)

    def chosen(self, content_id: str, showing: bool) -> None:
        """Someone set this picture as the art by hand."""
        with self._lock:
            playing = self.playlists.playing()
            if not playing or playing["state"] != "playing" or playing["content_id"] == content_id:
                return
            self._astray = None
            self._follow(self.playlists.get(playing["playlist_id"]), playing, content_id, showing)

    def leaving(self, playlist_id: int, image_id: str) -> None:
        """A picture is about to be taken out of a playlist. If it is the one that is up, move on first."""
        with self._lock:
            playing = self.playlists.playing()
            if not playing or playing["state"] != "playing":
                return
            if (playing["playlist_id"], playing["image_id"]) != (playlist_id, image_id):
                return
            playlist = self.playlists.get(playlist_id)
            if len(playlist["pictures"]) > 1:
                try:
                    self._step(playlist, playing, 1)
                except (TVUnavailable, OSError):
                    # The clock moves on from it when it next reaches the TV.
                    pass

    def changed(self) -> None:
        """A playlist was edited. Bring a running shuffle in line, and have the clock look now."""
        with self._lock:
            playing = self.playlists.playing()
            if playing and playing["state"] == "playing":
                playlist = self.playlists.get(playing["playlist_id"])
                ids = [p["id"] for p in playlist["pictures"]]
                cycle = _settled(ids, playing["image_id"], playing["cycle"], self._rng) if playlist["shuffle"] else None
                if cycle != playing["cycle"]:
                    self.playlists.note(cycle=cycle)
        self._wake.set()

    def status(self) -> dict | None:
        """What is playing, for the pages. Does not ask the TV anything."""
        playing = self.playlists.playing()
        playlist = self.playlists.get(playing["playlist_id"]) if playing else None
        if not playlist:
            return None
        names = {p["id"]: p["name"] for p in playlist["pictures"]}

        def brief(image_id):
            return {"id": image_id, "name": names[image_id]} if image_id in names else None

        seen = self._seen
        shown_for = playing["shown_for"] + (self._clock() - seen if seen is not None else 0)
        cycle = (playing["cycle"] or []) if playlist["shuffle"] else None
        return {
            "playlist": {key: playlist[key] for key in ("id", "name", "interval", "shuffle")},
            "state": playing["state"],
            "paused": playing["paused"],
            "on_tv": sum(p["on_tv"] for p in playlist["pictures"]),
            "total": len(names),
            "current": brief(playing["image_id"]),
            "next": brief(upcoming(list(names), playing["image_id"], cycle)),
            # Seconds of Art Mode left before the picture changes, and whether they are passing now.
            "remaining": max(0, round(playlist["interval"] - shown_for)),
            "counting": seen is not None,
            "problem": self._problem,
        }
