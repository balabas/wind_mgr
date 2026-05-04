from __future__ import annotations
from collections import OrderedDict
import json
import logging
import os
from pathlib import Path
import time
from typing import TYPE_CHECKING, Any, Callable

import gi
gi.require_version("WebKit2", "4.1")
from gi.repository import GLib, WebKit2

from core.activity_stats import ActivityStats
from core.config import read_config
from core.events import bus, EVT_FOCUS_CHANGED, EVT_GRAPH_UPDATED, EVT_WINDOW_CLOSED, EVT_WINDOW_OPENED
from core.relationship import RelationshipTree

if TYPE_CHECKING:
    from core.window_registry import WindowRegistry
    from capture.screenshot import ScreenshotCapture

log = logging.getLogger(__name__)
class JSBridge:
    def __init__(self, registry: "WindowRegistry", tree: RelationshipTree,
                 capture: "ScreenshotCapture") -> None:
        self._reg = registry
        self._tree = tree
        self._capture = capture
        self._webview: WebKit2.WebView | None = None
        self._auto_refresh = False
        self._auto_refresh_tag: int | None = None
        self._graph_update_tag: int | None = None
        self._active_refresh_tag: int | None = None
        self._bg_refresh_tag: int | None = None
        self._bg_refresh_idx: int = 0
        self._capture_running_xid: int | None = None
        self._capture_queue: OrderedDict[int, str] = OrderedDict()
        self._last_capture_at: dict[int, int] = {}
        self._last_capture_reason: dict[int, str] = {}
        self._successful_thumb_xids: set[int] = set()
        self._failed_capture_attempts: dict[int, int] = {}
        self._retry_capture_tag: int | None = None
        self._icon_captured_xids: set[int] = set()
        self._thumb_update_tag: int | None = None
        self._pending_thumb_xids: set[int] = set()
        self._last_thumb_urls: dict[int, tuple[str, str]] = {}
        self._before_activate_cb: Callable[[], None] | None = None
        self._ui_visible = False
        self._interaction_active = False
        self._last_graph_push_at = 0.0
        self._last_active_xid: int | None = None
        self._main_loop_latency_ms = 0.0
        self._activity_priority_enabled = True
        self._background_refresh_min_ms = 10000
        self._capture_retry_ms = 1000
        self._capture_retry_max_attempts = 12
        self._new_window_capture_delay_ms = 1000
        self._live_preview: Any | None = None
        self._live_preview_xid: int | None = None
        self._live_preview_enabled = True
        self._live_preview_fps = 20
        self._live_preview_idle_fps = 3
        self._last_live_preview_idle_capture_ms: dict[int, int] = {}
        self._last_live_preview_log_ms = 0
        self._hover_refresh_ms = 2000
        self._hovered_xid: int | None = None
        self._hover_refresh_tag: int | None = None
        self._pending_show_animation: dict[str, Any] | None = None
        self._js_msg_count = 0
        self._js_msg_rate_t0 = time.monotonic()
        self._cpu_probe_last_wall: float | None = None
        self._cpu_probe_last_proc: float | None = None
        self._cpu_probe_last_threads: dict[int, float] = {}

        cfg = read_config()
        self._graph_push_min_ms = max(
            250,
            int(cfg.getfloat("capture", "graph_push_min_interval", fallback=3.0) * 1000),
        )
        active_refresh_seconds = cfg.getfloat("capture", "active_refresh_interval", fallback=0.0)
        self._active_refresh_ms = (
            max(100, int(active_refresh_seconds * 1000))
            if active_refresh_seconds > 0
            else 0
        )
        self._bg_refresh_ms = max(
            1000,
            int(cfg.getfloat("capture", "background_refresh_interval", fallback=10.0) * 1000),
        )
        self._activity_priority_enabled = cfg.getboolean(
            "capture", "activity_priority_enabled", fallback=True
        )
        self._background_refresh_min_ms = max(
            1000,
            int(cfg.getfloat("capture", "background_refresh_min_interval", fallback=10.0) * 1000),
        )
        self._capture_retry_ms = max(
            250,
            int(cfg.getfloat("capture", "capture_retry_interval", fallback=1.0) * 1000),
        )
        self._capture_retry_max_attempts = max(
            0,
            int(cfg.getint("capture", "capture_retry_max_attempts", fallback=12)),
        )
        self._new_window_capture_delay_ms = max(
            0,
            int(cfg.getfloat("capture", "new_window_capture_delay", fallback=1.0) * 1000),
        )
        self._activity = ActivityStats(
            half_life_seconds=cfg.getfloat("capture", "activity_half_life_seconds", fallback=900.0)
        )
        self._live_preview_enabled = cfg.getboolean("capture", "live_preview_enabled", fallback=True)
        self._live_preview_fps = max(
            1,
            min(60, int(cfg.getfloat("capture", "live_preview_fps", fallback=20))),
        )
        self._live_preview_idle_fps = max(
            1,
            min(60, int(cfg.getfloat("capture", "live_preview_idle_fps", fallback=3))),
        )
        self._hover_refresh_ms = max(
            250,
            int(cfg.getfloat("capture", "hover_refresh_interval", fallback=0.5) * 1000),
        )
        self._raise_same_geometry_on_card_activate = cfg.getboolean(
            "activation",
            "raise_same_geometry_on_card_activate",
            fallback=False,
        )
        self._raise_same_geometry_method = cfg.get(
            "activation",
            "raise_same_geometry_method",
            fallback="restack",
        ).strip().lower()
        self._active_window_show_animation = cfg.getboolean(
            "activation",
            "active_window_show_animation",
            fallback=True,
        )
        self._active_window_show_animation_ms = max(
            100,
            int(cfg.getfloat("activation", "active_window_show_animation_ms", fallback=650)),
        )
        bus.subscribe(EVT_GRAPH_UPDATED, self._on_graph_updated)
        bus.subscribe(EVT_FOCUS_CHANGED, self._on_focus_changed)
        bus.subscribe(EVT_WINDOW_OPENED, self._on_window_opened)
        bus.subscribe(EVT_WINDOW_CLOSED, self._on_window_closed)

    def set_before_activate_callback(self, callback: Callable[[], None]) -> None:
        self._before_activate_cb = callback

    def set_ui_visible(self, visible: bool) -> None:
        self._ui_visible = visible
        if visible:
            self._capture_show_animation_source()
        if self._webview is not None:
            suspended = str(not visible).lower()
            js = f"try{{if(window.windMgr)window.windMgr.setSuspended({suspended});}}catch(e){{}}"
            self._webview.evaluate_javascript(
                js, -1, None, None, None,
                self._js_done_cb, None
            )
        if not visible:
            self._pending_thumb_xids.clear()
            self._capture_queue.clear()
            self._hovered_xid = None
            self._stop_hover_refresh_timer()
            self._hide_live_preview()
            log.info("ui hidden: suspended graph; active/hover refresh paused, background refresh continues")
        else:
            log.info("ui visible: graph and thumbnail refresh enabled")

    def push_show_active_animation(self) -> bool:
        if self._webview is None or not self._pending_show_animation:
            return False
        payload = dict(self._pending_show_animation)
        origin_x, origin_y = self._webview_screen_origin()
        payload["x"] = payload["screen_x"] - origin_x
        payload["y"] = payload["screen_y"] - origin_y
        payload["duration_ms"] = self._active_window_show_animation_ms
        js = (
            "(function(payload){"
            "function run(){"
            "try{"
            "if(window.windMgr&&window.windMgr.animateActiveWindowFromScreen){"
            "window.windMgr.animateActiveWindowFromScreen(payload);"
            "}else{setTimeout(run,120);}"
            "}catch(e){console.error('animateActiveWindowFromScreen error:',e.toString(),e.stack);}"
            "}"
            "run();"
            f"}})({json.dumps(payload)});"
        )
        self._webview.evaluate_javascript(js, -1, None, None, None, self._js_done_cb, None)
        log.debug("show active animation payload=%s origin=%s,%s", payload, origin_x, origin_y)
        self._pending_show_animation = None
        return False

    def _capture_show_animation_source(self) -> None:
        self._pending_show_animation = None
        if not self._active_window_show_animation:
            return
        try:
            gi.require_version("Wnck", "3.0")
            from gi.repository import Wnck
            screen = Wnck.Screen.get_default()
            if screen is None:
                return
            screen.force_update()
            active_xid = self._active_window_xid(allow_fallback=True)
            if active_xid is None:
                return
            record = self._reg.get(int(active_xid))
            if record is None or _is_self_record(record):
                return
            for window in screen.get_windows():
                if int(window.get_xid()) != int(active_xid):
                    continue
                x, y, width, height = window.get_geometry()
                self._pending_show_animation = {
                    "xid": int(active_xid),
                    "screen_x": int(x),
                    "screen_y": int(y),
                    "w": int(width),
                    "h": int(height),
                    "thumb_url": self._capture.thumb_url(int(active_xid)),
                }
                log.debug("captured show active animation source=%s", self._pending_show_animation)
                return
        except Exception:
            log.debug("failed to capture show active animation source", exc_info=True)

    def attach(self, webview: WebKit2.WebView) -> None:
        self._webview = webview
        ucm = webview.get_user_content_manager()
        ucm.register_script_message_handler("api")
        ucm.connect("script-message-received::api", self._on_js_message)
        self._start_thumbnail_refresh()

        # Forward JS console.log/warn/error to Python log
        _console_js = (
            "(function(){"
            "['log','warn','error'].forEach(function(m){"
            "var orig=console[m].bind(console);"
            "console[m]=function(){"
            "orig.apply(console,arguments);"
            "var msg=Array.prototype.map.call(arguments,function(a){"
            "try{return typeof a==='object'?JSON.stringify(a):String(a);}catch(e){return String(a);}"
            "}).join(' ');"
            "try{window.webkit.messageHandlers.api.postMessage("
            "{action:'__console__',level:m,msg:msg});}catch(e){}"
            "};"
            "});"
            "})();"
        )
        ucm.add_script(WebKit2.UserScript(
            _console_js,
            WebKit2.UserContentInjectedFrames.TOP_FRAME,
            WebKit2.UserScriptInjectionTime.START,
            None, None,
        ))

    def push_graph(self) -> None:
        if self._webview is None:
            return
        try:
            data = self._serialize()
            js = (
                "try{"
                f"if(window.windMgr)window.windMgr.updateGraph({json.dumps(data)});"
                "else console.warn('windMgr not ready');"
                "}catch(e){console.error('updateGraph error:',e.toString(),e.stack);}"
            )
            # evaluate_javascript requires a non-None callback to actually execute in WebKit2 4.1+
            self._webview.evaluate_javascript(
                js, -1, None, None, None,
                self._js_done_cb, None
            )
            log.debug("push_graph: sent %d nodes, %d edges, %d projects",
                      len(data["nodes"]), len(data["edges"]), len(data["projects"]))
        except Exception:
            log.exception("push_graph failed")

    def _schedule_graph_push(self, min_interval_ms: int | None = None) -> None:
        now = GLib.get_monotonic_time() // 1000
        min_ms = self._graph_push_min_ms if min_interval_ms is None else min_interval_ms
        wait_ms = max(0, min_ms - int(now - self._last_graph_push_at))
        if self._graph_update_tag is not None:
            return
        self._graph_update_tag = GLib.timeout_add(wait_ms, self._push_graph_debounced)

    def push_active_window(self) -> None:
        if self._webview is None:
            return
        try:
            active_xid = self._active_window_xid(allow_fallback=True)
            js = (
                "try{"
                f"if(window.windMgr)window.windMgr.setActiveWindow({json.dumps(active_xid)});"
                "else console.warn('windMgr not ready');"
                "}catch(e){console.error('setActiveWindow error:',e.toString(),e.stack);}"
            )
            self._webview.evaluate_javascript(
                js, -1, None, None, None,
                self._js_done_cb, None
            )
        except Exception:
            log.exception("push_active_window failed")

    def push_thumbnail_update(self, xids: list[int]) -> None:
        if self._webview is None or not xids:
            return
        try:
            items = []
            for xid in xids:
                if self._reg.get(xid) is None:
                    continue
                thumb_url = self._capture.thumb_url(xid)
                icon_url = self._capture.icon_url(xid)
                urls = (thumb_url, icon_url)
                if self._last_thumb_urls.get(xid) == urls:
                    continue
                self._last_thumb_urls[xid] = urls
                items.append({
                    "xid": xid,
                    "thumb_url": thumb_url,
                    "icon_url": icon_url,
                    "reason": self._last_capture_reason.get(xid, ""),
                })
            if not items:
                return
            js = (
                "try{"
                f"if(window.windMgr)window.windMgr.updateThumbnails({json.dumps(items)});"
                "else console.warn('windMgr not ready');"
                "}catch(e){console.error('updateThumbnails error:',e.toString(),e.stack);}"
            )
            self._webview.evaluate_javascript(
                js, -1, None, None, None,
                self._js_done_cb, None
            )
        except Exception:
            log.exception("push_thumbnail_update failed")

    def _on_thumbnail_updated(self, xids: list[int]) -> None:
        if not self._ui_visible:
            return
        self._pending_thumb_xids.update(xids)
        if self._thumb_update_tag is None:
            self._thumb_update_tag = GLib.timeout_add(250, self._flush_thumbnail_updates)

    def _flush_thumbnail_updates(self) -> bool:
        self._thumb_update_tag = None
        if not self._ui_visible:
            self._pending_thumb_xids.clear()
            return False
        xids = list(self._pending_thumb_xids)
        self._pending_thumb_xids.clear()
        self.push_thumbnail_update(xids)
        return False

    def _js_done_cb(self, source, result, _user_data) -> None:
        try:
            source.evaluate_javascript_finish(result)
        except Exception as e:
            log.warning("JS execution error: %s", e)

    # ── Serialisation ─────────────────────────────────────────────────────

    def _serialize(self) -> dict:
        alive = [r for r in self._reg.all_alive() if not _is_self_record(r)]
        projects = self._tree.get_projects()
        monitor_orientation = self._window_monitor_orientations()

        proj_id_set = {p["id"] for p in projects}

        nodes = []
        for r in alive:
            pid = self._tree.get_project_id(r)
            # Ensure project exists (handles manually moved nodes)
            if pid not in proj_id_set:
                projects.append({
                    "id": pid,
                    "name": f"project-{pid[-4:]}",
                    "root_xid": int(pid) if pid.isdigit() else 0,
                    "color": "#888888",
                })
                proj_id_set.add(pid)

            nodes.append({
                "xid": r.xid,
                "title": r.title,
                "app_type": r.app_type,
                "app_name": r.app_name,
                "tab_title": r.metadata.get("tab_title", ""),
                "domain": r.metadata.get("domain", ""),
                "project_name": r.metadata.get("project_name", ""),
                "active_file": r.metadata.get("active_file", ""),
                "active_directory": r.metadata.get("active_directory", ""),
                "window_width": r.metadata.get("window_width", 0),
                "window_height": r.metadata.get("window_height", 0),
                "monitor_orientation": monitor_orientation.get(r.xid, ""),
                "project_id": pid,
                "parent_xid": r.parent_xid,
                "thumb_url": self._capture.thumb_url(r.xid),
                "icon_url": self._capture.icon_url(r.xid),
                "is_alive": r.is_alive,
                "last_focused_at": r.last_focused_at,
                "breadcrumb": self._tree.get_breadcrumb(r.xid),
            })

        edges = []
        for r in alive:
            parent = self._reg.get(r.parent_xid) if r.parent_xid is not None else None
            if parent is not None and not _is_self_record(parent):
                edges.append({"source": r.parent_xid, "target": r.xid, "type": "parent-child"})

        active_xid = self._active_window_xid(allow_fallback=True)

        return {"nodes": nodes, "edges": edges, "projects": projects, "active_xid": active_xid}

    def _window_monitor_orientations(self) -> dict[int, str]:
        try:
            import gi
            gi.require_version("Gdk", "3.0")
            gi.require_version("Wnck", "3.0")
            from gi.repository import Gdk, Wnck

            display = Gdk.Display.get_default()
            screen = Wnck.Screen.get_default()
            if display is None or screen is None:
                return {}
            screen.force_update()
            orientations: dict[int, str] = {}
            for window in screen.get_windows():
                wx, wy, ww, wh = window.get_geometry()
                cx = wx + ww // 2
                cy = wy + wh // 2
                for idx in range(display.get_n_monitors()):
                    monitor = display.get_monitor(idx)
                    if monitor is None:
                        continue
                    area = monitor.get_workarea()
                    if area.x <= cx < area.x + area.width and area.y <= cy < area.y + area.height:
                        orientations[window.get_xid()] = "vertical" if area.height > area.width else "horizontal"
                        break
            return orientations
        except Exception:
            log.debug("could not compute monitor orientations", exc_info=True)
            return {}

    def _active_window_xid(self, *, allow_fallback: bool = False) -> int | None:
        try:
            import gi
            gi.require_version("Wnck", "3.0")
            from gi.repository import Wnck
            screen = Wnck.Screen.get_default()
            screen.force_update()
            active = screen.get_active_window()
            if active is None:
                return self._valid_last_active_xid() if allow_fallback else None
            xid = active.get_xid()
            record = self._reg.get(xid)
            if record is None or _is_self_record(record):
                return self._valid_last_active_xid() if allow_fallback else None
            self._last_active_xid = xid
            return xid
        except Exception:
            log.debug("Failed to read active window", exc_info=True)
            return self._valid_last_active_xid() if allow_fallback else None

    def _valid_last_active_xid(self) -> int | None:
        if self._last_active_xid is None:
            return None
        record = self._reg.get(self._last_active_xid)
        if record is None or not record.is_alive or _is_self_record(record):
            self._last_active_xid = None
            return None
        return self._last_active_xid

    # ── Incoming messages from JS ─────────────────────────────────────────

    def _on_js_message(self, ucm, result) -> None:
        try:
            js_val = result.get_js_value()
            raw = js_val.to_json(0)
            msg = json.loads(raw)
        except Exception:
            log.exception("Failed to parse JS message")
            return
        self._record_js_message_rate()
        GLib.idle_add(self._handle_message, msg)

    def _record_js_message_rate(self) -> None:
        self._js_msg_count += 1
        now = time.monotonic()
        elapsed = now - self._js_msg_rate_t0
        if elapsed < 5.0:
            return
        rate = self._js_msg_count / elapsed
        log.debug(
            "[msg-rate] %.1f msgs/s visible=%s queue=%s capture_running=%s",
            rate,
            self._ui_visible,
            len(self._capture_queue),
            self._capture_running_xid,
        )
        self._js_msg_count = 0
        self._js_msg_rate_t0 = now

    def _handle_message(self, msg: dict) -> bool:
        action = msg.get("action", "")
        if action == "__console__":
            lvl, txt = msg.get("level", "log"), msg.get("msg", "")
            (log.error if lvl == "error" else log.warning if lvl == "warn" else log.debug)(
                "JS %s: %s", lvl, txt)
            return False
        if action not in {
            "refresh_active",
            "set_interaction_active",
            "card_hover",
            "card_hover_leave",
            "live_preview_update",
            "live_preview_bounds",
            "live_preview_idle",
            "live_preview_hide",
        }:
            log.debug("JS message: %s", msg)
        try:
            if action == "activate":
                self._activate_window(msg["xid"])
            elif action == "move_node":
                self._move_node(
                    int(msg["xid"]),
                    str(msg["project_id"]),
                    with_children=msg.get("with_children", False),
                )
            elif action == "set_parent":
                self._set_parent(
                    int(msg["xid"]),
                    int(msg["parent_xid"]),
                    with_children=msg.get("with_children", False),
                )
            elif action == "rename_project":
                self._rename_project(msg["project_id"], msg["name"])
            elif action == "refresh_thumb":
                self._capture_one(int(msg["xid"]), reason="manual", force=True)
            elif action == "place_window":
                self._place_window(int(msg["xid"]), str(msg.get("placement", "")))
            elif action == "move_window_monitor":
                self._move_window_to_monitor(int(msg["xid"]), int(msg.get("monitor_index", 0)))
            elif action == "refresh_all_thumbs":
                self._refresh_all_thumbs()
            elif action == "toggle_auto_refresh":
                self._set_auto_refresh(msg.get("enabled", False))
            elif action == "set_interaction_active":
                self._interaction_active = bool(msg.get("active", False))
            elif action == "card_click":
                self._activity.mark_click(int(msg["xid"]))
            elif action == "card_hover":
                self._activity.mark_hover(int(msg["xid"]))
                self._refresh_hover_thumb(int(msg["xid"]))
                self._show_live_preview_from_msg(msg)
            elif action == "card_hover_leave":
                xid = int(msg["xid"])
                if self._hovered_xid == xid:
                    self._hovered_xid = None
                    self._stop_hover_refresh_timer()
            elif action == "live_preview_update":
                self._refresh_hover_thumb(int(msg["xid"]))
                self._show_live_preview_from_msg(msg)
            elif action == "live_preview_bounds":
                self._refresh_hover_thumb(int(msg["xid"]))
                self._show_live_preview_from_msg(msg, active_rate=False)
            elif action == "live_preview_idle":
                self._idle_live_preview(int(msg["xid"]))
            elif action == "live_preview_hide":
                self._hide_live_preview()
            elif action == "refresh_active":
                if self._ui_visible:
                    self.push_active_window()
            elif action == "remove_link":
                self._remove_link(int(msg["xid"]))
            elif action == "unlink_children":
                self._unlink_children(int(msg["xid"]))
            elif action == "toggle_project":
                pass  # future: collapse project
        except Exception:
            log.exception("Error handling action %r", action)
        return False  # GLib.idle_add one-shot

    def _activate_window(self, xid: int) -> None:
        self._activity.mark_click(xid)
        self._hide_live_preview()
        record = self._reg.get(int(xid))
        if record is not None:
            log.info(
                "activate request xid=%s title=%r app=%r class=%r project=%s parent=%s",
                record.xid,
                record.title,
                record.app_name,
                record.wm_class,
                self._tree.get_project_id(record),
                record.parent_xid,
            )
        else:
            log.info("activate request xid=%s record=missing", xid)
        import gi
        gi.require_version("Gtk", "3.0")
        gi.require_version("Wnck", "3.0")
        from gi.repository import Gtk, Wnck
        from ui.window_flash import flash_window_rect
        screen = Wnck.Screen.get_default()
        screen.force_update()
        windows_by_xid = {int(w.get_xid()): w for w in screen.get_windows()}
        target = windows_by_xid.get(int(xid))
        if target is not None:
            log.info(
                "activate matched xid=%s wnck_name=%r app=%r",
                xid,
                target.get_name(),
                target.get_application().get_name() if target.get_application() else "",
            )
            if self._before_activate_cb is not None:
                self._before_activate_cb()
            ts = Gtk.get_current_event_time()
            if not ts:
                # Wnck expects a 32-bit X timestamp, not Unix epoch time.
                ts = (GLib.get_monotonic_time() // 1000) & 0xFFFFFFFF
            if self._raise_same_geometry_on_card_activate:
                self._raise_same_geometry_then_activate(int(xid), windows_by_xid, int(ts))
            else:
                target.activate(ts)
                x, y, width, height = target.get_geometry()
                GLib.timeout_add(140, flash_window_rect, x, y, width, height)
            return
        log.warning("activate: window xid=%d not found", xid)

    def _raise_same_geometry_then_activate(self, xid: int, windows_by_xid: dict[int, Any], ts: int) -> None:
        record = self._reg.get(xid)
        target = windows_by_xid.get(xid)
        if record is None or target is None:
            if target is not None:
                target.activate(ts)
            return

        project_id = self._tree.get_project_id(record)
        same_geometry_records = [
            r for r in self._reg.all_alive()
            if r.xid != xid and self._tree.get_project_id(r) == project_id and r.xid in windows_by_xid
        ]
        same_geometry_records.sort(key=lambda r: (r.last_focused_at, r.xid))
        raise_xids = [r.xid for r in same_geometry_records]
        log.info(
            "activate same geometry: selected=%s project=%s method=%s raise=%s",
            xid,
            project_id,
            self._raise_same_geometry_method,
            raise_xids,
        )

        if self._raise_same_geometry_method == "activate":
            for window_xid in raise_xids:
                window = windows_by_xid.get(window_xid)
                if window is not None:
                    self._activate_wnck_window(window, ts, flash=False)
        else:
            self._restack_x11_windows(raise_xids)

        # Selected window is activated last, so it should finish focused/topmost.
        self._activate_wnck_window(target, ts, flash=True)

    def _activate_wnck_window(self, window: Any, ts: int, *, flash: bool) -> None:
        try:
            if window.is_minimized():
                window.unminimize(ts)
            window.activate(ts)
            if flash:
                x, y, width, height = window.get_geometry()
                from ui.window_flash import flash_window_rect
                GLib.timeout_add(140, flash_window_rect, x, y, width, height)
        except Exception:
            log.debug("activate window failed xid=%s", window.get_xid(), exc_info=True)

    def _restack_x11_windows(self, xids: list[int]) -> None:
        if not xids:
            return
        try:
            from Xlib import X, display, protocol
            dpy = display.Display()
            root = dpy.screen().root
            net_restack = dpy.intern_atom("_NET_RESTACK_WINDOW")
            source_application = 1

            for xid in xids:
                win = dpy.create_resource_object("window", int(xid))
                event = protocol.event.ClientMessage(
                    window=win,
                    client_type=net_restack,
                    data=(32, [source_application, 0, X.Above, 0, 0]),
                )
                root.send_event(
                    event,
                    event_mask=X.SubstructureRedirectMask | X.SubstructureNotifyMask,
                )
                # Some WMs ignore _NET_RESTACK_WINDOW; direct configure is a
                # harmless fallback when the WM allows client restacking.
                try:
                    win.configure(stack_mode=X.Above)
                except Exception:
                    log.debug("XRaise fallback failed xid=%s", xid, exc_info=True)
            dpy.sync()
            dpy.close()
        except Exception:
            log.debug("X11 restack failed xids=%s", xids, exc_info=True)

    def _place_window(self, xid: int, placement: str) -> None:
        import gi
        gi.require_version("Gdk", "3.0")
        gi.require_version("Wnck", "3.0")
        from gi.repository import Gdk, Wnck

        screen = Wnck.Screen.get_default()
        screen.force_update()
        target = None
        for w in screen.get_windows():
            if w.get_xid() == xid:
                target = w
                break
        if target is None:
            log.warning("place_window: window xid=%d not found", xid)
            return

        display = Gdk.Display.get_default()
        if display is None:
            log.warning("place_window: no GDK display")
            return

        if placement == "maximize":
            self._maximize_window(target, xid)
            return

        wx, wy, ww, wh = target.get_geometry()
        cx = wx + ww // 2
        cy = wy + wh // 2
        monitor = None
        for idx in range(display.get_n_monitors()):
            mon = display.get_monitor(idx)
            if mon is None:
                continue
            rect = mon.get_geometry()
            if rect.x <= cx < rect.x + rect.width and rect.y <= cy < rect.y + rect.height:
                monitor = mon
                break
        if monitor is None:
            monitor = display.get_primary_monitor() or display.get_monitor(0)
        if monitor is None:
            log.warning("place_window: no monitor found")
            return

        area = monitor.get_workarea()
        monitor_is_vertical = area.height > area.width
        if monitor_is_vertical:
            x, y, width, height = self._vertical_monitor_placement(area, placement)
        else:
            x, y, width, height = self._horizontal_monitor_placement(area, placement)
        if width <= 0 or height <= 0:
            log.warning("place_window: unknown placement=%r xid=%s", placement, xid)
            return

        was_fullscreen = bool(target.is_fullscreen()) if hasattr(target, "is_fullscreen") else False
        was_maximized = bool(target.is_maximized())
        self._clear_window_states_for_geometry(target)
        self._apply_window_geometry_delayed(target, x, y, width, height, delay_ms=90, attempts=10)
        log.info(
            "place_window xid=%s placement=%s geometry=%sx%s+%s+%s fullscreen=%s maximized=%s",
            xid,
            placement,
            width,
            height,
            x,
            y,
            was_fullscreen,
            was_maximized,
        )
        self._schedule_raise_webview_toplevel()

    def _move_window_to_monitor(self, xid: int, monitor_index: int) -> None:
        import gi
        gi.require_version("Gdk", "3.0")
        gi.require_version("Wnck", "3.0")
        from gi.repository import Gdk, Wnck

        screen = Wnck.Screen.get_default()
        screen.force_update()
        target = None
        for w in screen.get_windows():
            if w.get_xid() == xid:
                target = w
                break
        if target is None:
            log.warning("move_window_monitor: window xid=%d not found", xid)
            return

        display = Gdk.Display.get_default()
        if display is None:
            log.warning("move_window_monitor: no GDK display")
            return
        if monitor_index < 0 or monitor_index >= display.get_n_monitors():
            log.warning(
                "move_window_monitor: monitor %s unavailable, monitor_count=%s xid=%s",
                monitor_index + 1,
                display.get_n_monitors(),
                xid,
            )
            return

        source_area = self._window_workarea(display, target)
        dest_monitor = display.get_monitor(monitor_index)
        if dest_monitor is None:
            log.warning("move_window_monitor: target monitor %s missing xid=%s", monitor_index + 1, xid)
            return
        dest_area = dest_monitor.get_workarea()

        wx, wy, ww, wh = target.get_geometry()
        was_maximized = bool(target.is_maximized())
        if source_area is None:
            source_area = dest_area

        if was_maximized:
            nx, ny, nw, nh = dest_area.x, dest_area.y, dest_area.width, dest_area.height
        else:
            rel_x = (wx - source_area.x) / max(1, source_area.width)
            rel_y = (wy - source_area.y) / max(1, source_area.height)
            rel_w = ww / max(1, source_area.width)
            rel_h = wh / max(1, source_area.height)
            nw = max(120, min(dest_area.width, round(dest_area.width * rel_w)))
            nh = max(80, min(dest_area.height, round(dest_area.height * rel_h)))
            nx = round(dest_area.x + rel_x * dest_area.width)
            ny = round(dest_area.y + rel_y * dest_area.height)
            nx = max(dest_area.x, min(nx, dest_area.x + dest_area.width - nw))
            ny = max(dest_area.y, min(ny, dest_area.y + dest_area.height - nh))

        was_fullscreen = bool(target.is_fullscreen()) if hasattr(target, "is_fullscreen") else False
        self._clear_window_states_for_geometry(target)
        self._apply_window_geometry_delayed(target, nx, ny, nw, nh, delay_ms=90, attempts=10)
        if was_maximized:
            GLib.timeout_add(180, target.maximize)
        log.info(
            "move_window_monitor xid=%s monitor=%s geometry=%sx%s+%s+%s fullscreen=%s maximized=%s",
            xid,
            monitor_index + 1,
            nw,
            nh,
            nx,
            ny,
            was_fullscreen,
            was_maximized,
        )
        self._schedule_raise_webview_toplevel()

    def _clear_window_states_for_geometry(self, window) -> None:
        try:
            xid = window.get_xid()
            self._send_ewmh_fullscreen_state(xid, action=0)
            if hasattr(window, "is_fullscreen") and window.is_fullscreen() and hasattr(window, "unfullscreen"):
                window.unfullscreen()
                self._send_ewmh_fullscreen_state(xid, action=0)
            if window.is_maximized():
                window.unmaximize()
        except Exception:
            log.debug("could not clear window fullscreen/maximized state before geometry change", exc_info=True)

    def _fullscreen_window(self, window, xid: int) -> None:
        import gi
        gi.require_version("Gtk", "3.0")
        gi.require_version("Wnck", "3.0")
        from gi.repository import Gtk, Wnck

        try:
            if window.is_maximized():
                window.unmaximize()
            ts = Gtk.get_current_event_time()
            if not ts:
                ts = (GLib.get_monotonic_time() // 1000) & 0xFFFFFFFF
            window.activate(ts)
            if hasattr(window, "fullscreen"):
                window.fullscreen()
        except Exception:
            log.debug("Wnck fullscreen request failed xid=%s", xid, exc_info=True)

        try:
            self._send_ewmh_fullscreen_state(xid, action=1)
        except Exception:
            log.debug("EWMH fullscreen request failed xid=%s", xid, exc_info=True)

        GLib.timeout_add(250, self._log_window_geometry, window, "fullscreen")
        log.info("place_window xid=%s placement=fullscreen requested", xid)

    def _maximize_window(self, window, xid: int) -> None:
        try:
            self._clear_window_states_for_geometry(window)
            window.maximize()
            GLib.timeout_add(250, self._log_window_geometry, window, "maximize")
            self._schedule_raise_webview_toplevel()
            log.info("place_window xid=%s placement=maximize requested", xid)
        except Exception:
            log.exception("maximize request failed xid=%s", xid)

    def _send_ewmh_fullscreen_state(self, xid: int, *, action: int) -> None:
        from Xlib import X, display
        from Xlib.protocol import event

        dpy = display.Display()
        try:
            root = dpy.screen().root
            win = dpy.create_resource_object("window", int(xid))
            net_wm_state = dpy.intern_atom("_NET_WM_STATE")
            fullscreen = dpy.intern_atom("_NET_WM_STATE_FULLSCREEN")
            msg = event.ClientMessage(
                window=win,
                client_type=net_wm_state,
                data=(32, [int(action), fullscreen, 0, 2, 0]),
            )
            root.send_event(
                msg,
                event_mask=X.SubstructureRedirectMask | X.SubstructureNotifyMask,
            )
            dpy.flush()
            log.debug("ewmh_fullscreen_state xid=%s action=%s", xid, action)
        finally:
            dpy.close()

    def _log_window_geometry(self, window, reason: str) -> bool:
        try:
            import gi
            gi.require_version("Wnck", "3.0")
            from gi.repository import Wnck
            screen = Wnck.Screen.get_default()
            if screen is not None:
                screen.force_update()
            x, y, width, height = window.get_geometry()
            log.info(
                "window_geometry reason=%s xid=%s geometry=%sx%s+%s+%s fullscreen=%s maximized=%s",
                reason,
                window.get_xid(),
                width,
                height,
                x,
                y,
                window.is_fullscreen() if hasattr(window, "is_fullscreen") else False,
                window.is_maximized(),
            )
        except Exception:
            log.debug("could not log window geometry reason=%s", reason, exc_info=True)
        return False

    def _apply_window_geometry_delayed(self, window, x: int, y: int, width: int, height: int,
                                       *, delay_ms: int, attempts: int) -> None:
        GLib.timeout_add(delay_ms, self._apply_window_geometry_once,
                         window, x, y, width, height, attempts, 1)

    def _apply_window_geometry_once(self, window, x: int, y: int, width: int, height: int,
                                    attempts_left: int, attempt_num: int) -> bool:
        import gi
        gi.require_version("Wnck", "3.0")
        from gi.repository import Wnck

        try:
            xid = window.get_xid()
            if hasattr(window, "is_fullscreen") and window.is_fullscreen() and hasattr(window, "unfullscreen"):
                self._send_ewmh_fullscreen_state(xid, action=0)
                window.unfullscreen()
                self._send_ewmh_fullscreen_state(xid, action=0)
            if window.is_maximized():
                window.unmaximize()
            window.set_geometry(
                Wnck.WindowGravity.CURRENT,
                Wnck.WindowMoveResizeMask.X
                | Wnck.WindowMoveResizeMask.Y
                | Wnck.WindowMoveResizeMask.WIDTH
                | Wnck.WindowMoveResizeMask.HEIGHT,
                int(x),
                int(y),
                int(width),
                int(height),
            )
            screen = Wnck.Screen.get_default()
            if screen is not None:
                screen.force_update()
            ax, ay, aw, ah = window.get_geometry()
            ok = (
                abs(ax - int(x)) <= 12
                and abs(ay - int(y)) <= 12
                and abs(aw - int(width)) <= 24
                and abs(ah - int(height)) <= 24
            )
            log.debug(
                "apply_window_geometry attempt=%s requested=%sx%s+%s+%s actual=%sx%s+%s+%s ok=%s max=%s full=%s",
                attempt_num,
                width,
                height,
                x,
                y,
                aw,
                ah,
                ax,
                ay,
                ok,
                window.is_maximized(),
                window.is_fullscreen() if hasattr(window, "is_fullscreen") else False,
            )
            if not ok and attempts_left > 1:
                GLib.timeout_add(
                    150,
                    self._apply_window_geometry_once,
                    window,
                    x,
                    y,
                    width,
                    height,
                    attempts_left - 1,
                    attempt_num + 1,
                )
        except Exception:
            log.debug("apply_window_geometry failed attempt=%s", attempt_num, exc_info=True)
        return False

    def _window_workarea(self, display, window):
        wx, wy, ww, wh = window.get_geometry()
        cx = wx + ww // 2
        cy = wy + wh // 2
        for idx in range(display.get_n_monitors()):
            monitor = display.get_monitor(idx)
            if monitor is None:
                continue
            rect = monitor.get_geometry()
            if rect.x <= cx < rect.x + rect.width and rect.y <= cy < rect.y + rect.height:
                return monitor.get_workarea()
        return None

    def _raise_webview_toplevel(self) -> bool:
        if self._webview is None:
            return False
        try:
            toplevel = self._webview.get_toplevel()
            if hasattr(toplevel, "set_keep_above"):
                toplevel.set_keep_above(True)
            if hasattr(toplevel, "present"):
                toplevel.present()
        except Exception:
            log.debug("could not raise wind_mgr after placing window", exc_info=True)
        return False

    def _schedule_raise_webview_toplevel(self) -> None:
        for delay_ms in (180, 350, 700, 1200):
            GLib.timeout_add(delay_ms, self._raise_webview_toplevel)

    def _horizontal_monitor_placement(self, area, placement: str) -> tuple[int, int, int, int]:
        if placement in {"left_half", "top_half"}:
            return area.x, area.y, area.width // 2, area.height
        if placement in {"right_half", "bottom_half"}:
            width = area.width - area.width // 2
            return area.x + area.width // 2, area.y, width, area.height
        if placement in {"left_third", "top_third"}:
            return area.x, area.y, area.width // 3, area.height
        if placement in {"middle_third"}:
            third = area.width // 3
            return area.x + third, area.y, third, area.height
        if placement in {"right_third", "bottom_third"}:
            third = area.width // 3
            return area.x + 2 * third, area.y, area.width - 2 * third, area.height
        return 0, 0, 0, 0

    def _vertical_monitor_placement(self, area, placement: str) -> tuple[int, int, int, int]:
        if placement in {"top_half", "left_half"}:
            return area.x, area.y, area.width, area.height // 2
        if placement in {"bottom_half", "right_half"}:
            height = area.height - area.height // 2
            return area.x, area.y + area.height // 2, area.width, height
        if placement in {"top_third", "left_third"}:
            return area.x, area.y, area.width, area.height // 3
        if placement in {"middle_third"}:
            third = area.height // 3
            return area.x, area.y + third, area.width, third
        if placement in {"bottom_third", "right_third"}:
            third = area.height // 3
            return area.x, area.y + 2 * third, area.width, area.height - 2 * third
        return 0, 0, 0, 0

    def _move_node(self, xid: int, target_project_id: str,
                   with_children) -> None:
        record = self._reg.get(xid)
        if record is None:
            return

        before_project_id = self._tree.get_project_id(record)
        before_members = self._project_member_summary(before_project_id)
        target_before = self._project_member_summary(target_project_id)
        self._tree.move_node(xid, target_project_id, with_children=with_children)
        log.info(
            "move_node xid=%s title=%r mode=%r from=%s to=%s before_from=%s before_to=%s after_from=%s after_to=%s",
            xid,
            record.title,
            with_children,
            before_project_id,
            target_project_id,
            before_members,
            target_before,
            self._project_member_summary(before_project_id),
            self._project_member_summary(target_project_id),
        )
        self._reg.save()
        self.push_graph()

    def _project_member_summary(self, project_id: str) -> list[dict]:
        members = []
        for record in self._reg.all_alive():
            if _is_self_record(record):
                continue
            if self._tree.get_project_id(record) != project_id:
                continue
            members.append({
                "xid": record.xid,
                "parent": record.parent_xid,
                "explicit": record.project_id,
                "title": (record.title or "")[:40],
            })
        return members

    def _set_parent(self, xid: int, parent_xid: int, with_children) -> None:
        record = self._reg.get(xid)
        parent = self._reg.get(parent_xid)
        if record is None or parent is None:
            return
        log.info(
            "set parent: child=%s title=%r parent=%s title=%r",
            record.xid,
            record.title,
            parent.xid,
            parent.title,
        )
        self._tree.set_parent(xid, parent_xid, with_children=with_children)
        self._reg.save()
        self.push_graph()

    def _remove_link(self, xid: int) -> None:
        record = self._reg.get(xid)
        if record is None or record.parent_xid is None:
            return
        current_project_id = self._tree.get_project_id(record)
        parent = self._reg.get(record.parent_xid)
        if parent and xid in parent.children_xids:
            parent.children_xids.remove(xid)
        record.parent_xid = None
        if record.project_id is None:
            record.project_id = current_project_id
        log.info(
            "removed parent link: child=%s preserved_project=%s parent=%s",
            xid,
            current_project_id,
            parent.xid if parent else None,
        )
        self._reg.save()
        self.push_graph()

    def _unlink_children(self, xid: int) -> None:
        record = self._reg.get(xid)
        if record is None:
            return
        child_xids = list(record.children_xids)
        child_project_ids = {}
        for child_xid in child_xids:
            child = self._reg.get(child_xid)
            if child is None:
                continue
            child_project_ids[child_xid] = self._tree.get_project_id(child)
            if child.parent_xid == xid:
                child.parent_xid = None
            if child.project_id is None:
                child.project_id = child_project_ids[child_xid]
        record.children_xids.clear()
        log.info(
            "unlinked all children: parent=%s children=%s preserved_projects=%s",
            xid,
            child_xids,
            child_project_ids,
        )
        self._reg.save()
        self.push_graph()

    def _show_live_preview_from_msg(self, msg: dict, *, active_rate: bool = True) -> None:
        if not self._live_preview_enabled or not self._ui_visible:
            return
        try:
            xid = int(msg["xid"])
            record = self._reg.get(xid)
            if record is None or not record.is_alive or _is_self_record(record):
                self._hide_live_preview()
                return
            x, y = self._live_preview_screen_bounds(msg)
            width = int(float(msg.get("width", 1)))
            height = int(float(msg.get("height", 1)))
            if width <= 4 or height <= 4:
                return
            self._log_live_preview_bounds(xid, x, y, width, height, msg)
            if self._live_preview is None or self._live_preview_xid != xid:
                self._hide_live_preview()
                from live_preview.xcomposite_gl_preview import LivePreview
                fps = self._live_preview_fps if active_rate else self._live_preview_idle_fps
                self._live_preview = LivePreview(xid, fps, overlay=True)
                self._live_preview_xid = xid
                self._live_preview.set_bounds(x, y, width, height)
                self._live_preview.show_all()
            else:
                if active_rate:
                    self._live_preview.set_fps(self._live_preview_fps)
                self._live_preview.set_bounds(x, y, width, height)
        except Exception:
            log.exception("Failed to show live preview")
            self._hide_live_preview()

    def _refresh_hover_thumb(self, xid: int) -> None:
        self._hovered_xid = xid
        self._start_hover_refresh_timer()
        record = self._reg.get(xid)
        if record is None or not record.is_alive or _is_self_record(record):
            if self._hovered_xid == xid:
                self._hovered_xid = None
                self._stop_hover_refresh_timer()
            return
        now_ms = GLib.get_monotonic_time() // 1000
        if now_ms - self._last_capture_at.get(xid, 0) < self._hover_refresh_ms:
            return
        self._capture_one(xid, reason="hover", force=True)

    def _start_hover_refresh_timer(self) -> None:
        if self._hover_refresh_tag is not None:
            return
        self._hover_refresh_tag = GLib.timeout_add(self._hover_refresh_ms, self._hover_refresh_tick)

    def _stop_hover_refresh_timer(self) -> None:
        if self._hover_refresh_tag is None:
            return
        GLib.source_remove(self._hover_refresh_tag)
        self._hover_refresh_tag = None

    def _hover_refresh_tick(self) -> bool:
        xid = self._hovered_xid
        if xid is None:
            self._hover_refresh_tag = None
            return False
        self._refresh_hover_thumb(xid)
        return True

    def _log_live_preview_bounds(
        self, xid: int, x: int, y: int, width: int, height: int, msg: dict
    ) -> None:
        now_ms = GLib.get_monotonic_time() // 1000
        if now_ms - self._last_live_preview_log_ms < 1000:
            return
        self._last_live_preview_log_ms = now_ms
        log.debug(
            "live_preview bounds xid=%s screen=%s,%s size=%sx%s viewport=%s,%s",
            xid,
            x,
            y,
            width,
            height,
            msg.get("viewport_x"),
            msg.get("viewport_y"),
        )

    def _live_preview_screen_bounds(self, msg: dict) -> tuple[int, int]:
        if "screen_x" in msg and "screen_y" in msg:
            return (
                int(float(msg.get("screen_x", 0))),
                int(float(msg.get("screen_y", 0))),
            )
        viewport_x = int(float(msg.get("viewport_x", 0)))
        viewport_y = int(float(msg.get("viewport_y", 0)))
        origin_x, origin_y = self._webview_screen_origin()
        return origin_x + viewport_x, origin_y + viewport_y

    def _webview_screen_origin(self) -> tuple[int, int]:
        if self._webview is None:
            return 0, 0
        window = self._webview.get_window()
        if window is None:
            return 0, 0
        origin = window.get_origin()
        if isinstance(origin, tuple) and len(origin) == 3:
            ok, x, y = origin
            if ok:
                return int(x), int(y)
            return 0, 0
        if isinstance(origin, tuple) and len(origin) == 2:
            x, y = origin
            return int(x), int(y)
        return 0, 0

    def _idle_live_preview(self, xid: int) -> None:
        if self._live_preview is None:
            return
        try:
            self._live_preview.set_fps(self._live_preview_idle_fps)
            self._capture_live_preview_idle_thumb(xid)
        except Exception:
            log.debug("Failed to idle live preview", exc_info=True)

    def _capture_live_preview_idle_thumb(self, xid: int) -> None:
        now_ms = GLib.get_monotonic_time() // 1000
        last_ms = self._last_live_preview_idle_capture_ms.get(xid, 0)
        if now_ms - last_ms < 1000:
            return
        self._last_live_preview_idle_capture_ms[xid] = now_ms
        if (
            self._live_preview is not None
            and self._live_preview_xid == xid
            and self._live_preview.snapshot_to_png(str(self._capture.thumb_path(xid)))
        ):
            self._on_thumbnail_updated([xid])
            return
        self._capture_one(xid, reason="live-preview-idle", force=True)

    def _hide_live_preview(self) -> None:
        if self._live_preview is None:
            self._live_preview_xid = None
            return
        try:
            self._live_preview.destroy()
        except Exception:
            log.debug("Failed to destroy live preview", exc_info=True)
        self._live_preview = None
        self._live_preview_xid = None

    def _rename_project(self, project_id: str, name: str) -> None:
        project_id = str(project_id)
        name = str(name).strip()
        if not name:
            return

        # Project ids may be numeric roots, :solo synthetic ids, or explicit
        # manual ids. Keep user labels separate from provider project_name,
        # because VSCode/PyCharm providers refresh that field from window title.
        renamed = []
        for record in self._reg.all_records():
            if not record.is_alive:
                continue
            if self._tree.get_project_id(record) == project_id:
                record.metadata["custom_project_name"] = name
                renamed.append(record.xid)

        if project_id.isdigit():
            root = self._reg.get(int(project_id))
            if root is not None:
                root.metadata["custom_project_name"] = name
                if root.xid not in renamed:
                    renamed.append(root.xid)

        if not renamed:
            log.warning("rename_project: no records matched project_id=%s name=%r", project_id, name)
            return

        log.info("rename_project: project_id=%s name=%r records=%s", project_id, name, renamed)
        self._reg.save()
        self.push_graph()

    def _refresh_all_thumbs(self) -> None:
        records = [r for r in self._reg.all_alive() if not _is_self_record(r)]
        if not records:
            return
        for r in records:
            self._capture_one(r.xid, reason="manual", force=True)

    def _set_auto_refresh(self, enabled: bool) -> None:
        self._auto_refresh = enabled
        if self._auto_refresh_tag is not None:
            GLib.source_remove(self._auto_refresh_tag)
            self._auto_refresh_tag = None
        if enabled:
            self._auto_refresh_tag = GLib.timeout_add_seconds(30, self._auto_tick)

    def _auto_tick(self) -> bool:
        if not self._auto_refresh:
            return False
        self._refresh_all_thumbs()
        return True  # repeat

    def _on_graph_updated(self) -> None:
        if not self._ui_visible:
            return
        self._schedule_graph_push()

    def _push_graph_debounced(self) -> bool:
        self._graph_update_tag = None
        self._last_graph_push_at = GLib.get_monotonic_time() // 1000
        self.push_graph()
        return False

    def _on_focus_changed(self, new_xid: int | None, old_xid: int | None, **_kw) -> None:
        self._activity.mark_focus_change(new_xid, old_xid)
        if new_xid is not None:
            record = self._reg.get(new_xid)
            if record is not None and record.is_alive and not _is_self_record(record):
                self._last_active_xid = new_xid
        if self._ui_visible:
            self.push_active_window()
        if old_xid is None or old_xid == new_xid or new_xid is None:
            return
        log.debug("focus changed: capture previous active xid=%s new=%s", old_xid, new_xid)
        self._capture_one(old_xid, reason="focus-leave")

    def _on_window_opened(self, record, **_kw) -> None:
        if record is None or _is_self_record(record):
            return
        xid = int(record.xid)
        self._failed_capture_attempts[xid] = 0
        delay_ms = self._new_window_capture_delay_ms
        log.debug("new window capture scheduled xid=%s delay=%sms title=%r", xid, delay_ms, record.title)
        if delay_ms <= 0:
            self._capture_one(xid, reason="new-window", force=True)
        else:
            GLib.timeout_add(delay_ms, self._capture_new_window_once, xid)

    def _capture_new_window_once(self, xid: int) -> bool:
        self._capture_one(xid, reason="new-window", force=True)
        return False

    def _on_window_closed(self, xid: int, **_kw) -> None:
        self._capture_queue.pop(int(xid), None)
        self._last_capture_at.pop(int(xid), None)
        self._last_capture_reason.pop(int(xid), None)
        self._successful_thumb_xids.discard(int(xid))
        self._failed_capture_attempts.pop(int(xid), None)

    def _start_thumbnail_refresh(self) -> None:
        if self._active_refresh_ms > 0 and self._active_refresh_tag is None:
            self._active_refresh_tag = GLib.timeout_add(
                self._active_refresh_ms, self._refresh_active_thumb_tick
            )
        if self._bg_refresh_tag is None:
            self._bg_refresh_tag = GLib.timeout_add(
                self._bg_refresh_ms, self._refresh_background_thumb_tick
            )
        GLib.timeout_add(5000, self._main_loop_latency_probe)
        GLib.timeout_add(10000, self._cpu_metrics_probe)

    def _main_loop_latency_probe(self) -> bool:
        import time as _time
        scheduled_at = _time.monotonic()
        def _idle():
            latency_ms = (_time.monotonic() - scheduled_at) * 1000
            self._main_loop_latency_ms = latency_ms
            log.debug("main-loop idle latency: %.1fms", latency_ms)
            return False
        GLib.idle_add(_idle)
        return True

    def _cpu_metrics_probe(self) -> bool:
        now = time.monotonic()
        proc_cpu = time.process_time()
        if self._cpu_probe_last_wall is None or self._cpu_probe_last_proc is None:
            self._cpu_probe_last_wall = now
            self._cpu_probe_last_proc = proc_cpu
            self._cpu_probe_last_threads = self._read_thread_cpu_times()
            return True

        elapsed = max(0.001, now - self._cpu_probe_last_wall)
        cpu_pct = max(0.0, (proc_cpu - self._cpu_probe_last_proc) / elapsed * 100.0)
        thread_times = self._read_thread_cpu_times()
        thread_deltas: list[tuple[float, int, str]] = []
        for tid, cpu_time in thread_times.items():
            prev = self._cpu_probe_last_threads.get(tid)
            if prev is None:
                continue
            delta_pct = max(0.0, (cpu_time - prev) / elapsed * 100.0)
            if delta_pct >= 0.5:
                thread_deltas.append((delta_pct, tid, self._read_thread_name(tid)))
        thread_deltas.sort(reverse=True)
        top_threads = ", ".join(
            f"{name}:{tid}:{pct:.1f}%" for pct, tid, name in thread_deltas[:5]
        ) or "none"
        log.info(
            "[cpu] process=%.1f%% visible=%s latency=%.1fms queue=%s capture_running=%s top_threads=%s",
            cpu_pct,
            self._ui_visible,
            self._main_loop_latency_ms,
            len(self._capture_queue),
            self._capture_running_xid,
            top_threads,
        )
        self._cpu_probe_last_wall = now
        self._cpu_probe_last_proc = proc_cpu
        self._cpu_probe_last_threads = thread_times
        return True

    def _read_thread_cpu_times(self) -> dict[int, float]:
        ticks_per_second = os.sysconf(os.sysconf_names["SC_CLK_TCK"])
        result: dict[int, float] = {}
        task_dir = Path("/proc/self/task")
        try:
            for thread_dir in task_dir.iterdir():
                if not thread_dir.name.isdigit():
                    continue
                stat_text = (thread_dir / "stat").read_text(encoding="utf-8")
                right_paren = stat_text.rfind(")")
                if right_paren < 0:
                    continue
                fields = stat_text[right_paren + 2:].split()
                # After removing "pid (comm)", utime/stime are fields 12/13.
                utime = int(fields[11])
                stime = int(fields[12])
                result[int(thread_dir.name)] = (utime + stime) / ticks_per_second
        except Exception:
            log.debug("failed to read /proc thread cpu metrics", exc_info=True)
        return result

    def _read_thread_name(self, tid: int) -> str:
        try:
            return Path(f"/proc/self/task/{tid}/comm").read_text(encoding="utf-8").strip()
        except Exception:
            return "thread"

    def _refresh_active_thumb_tick(self) -> bool:
        # Active-card blinking is frontend-only. Screenshot refresh remains
        # independent from graph interaction; the queue only prevents parallel
        # captures and gives rarer updates priority.
        if not self._ui_visible:
            return True
        xid = self._active_window_xid(allow_fallback=False)
        if xid is not None and xid != self._hovered_xid:
            self._capture_one(xid, reason="active")
        return True

    def _refresh_background_thumb_tick(self) -> bool:
        active_xid = self._active_window_xid(allow_fallback=False)
        excluded_active_xid = active_xid if self._ui_visible else None
        records = [
            r for r in self._reg.all_alive()
            if not _is_self_record(r)
            and r.xid != excluded_active_xid
            and r.xid != self._hovered_xid
        ]
        if not records:
            return True
        record = self._pick_background_record(
            records,
            active_xid if self._ui_visible else None,
        )
        if record is None:
            return True
        self._capture_one(record.xid, reason="background")
        return True

    def _pick_background_record(self, records, active_xid: int | None):
        now_ms = GLib.get_monotonic_time() // 1000
        due = [
            r for r in records
            if now_ms - self._last_capture_at.get(r.xid, 0) >= self._background_due_ms(r.xid)
        ]
        if not due:
            return None
        if not self._activity_priority_enabled:
            self._bg_refresh_idx %= len(due)
            record = due[self._bg_refresh_idx]
            self._bg_refresh_idx = (self._bg_refresh_idx + 1) % len(due)
            return record
        return max(
            due,
            key=lambda r: self._activity.score(
                r.xid,
                active_xid=active_xid,
                last_capture_at_ms=self._last_capture_at.get(r.xid, 0),
            ),
        )

    def _background_due_ms(self, xid: int) -> int:
        if xid in self._successful_thumb_xids or self._capture.thumb_path(xid).exists():
            return self._background_refresh_min_ms
        return min(self._background_refresh_min_ms, self._capture_retry_ms)

    def _capture_one(self, xid: int, *, reason: str = "background", force: bool = False) -> None:
        if not self._ui_visible and reason in {"active", "hover", "live-preview-idle"}:
            return
        if not force and reason == "active" and not self._active_capture_due(xid):
            return
        record = self._reg.get(xid)
        if record is None or not record.is_alive or _is_self_record(record):
            return
        self._queue_capture(record.xid, reason)
        self._start_next_capture()

    def _active_capture_due(self, xid: int) -> bool:
        now = GLib.get_monotonic_time() // 1000
        interval_ms = self._active_refresh_ms or 0
        last = self._last_capture_at.get(xid, 0)
        return now - last >= interval_ms

    def _queue_capture(self, xid: int, reason: str) -> None:
        if reason == "hover":
            # Hover is user-visible and should preempt automatic refresh work.
            # Keep only one queued hover capture, and replace any stale lower
            # priority request for the same card.
            for queued_xid, queued_reason in list(self._capture_queue.items()):
                if queued_reason == "hover" or queued_xid == xid:
                    del self._capture_queue[queued_xid]
            self._capture_queue[xid] = reason
            self._capture_queue.move_to_end(xid, last=False)
            return
        if reason == "active":
            # Active refresh is frequent. Keep only the latest active-window
            # request so it cannot starve rarer focus/background/manual updates.
            for queued_xid, queued_reason in list(self._capture_queue.items()):
                if queued_reason == "active":
                    del self._capture_queue[queued_xid]
            self._capture_queue[xid] = reason
            return
        self._capture_queue[xid] = reason
        self._capture_queue.move_to_end(xid)

    def _start_capture(self, xid: int, reason: str) -> None:
        self._capture_running_xid = xid
        self._last_capture_reason[xid] = reason
        self._last_capture_at[xid] = GLib.get_monotonic_time() // 1000
        log.debug(
            "capture start xid=%d reason=%s latency=%.0fms queue=%d",
            xid, reason, self._main_loop_latency_ms, len(self._capture_queue),
        )

        def _done(success: bool) -> None:
            self._capture_running_xid = None
            if success:
                self._successful_thumb_xids.add(xid)
                self._failed_capture_attempts.pop(xid, None)
                self._on_thumbnail_updated([xid])
            else:
                self._handle_capture_failed(xid, reason)
            self._start_next_capture()
            return False

        self._capture.capture_async(xid, callback=_done)
        if xid not in self._icon_captured_xids and not self._capture.icon_path(xid).exists():
            self._icon_captured_xids.add(xid)
            def _capture_icon_once() -> bool:
                self._capture.capture_icon(xid)
                return False
            GLib.idle_add(_capture_icon_once)

    def _handle_capture_failed(self, xid: int, reason: str) -> None:
        attempts = self._failed_capture_attempts.get(xid, 0) + 1
        if self._capture_retry_max_attempts and attempts > self._capture_retry_max_attempts:
            self._failed_capture_attempts.pop(xid, None)
            log.warning(
                "capture failed xid=%s reason=%s attempts=%s; retry limit reached",
                xid,
                reason,
                attempts - 1,
            )
            return
        self._failed_capture_attempts[xid] = attempts
        log.debug(
            "capture failed xid=%s reason=%s attempts=%s; retry in %sms",
            xid,
            reason,
            attempts,
            self._capture_retry_ms,
        )
        self._ensure_retry_capture_timer()

    def _ensure_retry_capture_timer(self) -> None:
        if self._retry_capture_tag is not None:
            return
        self._retry_capture_tag = GLib.timeout_add(self._capture_retry_ms, self._retry_failed_capture_tick)

    def _retry_failed_capture_tick(self) -> bool:
        self._retry_capture_tag = None
        if not self._failed_capture_attempts:
            return False
        now_ms = GLib.get_monotonic_time() // 1000
        due_xids = [
            xid for xid in self._failed_capture_attempts
            if now_ms - self._last_capture_at.get(xid, 0) >= self._capture_retry_ms
        ]
        for xid in due_xids:
            record = self._reg.get(xid)
            if record is None or not record.is_alive or _is_self_record(record):
                self._failed_capture_attempts.pop(xid, None)
                continue
            self._capture_one(xid, reason="retry", force=True)
        if self._failed_capture_attempts:
            self._ensure_retry_capture_timer()
        return False

    def _start_next_capture(self) -> None:
        if self._capture_running_xid is not None:
            return
        while self._capture_queue:
            xid, reason = self._pop_next_queued_capture()
            record = self._reg.get(xid)
            if record is None or not record.is_alive or _is_self_record(record):
                continue
            self._start_capture(xid, reason)
            return

    def _pop_next_queued_capture(self) -> tuple[int, str]:
        priority = {
            "manual": 0,
            "hover": 1,
            "new-window": 2,
            "retry": 3,
            "focus-leave": 4,
            "live-preview-idle": 5,
            "background": 6,
            "active": 7,
        }
        best: tuple[int, str] | None = None
        best_score = 999
        for xid, reason in self._capture_queue.items():
            score = priority.get(reason, 3)
            if score < best_score:
                best = (xid, reason)
                best_score = score
        if best is not None:
            del self._capture_queue[best[0]]
            return best
        return self._capture_queue.popitem(last=False)


def _is_self_record(record) -> bool:
    values = {
        record.title,
        record.app_name,
        record.wm_class,
        record.wm_class_group,
    }
    return any((v or "").strip().lower() == "wind_mgr" for v in values)
