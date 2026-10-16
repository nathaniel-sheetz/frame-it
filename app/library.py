"""The picture library: every original brought in, and every result sent to the TV or saved.

Nothing here talks to the TV. It records which pictures have a copy on it, and
is told when that changes.
"""
import hashlib
import io
import json
import re
import sqlite3
import threading
import uuid
from collections.abc import Iterable
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageOps

BUILDING_BLOCK = "building-block"
THUMB_SIZE = (480, 480)
# Large enough to look at on a phone or in a popup, without sending the whole file.
VIEW_SIZE = (1600, 1600)
# What a picture can be described by, and the name each list goes by.
DETAILS = {"creator": "creators", "subject": "subjects"}
# An automatic fit that keeps less than this much of a picture means it needs work first.
MIN_KEPT = 0.90
# Narrower than this after the fit, a picture is stretched more than 2x to fill the screen.
MIN_FIT_WIDTH = 1920

_SCHEMA = """
CREATE TABLE IF NOT EXISTS images (
    id TEXT PRIMARY KEY,
    file TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('original', 'result')),
    name TEXT NOT NULL,
    width INTEGER NOT NULL,
    height INTEGER NOT NULL,
    bytes INTEGER NOT NULL,
    sha256 TEXT NOT NULL UNIQUE,
    origin TEXT NOT NULL,
    origin_url TEXT,
    recipe TEXT,
    added_at TEXT NOT NULL,
    last_shown_at TEXT
);
CREATE TABLE IF NOT EXISTS parents (
    image_id TEXT NOT NULL REFERENCES images(id) ON DELETE CASCADE,
    parent_id TEXT REFERENCES images(id) ON DELETE SET NULL,
    position INTEGER NOT NULL,
    crop TEXT,
    PRIMARY KEY (image_id, position)
);
CREATE TABLE IF NOT EXISTS tags (
    image_id TEXT NOT NULL REFERENCES images(id) ON DELETE CASCADE,
    tag TEXT NOT NULL,
    PRIMARY KEY (image_id, tag)
);
CREATE TABLE IF NOT EXISTS details (
    image_id TEXT NOT NULL REFERENCES images(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('creator', 'subject')),
    value TEXT NOT NULL COLLATE NOCASE,
    PRIMARY KEY (image_id, kind, value)
);
CREATE TABLE IF NOT EXISTS tv_copies (
    id INTEGER PRIMARY KEY,
    image_id TEXT NOT NULL REFERENCES images(id) ON DELETE CASCADE,
    content_id TEXT NOT NULL,
    sent_at TEXT NOT NULL,
    removed_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS tv_copies_open ON tv_copies(content_id) WHERE removed_at IS NULL;
"""

# The details of everything a picture was made from, however many steps back.
_MADE_FROM = """
WITH RECURSIVE made_from(image_id, ancestor) AS (
    SELECT image_id, parent_id FROM parents WHERE parent_id IS NOT NULL{only}
    UNION
    SELECT m.image_id, p.parent_id FROM made_from m JOIN parents p ON p.image_id = m.ancestor
    WHERE p.parent_id IS NOT NULL
)
SELECT m.image_id, d.kind, d.value FROM made_from m JOIN details d ON d.image_id = m.ancestor
ORDER BY d.rowid
"""

# Pictures that something made from them, however many steps on, is on the TV or ready.
# Only results have parents, and a result is on the TV, else a building block if tagged, else ready.
_WORKED_INTO = """
WITH RECURSIVE made_into(image_id, descendant) AS (
    SELECT parent_id, image_id FROM parents WHERE parent_id IS NOT NULL{only}
    UNION
    SELECT m.image_id, p.image_id FROM made_into m JOIN parents p ON p.parent_id = m.descendant
)
SELECT DISTINCT m.image_id FROM made_into m
WHERE EXISTS (SELECT 1 FROM tv_copies c WHERE c.image_id = m.descendant AND c.removed_at IS NULL)
   OR NOT EXISTS (SELECT 1 FROM tags t WHERE t.image_id = m.descendant AND t.tag = ?)
"""

