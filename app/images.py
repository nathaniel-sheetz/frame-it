import io

from PIL import Image, ImageOps, UnidentifiedImageError

SCREEN = (3840, 2160)


class BadImage(Exception):
    pass


def prepare(data: bytes, crop: bool) -> bytes:
    """Return a JPEG sized for the Frame's 4K panel.

    crop=True centre-crops to exactly 16:9 so the picture fills the screen.
    crop=False keeps the whole picture and only shrinks it if it is oversized.
    """
    try:
        img = Image.open(io.BytesIO(data))
        img = ImageOps.exif_transpose(img).convert("RGB")
    except (UnidentifiedImageError, OSError) as e:
        raise BadImage("That file is not an image this app can read.") from e

    if crop:
        img = ImageOps.fit(img, SCREEN, Image.Resampling.LANCZOS)
    else:
        img.thumbnail(SCREEN, Image.Resampling.LANCZOS)

    out = io.BytesIO()
    img.save(out, format="JPEG", quality=92)
    return out.getvalue()
