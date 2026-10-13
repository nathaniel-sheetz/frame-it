"""HTTP API for the picture editor: bring in a source, process it, send the result to the TV."""
import io
import logging
import re
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, Field

from . import editor, fetch, images
from .tv import TVUnavailable

log = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 50 * 1024 * 1024
MAX_AGE_SECONDS = 24 * 60 * 60
# Pillow reports multi-picture JPEGs (many phones and cameras) as MPO.
FORMATS = {"JPEG": "jpg", "MPO": "jpg", "PNG": "png", "WEBP": "webp", "BMP": "bmp"}

# Files are only ever addressed by names this module generated.
_SOURCE_NAME = re.compile(r"[0-9a-f]{32}\.(jpg|png|webp|bmp)")
_RESULT_NAME = re.compile(r"[0-9a-f]{32}\.jpg")


class Crop(BaseModel):
    x: float
    y: float
    width: float = Field(gt=0)
    height: float = Field(gt=0)


class ProcessRequest(BaseModel):
    filename: str
    original_filename: str = "image.jpg"
    preset: str = "4k"
    crop: Crop
    letterbox: bool = False


class DiptychRequest(BaseModel):
    filename1: str
    filename2: str
    original_filename1: str = "image1.jpg"
    original_filename2: str = "image2.jpg"
    preset: str = "4k"
    crop1: Crop
    crop2: Crop
    gap_percent: float = 1.0


class SendRequest(BaseModel):
    show: bool = False


class FetchRequest(BaseModel):
    url: str = Field(min_length=1, max_length=2048)


class SourceSendRequest(BaseModel):
    crop: bool = True
    show: bool = False


def _sweep(folder: Path) -> None:
    """Delete working files left over from earlier sessions.

    Best effort: a file that cannot be removed (open elsewhere on Windows) is
    left for the next sweep, and a folder deleted while running is recreated.
    """
    cutoff = time.time() - MAX_AGE_SECONDS
    folder.mkdir(parents=True, exist_ok=True)
    for path in folder.iterdir():
        try:
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)
        except OSError:
            continue


def _stem(original_filename: str) -> str:
    """A safe base name for the suggested download name."""
    stem = Path(original_filename.replace("\\", "/")).stem
    return re.sub(r"[^\w\- ]", "_", stem).strip() or "image"


def _download_name(name: str | None, fallback: str) -> str:
    name = re.sub(r"[^\w\-. ]", "_", name or "").strip(" .") or fallback
    return name if name.lower().endswith(".jpg") else f"{name}.jpg"


