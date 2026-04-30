from __future__ import annotations
import json
import logging
from typing import TYPE_CHECKING, Callable

import gi
gi.require_version("WebKit2", "4.1")
from gi.repository import GLib, WebKit2

from core.events import bus, EVT_GRAPH_UPDATED
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

        bus.subscribe(EVT_GRAPH_UPDATED, self._on_graph_updated)

    def attach(self, webview: WebKit2.WebView) -> None:
        self._webview = webview
        ucm = webview.get_user_content_manager()
        ucm.register_script_message_handler("api")
        ucm.connect("script-message-received::api", self._on_js_message)

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

    def push_active_window(self) -> None:
        if self._webview is None:
            return
        try:
            active_xid = self._active_window_xid()
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

        active_xid = self._active_window_xid()

        return {"nodes": nodes, "edges": edges, "projects": projects, "active_xid": active_xid}

    def _active_window_xid(self) -> int | None:
        try:
            import gi
            gi.require_version("Wnck", "3.0")
            from gi.repository import Wnck
            screen = Wnck.Screen.get_default()
            screen.force_update()
            active = screen.get_active_window()
            if active is None:
                return None
            xid = active.get_xid()
            record = self._reg.get(xid)
            if record is None or _is_self_record(record):
                return None
            return xid
        except Exception:
            log.debug("Failed to read active window", exc_info=True)
            return None

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
        log.debug("JS message: %s", msg)
        try:
            if action == "activate":
                self._activate_window(msg["xid"])
            elif action == "move_node":
                self._tree.move_node(
                    msg["xid"], msg["project_id"],
                    with_children=msg.get("with_children", False)
                )
                self._reg.save()
                self.push_graph()
            elif action == "rename_project":
                self._rename_project(msg["project_id"], msg["name"])
            elif action == "refresh_thumb":
                self._capture.capture_async(msg["xid"],
                                            callback=lambda ok: self.push_graph() if ok else None)
            elif action == "refresh_all_thumbs":
                self._refresh_all_thumbs()
            elif action == "toggle_auto_refresh":
                self._set_auto_refresh(msg.get("enabled", False))
            elif action == "refresh_active":
                self.push_active_window()
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
                ts = Gtk.get_current_event_time()
                if not ts:
                    # Wnck expects a 32-bit X timestamp, not Unix epoch time.
                    ts = (GLib.get_monotonic_time() // 1000) & 0xFFFFFFFF
                w.activate(ts)
                return
        log.warning("activate: window xid=%d not found", xid)

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
                log.info("All thumbnails captured — pushing graph")
                self.push_graph()

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
        GLib.idle_add(self.push_graph)


def _is_self_record(record) -> bool:
    values = {
        record.title,
        record.app_name,
        record.wm_class,
        record.wm_class_group,
    }
    return any((v or "").strip().lower() == "wind_mgr" for v in values)
