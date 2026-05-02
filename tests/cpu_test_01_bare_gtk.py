#!/usr/bin/env python3
"""Test 01: bare GTK main loop — should be ~0% CPU. Baseline."""
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib
import time, os, sys

def report():
    try:
        import psutil
        p = psutil.Process(os.getpid())
        cpu = p.cpu_percent(interval=None)
        mem = p.memory_info().rss // (1024*1024)
        print(f"[bare-gtk] cpu={cpu:.1f}%  mem={mem}MB", flush=True)
    except Exception as e:
        print(f"[bare-gtk] psutil error: {e}", flush=True)
    return True

GLib.timeout_add(2000, report)
GLib.timeout_add_seconds(30, Gtk.main_quit)
print("Running bare GTK loop for 30s. Watch CPU in htop.", flush=True)
Gtk.main()
