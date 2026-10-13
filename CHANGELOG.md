# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versions are four-part: `MAJOR.MINOR.PATCH.MICRO`.

## [0.1.0.0] - 2026-10-12

### Added

- Picture editor at `/editor`: upload a picture, crop it for the Frame's
  3840×2160 panel, review the result, then send it to the TV or download it.
- Letterbox mode ("Allow blank space") for pictures that should keep their own
  shape, with black bars filling the rest of the panel.
- Side-by-side mode: two pictures on one canvas with an adjustable gap of 1–10%,
  and a "Cancel second picture" button to back out of it.
- "Send to TV" with an optional "Display now". The processed JPEG is sent
  byte-for-byte, with no second re-encode. If the picture arrives but cannot be
  put on display, the editor says so instead of reporting a failure, so a retry
  does not upload a second copy.
- Editor API under `/api/editor`: `upload`, `source/{name}`, `process`,
  `process-diptych`, `result/{name}` and `result/{name}/send`.
- Uploads accept JPEG (including the multi-picture JPEGs phones and cameras
  write), PNG, WEBP and BMP up to 50 MB. The type is taken from the file's
  content, not its name.
- Crops are trimmed to the edges of the picture; a crop that is not a finite
  rectangle or falls entirely outside the picture is refused with a clear
  message.
- Working files live in `data/editor/`; files older than 24 hours are removed at
  startup and on each upload. A file that cannot be removed is skipped.
- Test suite for the editor (`tests/`), run with `pytest`.
- `VERSION` and this changelog.

### Changed

- The gallery page links to the editor.
- `todos.md` is ignored by git.
