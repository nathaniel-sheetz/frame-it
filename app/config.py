import tomllib
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOKEN_FILE = ROOT / "tv-token.txt"
THUMB_DIR = ROOT / "data" / "thumbs"
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


def load() -> Config:
    with open(ROOT / "config.toml", "rb") as f:
        raw = tomllib.load(f)
    tv, server = raw["tv"], raw.get("server", {})
    return Config(
        tv_host=tv["host"],
        tv_port=tv.get("port", 8002),
        tv_name=tv.get("name", "FrameArt"),
        tv_timeout=tv.get("timeout", 30),
        server_host=server.get("host", "0.0.0.0"),
        server_port=server.get("port", 8080),
    )
