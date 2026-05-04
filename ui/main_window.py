from __future__ import annotations
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("WebKit2", "4.1")
gi.require_version("AyatanaAppIndicator3", "0.1")
from gi.repository import Gtk, Gdk, WebKit2, GLib
from gi.repository import AyatanaAppIndicator3 as AppIndicator3
from pynput import keyboard
from core.config import read_config
cfg = read_config()
_HOTKEY = str(cfg.get("capture", "hotkey"))

print("Configured hotkey:", [_HOTKEY])

from .hotkey import bind_hotkey
from .edge_zones import EdgeZoneContext, EdgeZoneWatcher

if TYPE_CHECKING:
    from bridge.js_bridge import JSBridge

log = logging.getLogger(__name__)

WEB_DIR = Path(__file__).parent.parent / "web"
INDEX_URI = (WEB_DIR / "index.html").as_uri()


def _parse_config_section(cfg, section: str) -> dict:
    result: dict = {}
    if not cfg.has_section(section):
        return result
    for key, val in cfg.items(section):
        val = val.strip()
        try:
            f = float(val)
            result[key] = int(f) if f == int(f) else f
        except ValueError:
            result[key] = val
    return result


def _read_frontend_config() -> dict:
    cfg = read_config(raw=True, preserve_case=True)
    return {
        "layout": _parse_config_section(cfg, "layout"),
        "activation": _parse_config_section(cfg, "activation"),
    }


