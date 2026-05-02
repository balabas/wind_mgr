#!/usr/bin/env python3
"""Test 04: GTK + EdgeZoneWatcher polling at 80ms. Suspects GDK pointer queries."""
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, GLib, Gdk
import os

def poll_pointer():
    display = Gdk.Display.get_default()
    if display:
        seat = display.get_default_seat()
        if seat:
            pointer = seat.get_pointer()
            if pointer:
                _screen, x, y = pointer.get_position()
    return True  # 80ms

def report():
    try:
        import psutil, threading
        p = psutil.Process(os.getpid())
        cpu = p.cpu_percent(interval=None)
        mem = p.memory_info().rss // (1024*1024)
        threads_cpu = []
        for t in p.threads():
            th_name = next((th.name for th in threading.enumerate() if th.ident == t.id), f"tid={t.id}")
            threads_cpu.append(f"{th_name}={t.user_time+t.system_time:.2f}s")
        print(f"[edge-zones] cpu={cpu:.1f}%  mem={mem}MB  threads: {', '.join(threads_cpu)}", flush=True)
    except Exception as e:
        print(f"[edge-zones] error: {e}", flush=True)
    return True

GLib.timeout_add(80, poll_pointer)
GLib.timeout_add(2000, report)
GLib.timeout_add_seconds(30, Gtk.main_quit)
print("Edge zone 80ms pointer polling started. Running for 30s.", flush=True)
Gtk.main()
