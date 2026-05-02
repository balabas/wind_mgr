#!/usr/bin/env python3
"""Test 05: GTK + WebKit2 hidden window. Suspects WebKit rendering even when hidden."""
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("WebKit2", "4.1")
from gi.repository import Gtk, GLib, WebKit2
import os
from pathlib import Path

WEB_DIR = Path(__file__).parent.parent / "web"
INDEX_URI = (WEB_DIR / "index.html").as_uri()

win = Gtk.Window()
win.set_default_size(1400, 900)
win.connect("delete-event", lambda *_: Gtk.main_quit())

ctx = WebKit2.WebContext.get_default()
ctx.set_cache_model(WebKit2.CacheModel.DOCUMENT_VIEWER)

settings = WebKit2.Settings()
settings.set_enable_javascript(True)
settings.set_allow_file_access_from_file_urls(True)
settings.set_allow_universal_access_from_file_urls(True)

webview = WebKit2.WebView()
webview.set_settings(settings)
webview.load_uri(INDEX_URI)
win.add(webview)

phase = {"shown": False, "hidden": False}

def on_load(wv, event):
    if event == WebKit2.LoadEvent.FINISHED and not phase["shown"]:
        phase["shown"] = True
        print("Page loaded. Showing window for 3s then hiding.", flush=True)
        win.show_all()
        GLib.timeout_add(3000, do_hide)

def do_hide():
    print("Hiding window now — watch CPU.", flush=True)
    # Suspend animations via JS
    webview.evaluate_javascript(
        "document.getElementById('graph') && document.getElementById('graph').classList.add('suspended');",
        -1, None, None, None, None, None
    )
    win.hide()
    phase["hidden"] = True
    return False

webview.connect("load-changed", on_load)

def report():
    try:
        import psutil, threading
        p = psutil.Process(os.getpid())
        cpu = p.cpu_percent(interval=None)
        mem = p.memory_info().rss // (1024*1024)
        children = p.children(recursive=True)
        child_str = "  ".join(
            f"{c.name()[:15]}(pid={c.pid}) cpu={c.cpu_percent(interval=None):.1f}%"
            for c in children
        )
        print(
            f"[webkit] hidden={phase['hidden']}  self cpu={cpu:.1f}%  mem={mem}MB  "
            f"children: {child_str or 'none'}",
            flush=True
        )
    except Exception as e:
        print(f"[webkit] error: {e}", flush=True)
    return True

GLib.timeout_add(2000, report)
GLib.timeout_add_seconds(40, Gtk.main_quit)
print("Starting WebKit test. Window will show then hide after 3s.", flush=True)
Gtk.main()
