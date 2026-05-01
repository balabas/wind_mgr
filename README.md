# wind_mgr

`wind_mgr` is an X11/GTK window graph for Ubuntu/GNOME-style desktops. It shows open windows as D3/SVG cards, groups related windows into geometry hulls, and keeps card thumbnails updated without replacing the SVG overlays.

## Current Behavior

- Shows live windows as SVG cards inside a WebKit/D3 graph.
- Preserves active-window border/overlay, selection, hulls, links, labels, and drag feedback because thumbnails update inside SVG `<image>` elements.
- Refreshes thumbnails by priority:
  - hovered card: `hover_refresh_interval`
  - active window: `active_refresh_interval`
  - inactive windows: `background_refresh_interval` and `background_refresh_min_interval`
- Uses a single capture queue so thumbnail captures do not run in parallel.
- Capture priority is: manual, hover, focus-leave, live-preview-idle, background, active.
- Supports moving cards between geometries. Drag move carries same-geometry descendants with the parent; children already separated into another geometry stay separated.
- Optional native XComposite/OpenGL popup preview exists, but is disabled by default because it renders above SVG overlays.

## Install

Install system packages first:

```bash
sudo apt install -y \
  python3-gi python3-gi-cairo python3-cairo \
  gir1.2-gtk-3.0 gir1.2-gdkpixbuf-2.0 gir1.2-wnck-3.0 \
  gir1.2-webkit2-4.1 gir1.2-ayatanaappindicator3-0.1 \
  gir1.2-keybinder-3.0 \
  libxcomposite1 libxfixes3 libgl1 \
  ffmpeg x11-apps
```

Install Python package dependencies if needed:

```bash
python3 -m pip install -r requirements.txt
```

`python3-xlib` is optional and only used as a fallback global-hotkey backend if Keybinder is unavailable.

## Run

Preferred launcher:

```bash
bash ./wind_mgr.sh
```

The launcher clears common Snap/VS Code GTK environment variables and writes logs to:

```text
/tmp/windmgr.log
```

Direct run:

```bash
python3 main.py
```

## Configuration

Main settings are in `config.ini`.

Important capture settings:

- `active_refresh_interval`: seconds between active-window SVG thumbnail updates.
- `hover_refresh_interval`: seconds between hovered-card SVG thumbnail updates. Keep this lower than `active_refresh_interval` for a more responsive hover stream.
- `background_refresh_interval`: how often one inactive window is considered for refresh.
- `background_refresh_min_interval`: minimum age before the same inactive window can refresh again.
- `activity_priority_enabled`: prioritizes recently used windows for background refresh.
- `live_preview_enabled`: enables the native XComposite/OpenGL popup preview. Default is `false` to preserve SVG overlays.

Important layout settings:

- `geometrySpacing`: one spacing value for both horizontal and vertical distance between geometry groups.
- `hullPad`: padding around cards when drawing geometry hulls.
- `hullCornerRadius`: rounded hull corner radius.
- `sameProjectLinkDistance`: target spacing between linked cards inside one geometry.
- `nodeCollideRadius`: card collision radius.
- `maxZoom`: maximum zoom-in level.

## Controls

- Click card: activate that window.
- Drag card: move it between geometries.
- Middle mouse drag: pan.
- Middle double click: fit graph.
- Mouse wheel: zoom.
- Context menu on card/link: window actions and relationship actions.
- Configured hotkey: see `hotkey` in `config.ini`.

## Architecture

- `main.py`: app wiring, registry, watcher, providers, UI startup.
- `core/`: window records, activity watcher, relationship tree, activity statistics.
- `capture/`: thumbnail and icon capture.
- `bridge/`: WebKit JavaScript bridge and thumbnail scheduling.
- `web/`: D3/SVG graph UI.
- `ui/`: GTK window, tray, hotkey handling.
- `providers/`: app-specific metadata extraction.
- `live_preview/`: optional XComposite/OpenGL preview prototype and readback helper.

## Notes

This project targets X11. XComposite/GLX-based preview and some Wnck behavior are not expected to work the same way on Wayland.
