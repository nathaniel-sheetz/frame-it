"""Sending a picture to the TV and noting it in the library. Shared by the editor and the library."""
import logging
import sqlite3

from fastapi import HTTPException

from . import images
from .tv import TVUnavailable

log = logging.getLogger(__name__)


def deliver(tv, data: bytes, show: bool) -> dict:
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


def record(library, sent: dict, keep) -> dict:
    """Note in the library that a picture is now on the TV.

    `keep` returns the library picture, saving it first if it is new. Like the
    display step, a failure here is a warning: the picture has already been sent.
    """
    try:
        image = keep()
        library.record_sent(image["id"], sent["id"])
        if sent["shown"]:
            library.shown(sent["id"])
    except (OSError, sqlite3.Error):
        log.exception("A sent picture could not be recorded in the library")
        sent.setdefault("warning", "Sent to the TV, but it could not be saved to the library.")
    return sent


def _fitted(library, original: dict, name: str, crop: bool):
    """The automatic fit of a library picture, and a way to keep it as a result of that picture."""
    try:
        jpeg = images.prepare(library.path(original["file"]).read_bytes(), crop)
    except images.BadImage as e:
        raise HTTPException(400, str(e))
    return jpeg, lambda: library.keep_result(
        jpeg, name, {"type": "auto", "crop": crop}, [(original["id"], None)], origin="auto-fit")


def send_fitted(tv, library, original: dict, name: str, crop: bool, show: bool) -> dict:
    """Send a library picture with the automatic fit, keeping what was sent as a result of it."""
    jpeg, keep = _fitted(library, original, name, crop)
    return record(library, deliver(tv, jpeg, show), keep)


def fit(library, original: dict, name: str) -> dict:
    """Keep the automatic fit of a library picture without sending it. The same picture gives the same fit."""
    return _fitted(library, original, name, True)[1]()