def create_router(tv, upload_dir: Path, processed_dir: Path) -> APIRouter:
    router = APIRouter(prefix="/api/editor")
    for folder in (upload_dir, processed_dir):
        _sweep(folder)

    def source_path(name: str) -> Path:
        path = upload_dir / name
        if not _SOURCE_NAME.fullmatch(name) or not path.is_file():
            raise HTTPException(404, "File not found")
        return path

    def result_path(name: str) -> Path:
        path = processed_dir / name
        if not _RESULT_NAME.fullmatch(name) or not path.is_file():
            raise HTTPException(404, "File not found")
        return path

    def target(preset: str) -> dict:
        if preset not in editor.PRESETS:
            raise HTTPException(400, "Invalid preset")
        return editor.PRESETS[preset]

    def result(output: Path, suggested_filename: str) -> dict:
        return {
            "success": True,
            "filename": output.name,
            "suggested_filename": suggested_filename,
            "url": f"/api/editor/result/{output.name}",
            "download_url": f"/api/editor/result/{output.name}?download=1",
        }

    def store(data: bytes, original_filename: str, refusal: str = "Invalid file type") -> dict:
        """Check that the bytes are a picture the editor accepts and keep them as a source."""
        try:
            with Image.open(io.BytesIO(data), formats=["JPEG", "PNG", "WEBP", "BMP"]) as img:
                extension = FORMATS.get(img.format)
                if not extension:
                    raise HTTPException(400, refusal)
                width, height = ImageOps.exif_transpose(img).size
        except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError):
            raise HTTPException(400, refusal)

        _sweep(upload_dir)
        _sweep(processed_dir)
        name = f"{uuid.uuid4().hex}.{extension}"
        (upload_dir / name).write_bytes(data)
        return {
            "success": True,
            "filename": name,
            "original_filename": original_filename,
            "width": width,
            "height": height,
            "url": f"/api/editor/source/{name}",
        }

    def deliver(data: bytes, show: bool) -> dict:
        try:
            content_id = tv.upload(data)
        except TVUnavailable as e:
            raise HTTPException(503, str(e))
        if not content_id:
            raise HTTPException(502, "The TV did not confirm the upload.")
        sent = {"id": content_id, "selected": show, "shown": False}
        if show:
            try:
                sent["shown"] = tv.select(content_id)
            except TVUnavailable:
                # The picture is on the TV by now, so this is not an error to retry:
                # sending again would upload a second copy.
                sent["selected"] = False
                sent["warning"] = "Sent to the TV, but it could not be put on display."
        return sent

    @router.post("/upload")
    def upload(file: UploadFile = File(...)):
        data = file.file.read(MAX_UPLOAD_BYTES + 1)
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "File too large. Maximum size is 50MB.")
        return store(data, file.filename or "image.jpg")

    @router.post("/fetch")
    def fetch_link(req: FetchRequest):
        try:
            data, name = fetch.fetch(req.url, MAX_UPLOAD_BYTES)
        except fetch.FetchError as e:
            raise HTTPException(e.status, str(e))
        return store(data, name, "That link is not a picture this app can use (JPEG, PNG, WEBP or BMP).")

    @router.get("/source/{name}")
    def source(name: str):
        return FileResponse(source_path(name))

    @router.post("/source/{name}/send")
    def send_source(name: str, req: SourceSendRequest = SourceSendRequest()):
        # The quick path, skipping the editor: the same automatic fit as POST /api/art.
        try:
            jpeg = images.prepare(source_path(name).read_bytes(), req.crop)
        except images.BadImage as e:
            raise HTTPException(400, str(e))
        return deliver(jpeg, req.show)

    @router.post("/process")
    def process(req: ProcessRequest):
        size = target(req.preset)
        output = processed_dir / f"{uuid.uuid4().hex}.jpg"
        try:
            editor.crop_and_upscale(
                source_path(req.filename), output, req.crop.model_dump(),
                size["width"], size["height"], letterbox=req.letterbox,
            )
        except ValueError as e:
            raise HTTPException(400, f"That crop cannot be processed: {e}")
        except OSError:
            # Logged, not returned: the message carries server paths.
            log.exception("Editor processing failed")
            raise HTTPException(500, "Processing failed.")
        return result(output, f"{_stem(req.original_filename)}_{req.preset}.jpg")

    @router.post("/process-diptych")
    def process_diptych(req: DiptychRequest):
        size = target(req.preset)
        gap_percent = max(1.0, min(10.0, req.gap_percent))
        output = processed_dir / f"{uuid.uuid4().hex}.jpg"
        try:
            editor.crop_and_combine_diptych(
                source_path(req.filename1), source_path(req.filename2), output,
                req.crop1.model_dump(), req.crop2.model_dump(),
                size["width"], size["height"], gap_percent=gap_percent,
            )
        except ValueError as e:
            # Raised when the first crop is so wide there is no room for a second picture.
            raise HTTPException(400, f"That pair cannot be processed: {e}")
        except OSError:
            log.exception("Editor processing failed")
            raise HTTPException(500, "Processing failed.")
        name = f"{_stem(req.original_filename1)}_{_stem(req.original_filename2)}_pair_{req.preset}.jpg"
        return result(output, name)

    @router.get("/result/{filename}")
    def get_result(filename: str, download: bool = False, name: str | None = None):
        path = result_path(filename)
        if download:
            return FileResponse(path, media_type="image/jpeg", filename=_download_name(name, f"frame_tv_{filename}"))
        return FileResponse(path, media_type="image/jpeg")

    @router.post("/result/{filename}/send")
    def send(filename: str, req: SendRequest = SendRequest()):
        # The processed JPEG goes to the TV byte-for-byte; no second re-encode.
        return deliver(result_path(filename).read_bytes(), req.show)

    return router
