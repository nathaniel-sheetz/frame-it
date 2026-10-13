# Frame TV Art

A small web app for managing the "My Photos" artwork on a Samsung Frame TV from
any browser on the same network.

- **Gallery** (`/`): see what is on the TV, set the current art, remove pictures,
  and quick-upload with an automatic centre crop.
- **Editor** (`/editor`): crop a picture for the Frame's 3840×2160 panel, with
  optional letterboxing or a side-by-side pair, review the result, and send it
  straight to the TV.

Built on [NickWaterton/samsung-tv-ws-api](https://github.com/NickWaterton/samsung-tv-ws-api).
The editor is ported from [image_crop_upscale](https://github.com/nathaniel-sheetz/image_crop_upscale).
Tested against a QN55LS03D (2024), art API 5.0.1.0.

## Run

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m app
```

Then open `http://<this-computer's-ip>:8080`. The TV's address and the server
port are in `config.toml`.

The first connection makes the TV show an "Allow" prompt; the resulting token
is saved to `tv-token.txt`.

## What it will and won't do to the TV

- Only talks to the art channel. It never sends power or remote-key commands.
- Only lists, sets, and removes pictures in "My Photos". Art Store content is
  not touched.
- If Art Mode is off (someone is watching TV), setting a picture selects it
  without putting it on screen.

## API

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/art` | Art Mode state, current picture, list of pictures |
| GET | `/api/art/{id}/thumbnail` | JPEG thumbnail |
| POST | `/api/art` | Upload (`file`, `crop`, `show` form fields) |
| POST | `/api/art/{id}/select` | Set as current art |
| DELETE | `/api/art/{id}` | Remove from the TV |
| POST | `/api/editor/upload` | Upload a source picture to edit (`file` form field, max 50 MB) |
| GET | `/api/editor/source/{name}` | The uploaded source |
| POST | `/api/editor/process` | Crop and scale (`filename`, `crop`, `letterbox`, `preset`, `original_filename`) |
| POST | `/api/editor/process-diptych` | Two crops side by side (`filename1/2`, `crop1/2`, `gap_percent`, `preset`, `original_filename1/2`) |
| GET | `/api/editor/result/{name}` | The processed JPEG; add `?download=1&name=...` to download |
| POST | `/api/editor/result/{name}/send` | Send the processed JPEG to the TV unchanged (`show`) |

Editor working files live in `data/editor/`; files older than 24 hours are removed at startup and on each upload.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest
```

The tests use a fake TV, so they run without one.

## Probe

`scripts/probe.py read` and `scripts/probe.py write` exercise the TV's art API
directly. Run them after a TV firmware update if the app stops working.
