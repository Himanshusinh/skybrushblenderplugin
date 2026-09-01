# Skybrush Setup

Everything needed to run the Skybrush server and the Blender plugin on this
machine is already installed. Day to day, the only thing you need to do is
run `start_server.bat`.

## Running the server

Double-click **`start_server.bat`**. It prints its progress and then serves on
<http://localhost:5000>.

To stop it, press `Ctrl+C` in the server window or double-click
**`stop_server.bat`**.

The launcher repairs itself: if `uv` is missing it installs it, and if the
Python environment is missing or broken it rebuilds it before starting. A
normal start takes a couple of seconds; a rebuild takes a minute or two and
needs an internet connection.

## Using the Blender plugin

The **Skybrush Studio 5.0.0** add-on is installed for **Blender 4.4**
(`%APPDATA%\Blender Foundation\Blender\4.4\scripts\addons`). You need to enable
it once:

1. Open **Blender 4.4** (not 5.2 — see the note below).
2. Go to `Edit` > `Preferences` > `Add-ons`.
3. Search for `Skybrush` and tick the **Skybrush Studio** checkbox.
4. Close preferences. The `Skybrush` tab appears in the sidebar of the 3D
   viewport (press `N` if the sidebar is hidden).

Start the server before exporting a show, since the plugin talks to it over
`http://localhost:5000`.

Use Blender 4.4 rather than the 5.2 install you also have. This add-on is a
legacy `bl_info` add-on targeting Blender 4.4, and Blender 5.x dropped support
for that format in favour of extensions.

## What was set up

| Component | Detail |
| --- | --- |
| `uv` package manager | 0.12.8, installed to `%USERPROFILE%\.local\bin` |
| Server environment | `skybrush-server\.venv`, 89 runtime packages |
| Blender dev environment | `studio-blender\.venv`, 17 dev packages |
| Blender add-on | Skybrush Studio 5.0.0, installed into Blender 4.4 |
| Python | 3.11.9 (Microsoft Store build, already on the machine) |

Both `.venv` folders that came with the project were copies from another
machine and pointed at `C:\Users\Flame\...\python.exe`, so nothing in them
could run. They were deleted and rebuilt from `uv.lock`.

## Configuration

The server starts with `skybrush-server\etc\conf\skybrush-virtual.jsonc`, which
creates one simulated drone — good for testing without hardware. To use real
drones, change the `CONFIG` line near the top of `start_server.bat` to another
file in that folder, for example `skybrush-outdoor.jsonc`.

## Troubleshooting

**`No suitable backend found for scanning the USB bus`** — harmless with the
virtual configuration. It only matters when connecting drones over USB, which
needs the libusb driver installed.

**Port 5000 already in use** — run `stop_server.bat`, or find the other process
with `netstat -ano | findstr :5000`.

**Plugin cannot reach the server** — confirm `start_server.bat` is running and
that <http://localhost:5000> loads in a browser.

**Rebuilding the add-on ZIP** — the prebuilt ZIP is at
`studio-blender\dist\skybrush-studio-for-blender-5.0.0.zip`. Rebuilding it
requires `bash` (Git Bash or WSL), since the build script is a shell script:
`bash etc/scripts/create_blender_dist.sh`.
