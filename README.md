# Frame TV Art

A small web app for managing the "My Photos" artwork on a Samsung Frame TV from
any browser on the same network: upload, set as current art, and remove.

Built on [NickWaterton/samsung-tv-ws-api](https://github.com/NickWaterton/samsung-tv-ws-api).
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

## Probe

`scripts/probe.py read` and `scripts/probe.py write` exercise the TV's art API
directly. Run them after a TV firmware update if the app stops working.
