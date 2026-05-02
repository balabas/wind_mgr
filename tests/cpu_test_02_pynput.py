#!/usr/bin/env python3
"""Test 02: GTK + pynput GlobalHotKeys. Suspects pynput listener thread."""
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib
from pynput import keyboard
import os, sys

def report():
    try:
        import psutil, threading
        p = psutil.Process(os.getpid())
        cpu = p.cpu_percent(interval=None)
        mem = p.memory_info().rss // (1024*1024)
        th_names = [t.name for t in threading.enumerate()]
        threads_cpu = []
        for t in p.threads():
            th_name = next((th.name for th in threading.enumerate() if th.ident == t.id), f"tid={t.id}")
            threads_cpu.append(f"{th_name}={t.user_time+t.system_time:.2f}s")
        print(f"[pynput] cpu={cpu:.1f}%  mem={mem}MB  threads: {', '.join(threads_cpu)}", flush=True)
    except Exception as e:
        print(f"[pynput] error: {e}", flush=True)
    return True

hotkey_listener = keyboard.GlobalHotKeys({"<ctrl>+<cmd>+a": lambda: print("hotkey fired", flush=True)})
hotkey_listener.start()
print("pynput GlobalHotKeys started. Running for 30s.", flush=True)

GLib.timeout_add(2000, report)
GLib.timeout_add_seconds(30, Gtk.main_quit)
Gtk.main()
hotkey_listener.stop()
