from __future__ import annotations
import logging
import time

import gi
gi.require_version("Wnck", "3.0")
from gi.repository import Wnck, GLib

from .events import bus, EVT_WINDOW_OPENED, EVT_WINDOW_CLOSED, EVT_FOCUS_CHANGED, EVT_TITLE_CHANGED, EVT_GRAPH_UPDATED
from .window_record import WindowRecord
from .window_registry import WindowRegistry

log = logging.getLogger(__name__)

_SKIP_TYPES = {
    Wnck.WindowType.DESKTOP,
    Wnck.WindowType.DOCK,
    Wnck.WindowType.TOOLBAR,
    Wnck.WindowType.MENU,
    Wnck.WindowType.SPLASHSCREEN,
    Wnck.WindowType.UTILITY,
}

_SELF_TITLES = {"wind_mgr"}


class ActivityWatcher:
    def __init__(self, registry: WindowRegistry) -> None:
        self._registry = registry
        self._screen: Wnck.Screen | None = None
        self._active_xid: int | None = None

    def start(self) -> None:
        self._screen = Wnck.Screen.get_default()
        self._screen.force_update()

        # Populate from already-open windows
        for window in self._screen.get_windows():
            if self._should_track(window):
                self._register_existing(window)

        self._screen.connect("window-opened", self._on_window_opened)
        self._screen.connect("window-closed", self._on_window_closed)
        self._screen.connect("active-window-changed", self._on_active_window_changed)

        active = self._screen.get_active_window()
        if active:
            self._active_xid = active.get_xid()

        bus.emit(EVT_FOCUS_CHANGED, new_xid=self._active_xid, old_xid=None)
        log.info("ActivityWatcher started, tracking %d existing windows",
                 len(self._registry.all_alive()))
        GLib.timeout_add(1000, self._poll_titles)

    def _should_track(self, window: Wnck.Window) -> bool:
        if window.get_window_type() in _SKIP_TYPES:
            return False
        return not _is_self_window(window)

    def _register_existing(self, window: Wnck.Window) -> None:
        xid = window.get_xid()
        if self._registry.get(xid) is None:
            record = self._build_record(window, parent_xid=None)
            record.is_alive = True
            self._registry.add(record)
        else:
            record = self._registry.get(xid)
            if record:
                self._sync_geometry(window, record)
        window.connect("name-changed", self._on_name_changed)
        window.connect("geometry-changed", self._on_geometry_changed)

    def _on_window_opened(self, screen: Wnck.Screen, window: Wnck.Window) -> None:
        if not self._should_track(window):
            return
        xid = window.get_xid()
        if self._registry.get(xid) is not None:
            return

        # Determine parent: the window active before this one appeared
        parent_xid = self._active_xid
        prev = screen.get_previously_active_window()
        if prev is not None:
            parent_xid = prev.get_xid()
        parent = self._registry.get(parent_xid) if parent_xid is not None else None
        if parent is not None and _is_self_record(parent):
            parent_xid = None

        record = self._build_record(window, parent_xid=parent_xid)
        self._registry.add(record)
        window.connect("name-changed", self._on_name_changed)
        window.connect("geometry-changed", self._on_geometry_changed)
        bus.emit(EVT_WINDOW_OPENED, record=record)
        bus.emit(EVT_GRAPH_UPDATED)
        log.debug("Window opened xid=%d title=%r parent=%s", xid, record.title, parent_xid)

    def _on_window_closed(self, screen: Wnck.Screen, window: Wnck.Window) -> None:
        xid = window.get_xid()
        self._registry.remove(xid)
        if self._active_xid == xid:
            self._active_xid = None
        bus.emit(EVT_WINDOW_CLOSED, xid=xid)
        bus.emit(EVT_GRAPH_UPDATED)
        log.debug("Window closed xid=%d", xid)

    def _on_active_window_changed(self, screen: Wnck.Screen,
                                  prev: Wnck.Window | None) -> None:
        old_xid = prev.get_xid() if prev else None
        active = screen.get_active_window()
        new_xid = active.get_xid() if active and not _is_self_window(active) else None

        if new_xid and (record := self._registry.get(new_xid)):
            record.last_focused_at = time.time()

        self._active_xid = new_xid
        bus.emit(EVT_FOCUS_CHANGED, new_xid=new_xid, old_xid=old_xid)

    def _on_name_changed(self, window: Wnck.Window) -> None:
        xid = window.get_xid()
        new_title = window.get_name() or ""
        record = self._registry.get(xid)
        if record and record.title != new_title:
            record.title = new_title
            bus.emit(EVT_TITLE_CHANGED, xid=xid, new_title=new_title)
            bus.emit(EVT_GRAPH_UPDATED)

    def _on_geometry_changed(self, window: Wnck.Window) -> None:
        record = self._registry.get(window.get_xid())
        if record and self._sync_geometry(window, record):
            bus.emit(EVT_GRAPH_UPDATED)

    def _poll_titles(self) -> bool:
        if self._screen is None:
            return False
        self._screen.force_update()
        changed = False
        for window in self._screen.get_windows():
            if not self._should_track(window):
                continue
            xid = window.get_xid()
            record = self._registry.get(xid)
            if record is None:
                continue
            new_title = window.get_name() or ""
            if record.title != new_title:
                record.title = new_title
                bus.emit(EVT_TITLE_CHANGED, xid=xid, new_title=new_title)
                changed = True
        if changed:
            bus.emit(EVT_GRAPH_UPDATED)
        return True  # repeat

    def _build_record(self, window: Wnck.Window,
                      parent_xid: int | None) -> WindowRecord:
        app = window.get_application()
        record = WindowRecord.make(
            xid=window.get_xid(),
            title=window.get_name() or "",
            app_name=app.get_name() if app else "",
            pid=window.get_pid(),
            wm_class=window.get_class_instance_name() or "",
            wm_class_group=window.get_class_group_name() or "",
            parent_xid=parent_xid,
        )
        self._sync_geometry(window, record)
        return record

    @property
    def active_xid(self) -> int | None:
        return self._active_xid

    def _sync_geometry(self, window: Wnck.Window, record: WindowRecord) -> bool:
        _x, _y, width, height = window.get_geometry()
        if width <= 0 or height <= 0:
            return False
        old = (record.metadata.get("window_width"), record.metadata.get("window_height"))
        new = (int(width), int(height))
        record.metadata["window_width"] = new[0]
        record.metadata["window_height"] = new[1]
        return old != new


def _is_self_window(window: Wnck.Window) -> bool:
    app = window.get_application()
    app_name = (app.get_name() if app else "") or ""
    values = {
        window.get_name() or "",
        app_name,
        window.get_class_instance_name() or "",
        window.get_class_group_name() or "",
    }
    return any(v.strip().lower() in _SELF_TITLES for v in values)


def _is_self_record(record: WindowRecord) -> bool:
    values = {
        record.title,
        record.app_name,
        record.wm_class,
        record.wm_class_group,
    }
    return any((v or "").strip().lower() in _SELF_TITLES for v in values)
