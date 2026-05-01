from __future__ import annotations
import configparser
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Callable

import gi
gi.require_version("WebKit2", "4.1")
from gi.repository import GLib, WebKit2

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
        self._capture_inflight: set[int] = set()
        self._thumb_update_tag: int | None = None
        self._pending_thumb_xids: set[int] = set()
        self._last_thumb_urls: dict[int, tuple[str, str]] = {}
        self._before_activate_cb: Callable[[], None] | None = None
        self._ui_visible = False
        self._interaction_active = False
        self._last_graph_push_at = 0.0
        self._last_active_xid: int | None = None

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

        bus.subscribe(EVT_GRAPH_UPDATED, self._on_graph_updated)
        bus.subscribe(EVT_FOCUS_CHANGED, self._on_focus_changed)

    def set_before_activate_callback(self, callback: Callable[[], None]) -> None:
        self._before_activate_cb = callback

    def set_ui_visible(self, visible: bool) -> None:
        self._ui_visible = visible
        if not visible:
            self._pending_thumb_xids.clear()

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
        if action not in {"refresh_active", "set_interaction_active"}:
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
            elif action == "rename_project":
                self._rename_project(msg["project_id"], msg["name"])
            elif action == "refresh_thumb":
                self._capture_one(int(msg["xid"]))
            elif action == "refresh_all_thumbs":
                self._refresh_all_thumbs()
            elif action == "toggle_auto_refresh":
                self._set_auto_refresh(msg.get("enabled", False))
            elif action == "set_interaction_active":
                self._interaction_active = bool(msg.get("active", False))
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
        import gi
        gi.require_version("Gtk", "3.0")
        gi.require_version("Wnck", "3.0")
        from gi.repository import Gtk, Wnck
        screen = Wnck.Screen.get_default()
        screen.force_update()
        for w in screen.get_windows():
            if w.get_xid() == xid:
                if self._before_activate_cb is not None:
                    self._before_activate_cb()
                ts = Gtk.get_current_event_time()
                if not ts:
                    # Wnck expects a 32-bit X timestamp, not Unix epoch time.
                    ts = (GLib.get_monotonic_time() // 1000) & 0xFFFFFFFF
                w.activate(ts)
                return
        log.warning("activate: window xid=%d not found", xid)

    def _move_node(self, xid: int, target_project_id: str,
                   with_children: bool) -> None:
        record = self._reg.get(xid)
        if record is None:
            return

        self._tree.move_node(xid, target_project_id, with_children=with_children)
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

    def _rename_project(self, project_id: str, name: str) -> None:
        # Store custom name in registry as metadata on root record
        if project_id.isdigit():
            record = self._reg.get(int(project_id))
            if record:
                record.metadata["project_name"] = name
                self._reg.save()
                self.push_graph()

    def _refresh_all_thumbs(self) -> None:
        records = self._reg.all_alive()
        if not records:
            return
        remaining = [len(records)]

        def _on_one_done(success: bool) -> None:
            remaining[0] -= 1
            log.debug("capture done success=%s remaining=%d", success, remaining[0])
            if remaining[0] <= 0:
                log.info("All thumbnails captured — updating thumbnails")
                self._on_thumbnail_updated([r.xid for r in records])

        for r in records:
            self._capture.capture_async(r.xid, callback=_on_one_done)
            GLib.idle_add(self._capture.capture_icon, r.xid)

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
        if new_xid is not None:
            record = self._reg.get(new_xid)
            if record is not None and record.is_alive and not _is_self_record(record):
                self._last_active_xid = new_xid
        if self._ui_visible:
            self.push_active_window()
        if old_xid is None or old_xid == new_xid or new_xid is None:
            return
        log.debug("focus changed: capture previous active xid=%s new=%s", old_xid, new_xid)
        self._capture_one(old_xid)

    def _start_thumbnail_refresh(self) -> None:
        if self._active_refresh_ms > 0 and self._active_refresh_tag is None:
            self._active_refresh_tag = GLib.timeout_add(
                self._active_refresh_ms, self._refresh_active_thumb_tick
            )
        if self._bg_refresh_tag is None:
            self._bg_refresh_tag = GLib.timeout_add(
                self._bg_refresh_ms, self._refresh_background_thumb_tick
            )

    def _refresh_active_thumb_tick(self) -> bool:
        if not self._ui_visible or self._interaction_active:
            return True
        xid = self._active_window_xid(allow_fallback=False)
        if xid is not None:
            self._capture_one(xid)
        return True

    def _refresh_background_thumb_tick(self) -> bool:
        if self._interaction_active:
            return True
        active_xid = self._active_window_xid(allow_fallback=False)
        records = [
            r for r in self._reg.all_alive()
            if not _is_self_record(r) and r.xid != active_xid
        ]
        if not records:
            return True
        self._bg_refresh_idx %= len(records)
        record = records[self._bg_refresh_idx]
        self._bg_refresh_idx = (self._bg_refresh_idx + 1) % len(records)
        self._capture_one(record.xid)
        return True

    def _capture_one(self, xid: int) -> None:
        if self._interaction_active:
            return
        if xid in self._capture_inflight:
            return
        record = self._reg.get(xid)
        if record is None or not record.is_alive or _is_self_record(record):
            return
        self._capture_inflight.add(xid)

        def _done(success: bool) -> None:
            self._capture_inflight.discard(xid)
            if success:
                self._on_thumbnail_updated([xid])

        self._capture.capture_async(xid, callback=_done)
        GLib.idle_add(self._capture.capture_icon, xid)


def _is_self_record(record) -> bool:
    values = {
        record.title,
        record.app_name,
        record.wm_class,
        record.wm_class_group,
    }
    return any((v or "").strip().lower() == "wind_mgr" for v in values)
