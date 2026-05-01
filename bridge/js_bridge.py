from __future__ import annotations
from collections import OrderedDict
import configparser
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

import gi
gi.require_version("WebKit2", "4.1")
from gi.repository import GLib, WebKit2

from core.activity_stats import ActivityStats
from core.events import bus, EVT_FOCUS_CHANGED, EVT_GRAPH_UPDATED
from core.relationship import RelationshipTree

if TYPE_CHECKING:
    from core.window_registry import WindowRegistry
    from capture.screenshot import ScreenshotCapture

log = logging.getLogger(__name__)
_CONFIG_PATH = Path(__file__).parent.parent / "config.ini"


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

        cfg = configparser.ConfigParser()
        cfg.read(_CONFIG_PATH)
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
        bus.subscribe(EVT_GRAPH_UPDATED, self._on_graph_updated)
        bus.subscribe(EVT_FOCUS_CHANGED, self._on_focus_changed)

    def set_before_activate_callback(self, callback: Callable[[], None]) -> None:
        self._before_activate_cb = callback

    def set_ui_visible(self, visible: bool) -> None:
        self._ui_visible = visible
        if not visible:
            self._pending_thumb_xids.clear()
            self._hovered_xid = None
            self._stop_hover_refresh_timer()
            self._hide_live_preview()

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
        GLib.idle_add(self._handle_message, msg)

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
        for w in screen.get_windows():
            if w.get_xid() == xid:
                log.info(
                    "activate matched xid=%s wnck_name=%r app=%r",
                    xid,
                    w.get_name(),
                    w.get_application().get_name() if w.get_application() else "",
                )
                if self._before_activate_cb is not None:
                    self._before_activate_cb()
                ts = Gtk.get_current_event_time()
                if not ts:
                    # Wnck expects a 32-bit X timestamp, not Unix epoch time.
                    ts = (GLib.get_monotonic_time() // 1000) & 0xFFFFFFFF
                w.activate(ts)
                x, y, width, height = w.get_geometry()
                GLib.timeout_add(140, flash_window_rect, x, y, width, height)
                return
        log.warning("activate: window xid=%d not found", xid)

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
        parent = self._reg.get(record.parent_xid)
        if parent and xid in parent.children_xids:
            parent.children_xids.remove(xid)
        record.parent_xid = None
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
        # Store custom name in registry as metadata on root record
        if project_id.isdigit():
            record = self._reg.get(int(project_id))
            if record:
                record.metadata["project_name"] = name
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

    def _refresh_active_thumb_tick(self) -> bool:
        # Active-card blinking is frontend-only. Screenshot refresh remains
        # independent from graph interaction; the queue only prevents parallel
        # captures and gives rarer updates priority.
        xid = self._active_window_xid(allow_fallback=False)
        if xid is not None and xid != self._hovered_xid:
            self._capture_one(xid, reason="active")
        return True

    def _refresh_background_thumb_tick(self) -> bool:
        active_xid = self._active_window_xid(allow_fallback=False)
        records = [
            r for r in self._reg.all_alive()
            if not _is_self_record(r) and r.xid != active_xid and r.xid != self._hovered_xid
        ]
        if not records:
            return True
        record = self._pick_background_record(records, active_xid)
        if record is None:
            return True
        self._capture_one(record.xid, reason="background")
        return True

    def _pick_background_record(self, records, active_xid: int | None):
        now_ms = GLib.get_monotonic_time() // 1000
        due = [
            r for r in records
            if now_ms - self._last_capture_at.get(r.xid, 0) >= self._background_refresh_min_ms
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

    def _capture_one(self, xid: int, *, reason: str = "background", force: bool = False) -> None:
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
                self._on_thumbnail_updated([xid])
            self._start_next_capture()
            return False

        self._capture.capture_async(xid, callback=_done)
        if xid not in self._icon_captured_xids and not self._capture.icon_path(xid).exists():
            self._icon_captured_xids.add(xid)
            GLib.idle_add(self._capture.capture_icon, xid)

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
            "focus-leave": 2,
            "live-preview-idle": 3,
            "background": 4,
            "active": 5,
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
