#!/usr/bin/env python3
"""Test 03: GTK + Wnck screen watching. Suspects force_update polling."""
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Wnck", "3.0")
from gi.repository import Gtk, GLib, Wnck
import os

screen = None

def poll_wnck():
    if screen:
        screen.force_update()
    return True  # 1s

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
        print(f"[wnck] cpu={cpu:.1f}%  mem={mem}MB  threads: {', '.join(threads_cpu)}", flush=True)
    except Exception as e:
        print(f"[wnck] error: {e}", flush=True)
    return True

screen = Wnck.Screen.get_default()
screen.force_update()

def on_geometry_changed(window):
    pass  # just connect — like ActivityWatcher does

for w in screen.get_windows():
    w.connect("geometry-changed", on_geometry_changed)

GLib.timeout_add(1000, poll_wnck)
GLib.timeout_add(2000, report)
GLib.timeout_add_seconds(30, Gtk.main_quit)
print("Wnck polling started. Running for 30s.", flush=True)
Gtk.main()
