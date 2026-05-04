# wind_mgr

`wind_mgr` is an X11/GTK window graph for Ubuntu/GNOME-style desktops. It shows open windows as D3/SVG cards, groups related windows into card groups with visible outlines, and keeps card thumbnails updated without replacing the SVG overlays.

## Current Behavior

- Shows live windows as SVG cards inside a WebKit/D3 graph.
- Preserves active-window border/overlay, selection, group outlines, links, labels, and drag feedback because thumbnails update inside SVG `<image>` elements.
- Refreshes thumbnails by priority:
  - hovered card: `hover_refresh_interval`
  - newly opened windows: delayed by `new_window_capture_delay`, then retried by `capture_retry_interval` if capture fails
  - active window: `active_refresh_interval`
  - inactive windows: `background_refresh_interval` and `background_refresh_min_interval`
- Uses a single capture queue so thumbnail captures do not run in parallel.
- Capture priority is: manual, hover, new-window, retry, focus-leave, live-preview-idle, background, active.
- Supports moving cards between card groups. Drag move carries descendants that are still in the same group with the parent; children already separated into another group stay separated.
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

The committed `config.ini` is the default app config. If behavior depends on a
setting, commit the matching `config.ini` change with the code change.

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

Main settings are in `config.ini`. This file is tracked because many layout,
capture, and interaction parameters are part of the app behavior.

Use optional `config.user.ini` for private local overrides. It is ignored by Git
and is loaded after `config.ini`, so values there replace the committed defaults
without changing the distribution config.

Terminology:

- Card: one visual item in the graph, bound to one real desktop window.
- Card group: a set of related cards shown inside one visible outline. Older internal code may still call this a `project`.
- Group outline: the dashed/fill boundary around a card group. Older internal code may still call this a `hull`.
- Link: parent/child relationship line between two cards.

Startup settings:

- `start_hidden`: if `true`, starts tray/hotkey/edge zones without showing the main window. Use this for Ubuntu autostart.

Important capture settings:

- `active_refresh_interval`: seconds between active-window SVG thumbnail updates.
- `hover_refresh_interval`: seconds between hovered-card SVG thumbnail updates. Keep this lower than `active_refresh_interval` for a more responsive hover stream.
- `background_refresh_interval`: how often one inactive window is considered for refresh.
- `background_refresh_min_interval`: minimum age before the same inactive window can refresh again.
- `new_window_capture_delay`: waits before first capture of a new window so apps can paint their first frame.
- `capture_retry_interval`: faster retry interval for windows whose thumbnail capture failed.
- `capture_retry_max_attempts`: retry limit for failed thumbnail captures; `0` means unlimited.
- `activity_priority_enabled`: prioritizes recently used windows for background refresh.
- `live_preview_enabled`: enables the native XComposite/OpenGL popup preview. Default is `false` to preserve SVG overlays.

Important activation settings:

- `default_raise_card_group_on_card_activate`: startup default for the toolbar `Raise Group` toggle. When enabled, clicking a card first brings forward other real windows whose cards are in the same card group, then activates the clicked window.
- `raise_card_group_method`: allowed values are `restack` and `activate`. `restack` avoids focus flicker but may be ignored by GNOME/Mutter; `activate` is more reliable but briefly focuses each group window.

Important layout settings:

- `cardGroupSpacing`: one spacing value for both horizontal and vertical distance between card groups.
- `cardGroupBoundaryPadding`: padding around cards when drawing group outlines.
- `cardGroupBoundaryCornerRadius`: rounded group-outline corner radius.
- `sameCardGroupLinkDistance`: target spacing between linked cards inside one card group.
- `cardCollisionRadius`: invisible card collision radius.
- `hierarchySiblingSpread`: horizontal spacing between children of the same parent card.
- `hierarchyPrelayoutEnabled`: starts each new card group from a tree-like non-crossing placement; link/unlink changes keep existing group positions.
- `hierarchyBranchStrength`: how strongly a whole child branch keeps its horizontal lane.
- `hierarchyOrderStrength`: how strongly sibling branches keep left-to-right order to reduce crossings.
- `linkCurveSpread`: visual offset for curved branches from the same parent card.
- `linkCardAvoidanceMargin`: protected distance around links so unrelated cards do not sit on top of branches.
- `linkCardAvoidanceStrength`: how strongly cards are pushed away from unrelated links.
- `maxZoom`: maximum zoom-in level.

## Controls

- Click card: activate that window.
- Drag card: move it between card groups.
- Middle mouse drag: pan.
- Middle double click: fit graph.
- Mouse wheel: zoom.
- Toolbar `Options` toggles:
- `Auto Thumbs`: automatic refresh of all thumbnails every 30 seconds.
- `Raise Group`: whether card click raises other real windows from the same card group before activating the selected window.
- Context menu on card/link: window actions and relationship actions.
- Configured hotkey: see `hotkey` in `config.ini`.

## Autostart

Create `~/.config/autostart/wind_mgr.desktop`:

```ini
[Desktop Entry]
Type=Application
Name=wind_mgr
Comment=Window graph manager
Exec=bash -lc 'cd /path/to/wind_mgr && ./wind_mgr.sh'
Terminal=false
X-GNOME-Autostart-enabled=true
```

Set `start_hidden = true` in `config.ini` if this should be the default for the
repo. For only your local machine, put the same setting in `config.user.ini`.

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

## License

MIT. See `LICENSE`.
