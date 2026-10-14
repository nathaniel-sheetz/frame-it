"""HTTP API for the picture library: what is kept, and moving it on and off the TV."""
import re
from typing import Annotated

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import sending
from .editor_routes import MAX_NAME, _stem
from .library import StillOnTV, summary
from .tv import TVUnavailable

# Pictures are only ever addressed by ids the library generated.
_ID = re.compile(r"[0-9a-f]{32}")


class SendRequest(BaseModel):
    show: bool = False


Details = Annotated[list[Annotated[str, Field(max_length=100)]], Field(max_length=50)]


class EditRequest(BaseModel):
    name: str | None = Field(None, max_length=MAX_NAME)
    creators: Details | None = None
    subjects: Details | None = None


def create_router(tv, library) -> APIRouter:
    router = APIRouter(prefix="/api/library")

    def find(image_id: str) -> dict:
        image = library.get(image_id) if _ID.fullmatch(image_id) else None
        if not image:
            raise HTTPException(404, "No such picture.")
        return image

    @router.get("")
    def list_pictures():
        images = library.list()
        return {"items": images, **summary(images)}

    @router.get("/{image_id}")
    def picture(image_id: str):
        return find(image_id)

    @router.patch("/{image_id}")
    def edit(image_id: str, req: EditRequest):
        try:
            return library.edit(find(image_id)["id"], req.name, req.creators, req.subjects)
        except ValueError as e:
            raise HTTPException(400, str(e))

    @router.get("/{image_id}/thumb")
    def thumb(image_id: str):
        path = library.thumb_path(find(image_id)["id"])
        if not path.is_file():
            raise HTTPException(404, "No preview for that picture.")
        # A picture never changes once it is kept, so its preview can be cached for good.
        return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "max-age=31536000, immutable"})

    @router.get("/{image_id}/view")
    def view(image_id: str):
        try:
            path = library.view_path(find(image_id))
        except OSError:
            raise HTTPException(404, "That picture's file is missing.")
        return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "max-age=31536000, immutable"})

    @router.get("/{image_id}/file")
    def file(image_id: str):
        path = library.path(find(image_id)["file"])
        if not path.is_file():
            raise HTTPException(404, "That picture's file is missing.")
        return FileResponse(path)

    @router.post("/{image_id}/send")
    def send(image_id: str, req: SendRequest = SendRequest()):
        image = find(image_id)
        if not library.path(image["file"]).is_file():
            raise HTTPException(404, "That picture's file is missing.")
        if image["kind"] == "original":
            # An original is never sent as it is: the TV gets a copy fitted to the screen.
            return sending.send_fitted(tv, library, image, f"{_stem(image['name'])}_fit.jpg", True, req.show)
        if image["tv"]:
            raise HTTPException(409, "That picture is already on the TV.")
        sent = sending.deliver(tv, library.path(image["file"]).read_bytes(), req.show)
        return sending.record(library, sent, lambda: image)

    @router.post("/{image_id}/display")
    def display(image_id: str):
        image = find(image_id)
        if not image["tv"]:
            raise HTTPException(409, "That picture is not on the TV.")
        content_id = image["tv"][-1]
        try:
            shown = tv.select(content_id)
        except TVUnavailable as e:
            raise HTTPException(503, str(e))
        if shown:
            library.shown(content_id)
        return {"id": content_id, "shown": shown}

    @router.post("/{image_id}/remove-from-tv")
    def remove_from_tv(image_id: str):
        image = find(image_id)
        for content_id in image["tv"]:
            try:
                removed = tv.delete(content_id)
            except TVUnavailable as e:
                raise HTTPException(503, str(e))
            if not removed:
                raise HTTPException(502, "The TV did not confirm the delete.")
            library.close_copy(content_id)
        return {"id": image["id"], "removed": image["tv"]}

    @router.put("/{image_id}/tags/building-block")
    def tag(image_id: str):
        library.tag(find(image_id)["id"])
        return {"id": image_id, "building_block": True}

    @router.delete("/{image_id}/tags/building-block")
    def untag(image_id: str):
        library.untag(find(image_id)["id"])
        return {"id": image_id, "building_block": False}

    @router.delete("/{image_id}")
    def delete(image_id: str):
        try:
            library.delete(find(image_id)["id"])
        except StillOnTV:
            raise HTTPException(409, "That picture is on the TV. Remove it from the TV first.")
        return {"id": image_id, "deleted": True}

    return router
