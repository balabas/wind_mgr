#!/usr/bin/env python3
"""Launcher that strips snap-injected env vars before starting wind_mgr."""
import os
import subprocess
import sys

_SNAP_KEYS = {
    "GDK_PIXBUF_MODULE_FILE", "GTK_EXE_PREFIX", "GSETTINGS_SCHEMA_DIR",
    "XDG_DATA_HOME", "SNAP", "SNAP_ARCH", "SNAP_CONTEXT", "SNAP_EUID",
    "SNAP_INSTANCE_NAME", "SNAP_LAUNCHER_ARCH_TRIPLET", "SNAP_REAL_HOME",
    "SNAP_REVISION", "SNAP_UID", "SNAP_USER_COMMON", "SNAP_USER_DATA",
}

clean = {k: v for k, v in os.environ.items() if k not in _SNAP_KEYS}
clean.setdefault("DISPLAY", ":0")

script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py")
os.execve(sys.executable, [sys.executable, script] + sys.argv[1:], clean)
