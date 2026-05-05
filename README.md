# wind_mgr

**wind_mgr** is a visual window manager overlay for Ubuntu/GNOME on X11. Instead of alt-tabbing through a flat list, you see all your open windows as thumbnail cards arranged in a zoomable graph — grouped, linked, and always one click away.

![wind_mgr screenshot](<docs/Screenshot from 2026-05-05 17-08-07.png>)

---

## What it does

### See everything at once
All open windows appear as live thumbnail cards. You can zoom out to see every workspace at a glance, or zoom in to read a card's content before switching to it.

### Windows that belong together stay together
wind_mgr automatically groups related windows — browser tabs for the same project, a terminal and its editor, a file manager and a viewer — into card groups with visible outlines. You can also drag cards manually to reorganize groups any way you want.

### Parent–child relationships
Link any two windows as parent and child by dragging one card onto another. Links are drawn as curves connecting the cards, and grouped windows can be navigated as a tree. Detach or unlink at any time from the right-click menu.

### Launch apps into a group
Right-click anywhere on the canvas to open a radial app launcher. Right-click on a group to launch the app directly into that group. Favorites from your GNOME taskbar appear in the inner ring for quick access.

### One click to switch
Clicking a card activates that window. Enable **Raise Group** in the toolbar to bring all related windows forward together before focusing the selected one — useful when a project spans multiple windows across different monitors.

### Always accessible
- **Global hotkey** (default `Ctrl+Super+A`, configurable) shows or hides wind_mgr from any application.
- **Edge zones** — move the mouse to a screen edge to toggle the overlay without leaving the keyboard.
- **System tray icon** for quick access and to quit.
- Starts hidden at login if you want it always running in the background.

### Rename and label
Drag a card onto a group label to rename that group. Right-click a card to rename it independently of its window title.

---

## Install

### System dependencies

```bash
sudo apt install -y \
  python3-gi python3-gi-cairo python3-cairo \
  gir1.2-gtk-3.0 gir1.2-gdkpixbuf-2.0 gir1.2-wnck-3.0 \
  gir1.2-webkit2-4.1 gir1.2-ayatanaappindicator3-0.1 \
  gir1.2-keybinder-3.0 \
  libxcomposite1 libxfixes3 libgl1 \
  ffmpeg x11-apps
```

### Python dependencies

```bash
python3 -m pip install -r requirements.txt
```

---

## Run

```bash
bash ./wind_mgr.sh
```

Logs are written to `/tmp/windmgr.log`.

---

## Controls

| Action | Result |
|---|---|
| Click card | Activate that window |
| Right-click canvas | Open radial app launcher |
| Right-click card | Window actions (activate, move to monitor, rename, detach, …) |
| Drag card | Move to a different group |
| Drag card onto another card | Create parent–child link |
| Drag card onto group label | Rename that group |
| Middle-button drag | Pan the canvas |
| Middle double-click | Fit all cards to screen |
| Scroll wheel | Zoom |
| Configured hotkey | Show / hide wind_mgr |

---

## Autostart at login

Create `~/.config/autostart/wind_mgr.desktop`:

```ini
[Desktop Entry]
Type=Application
Name=wind_mgr
Exec=bash -lc 'cd /path/to/wind_mgr && ./wind_mgr.sh'
Terminal=false
X-GNOME-Autostart-enabled=true
```

Set `start_hidden = true` in `config.ini` (or in your local `config.user.ini`) so wind_mgr starts in the background without opening its window.

---

## Configuration

Settings live in `config.ini`. Create `config.user.ini` for local overrides — it is loaded after `config.ini` and is ignored by Git.

Key settings:

| Setting | What it changes |
|---|---|
| `hotkey` | Global show/hide hotkey |
| `start_hidden` | Start without showing the window |
| `bottom_action` / `top_action` | What moving the mouse to a screen edge does (`toggle`, `show`, `hide`, `none`) |
| `default_raise_card_group_on_card_activate` | Whether clicking a card raises its whole group |
| `active_refresh_interval` | How often the active window's thumbnail refreshes (seconds) |
| `hover_refresh_interval` | How often a hovered card's thumbnail refreshes |
| `cardGroupBoundaryShape` | Group outline shape (`convex` or `cards`) |
| `cardArea` | Visual size of each card |

All settings have inline comments in `config.ini`.

---

## Platform

X11 only. Requires Ubuntu 22.04+ or any GNOME/GTK desktop with WebKit2 4.1. Wayland is not supported.

---

## Architecture (brief)

| Path | Role |
|---|---|
| `web/` | D3/SVG graph UI rendered in an embedded WebKit view |
| `core/` | Window tracking, grouping, relationship tree |
| `bridge/` | Python ↔ JS bridge, thumbnail scheduling |
| `capture/` | Window screenshot and icon extraction |
| `providers/` | App-specific metadata (VSCode project name, browser tab title, …) |
| `ui/` | GTK window, tray icon, hotkey, edge-zone detection |

---

## License

MIT — see `LICENSE`.