class MainWindow:
    def __init__(self, bridge: "JSBridge") -> None:
        self._bridge = bridge
        self._visible = False
        self._win: Gtk.Window | None = None
        self._webview: WebKit2.WebView | None = None
        self._indicator: AppIndicator3.Indicator | None = None
        self._edge_zones: EdgeZoneWatcher | None = None
        self._last_edge_monitor: int | None = None

    def build(self) -> None:
        self._win = Gtk.Window()
        self._win.set_title("wind_mgr")
        self._win.set_default_size(1400, 900)
        self._win.set_keep_above(True)
        self._win.set_skip_taskbar_hint(True)
        self._win.set_skip_pager_hint(True)
        self._win.connect("delete-event", self._on_delete)
        self._win.connect("key-press-event", self._on_key)
        self._win.connect("map-event", self._on_map)

        # Center on screen
        self._win.set_position(Gtk.WindowPosition.CENTER)

        # WebView
        # DOCUMENT_VIEWER cache model: keep only resources referenced by the
        # current page. Prevents stale ?t=mtime thumbnail URLs from accumulating
        # decoded bitmaps in the image cache indefinitely.
        ctx = WebKit2.WebContext.get_default()
        ctx.set_cache_model(WebKit2.CacheModel.DOCUMENT_VIEWER)

        settings = WebKit2.Settings()
        settings.set_enable_javascript(True)
        settings.set_allow_file_access_from_file_urls(True)
        settings.set_allow_universal_access_from_file_urls(True)
        settings.set_enable_developer_extras(True)

        self._webview = WebKit2.WebView()
        self._webview.set_settings(settings)

        frontend_cfg = _read_frontend_config()
        config_js = (
            "window.windMgrConfig=" + json.dumps(frontend_cfg) + ";"
            "window.windMgrConfigReady=Promise.resolve(window.windMgrConfig);"
        )
        ucm = self._webview.get_user_content_manager()
        ucm.add_script(WebKit2.UserScript(
            config_js,
            WebKit2.UserContentInjectedFrames.TOP_FRAME,
            WebKit2.UserScriptInjectionTime.START,
            None, None,
        ))

        self._webview.load_uri(INDEX_URI)
        self._webview.connect("load-changed", self._on_load_changed)

        self._win.add(self._webview)

        # Periodically flush WebKit's memory cache to evict stale decoded images.
        GLib.timeout_add_seconds(60, self._flush_webkit_cache)

        # Wire bridge to webview
        self._bridge.attach(self._webview)
        self._bridge.set_before_activate_callback(self._hide_after_activation)

        # Tray
        self._build_tray()

        # Global hotkey
        self._bind_hotkey()

        self._edge_zones = EdgeZoneWatcher(
            on_toggle=self.toggle,
            on_show=self.show,
            on_hide=self.hide,
        )
        self._edge_zones.start()

    def _build_tray(self) -> None:
        try:
            self._indicator = AppIndicator3.Indicator.new(
                "wind_mgr",
                "preferences-system-windows",
                AppIndicator3.IndicatorCategory.APPLICATION_STATUS,
            )
            self._indicator.set_status(AppIndicator3.IndicatorStatus.ACTIVE)
            self._indicator.set_menu(self._build_tray_menu())
        except Exception:
            log.warning("Could not create system tray indicator", exc_info=True)

    def _build_tray_menu(self) -> Gtk.Menu:
        menu = Gtk.Menu()

        item_toggle = Gtk.MenuItem(label="Show / Hide  [Super+W]")
        item_toggle.connect("activate", lambda _: self.toggle())
        menu.append(item_toggle)

        item_rebind = Gtk.MenuItem(label="Rebind Super+W hotkey")
        item_rebind.connect("activate", lambda _: self._show_hotkey_dialog())
        menu.append(item_rebind)

        item_sep = Gtk.SeparatorMenuItem()
        menu.append(item_sep)

        item_quit = Gtk.MenuItem(label="Quit wind_mgr")
        item_quit.connect("activate", lambda _: Gtk.main_quit())
        menu.append(item_quit)

        menu.show_all()
        return menu

    def _on_load_changed(self, webview: WebKit2.WebView,
                         event: WebKit2.LoadEvent) -> None:
        if event == WebKit2.LoadEvent.FINISHED:
            uri = webview.get_uri() or ""
            if "index.html" in uri:
                # Small delay to let D3 initialise before first push
                GLib.timeout_add(300, self._initial_push)

    #def _bind_hotkey(self) -> None:
    #    bind_hotkey(lambda: GLib.idle_add(self.toggle))

    def _bind_hotkey(self) -> None:
        # This runs in a background thread
        def on_activate():
            # Use GLib.idle_add because GTK is not thread-safe
            GLib.idle_add(self.toggle)

        # Define the shortcut (e.g., <Super>+w)
        # Note: pynput uses <cmd> for the Super/Windows key
        # pynput does uses for "Ctrl" 
        self.hotkey_listener = keyboard.GlobalHotKeys({
            _HOTKEY: on_activate
        })
        self.hotkey_listener.start()

    def _flush_webkit_cache(self) -> bool:
        WebKit2.WebContext.get_default().clear_cache()
        log.debug("WebKit memory cache cleared")
        return True  # keep repeating

    def _show_hotkey_dialog(self) -> None:
        dialog = Gtk.Dialog(
            title="Set wind_mgr hotkey",
            transient_for=self._win,
            flags=Gtk.DialogFlags.MODAL,
        )
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        apply_btn = dialog.add_button("Apply", Gtk.ResponseType.OK)
        apply_btn.set_sensitive(False)

        box = dialog.get_content_area()
        box.set_spacing(10)
        box.set_border_width(12)

        label = Gtk.Label(label="Press the new shortcut, then click Apply.")
        label.set_xalign(0)
        box.pack_start(label, False, False, 0)

        captured = {"accel": ""}
        value = Gtk.Label(label="No shortcut captured")
        value.set_xalign(0)
        box.pack_start(value, False, False, 0)

        def _on_key(_widget, event) -> bool:
            accel = Gtk.accelerator_name(event.keyval, event.state & Gtk.accelerator_get_default_mod_mask())
            if not accel or accel in {"Escape", "Return", "KP_Enter"}:
                return False
            captured["accel"] = accel
            value.set_text(accel)
            apply_btn.set_sensitive(True)
            return True

        dialog.connect("key-press-event", _on_key)
        dialog.show_all()
        response = dialog.run()
        accel = captured["accel"]
        dialog.destroy()

        if response == Gtk.ResponseType.OK and accel:
            if bind_hotkey(lambda: GLib.idle_add(self.toggle), accel):
                log.info("Hotkey changed to %s", accel)
            else:
                log.warning("Could not bind selected hotkey: %s", accel)

    def _initial_push(self) -> bool:
        self._bridge.push_graph()
        return False  # one-shot

    def _on_delete(self, win: Gtk.Window, event) -> bool:
        # Hide instead of destroy so tray remains
        self.hide()
        return True

    def _on_key(self, win: Gtk.Window, event: Gdk.EventKey) -> bool:
        if event.keyval == Gdk.KEY_Escape:
            self.hide()
            return True
        return False

    def _on_map(self, win: Gtk.Window, event) -> bool:
        return False

    def show(self, edge_context: EdgeZoneContext | None = None) -> None:
        if self._win:
            monitor_changed = (
                edge_context is not None
                and self._last_edge_monitor is not None
                and edge_context.monitor_index != self._last_edge_monitor
            )
            should_center_monitor = (
                edge_context is not None
                and (self._last_edge_monitor is None or monitor_changed)
            )
            self._bridge.set_ui_visible(True)
            if edge_context is not None:
                self._move_to_monitor(edge_context)
            self._bridge.push_show_active_animation()
            self._win.show_all()
            self._win.deiconify()  # Разворачивает, если было свернуто
            self._win.maximize()
            self._win.present()
            log.info(
                "show request edge_monitor=%s last_monitor=%s monitor_changed=%s center_monitor=%s visible=%s",
                edge_context.monitor_index if edge_context is not None else None,
                self._last_edge_monitor,
                monitor_changed,
                should_center_monitor,
                self._visible,
            )
            GLib.timeout_add(120, self._ensure_maximized_visible_window, edge_context)
            GLib.timeout_add(20, self._bridge.push_show_active_animation)
            GLib.timeout_add(180, self._bridge.push_stable_show_active_animation)
            GLib.timeout_add(320, self._bridge.push_stable_show_active_animation)
            if should_center_monitor:
                GLib.timeout_add(350, self._center_visible_graph)
                GLib.timeout_add(700, self._center_visible_graph)
            if edge_context is not None:
                self._last_edge_monitor = edge_context.monitor_index
            self._visible = True
            if self._webview and (self._webview.get_uri() or "").startswith("about:"):
                self._webview.load_uri(INDEX_URI)
            else:
                self._bridge.push_graph()

    def _move_to_monitor(self, edge_context: EdgeZoneContext) -> None:
        if not self._win:
            return
        try:
            self._win.unmaximize()
            self._win.move(edge_context.x, edge_context.y)
            self._win.resize(edge_context.width, edge_context.height)
            log.info(
                "show on monitor=%s geometry=%sx%s+%s+%s",
                edge_context.monitor_index,
                edge_context.width,
                edge_context.height,
                edge_context.x,
                edge_context.y,
            )
        except Exception:
            log.warning("failed to move wind_mgr to touched monitor", exc_info=True)

    def _ensure_maximized_visible_window(self, edge_context: EdgeZoneContext | None = None) -> bool:
        if self._win and self._visible:
            if edge_context is not None and not self._window_is_on_monitor(edge_context):
                log.info("window missed requested monitor; applying fallback move")
                self._move_to_monitor(edge_context)
            self._win.maximize()
            try:
                x, y = self._win.get_position()
                w, h = self._win.get_size()
                log.info("ensure maximized wind_mgr window=%sx%s+%s+%s", w, h, x, y)
            except Exception:
                log.debug("could not read wind_mgr window geometry", exc_info=True)
        return False

    def _window_is_on_monitor(self, edge_context: EdgeZoneContext) -> bool:
        if not self._win:
            return False
        try:
            x, y = self._win.get_position()
            w, h = self._win.get_size()
            cx = x + w // 2
            cy = y + h // 2
            return (
                edge_context.x <= cx < edge_context.x + edge_context.width
                and edge_context.y <= cy < edge_context.y + edge_context.height
            )
        except Exception:
            log.debug("could not verify wind_mgr monitor placement", exc_info=True)
            return False

    def _center_visible_graph(self) -> bool:
        if not self._visible or self._webview is None:
            return False
        if self._win:
            try:
                x, y = self._win.get_position()
                w, h = self._win.get_size()
                log.info("center graph request window=%sx%s+%s+%s", w, h, x, y)
            except Exception:
                log.debug("could not read wind_mgr window geometry before center", exc_info=True)
        js = (
            "try{"
            "if(window.windMgr) window.windMgr.centerRememberedView();"
            "}catch(e){ console.error(e.toString()); }"
        )
        self._webview.evaluate_javascript(js, -1, None, None, None, None, None)
        return False

    def hide(self) -> None:
        if self._win:
            self._win.hide()
            self._bridge.set_ui_visible(False)
            self._visible = False

    def _hide_after_activation(self) -> None:
        if self._edge_zones is not None:
            self._edge_zones.suppress(900)
        self.hide()

    def toggle(self, edge_context: EdgeZoneContext | None = None) -> None:
        if self._win and self._win.get_visible():
            self.hide()
        else:
            self.show(edge_context)

    def run(self, *, start_hidden: bool = False) -> None:
        if start_hidden:
            self._bridge.set_ui_visible(False)
            self._visible = False
            log.info("wind_mgr started hidden")
        else:
            self.show()
        Gtk.main()

    def quit(self) -> None:
        Gtk.main_quit()
