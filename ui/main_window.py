from __future__ import annotations
import configparser
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

from .hotkey import bind_hotkey

if TYPE_CHECKING:
    from bridge.js_bridge import JSBridge

log = logging.getLogger(__name__)

WEB_DIR = Path(__file__).parent.parent / "web"
CONFIG_PATH = Path(__file__).parent.parent / "config.ini"
INDEX_URI = (WEB_DIR / "index.html").as_uri()


def _read_layout_config() -> dict:
    cfg = configparser.RawConfigParser()
    cfg.optionxform = str  # preserve camelCase keys
    cfg.read(CONFIG_PATH)
    layout: dict = {}
    if not cfg.has_section("layout"):
        return layout
    for key, val in cfg.items("layout"):
        val = val.strip()
        try:
            f = float(val)
            layout[key] = int(f) if f == int(f) else f
        except ValueError:
            layout[key] = val
    return layout


class MainWindow:
    def __init__(self, bridge: "JSBridge") -> None:
        self._bridge = bridge
        self._visible = False
        self._win: Gtk.Window | None = None
        self._webview: WebKit2.WebView | None = None
        self._indicator: AppIndicator3.Indicator | None = None

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
        settings = WebKit2.Settings()
        settings.set_enable_javascript(True)
        settings.set_allow_file_access_from_file_urls(True)
        settings.set_allow_universal_access_from_file_urls(True)
        settings.set_enable_developer_extras(True)

        self._webview = WebKit2.WebView()
        self._webview.set_settings(settings)

        layout = _read_layout_config()
        config_js = (
            "window.windMgrConfig=" + json.dumps({"layout": layout}) + ";"
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

        # Wire bridge to webview
        self._bridge.attach(self._webview)
        self._bridge.set_before_activate_callback(self.hide)

        # Tray
        self._build_tray()

        # Global hotkey
        self._bind_hotkey()

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
            # Small delay to let D3 initialise before first push
            GLib.timeout_add(300, self._initial_push)

    def _bind_hotkey(self) -> None:
        bind_hotkey(lambda: GLib.idle_add(self.toggle))

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
        GLib.idle_add(self._maximize_visible_window)
        GLib.timeout_add(100, self._maximize_visible_window)
        return False

    def show(self) -> None:
        if self._win:
            self._win.show_all()
            self._win.maximize()
            self._win.present()
            GLib.idle_add(self._maximize_visible_window)
            GLib.timeout_add(100, self._maximize_visible_window)
            self._visible = True
            self._bridge.push_graph()

    def _maximize_visible_window(self) -> bool:
        if self._win and self._visible:
            self._win.maximize()
            self._win.present()
        return False

    def hide(self) -> None:
        if self._win:
            self._win.hide()
            self._visible = False

    def toggle(self) -> None:
        if self._win and self._win.get_visible():
            self.hide()
        else:
            self.show()

    def run(self) -> None:
        self.show()
        Gtk.main()

    def quit(self) -> None:
        Gtk.main_quit()
