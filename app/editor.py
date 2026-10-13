"""Crop, letterbox and side-by-side processing for Frame TV pictures.

Ported from image_crop_upscale/app.py.
"""
import math

from PIL import Image, ImageOps

PRESETS = {
    '4k': {'width': 3840, 'height': 2160, 'name': '4K Ultra HD'},
    'fhd': {'width': 1920, 'height': 1080, 'name': 'Full HD'}
}


def open_oriented(path):
    """Open an image the way a browser displays it.

    Applies the EXIF rotation so crop coordinates picked on screen line up
    with the pixels, and converts to RGB so any source can be saved as JPEG.
    """
    with Image.open(path) as img:
        return ImageOps.exif_transpose(img).convert('RGB')


def _crop(img, crop_coords):
    """Crop to the requested rectangle, trimmed to the edges of the image.

    Raises ValueError if the rectangle is not finite or misses the image.
    """
    x, y = crop_coords['x'], crop_coords['y']
    edges = (x, y, x + crop_coords['width'], y + crop_coords['height'])
    if not all(math.isfinite(edge) for edge in edges):
        raise ValueError('the crop must be finite numbers')
    left = max(0, int(edges[0]))
    top = max(0, int(edges[1]))
    right = min(img.width, int(edges[2]))
    bottom = min(img.height, int(edges[3]))
    if right <= left or bottom <= top:
        raise ValueError('the crop is outside the picture')
    return img.crop((left, top, right, bottom))


def crop_and_upscale(input_path, output_path, crop_coords, target_width, target_height, letterbox=False):
    """
    Crop and upscale image to target resolution

    Args:
        input_path: Path to input image
        output_path: Path to save processed image
        crop_coords: Dict with x, y, width, height (in pixels)
        target_width: Target output width
        target_height: Target output height
        letterbox: If True, fit image within target maintaining aspect ratio
                   and fill remaining space with black bars
    """
    cropped = _crop(open_oriented(input_path), crop_coords)

    if letterbox:
        # Scale to fit within target while maintaining aspect ratio
        crop_w, crop_h = cropped.size
        scale = min(target_width / crop_w, target_height / crop_h)
        new_w = min(target_width, round(crop_w * scale))
        new_h = min(target_height, round(crop_h * scale))
        resized = cropped.resize((new_w, new_h), Image.Resampling.LANCZOS)

        # Center on a black canvas
        canvas = Image.new('RGB', (target_width, target_height), (0, 0, 0))
        paste_x = (target_width - new_w) // 2
        paste_y = (target_height - new_h) // 2
        canvas.paste(resized, (paste_x, paste_y))

        canvas.save(output_path, quality=95, optimize=True)
    else:
        # Resize to target resolution using high-quality Lanczos resampling
        resized = cropped.resize((target_width, target_height), Image.Resampling.LANCZOS)

        # Save with high quality
        resized.save(output_path, quality=95, optimize=True)

    return output_path


def crop_and_combine_diptych(input_path1, input_path2, output_path, crop1, crop2, target_width, target_height, gap_percent=1.0):
    """
    Crop two images and combine them side-by-side on a single canvas.

    The layout uses zero-waste math: image 1 is scaled to target height,
    image 2 fills the remaining width, with only a thin gap between them.
    """
    gap = round(target_width * gap_percent / 100)

    cropped1 = _crop(open_oriented(input_path1), crop1)

    # Scale image 1 to target height, preserving aspect ratio
    sw1 = round(cropped1.width * target_height / cropped1.height)
    sw2 = target_width - gap - sw1
    # Checked before any resize: a very wide first crop would otherwise be scaled up first.
    if sw1 < 1 or sw2 < 1:
        raise ValueError('the first picture leaves no room for a second')

    resized1 = cropped1.resize((sw1, target_height), Image.Resampling.LANCZOS)

    cropped2 = _crop(open_oriented(input_path2), crop2)
    resized2 = cropped2.resize((sw2, target_height), Image.Resampling.LANCZOS)

    # Create black canvas and paste both images
    canvas = Image.new('RGB', (target_width, target_height), (0, 0, 0))
    canvas.paste(resized1, (0, 0))
    canvas.paste(resized2, (sw1 + gap, 0))

    canvas.save(output_path, quality=95, optimize=True)

    return output_path
