"""Probe what the Frame TV's art API supports on this model/firmware.

    python scripts/probe.py read    pair + read-only queries
    python scripts/probe.py write   upload a test image, select it, restore, delete it

The write stage only ever deletes the image it uploaded itself.
"""
import io
import json
import sys
import time
from pathlib import Path

from PIL import Image, ImageDraw
from samsungtvws import SamsungTVWS

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import config  # noqa: E402

CFG = config.load()
TOKEN_FILE = str(config.TOKEN_FILE)
MY_PHOTOS = "MY-C0002"


def step(name, fn):
    """Run one probe step, report the outcome, and keep going on failure."""
    try:
        result = fn()
        text = json.dumps(result, indent=2, default=str) if not isinstance(result, str) else result
        print(f"[ OK ] {name}: {text}")
        return result
    except Exception as e:  # noqa: BLE001 - we want every failure reported
        print(f"[FAIL] {name}: {type(e).__name__}: {e}")
        return None


def connect():
    tv = SamsungTVWS(host=CFG.tv_host, port=CFG.tv_port, token_file=TOKEN_FILE, timeout=60, name=CFG.tv_name)
    # Opening the remote channel is what triggers the on-screen "Allow" prompt
    # and saves the token; no key presses are sent.
    tv.open()
    tv.close()
    return tv.art()


def read_stage(art):
    step("api_version", art.get_api_version)
    step("artmode_status", art.get_artmode)
    step("current_artwork", art.get_current)
    step("device_info", art.get_device_info)
    content = step("content_list (all)", lambda: summarise(art.available()))
    step("my_photos", lambda: art.available(MY_PHOTOS))
    step("slideshow_status", art.get_slideshow_status)
    step("auto_rotation_status", art.get_auto_rotation_status)
    step("artmode_settings", art.get_artmode_settings)
    step("matte_list", lambda: art.get_matte_list(include_colour=True))
    step("photo_filter_list", art.get_photo_filter_list)
    step("rotation", art.get_rotation)
    return content


def summarise(items):
    counts = {}
    for item in items:
        counts[item.get("category_id")] = counts.get(item.get("category_id"), 0) + 1
    return {"total": len(items), "by_category": counts}


def test_image():
    img = Image.new("RGB", (3840, 2160), (24, 40, 72))
    draw = ImageDraw.Draw(img)
    for i in range(0, 3840, 160):
        draw.line([(i, 0), (3840 - i, 2160)], fill=(200, 160, 60), width=6)
    draw.rectangle([1400, 900, 2440, 1260], fill=(240, 240, 235))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def write_stage(art):
    artmode = step("artmode_status", art.get_artmode)
    previous = step("current_artwork (before)", art.get_current)
    before = {i["content_id"] for i in art.available(MY_PHOTOS)}

    data = test_image()
    t0 = time.time()
    content_id = step(f"upload ({len(data)} bytes)", lambda: art.upload(data, file_type="jpg", matte="none", portrait_matte="none"))
    if not content_id:
        return
    print(f"       upload took {time.time() - t0:.1f}s")

    try:
        step("uploaded image is listed", lambda: content_id in {i["content_id"] for i in art.available(MY_PHOTOS)})
        step("thumbnail", lambda: f"{len(art.get_thumbnail_list([content_id]) or {})} thumbnail(s)")

        # Only put it on screen if art mode is already showing; never interrupt TV viewing.
        show = artmode == "on"
        step(f"select_image (show={show})", lambda: art.select_image(content_id, show=show) or "sent")
        time.sleep(3)
        step("current_artwork (after select)", lambda: art.get_current().get("content_id"))

        if previous and previous.get("content_id"):
            step("restore previous artwork", lambda: art.select_image(previous["content_id"], show=show) or "sent")
            time.sleep(3)
            step("current_artwork (after restore)", lambda: art.get_current().get("content_id"))
    finally:
        step("delete test image", lambda: art.delete(content_id))
        after = {i["content_id"] for i in art.available(MY_PHOTOS)}
        step("my_photos unchanged after cleanup", lambda: after == before)


def main():
    stage = sys.argv[1] if len(sys.argv) > 1 else "read"
    art = connect()
    try:
        if stage == "write":
            write_stage(art)
        else:
            read_stage(art)
    finally:
        art.close()


if __name__ == "__main__":
    main()
