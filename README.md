# Frame TV Art

A small web app for managing the "My Photos" artwork on a Samsung Frame TV from
any browser on the same network.

- **Gallery** (`/`): see what is on the TV, set the current art, and remove
  pictures. Add a picture (JPEG, PNG, WEBP or BMP) from a file or a link, then
  choose: **Send to TV** with an automatic centre crop, or **Custom fit** to
  open it in the editor.
- **Editor** (`/editor`): crop a picture for the Frame's 3840×2160 panel, with
  optional letterboxing or a side-by-side pair, review the result, and send it
  straight to the TV. Pictures can come from a file or a link here too.

Links are downloaded by the server, so it only follows `http` and `https`
addresses on the public internet (not this computer or the home network), and
gives up on anything over 50 MB or slower than 30 seconds.

Built on [NickWaterton/samsung-tv-ws-api](https://github.com/NickWaterton/samsung-tv-ws-api).
The editor is ported from [image_crop_upscale](https://github.com/nathaniel-sheetz/image_crop_upscale).
Tested against a QN55LS03D (2024), art API 5.0.1.0.

## Run

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m app
```

Then open `http://<this-computer's-ip>:8090`. The TV's address and the server
port are in `config.toml`.

Each computer pairs with the TV once. The first time the page is opened, the TV
shows an "Allow" prompt for about 30 seconds; accept it and the resulting token
is saved to `tv-token.txt`. If the prompt goes unanswered, the page says so and
reloading it brings the prompt back. To pair again, delete `tv-token.txt`.

## Always-on host (Mac Mini)

The app runs on the Mini as a system daemon: it starts at boot with nobody
logged in, runs as an ordinary user, and is restarted if it stops.

Install, with the checkout at `~/frame_tv` (the app needs Python 3.11 or later;
the system Python is older, so `uv` supplies one):

```sh
cd ~/frame_tv
uv venv --python 3.13
uv pip install -r requirements.txt -r requirements-dev.txt
.venv/bin/python -m pytest
```

Install the service. Run this from the checkout as the user the app should run
as; the first command fills that user and path into
`deploy/local.frame-tv.plist`.

```sh
sed -e "s|__USER__|$USER|g" -e "s|__HOME__|$HOME|g" -e "s|__CHECKOUT__|$PWD|g" deploy/local.frame-tv.plist \
  | sudo tee /Library/LaunchDaemons/local.frame-tv.plist > /dev/null
sudo launchctl bootstrap system /Library/LaunchDaemons/local.frame-tv.plist
```

Two one-time approvals follow, in this order:

1. **macOS.** The first time the service tries to reach the TV, macOS shows a
   popup on the Mini's own screen asking whether Python may find devices on the
   local network. Log in at the Mini, open `http://<the-mini>:8090` from any
   device to trigger it, and choose Allow. Until then the page says the TV
   could not be reached (`OSError`). The approval survives restarts and applies
   before anyone logs in.
2. **The TV.** Reload the page with someone at the TV to accept its "Allow"
   prompt, as described under Run.

Then `http://<the-mini>:8090` shows the gallery.

Update to the latest code. Stopping the app is enough to restart it, and needs
no `sudo` because it runs as you:

```sh
cd ~/frame_tv && git pull --ff-only && uv pip install -r requirements.txt
pkill -f "frame_tv/.venv/bin/python -m app"
```

The log is `~/Library/Logs/frame-tv.log`. To remove the service:
`sudo launchctl bootout system/local.frame-tv`, then delete the file from
`/Library/LaunchDaemons`.

## What it will and won't do to the TV

- Only talks to the art channel, apart from opening the remote-control channel
  once to pair. It never sends power or remote-key commands.
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
| POST | `/api/editor/fetch` | Download a source picture from a link (`url`); same answer as `upload` |
| GET | `/api/editor/source/{name}` | The uploaded or fetched source |
| POST | `/api/editor/source/{name}/send` | Send a source to the TV with the automatic fit, skipping the editor (`crop`, `show`) |
| POST | `/api/editor/process` | Crop and scale (`filename`, `crop`, `letterbox`, `preset`, `original_filename`) |
| POST | `/api/editor/process-diptych` | Two crops side by side (`filename1/2`, `crop1/2`, `gap_percent`, `preset`, `original_filename1/2`) |
| GET | `/api/editor/result/{name}` | The processed JPEG; add `?download=1&name=...` to download |
| POST | `/api/editor/result/{name}/send` | Send the processed JPEG to the TV unchanged (`show`) |

Editor working files live in `data/editor/`; files older than 24 hours are removed at startup and on each upload or fetch.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest
```

The tests use a fake TV, so they run without one.

## Probe

`scripts/probe.py read` and `scripts/probe.py write` exercise the TV's art API
directly. Run them after a TV firmware update if the app stops working.
