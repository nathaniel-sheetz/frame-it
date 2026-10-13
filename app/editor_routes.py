"""HTTP API for the picture editor: bring in a source, process it, send the result to the TV.

Sources are kept in the library as soon as they arrive. A processed result is a
working file until it is sent or saved, when it joins them.
"""
import io
import json
import logging
import re
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, Field

from . import editor, fetch, sending

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
    name: str | None = Field(None, max_length=200)


class SaveRequest(BaseModel):
    name: str | None = Field(None, max_length=200)


class FetchRequest(BaseModel):
    url: str = Field(min_length=1, max_length=2048)


class SourceSendRequest(BaseModel):
    crop: bool = True
    show: bool = False


def _sweep(folder: Path) -> None:
    """Delete working files left over from earlier sessions. Never used on the library.

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


def create_router(tv, library, processed_dir: Path) -> APIRouter:
    router = APIRouter(prefix="/api/editor")
    _sweep(processed_dir)

    def source_path(name: str) -> Path:
        path = library.path(name)
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

    def result(output: Path, suggested_filename: str, recipe: dict, sources: list[tuple[str, Crop]]) -> dict:
        # Kept beside the result, and swept with it, in case it is sent or saved.
        output.with_suffix(".json").write_text(json.dumps({
            "name": suggested_filename,
            "recipe": recipe,
            "parents": [{"id": Path(name).stem, "crop": crop.model_dump()} for name, crop in sources],
        }))
        return {
            "success": True,
            "filename": output.name,
            "suggested_filename": suggested_filename,
            "url": f"/api/editor/result/{output.name}",
            "download_url": f"/api/editor/result/{output.name}?download=1",
        }

    def keep(path: Path, name: str | None) -> dict:
        """Move a processed result into the library, with the settings that made it."""
        try:
            draft = json.loads(path.with_suffix(".json").read_text())
        except (OSError, ValueError):
            draft = {"name": f"frame_tv_{path.name}", "recipe": None, "parents": []}
        return library.keep_result(
            path.read_bytes(), _download_name(name, draft["name"]), draft["recipe"],
            [(parent["id"], parent["crop"]) for parent in draft["parents"]], image_id=path.stem,
        )

    def store(data: bytes, original_filename: str, origin: str, origin_url: str | None = None,
              refusal: str = "Invalid file type") -> dict:
        """Check that the bytes are a picture the editor accepts and keep them in the library."""
        try:
            with Image.open(io.BytesIO(data), formats=["JPEG", "PNG", "WEBP", "BMP"]) as img:
                extension = FORMATS.get(img.format)
                if not extension:
                    raise HTTPException(400, refusal)
                oriented = ImageOps.exif_transpose(img)
        except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError):
            raise HTTPException(400, refusal)

        _sweep(processed_dir)
        # A picture the library already holds comes back as the entry it has.
        image = library.add_original(data, original_filename, extension, oriented, origin, origin_url)
        return {
            "success": True,
            "filename": image["file"],
            "original_filename": image["name"],
            "width": image["width"],
            "height": image["height"],
            "url": f"/api/editor/source/{image['file']}",
        }

    @router.post("/upload")
    def upload(file: UploadFile = File(...)):
        data = file.file.read(MAX_UPLOAD_BYTES + 1)
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "File too large. Maximum size is 50MB.")
        return store(data, file.filename or "image.jpg", "upload")

    @router.post("/fetch")
    def fetch_link(req: FetchRequest):
        try:
            data, name = fetch.fetch(req.url, MAX_UPLOAD_BYTES)
        except fetch.FetchError as e:
            raise HTTPException(e.status, str(e))
        return store(data, name, "link", req.url,
                     "That link is not a picture this app can use (JPEG, PNG, WEBP or BMP).")

    @router.get("/source/{name}")
    def source(name: str):
        return FileResponse(source_path(name))

    @router.post("/source/{name}/send")
    def send_source(name: str, req: SourceSendRequest = SourceSendRequest()):
        # The quick path, skipping the editor: the same automatic fit as POST /api/art.
        source_path(name)
        original = library.get(Path(name).stem)
        if not original:
            raise HTTPException(404, "File not found")
        return sending.send_fitted(tv, library, original, f"{_stem(original['name'])}_fit.jpg", req.crop, req.show)

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
        recipe = {"type": "single", "preset": req.preset, "letterbox": req.letterbox}
        return result(output, f"{_stem(req.original_filename)}_{req.preset}.jpg", recipe, [(req.filename, req.crop)])

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
        recipe = {"type": "pair", "preset": req.preset, "gap_percent": gap_percent}
        return result(output, name, recipe, [(req.filename1, req.crop1), (req.filename2, req.crop2)])

    @router.get("/result/{filename}")
    def get_result(filename: str, download: bool = False, name: str | None = None):
        path = result_path(filename)
        if download:
            return FileResponse(path, media_type="image/jpeg", filename=_download_name(name, f"frame_tv_{filename}"))
        return FileResponse(path, media_type="image/jpeg")

    @router.post("/result/{filename}/send")
    def send(filename: str, req: SendRequest = SendRequest()):
        # The processed JPEG goes to the TV byte-for-byte; no second re-encode.
        path = result_path(filename)
        sent = sending.deliver(tv, path.read_bytes(), req.show)
        return sending.record(library, sent, lambda: keep(path, req.name))

    @router.post("/result/{filename}/save")
    def save(filename: str, req: SaveRequest = SaveRequest()):
        image = keep(result_path(filename), req.name)
        return {"success": True, "id": image["id"], "name": image["name"]}

    return router