# Only a picture's extension counts as one: "v1.2" and "Screenshot 10.30.45" have none.
_EXTENSION = re.compile(r"\.(jpe?g|png|webp|bmp)$", re.IGNORECASE)


class StillOnTV(Exception):
    """The picture has a copy on the TV, so it cannot be deleted yet."""


def is_building_block(width: int, height: int) -> bool:
    """Whether a picture needs cropping, pairing or enlarging before it suits the screen."""
    fit_width = min(width, height * 16 / 9)
    kept = fit_width * (fit_width * 9 / 16) / (width * height)
    return kept < MIN_KEPT or fit_width < MIN_FIT_WIDTH


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _jpeg(image: Image.Image, size: tuple[int, int], quality: int) -> bytes:
    """A JPEG of the picture no larger than `size`. Shrinks the image it is given."""
    image.thumbnail(size)
    out = io.BytesIO()
    image.convert("RGB").save(out, format="JPEG", quality=quality)
    return out.getvalue()


def _thumbnail(image: Image.Image) -> bytes:
    return _jpeg(image, THUMB_SIZE, 80)


def _write(path: Path, data: bytes) -> None:
    """Write a file whole or not at all, so a write cut short never passes for the picture."""
    partial = path.with_name(f"{path.name}.part")
    try:
        partial.write_bytes(data)
        partial.replace(path)
    except OSError:
        partial.unlink(missing_ok=True)
        raise


def _distinct(values: Iterable[str]) -> list[str]:
    """Tidied values in the order given, without blanks or the same one twice in other capitals."""
    kept, seen = [], set()
    for value in values:
        value = " ".join(value.split())
        if value and value.casefold() not in seen:
            seen.add(value.casefold())
            kept.append(value)
    return kept


