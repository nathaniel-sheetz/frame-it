"""HTTP API for playlists: what is in them, and running one on the TV."""
import re
from typing import Annotated

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from . import sending
from .editor_routes import _stem
from .player import Refused
from .playlists import DEFAULT_INTERVAL, MAX_INTERVAL, MAX_NAME, MIN_INTERVAL
from .tv import TVUnavailable

_ID = re.compile(r"[0-9a-f]{32}")

Interval = Annotated[int, Field(ge=MIN_INTERVAL, le=MAX_INTERVAL)]
Pictures = Annotated[list[str], Field(max_length=500)]


class NewPlaylist(BaseModel):
    name: str = Field(max_length=MAX_NAME)
    interval: Interval = DEFAULT_INTERVAL
    shuffle: bool = False
    pictures: Pictures = []


class Changes(BaseModel):
    name: str | None = Field(None, max_length=MAX_NAME)
    interval: Interval | None = None
    shuffle: bool | None = None
    order: Pictures | None = None


class AddRequest(BaseModel):
    pictures: Pictures = Field(min_length=1)


class StopRequest(BaseModel):
    remove: bool = False


def create_router(player, playlists, library) -> APIRouter:
    router = APIRouter(prefix="/api")

    def find(playlist_id: int) -> dict:
        playlist = playlists.get(playlist_id)
        if not playlist:
            raise HTTPException(404, "No such playlist.")
        return playlist

    def now() -> dict:
        return {"playing": player.status(), "ended": player.ended}

    def playable(image_ids: list[str]) -> tuple[list[str], list[str]]:
        """What a playlist holds for these library pictures, and the names of those it cannot take.

        An original goes in as its automatic fit to the screen, which is what the
        TV would be sent. A building block needs editing first.
        """
        images = [library.get(image_id) if _ID.fullmatch(image_id) else None for image_id in image_ids]
        if not all(images):
            raise HTTPException(404, "No such picture.")
        ready, refused = [], []
        for image in images:
            if image["building_block"]:
                refused.append(image["name"])
            elif image["kind"] == "original":
                if not library.path(image["file"]).is_file():
                    raise HTTPException(404, "That picture's file is missing.")
                ready.append(sending.fit(library, image, f"{_stem(image['name'])}_fit.jpg")["id"])
            else:
                ready.append(image["id"])
        return ready, refused

    def add_pictures(playlist_id: int, ready: list[str], refused: list[str]) -> dict:
        added = playlists.add(playlist_id, ready)
        player.changed()
        return {"playlist": playlists.get(playlist_id), "added": added, "refused": refused}

    def control(action) -> dict:
        try:
            done = action()
        except Refused as e:
            raise HTTPException(409, str(e))
        except TVUnavailable as e:
            raise HTTPException(503, str(e))
        return {**(done or {}), **now()}

    @router.get("/playlists")
    def list_playlists():
        return {"items": playlists.all(), **now()}

    @router.post("/playlists")
    def create(req: NewPlaylist):
        pictures = playable(req.pictures)
        try:
            playlist = playlists.create(req.name, req.interval, req.shuffle)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return add_pictures(playlist["id"], *pictures)

    @router.get("/playlists/{playlist_id}")
    def playlist(playlist_id: int):
        return find(playlist_id)

    @router.patch("/playlists/{playlist_id}")
    def edit(playlist_id: int, req: Changes):
        find(playlist_id)
        try:
            changed = playlists.edit(playlist_id, req.name, req.interval, req.shuffle, req.order)
        except ValueError as e:
            raise HTTPException(400, str(e))
        player.changed()
        return changed

    @router.delete("/playlists/{playlist_id}")
    def delete(playlist_id: int):
        find(playlist_id)
        # Deleting the playlist that is running stops it. Its pictures stay where they are.
        playlists.delete(playlist_id)
        return {"id": playlist_id, "deleted": True}

    @router.post("/playlists/{playlist_id}/pictures")
    def add(playlist_id: int, req: AddRequest):
        return add_pictures(find(playlist_id)["id"], *playable(req.pictures))

    @router.delete("/playlists/{playlist_id}/pictures/{image_id}")
    def remove(playlist_id: int, image_id: str):
        find(playlist_id)
        player.leaving(playlist_id, image_id)
        playlists.remove(playlist_id, image_id)
        player.changed()
        return playlists.get(playlist_id)

    @router.post("/playlists/{playlist_id}/play")
    def play(playlist_id: int):
        if not find(playlist_id)["pictures"]:
            raise HTTPException(409, "Add pictures to the playlist first.")
        return control(lambda: player.play(playlist_id))

    @router.get("/playing")
    def playing():
        return now()

    @router.post("/playing/next")
    def skip():
        return control(lambda: player.skip(1))

    @router.post("/playing/back")
    def back():
        return control(lambda: player.skip(-1))

    @router.post("/playing/pause")
    def pause():
        return control(lambda: player.pause(True))

    @router.post("/playing/resume")
    def resume():
        return control(lambda: player.pause(False))

    @router.post("/playing/stop")
    def stop(req: StopRequest = StopRequest()):
        return control(lambda: player.stop(req.remove))

    return router
