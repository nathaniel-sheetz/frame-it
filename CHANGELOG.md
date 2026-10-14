# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versions are four-part: `MAJOR.MINOR.PATCH.MICRO`.

## [0.3.0.0] - 2026-10-13

### Added

- A picture library at `/library`. Every picture brought in is kept, and so is
  every result sent to the TV or saved from the editor, with the originals and
  crop settings it was made from. Nothing in it is deleted automatically.
- The library page shows one grid with filters for On TV, Ready and Building
  blocks, and a box that finds pictures by name, creator or subject. Send a
  picture again, take it off the TV without losing it, or open an original in
  the editor.
- A box at the top of the library page takes any number of picture files or
  links and keeps them without sending them anywhere.
- Tapping a picture opens it in a popup, where it can be renamed and given
  creators and subjects. A result also shows the creators and subjects of the
  originals it was made from. The popup loads a preview of up to 1600 px
  instead of the whole file.
- Originals are tagged as building blocks when an automatic 16:9 fit would cut
  off more than 10%, or leave them under 1920 wide. The tag can be changed by
  hand.
- "Save to library" in the editor keeps a result without sending it.
- Library API under `/api/library`: list, one picture, `PATCH` for `name`,
  `creators` and `subjects`, `thumb`, `view`, `file`, `send`, `display`,
  `remove-from-tv`, the building block tag, and `DELETE`.
  `POST /api/editor/result/{name}/save` keeps a result.
- `[library] path` in `config.toml` sets where the library lives. The default
  is `data/library`.
- Tests for the library (`tests/test_library.py`, `tests/test_library_api.py`),
  the gallery routes (`tests/test_main_api.py`) and the config
  (`tests/test_config.py`).

### Changed

- Sources are library pictures now: `upload` and `fetch` add to the library,
  and the same file a second time is the same entry. They are no longer removed
  after 24 hours. Processed results that are neither sent nor saved still are.
- The gallery names the pictures the library holds and marks the rest "Not in
  library". Removing a picture from the TV keeps it in the library, and a
  removal made with the remote is noticed the next time the gallery loads.
- Sending from the editor takes a `name`, and what is sent is kept in the
  library under it.
- A picture's name is kept on one line and at 200 characters at most. Names
  suggested for results stay within that, so a very long file name no longer
  stops a picture from being sent or its details from being saved.

### Fixed

- The gallery no longer fails as if the TV were unreachable when the TV drops
  the connection that carries thumbnails. The transfer is asked for again, and
  the list comes back without the thumbnails the TV will not send.
- Sending a library picture whose file has gone from the disk says so instead
  of failing with an unexplained error.
- A picture or preview that could not be written in full (a full disk, say) is
  no longer left behind half-written and served as if it were whole.
- Renaming a picture whose name has no extension, such as "Screenshot
  10.30.45", no longer keeps the last part of the old name.
- The library loads on Python 3.13 and earlier.

## [0.2.0.0] - 2026-10-13

### Added

- Add a picture from a link. Paste or drop a link in the gallery, or paste one
  into either picture slot of the editor, and the server downloads it.
- The gallery now stages one picture at a time and offers a choice: "Send to TV"
  with the automatic fit, or "Custom fit" to open the same picture in the
  editor without uploading it again.
- `POST /api/editor/fetch` downloads a source from a link and answers like
  `upload`. `POST /api/editor/source/{name}/send` sends a stored source to the
  TV with the automatic fit (`crop`, `show`).
- Link downloads are limited to `http` and `https` addresses on the public
  internet. This computer, the home network (including its own IPv6 network)
  and addresses that only look public are refused, on the first address and
  after every redirect. Proxy settings are ignored.
- Link downloads stop at 50 MB and at 30 seconds. A web page, a download cut
  short and an error from the site each get their own message.
- Tests for the downloader (`tests/test_fetch.py`) and the new routes.

### Changed

- The gallery adds one picture at a time; the multi-file quick upload is gone.
- Pictures added from the gallery must be JPEG, PNG, WEBP or BMP and at most
  50 MB, the same as the editor. GIF, TIFF and other formats that the old quick
  upload accepted are now refused.
- The gallery's link to the editor is labelled "Editor".
- In the editor, a picture that arrives after another one was chosen, or after
  the second picture was cancelled, is ignored instead of replacing it.
- Working files are also cleared out on each fetch.

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
