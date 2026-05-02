#!/usr/bin/env python3
"""Test 06: screenshot capture loop — simulates what _refresh_active_thumb_tick
was doing every 1s even when the window is hidden. Suspects PNG encode load."""
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkX11", "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Wnck", "3.0")
from gi.repository import Gtk, GLib, Gdk, GdkX11, GdkPixbuf, Wnck
import threading, time, os
from pathlib import Path

OUT = Path("/tmp/cpu_test_thumb.png")
THUMB_W, THUMB_H = 720, 450
capture_count = 0

def get_active_xid():
    screen = Wnck.Screen.get_default()
    screen.force_update()
    w = screen.get_active_window()
    return w.get_xid() if w else None

def capture_once():
    global capture_count
    xid = get_active_xid()
    if not xid:
        return

    pixbuf_holder = [None]
    done = threading.Event()

    def _on_main():
        try:
            display = GdkX11.X11Display.get_default()
            Gdk.error_trap_push()
            win = GdkX11.X11Window.foreign_new_for_display(display, xid)
            if win:
                w, h = win.get_width(), win.get_height()
                if w > 0 and h > 0:
                    pixbuf_holder[0] = Gdk.pixbuf_get_from_window(win, 0, 0, w, h)
            Gdk.flush()
            Gdk.error_trap_pop()
        except Exception as e:
            print(f"grab error: {e}", flush=True)
        finally:
            done.set()

    GLib.idle_add(_on_main)
    done.wait(timeout=3.0)

    pb = pixbuf_holder[0]
    if pb:
        # This is the CPU-heavy part — scale + PNG save
        t0 = time.monotonic()
        scale = min(THUMB_W / pb.get_width(), THUMB_H / pb.get_height())
        sw = max(1, round(pb.get_width() * scale))
        sh = max(1, round(pb.get_height() * scale))
        scaled = pb.scale_simple(sw, sh, GdkPixbuf.InterpType.BILINEAR)
        scaled.savev(str(OUT), "png", [], [])
        ms = (time.monotonic() - t0) * 1000
        capture_count += 1
        print(f"  capture #{capture_count}: scale+save took {ms:.0f}ms  size={sw}x{sh}", flush=True)

def tick():
    threading.Thread(target=capture_once, name="screenshot", daemon=True).start()
    return True  # repeat every 1s

def report():
    try:
        import psutil
        p = psutil.Process(os.getpid())
        cpu = p.cpu_percent(interval=None)
        mem = p.memory_info().rss // (1024*1024)
        print(f"[screenshot-loop] cpu={cpu:.1f}%  mem={mem}MB  captures={capture_count}", flush=True)
    except Exception as e:
        print(f"error: {e}", flush=True)
    return True

GLib.timeout_add(1000, tick)
GLib.timeout_add(2000, report)
GLib.timeout_add_seconds(30, Gtk.main_quit)
print("Screenshot loop started (1/s). Running for 30s.", flush=True)
Gtk.main()
