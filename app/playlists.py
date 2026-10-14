"""Playlists: library pictures in an order, and the one playlist that is running.

They are kept in the library's database, so a picture deleted from the library
leaves its playlists too. Nothing here talks to the TV.
"""
import json
from collections.abc import Iterable

from .library import Library

# How long one picture stays up, in seconds.
MIN_INTERVAL = 60
MAX_INTERVAL = 30 * 24 * 60 * 60
DEFAULT_INTERVAL = 60 * 60
MAX_NAME = 100

_SCHEMA = """
CREATE TABLE IF NOT EXISTS playlists (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    interval INTEGER NOT NULL,
    shuffle INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS playlist_items (
    playlist_id INTEGER NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
    image_id TEXT NOT NULL REFERENCES images(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    PRIMARY KEY (playlist_id, image_id)
);
CREATE TABLE IF NOT EXISTS playing (
    slot INTEGER PRIMARY KEY CHECK (slot = 1),
    playlist_id INTEGER NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
    state TEXT NOT NULL CHECK (state IN ('sending', 'playing')),
    paused INTEGER NOT NULL DEFAULT 0,
    image_id TEXT,
    content_id TEXT,
    shown_for REAL NOT NULL DEFAULT 0,
    cycle TEXT
);
"""

def _named(name: str) -> str:
    name = " ".join(name.split())
    if not name:
        raise ValueError("A playlist needs a name.")
    return name


class Playlists:
    def __init__(self, library: Library):
        # The same database as the pictures, opened the same way.
        self._db = library._db
        with self._db() as db:
            db.executescript(_SCHEMA)

    def _describe(self, db, row) -> dict:
        pictures = [
            {"id": r["id"], "name": r["name"], "on_tv": bool(r["on_tv"])}
            for r in db.execute(
                "SELECT i.id, i.name, EXISTS (SELECT 1 FROM tv_copies c WHERE c.image_id = i.id AND c.removed_at IS NULL) AS on_tv"
                " FROM playlist_items p JOIN images i ON i.id = p.image_id WHERE p.playlist_id = ? ORDER BY p.position",
                (row["id"],))
        ]
        return {
            "id": row["id"],
            "name": row["name"],
            "interval": row["interval"],
            "shuffle": bool(row["shuffle"]),
            "pictures": pictures,
        }

    def _get(self, db, playlist_id: int) -> dict | None:
        row = db.execute("SELECT * FROM playlists WHERE id = ?", (playlist_id,)).fetchone()
        return self._describe(db, row) if row else None

    def create(self, name: str, interval: int = DEFAULT_INTERVAL, shuffle: bool = False) -> dict:
        with self._db() as db:
            added = db.execute("INSERT INTO playlists (name, interval, shuffle) VALUES (?, ?, ?)",
                               (_named(name), interval, shuffle))
            return self._get(db, added.lastrowid)

    def get(self, playlist_id: int) -> dict | None:
        """One playlist, with its pictures in order."""
        with self._db() as db:
            return self._get(db, playlist_id)

    def all(self) -> list[dict]:
        """Every playlist, oldest first."""
        with self._db() as db:
            return [self._describe(db, row) for row in db.execute("SELECT * FROM playlists ORDER BY id")]

    def edit(self, playlist_id: int, name: str | None = None, interval: int | None = None,
             shuffle: bool | None = None, order: list[str] | None = None) -> dict | None:
        """Change a playlist. Anything left as None stays as it is.

        `order` is every picture in the playlist, in the order they should play.
        """
        with self._db() as db:
            if not db.execute("SELECT 1 FROM playlists WHERE id = ?", (playlist_id,)).fetchone():
                return None
            changes = {"name": _named(name) if name is not None else None, "interval": interval, "shuffle": shuffle}
            for column, value in changes.items():
                if value is not None:
                    db.execute(f"UPDATE playlists SET {column} = ? WHERE id = ?", (value, playlist_id))
            if order is not None:
                held = [r["image_id"] for r in db.execute(
                    "SELECT image_id FROM playlist_items WHERE playlist_id = ?", (playlist_id,))]
                if sorted(order) != sorted(held):
                    raise ValueError("The playlist has changed since that order was made.")
                db.executemany(
                    "UPDATE playlist_items SET position = ? WHERE playlist_id = ? AND image_id = ?",
                    [(position, playlist_id, image_id) for position, image_id in enumerate(order, start=1)])
            return self._get(db, playlist_id)

    def delete(self, playlist_id: int) -> None:
        with self._db() as db:
            db.execute("DELETE FROM playlists WHERE id = ?", (playlist_id,))

    def add(self, playlist_id: int, image_ids: Iterable[str]) -> int:
        """Put pictures at the end of a playlist. Returns how many were not in it already."""
        with self._db() as db:
            last = db.execute(
                "SELECT COALESCE(MAX(position), 0) FROM playlist_items WHERE playlist_id = ?", (playlist_id,)).fetchone()[0]
            added = 0
            for image_id in image_ids:
                added += db.execute(
                    "INSERT OR IGNORE INTO playlist_items (playlist_id, image_id, position) VALUES (?, ?, ?)",
                    (playlist_id, image_id, last + added + 1)).rowcount
            return added

    def remove(self, playlist_id: int, image_id: str) -> None:
        with self._db() as db:
            db.execute("DELETE FROM playlist_items WHERE playlist_id = ? AND image_id = ?", (playlist_id, image_id))

    def begin(self, playlist_id: int) -> None:
        """Make this the playlist that is running, in place of any other. Its pictures are sent first."""
        with self._db() as db:
            db.execute("INSERT OR REPLACE INTO playing (slot, playlist_id, state) VALUES (1, ?, 'sending')", (playlist_id,))

    def playing(self) -> dict | None:
        """Where the running playlist has got to, if one is running."""
        with self._db() as db:
            row = db.execute("SELECT * FROM playing").fetchone()
        if not row:
            return None
        return {
            "playlist_id": row["playlist_id"],
            "state": row["state"],
            "paused": bool(row["paused"]),
            # The picture it last put up, and the TV's copy of it.
            "image_id": row["image_id"],
            "content_id": row["content_id"],
            # Seconds the picture has spent on screen so far.
            "shown_for": row["shown_for"],
            # The order of this pass through a shuffled playlist.
            "cycle": json.loads(row["cycle"]) if row["cycle"] else None,
        }

    def note(self, **changes) -> None:
        """Change what is recorded about the running playlist."""
        values = [json.dumps(value) if column == "cycle" and value is not None else value
                  for column, value in changes.items()]
        with self._db() as db:
            db.execute(f"UPDATE playing SET {', '.join(f'{column} = ?' for column in changes)}", values)

    def stop(self) -> dict | None:
        """No playlist is running any more. Returns where the one that was had got to."""
        playing = self.playing()
        with self._db() as db:
            db.execute("DELETE FROM playing")
        return playing
