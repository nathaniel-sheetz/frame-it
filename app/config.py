import tomllib
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOKEN_FILE = ROOT / "tv-token.txt"
THUMB_DIR = ROOT / "data" / "thumbs"
# Where sources were kept before the library held them. Only swept now.
EDITOR_UPLOAD_DIR = ROOT / "data" / "editor" / "uploads"
EDITOR_PROCESSED_DIR = ROOT / "data" / "editor" / "processed"


@dataclass(frozen=True)
class Config:
    tv_host: str
    tv_port: int
    tv_name: str
    tv_timeout: float
    server_host: str
    server_port: int
    library_path: Path = ROOT / "data" / "library"


def load() -> Config:
    with open(ROOT / "config.toml", "rb") as f:
        raw = tomllib.load(f)
    tv, server = raw["tv"], raw.get("server", {})
    library = Path(raw.get("library", {}).get("path", "data/library")).expanduser()
    return Config(
        tv_host=tv["host"],
        tv_port=tv.get("port", 8002),
        tv_name=tv.get("name", "FrameArt"),
        tv_timeout=tv.get("timeout", 30),
        server_host=server.get("host", "0.0.0.0"),
        server_port=server.get("port", 8080),
        # A relative path starts at the checkout.
        library_path=ROOT / library,
    )