class Library:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.files = self.root / "files"
        self.thumbs = self.root / "thumbs"
        self.views = self.root / "views"
        for folder in (self.files, self.thumbs, self.views):
            folder.mkdir(parents=True, exist_ok=True)
        # One writer at a time, so the same picture arriving twice at once is still one entry.
        self._lock = threading.Lock()
        with self._db() as db:
            db.executescript(_SCHEMA)

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.root / "library.db", timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def path(self, file: str) -> Path:
        return self.files / file

    def thumb_path(self, image_id: str) -> Path:
        return self.thumbs / f"{image_id}.jpg"

    def view_path(self, image: dict) -> Path:
        """The picture at a size for looking at, made the first time it is asked for."""
        path = self.views / f"{image['id']}.jpg"
        with self._lock:
            if not path.is_file():
                with Image.open(self.path(image["file"])) as full:
                    _write(path, _jpeg(ImageOps.exif_transpose(full), VIEW_SIZE, 85))
        return path

    def _add(self, db, image_id, data, extension, thumb, **columns) -> None:
        file = f"{image_id}.{extension}"
        row = {"id": image_id, "file": file, "bytes": len(data), "added_at": _now(), **columns}
        try:
            _write(self.path(file), data)
            _write(self.thumb_path(image_id), thumb)
            db.execute(
                f"INSERT INTO images ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})",
                list(row.values()),
            )
        except (OSError, sqlite3.Error):
            # A picture that was not recorded leaves no files behind.
            self.path(file).unlink(missing_ok=True)
            self.thumb_path(image_id).unlink(missing_ok=True)
            raise

    def add_original(self, data: bytes, name: str, extension: str, image: Image.Image,
                     origin: str, origin_url: str | None = None) -> dict:
        """Keep a picture that was brought in. The same bytes a second time are the same entry.

        `image` is the decoded picture, turned the way it is displayed. It is shrunk here.
        """
        sha256 = hashlib.sha256(data).hexdigest()
        with self._lock, self._db() as db:
            known = db.execute("SELECT id FROM images WHERE sha256 = ?", (sha256,)).fetchone()
            if known:
                return self._get(db, known["id"])
            image_id = uuid.uuid4().hex
            width, height = image.size
            self._add(db, image_id, data, extension, _thumbnail(image), kind="original", name=name,
                      width=width, height=height, sha256=sha256, origin=origin, origin_url=origin_url)
            if is_building_block(width, height):
                db.execute("INSERT INTO tags (image_id, tag) VALUES (?, ?)", (image_id, BUILDING_BLOCK))
            return self._get(db, image_id)

    def keep_result(self, data: bytes, name: str, recipe: dict | None, parents: list[tuple[str, dict | None]],
                    origin: str = "editor", image_id: str | None = None) -> dict:
        """Keep a JPEG made for the TV, with what it was made from and how.

        `parents` are (library id, crop) in order. Keeping the same result again changes nothing.
        """
        sha256 = hashlib.sha256(data).hexdigest()
        with self._lock, self._db() as db:
            known = db.execute("SELECT id FROM images WHERE sha256 = ? OR id = ?", (sha256, image_id)).fetchone()
            if known:
                return self._get(db, known["id"])
            image_id = image_id or uuid.uuid4().hex
            with Image.open(io.BytesIO(data)) as image:
                width, height = image.size
                thumb = _thumbnail(image)
            self._add(db, image_id, data, "jpg", thumb, kind="result", name=name, width=width, height=height,
                      sha256=sha256, origin=origin, recipe=json.dumps(recipe) if recipe else None)
            for position, (parent_id, crop) in enumerate(parents, start=1):
                # An original deleted since the result was made leaves a gap, not an error.
                held = db.execute("SELECT 1 FROM images WHERE id = ?", (parent_id,)).fetchone()
                db.execute(
                    "INSERT INTO parents (image_id, parent_id, position, crop) VALUES (?, ?, ?, ?)",
                    (image_id, parent_id if held else None, position, json.dumps(crop) if crop else None),
                )
            return self._get(db, image_id)

    def _details(self, db, image_id: str | None = None) -> tuple[dict, dict]:
        """Each picture's own details, and those of what it was made from, by id then kind.

        For one picture when `image_id` is given, otherwise for all of them.
        """
        only = (image_id,) if image_id else ()
        own, inherited = {}, {}
        for r in db.execute(
                f"SELECT image_id, kind, value FROM details{' WHERE image_id = ?' if image_id else ''} ORDER BY rowid", only):
            own.setdefault(r["image_id"], {}).setdefault(r["kind"], []).append(r["value"])
        for r in db.execute(_MADE_FROM.format(only=" AND image_id = ?" if image_id else ""), only):
            inherited.setdefault(r["image_id"], {}).setdefault(r["kind"], []).append(r["value"])
        return own, inherited

    def _worked_into(self, db, image_id: str | None = None) -> set[str]:
        """The pictures that have been made into something on the TV or ready, however many steps on.

        For one picture when `image_id` is given, otherwise for all of them.
        """
        only = (image_id,) if image_id else ()
        query = _WORKED_INTO.format(only=" AND parent_id = ?" if image_id else "")
        return {r["image_id"] for r in db.execute(query, (*only, BUILDING_BLOCK))}

    def _describe(self, row, tags: set, copies: list[str], used_in: int, own: dict, inherited: dict,
                  worked_into: bool) -> dict:
        tagged = BUILDING_BLOCK in tags
        details = {"from_originals": {}}
        for kind, field in DETAILS.items():
            details[field] = own.get(kind, [])
            # A detail the picture has itself is not listed again as coming from its original.
            details["from_originals"][field] = _distinct(details[field] + inherited.get(kind, []))[len(details[field]):]
        if copies:
            status = "on-tv"
        elif tagged:
            status = "block"
        elif row["kind"] == "result" or not used_in:
            status = "ready"
        else:
            # An original that suits the screen and has already been made into a picture.
            status = "original"
        return {
            "id": row["id"],
            "file": row["file"],
            "kind": row["kind"],
            "name": row["name"],
            "width": row["width"],
            "height": row["height"],
            "bytes": row["bytes"],
            "added_at": row["added_at"],
            "last_shown_at": row["last_shown_at"],
            "status": status,
            "building_block": tagged,
            "tv": copies,
            "used_in": used_in,
            "worked_into": worked_into,
            **details,
        }

    def _get(self, db, image_id: str) -> dict | None:
        row = db.execute("SELECT * FROM images WHERE id = ?", (image_id,)).fetchone()
        if not row:
            return None
        tags = {r["tag"] for r in db.execute("SELECT tag FROM tags WHERE image_id = ?", (image_id,))}
        copies = [r["content_id"] for r in db.execute(
            "SELECT content_id FROM tv_copies WHERE image_id = ? AND removed_at IS NULL ORDER BY id", (image_id,))]
        used_in = db.execute("SELECT COUNT(*) FROM parents WHERE parent_id = ?", (image_id,)).fetchone()[0]
        own, inherited = self._details(db, image_id)
        image = self._describe(row, tags, copies, used_in, own.get(image_id, {}), inherited.get(image_id, {}),
                               image_id in self._worked_into(db, image_id))
        image["recipe"] = json.loads(row["recipe"]) if row["recipe"] else None
        image["parents"] = [
            {
                "id": r["parent_id"],
                "file": r["file"],
                "name": r["name"],
                "crop": json.loads(r["crop"]) if r["crop"] else None,
            }
            for r in db.execute(
                "SELECT p.parent_id, p.crop, i.file, i.name FROM parents p"
                " LEFT JOIN images i ON i.id = p.parent_id WHERE p.image_id = ? ORDER BY p.position", (image_id,))
        ]
        return image

    def get(self, image_id: str) -> dict | None:
        """One picture, with its recipe and the originals it was made from."""
        with self._db() as db:
            return self._get(db, image_id)

    def list(self) -> list[dict]:
        """Every picture, newest first."""
        with self._db() as db:
            tags, copies, used_in = {}, {}, {}
            for r in db.execute("SELECT image_id, tag FROM tags"):
                tags.setdefault(r["image_id"], set()).add(r["tag"])
            for r in db.execute("SELECT image_id, content_id FROM tv_copies WHERE removed_at IS NULL ORDER BY id"):
                copies.setdefault(r["image_id"], []).append(r["content_id"])
            for r in db.execute("SELECT parent_id, COUNT(*) AS n FROM parents WHERE parent_id IS NOT NULL GROUP BY parent_id"):
                used_in[r["parent_id"]] = r["n"]
            own, inherited = self._details(db)
            worked_into = self._worked_into(db)
            return [
                self._describe(row, tags.get(row["id"], set()), copies.get(row["id"], []), used_in.get(row["id"], 0),
                               own.get(row["id"], {}), inherited.get(row["id"], {}), row["id"] in worked_into)
                for row in db.execute("SELECT * FROM images ORDER BY added_at DESC, rowid DESC")
            ]

    # Not list[str]: from here down in this class, `list` is the method above.
    def edit(self, image_id: str, name: str | None = None, creators: Iterable[str] | None = None,
             subjects: Iterable[str] | None = None) -> dict | None:
        """Rename a picture, or say who made it and what it shows. Anything left as None stays as it is."""
        with self._db() as db:
            row = db.execute("SELECT name FROM images WHERE id = ?", (image_id,)).fetchone()
            if not row:
                return None
            if name is not None:
                # Names of pictures made from this one are built from the part before
                # the extension, so the extension stays whatever is typed.
                extension = _EXTENSION.search(row["name"])
                extension = extension.group() if extension else ""
                name = " ".join(name.split())
                if extension and name.lower().endswith(extension.lower()):
                    name = name[:-len(extension)].rstrip()
                if not name:
                    raise ValueError("A picture needs a name.")
                db.execute("UPDATE images SET name = ? WHERE id = ?", (name + extension, image_id))
            for kind, values in (("creator", creators), ("subject", subjects)):
                if values is None:
                    continue
                db.execute("DELETE FROM details WHERE image_id = ? AND kind = ?", (image_id, kind))
                db.executemany("INSERT INTO details (image_id, kind, value) VALUES (?, ?, ?)",
                               [(image_id, kind, value) for value in _distinct(values)])
            return self._get(db, image_id)

    def tag(self, image_id: str) -> None:
        with self._db() as db:
            db.execute("INSERT OR IGNORE INTO tags (image_id, tag) VALUES (?, ?)", (image_id, BUILDING_BLOCK))

    def untag(self, image_id: str) -> None:
        with self._db() as db:
            db.execute("DELETE FROM tags WHERE image_id = ? AND tag = ?", (image_id, BUILDING_BLOCK))

    def record_sent(self, image_id: str, content_id: str) -> None:
        """Note that the TV now holds this picture under `content_id`."""
        with self._db() as db:
            # Whatever the TV called by that id before is gone if it is handing the id out again.
            db.execute("UPDATE tv_copies SET removed_at = ? WHERE content_id = ? AND removed_at IS NULL", (_now(), content_id))
            db.execute("INSERT INTO tv_copies (image_id, content_id, sent_at) VALUES (?, ?, ?)", (image_id, content_id, _now()))

    def close_copy(self, content_id: str) -> None:
        """Note that the TV no longer holds `content_id`. The library picture stays."""
        with self._db() as db:
            db.execute("UPDATE tv_copies SET removed_at = ? WHERE content_id = ? AND removed_at IS NULL", (_now(), content_id))

    def copy_of(self, content_id: str) -> str | None:
        """The id of the picture the TV holds under `content_id`, if the app sent it."""
        with self._db() as db:
            row = db.execute(
                "SELECT image_id FROM tv_copies WHERE content_id = ? AND removed_at IS NULL", (content_id,)).fetchone()
            return row["image_id"] if row else None

    def shown(self, content_id: str) -> None:
        """Note that the TV has this copy on display now."""
        with self._db() as db:
            db.execute(
                "UPDATE images SET last_shown_at = ? WHERE id IN"
                " (SELECT image_id FROM tv_copies WHERE content_id = ? AND removed_at IS NULL)", (_now(), content_id))

    def reconcile(self, on_tv: set[str], current: str | None, art_mode: bool) -> dict[str, dict]:
        """Bring the record of TV copies in line with what the TV reports.

        Copies removed some other way (the remote, the phone app) are closed. Returns
        the library picture behind each content id that is still there.
        """
        with self._db() as db:
            held = {r["content_id"]: {"id": r["id"], "name": r["name"]} for r in db.execute(
                "SELECT c.content_id, i.id, i.name FROM tv_copies c JOIN images i ON i.id = c.image_id"
                " WHERE c.removed_at IS NULL")}
            for content_id in held.keys() - on_tv:
                db.execute("UPDATE tv_copies SET removed_at = ? WHERE content_id = ? AND removed_at IS NULL", (_now(), content_id))
            if art_mode and current in held and current in on_tv:
                db.execute("UPDATE images SET last_shown_at = ? WHERE id = ?", (_now(), held[current]["id"]))
            return {content_id: image for content_id, image in held.items() if content_id in on_tv}

    def delete(self, image_id: str) -> None:
        """Remove a picture and its files. Pictures made from it stay, without their original."""
        with self._lock, self._db() as db:
            row = db.execute("SELECT file FROM images WHERE id = ?", (image_id,)).fetchone()
            if not row:
                return
            if db.execute("SELECT 1 FROM tv_copies WHERE image_id = ? AND removed_at IS NULL", (image_id,)).fetchone():
                raise StillOnTV(image_id)
            db.execute("DELETE FROM images WHERE id = ?", (image_id,))
            self.path(row["file"]).unlink(missing_ok=True)
            self.thumb_path(image_id).unlink(missing_ok=True)
            (self.views / f"{image_id}.jpg").unlink(missing_ok=True)


def summary(images: list[dict]) -> dict:
    """How many pictures there are, under each filter, and the disk space they take."""
    return {
        "count": len(images),
        "bytes": sum(image["bytes"] for image in images),
        "on_tv": sum(image["status"] == "on-tv" for image in images),
        "ready": sum(image["status"] == "ready" for image in images),
        "blocks": sum(image["building_block"] for image in images),
    }
