from __future__ import annotations
import ast
import configparser
import glob
import logging
import os
import re
import shlex
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)

_DESKTOP_DIRS = [
    "/usr/share/applications",
    str(Path.home() / ".local/share/applications"),
    "/var/lib/snapd/desktop/applications",
    "/var/lib/flatpak/exports/share/applications",
    str(Path.home() / ".local/share/flatpak/exports/share/applications"),
]

# Icon search order: (base_dir, sub_template) where {size} is replaced by pixel size.
_ICON_BASES = [
    ("/usr/share/icons/hicolor", "{size}x{size}/apps"),
    ("/usr/share/icons/hicolor", "scalable/apps"),
    ("/usr/share/icons/Adwaita", "{size}x{size}/apps"),
    ("/usr/share/icons/Adwaita", "scalable/apps"),
    ("/usr/share/icons/gnome", "{size}x{size}/apps"),
    ("/usr/share/pixmaps", ""),
]
_ICON_EXTS = ("png", "svg", "xpm")


def _find_icon(name: str, size: int = 48) -> str:
    if not name:
        return ""
    if os.path.isabs(name) and os.path.exists(name):
        return name
    for base, sub_tmpl in _ICON_BASES:
        sub = sub_tmpl.format(size=size) if sub_tmpl else ""
        directory = os.path.join(base, sub) if sub else base
        for ext in _ICON_EXTS:
            path = os.path.join(directory, f"{name}.{ext}")
            if os.path.exists(path):
                return path
    return ""


def _parse_desktop(path: str) -> dict | None:
    parser = configparser.RawConfigParser(strict=False)
    try:
        parser.read(path, encoding="utf-8")
    except Exception:
        return None
    if not parser.has_section("Desktop Entry"):
        return None
    s = dict(parser.items("Desktop Entry"))
    if s.get("type", "") != "Application":
        return None
    if s.get("nodisplay", "").lower() == "true":
        return None
    if s.get("hidden", "").lower() == "true":
        return None
    name = s.get("name", "").strip()
    if not name:
        return None
    return {
        "id": os.path.basename(path),
        "name": name,
        "exec": s.get("exec", ""),
        "icon": s.get("icon", ""),
        "wm_class": s.get("startupwmclass", "").strip(),
    }


def _get_favorites() -> list[str]:
    try:
        r = subprocess.run(
            ["gsettings", "get", "org.gnome.shell", "favorite-apps"],
            capture_output=True, text=True, timeout=2,
        )
        if r.returncode == 0 and r.stdout.strip():
            return ast.literal_eval(r.stdout.strip())
    except Exception:
        log.debug("Could not read GNOME favorites", exc_info=True)
    return []


_FIELD_CODE_RE = re.compile(r"%[a-zA-Z]")


def _clean_exec(exec_str: str) -> list[str]:
    cleaned = _FIELD_CODE_RE.sub("", exec_str).strip()
    try:
        return [p for p in shlex.split(cleaned) if p]
    except ValueError:
        return cleaned.split()


class AppLauncher:
    def __init__(self) -> None:
        self._apps: dict[str, dict] = {}
        self._favorites: list[str] = []
        self.refresh()

    def refresh(self) -> None:
        apps: dict[str, dict] = {}
        for directory in _DESKTOP_DIRS:
            for path in glob.glob(os.path.join(directory, "*.desktop")):
                app = _parse_desktop(path)
                if app:
                    apps[app["id"]] = app
        self._apps = apps
        self._favorites = _get_favorites()
        log.info("AppLauncher: %d apps, %d favorites", len(apps), len(self._favorites))

    def get_app_list(self) -> list[dict]:
        fav_set = set(self._favorites)
        fav_order = {fid: i for i, fid in enumerate(self._favorites)}
        result = []
        for app_id, app in self._apps.items():
            icon_path = _find_icon(app["icon"])
            result.append({
                "id": app_id,
                "name": app["name"],
                "icon_url": f"file://{icon_path}" if icon_path else "",
                "favorite": app_id in fav_set,
            })
        result.sort(key=lambda a: (
            0 if a["favorite"] else 1,
            fav_order.get(a["id"], 9999),
            a["name"].lower(),
        ))
        return result

    def get_app(self, app_id: str) -> dict | None:
        return self._apps.get(app_id)

    def find_app_for_window(self, wm_class: str, wm_class_group: str, app_name: str) -> str:
        """Return the best-matching app_id for an open window, or ''."""
        candidates = {wm_class.lower(), wm_class_group.lower(), app_name.lower()} - {""}
        # 1. Exact StartupWMClass match
        for app_id, app in self._apps.items():
            wmc = app.get("wm_class", "").lower()
            if wmc and wmc in candidates:
                return app_id
        # 2. Desktop file stem matches wm_class / app_name
        for app_id, app in self._apps.items():
            stem = app_id.removesuffix(".desktop").lower()
            if stem and stem in candidates:
                return app_id
        # 3. App display name matches app_name case-insensitively
        app_name_lc = app_name.lower()
        if app_name_lc:
            for app_id, app in self._apps.items():
                if app.get("name", "").lower() == app_name_lc:
                    return app_id
        return ""

    def launch(self, app_id: str, extra_args: list[str] | None = None) -> int | None:
        """Launch app and return the process PID, or None on failure."""
        app = self._apps.get(app_id)
        if not app:
            log.warning("AppLauncher: unknown app_id %r", app_id)
            return None
        argv = _clean_exec(app["exec"])
        if not argv:
            log.warning("AppLauncher: empty exec for %r", app_id)
            return None
        if extra_args:
            argv = argv + [str(a) for a in extra_args]
        try:
            proc = subprocess.Popen(argv, start_new_session=True,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            log.info("AppLauncher: launched %s pid=%s -> %s", app_id, proc.pid, argv)
            return proc.pid
        except Exception:
            log.warning("AppLauncher: failed to launch %r", app_id, exc_info=True)
            return None
