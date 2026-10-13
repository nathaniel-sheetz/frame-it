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
- **Library** (`/library`): every picture you have brought in, and every result
  you sent to the TV or saved. Send a picture again, take it off the TV without
  losing it, or open an original in the editor.

## Library

The library keeps pictures on the computer running the app, in `data/library/`
(a database, the pictures, and small previews). The folder is set by
`[library] path` in `config.toml`. Nothing in it is deleted automatically.

- **Originals** are kept as soon as they are uploaded or fetched. The same file
  a second time is the same entry.
- **Results** (the JPEGs made for the TV) are kept when they are sent, or with
  **Save to library** in the editor. Each remembers its originals and crop
  settings. Results that were processed but neither sent nor saved are removed
  after 24 hours.

Each picture carries one of these labels, and the page filters by them:

| Label | Meaning |
| --- | --- |
| On TV | A result with a copy on the TV now |
| Ready | A result that is not on the TV, or an original that suits the screen and has not been used yet |
| Building block | Tagged as needing work before it suits the screen |
| Original | An original that suits the screen and has already been made into a picture |

An original is tagged as a building block when it is added if an automatic
16:9 fit would cut off more than 10% of it, or if it would be under 1920 wide
after that fit. The tag can be removed or added by hand on the library page.

Tap a picture on the library page to look at it and to change its details:

- **Name.** The extension stays as it is. Renaming does not rename pictures
  already made from it.
- **Creators** and **subjects**, as many of each as you like. Type one and
  press Enter, or separate several with commas. Ones already in the library
  are offered as you type.
- A result also shows the creators and subjects of the originals it was made
  from, with a dashed outline. Those are changed on the original.

The box beside the filters finds pictures by name, creator or subject.

Removing a picture from the TV, here or with the TV's remote, leaves it in the
library. Pictures that were on the TV before the library existed show in the
gallery as "Not in library": the TV only hands back small thumbnails, so they
cannot be copied in.

The library is not backed up by the app. Copy the folder somewhere else if the
crop work matters to you.

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
| POST | `/api/editor/result/{name}/send` | Send the processed JPEG to the TV unchanged and keep it in the library (`show`, `name`) |
| POST | `/api/editor/result/{name}/save` | Keep the processed JPEG in the library without sending it (`name`) |
| GET | `/api/library` | Every library picture, the count under each filter, and disk use |
| GET | `/api/library/{id}` | One picture, with its crop settings and originals. `from_originals` holds the creators and subjects it takes from them |
| PATCH | `/api/library/{id}` | Change its `name`, `creators` or `subjects`. What is left out stays as it is |
| GET | `/api/library/{id}/thumb`, `/view`, `/file` | Its preview, a larger one (up to 1600 px, made when first asked for), and the picture itself |
| POST | `/api/library/{id}/send` | Send to the TV (`show`). An original is sent with the automatic fit; a result already on the TV is refused |
| POST | `/api/library/{id}/display` | Set a picture that is on the TV as current art |
| POST | `/api/library/{id}/remove-from-tv` | Remove its copy from the TV and keep the picture |
| PUT, DELETE | `/api/library/{id}/tags/building-block` | Add or remove the building block tag |
| DELETE | `/api/library/{id}` | Delete from the library. Refused while the picture is on the TV |

Sources are library pictures, so `upload` and `fetch` add to the library and
`{name}` is the library id plus the file's extension. Processed results are
working files in `data/editor/processed/` until they are sent or saved; ones
older than 24 hours are removed at startup and on each upload or fetch.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest
```

The tests use a fake TV, so they run without one.

## Probe

`scripts/probe.py read` and `scripts/probe.py write` exercise the TV's art API
directly. Run them after a TV firmware update if the app stops working.
