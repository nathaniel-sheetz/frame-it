from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response

from . import config, images
from .tv import FrameTV, NotAPhoto, TVUnavailable, check_photo_id

cfg = config.load()
tv = FrameTV(cfg)
app = FastAPI(title="Frame TV Art")

STATIC = Path(__file__).parent / "static"
config.THUMB_DIR.mkdir(parents=True, exist_ok=True)


@app.exception_handler(TVUnavailable)
def tv_unavailable(_, exc: TVUnavailable):
    return JSONResponse(status_code=503, content={"detail": str(exc)})


@app.exception_handler(NotAPhoto)
def not_a_photo(_, exc: NotAPhoto):
    return JSONResponse(status_code=404, content={"detail": "No such photo."})


def _thumb_path(content_id: str) -> Path:
    return config.THUMB_DIR / f"{content_id}.jpg"


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/art")
def list_art():
    cached = {p.stem for p in config.THUMB_DIR.glob("*.jpg")}
    snap = tv.snapshot(cached)
    for content_id, data in snap["thumbs"].items():
        _thumb_path(content_id).write_bytes(data)
    live = {p["content_id"] for p in snap["photos"]}
    for stale in cached - live:
        _thumb_path(stale).unlink(missing_ok=True)
    return {
        "art_mode": snap["art_mode"],
        "current": snap["current"],
        "items": [
            {
                "id": p["content_id"],
                "width": p.get("width"),
                "height": p.get("height"),
                "date": p.get("image_date"),
                "matte": p.get("matte_id"),
            }
            for p in snap["photos"]
        ],
    }


@app.get("/api/art/{content_id}/thumbnail")
def thumbnail(content_id: str):
    path = _thumb_path(check_photo_id(content_id))
    if not path.exists():
        data = tv.thumbnail(content_id)
        if not data:
            raise HTTPException(404, "No thumbnail for that photo.")
        path.write_bytes(data)
    return Response(path.read_bytes(), media_type="image/jpeg", headers={"Cache-Control": "max-age=86400"})


@app.post("/api/art")
def upload(file: UploadFile = File(...), crop: bool = Form(True), show: bool = Form(False)):
    try:
        jpeg = images.prepare(file.file.read(), crop)
    except images.BadImage as e:
        raise HTTPException(400, str(e))
    content_id = tv.upload(jpeg)
    if not content_id:
        raise HTTPException(502, "The TV did not confirm the upload.")
    shown = tv.select(content_id) if show else False
    return {"id": content_id, "selected": show, "shown": shown}


@app.post("/api/art/{content_id}/select")
def select(content_id: str):
    return {"id": content_id, "shown": tv.select(content_id)}


@app.delete("/api/art/{content_id}")
def delete(content_id: str):
    if not tv.delete(content_id):
        raise HTTPException(502, "The TV did not confirm the delete.")
    _thumb_path(content_id).unlink(missing_ok=True)
    return {"id": content_id, "deleted": True}
