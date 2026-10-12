"""Sources a browser shows differently from how Pillow reads them."""
from PIL import Image

from app.editor import crop_and_upscale


def test_exif_rotation_matches_browser(tmp_path):
    """Crop coordinates refer to the picture as displayed, after EXIF rotation"""
    # Stored landscape (red left, blue right) with a "rotate 90 CW" tag, so
    # it displays as a 200x400 portrait: red on top, blue on the bottom.
    source = tmp_path / 'rotated.jpg'
    img = Image.new('RGB', (400, 200), color='red')
    img.paste((0, 0, 255), (200, 0, 400, 200))
    exif = Image.Exif()
    exif[0x0112] = 6
    img.save(source, exif=exif, quality=95)

    output = tmp_path / 'out.jpg'
    bottom_half = {'x': 0, 'y': 200, 'width': 200, 'height': 200}
    crop_and_upscale(source, output, bottom_half, 1920, 1080)

    with Image.open(output) as result:
        r, g, b = result.getpixel((960, 540))
        assert b > 200 and r < 60 and g < 60


def test_transparent_png_saves_as_jpeg(tmp_path):
    """A PNG with an alpha channel is processed without error"""
    source = tmp_path / 'transparent.png'
    Image.new('RGBA', (800, 600), (255, 0, 0, 128)).save(source)

    output = tmp_path / 'out.jpg'
    crop = {'x': 0, 'y': 0, 'width': 800, 'height': 450}
    crop_and_upscale(source, output, crop, 3840, 2160)

    with Image.open(output) as result:
        assert result.size == (3840, 2160)
        assert result.mode == 'RGB'
