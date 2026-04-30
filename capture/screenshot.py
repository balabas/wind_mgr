from __future__ import annotations
import logging
import os
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

import gi
gi.require_version("Gdk", "3.0")
gi.require_version("GdkX11", "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Wnck", "3.0")
from gi.repository import Gdk, GdkX11, GdkPixbuf, GLib, Wnck

log = logging.getLogger(__name__)

THUMB_W = 240
THUMB_H = 150
THUMBS_DIR = Path.home() / ".local" / "share" / "wind_mgr" / "thumbs"
ICONS_DIR  = Path.home() / ".local" / "share" / "wind_mgr" / "icons"


class ScreenshotCapture:
    def __init__(self) -> None:
        THUMBS_DIR.mkdir(parents=True, exist_ok=True)
        ICONS_DIR.mkdir(parents=True, exist_ok=True)
        self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="screenshot")
        self._display: GdkX11.X11Display | None = None

    def _get_display(self) -> GdkX11.X11Display:
        if self._display is None:
            self._display = GdkX11.X11Display.get_default()
        return self._display

    # ── Public API ─────────────────────────────────────────────────────────

    def thumb_path(self, xid: int) -> Path:
        return THUMBS_DIR / f"{xid}.png"

    def icon_path(self, xid: int) -> Path:
        return ICONS_DIR / f"{xid}.png"

    def thumb_url(self, xid: int) -> str:
        p = self.thumb_path(xid)
        mtime = int(p.stat().st_mtime) if p.exists() else 0
        return f"file://{p}?t={mtime}"

    def icon_url(self, xid: int) -> str:
        p = self.icon_path(xid)
        return f"file://{p}" if p.exists() else ""

    def capture_async(self, xid: int,
                      callback: Callable[[bool], None] | None = None) -> None:
        """Schedule screenshot capture. callback(success) called on GTK main thread."""
        self._executor.submit(self._capture_worker, xid, callback)

    def capture_icon(self, xid: int) -> bool:
        """Save the Wnck app icon. Must run on GTK main thread."""
        try:
            screen = Wnck.Screen.get_default()
            for w in screen.get_windows():
                if w.get_xid() == xid:
                    pixbuf = w.get_icon()
                    if pixbuf:
                        scaled = pixbuf.scale_simple(32, 32, GdkPixbuf.InterpType.BILINEAR)
                        scaled.savev(str(self.icon_path(xid)), "png", [], [])
                        return True
        except Exception:
            log.debug("Failed to capture icon for xid=%d", xid, exc_info=True)
        return False

    def capture_all_async(self) -> None:
        """Request fresh screenshots for all known thumb files."""
        for p in THUMBS_DIR.glob("*.png"):
            try:
                xid = int(p.stem)
                self.capture_async(xid)
            except ValueError:
                pass

    # ── Workers ────────────────────────────────────────────────────────────

    def _capture_worker(self, xid: int,
                        callback: Callable[[bool], None] | None) -> None:
        success = self._try_gdk(xid) or self._try_ffmpeg(xid)
        if callback is not None:
            GLib.idle_add(callback, success)

    def _try_gdk(self, xid: int) -> bool:
        """Capture via GdkX11 composite. Must coordinate with GTK thread."""
        result: list[bool] = []
        done = threading.Event()

        def _on_main():
            try:
                display = self._get_display()
                win = GdkX11.X11Window.foreign_new_for_display(display, xid)
                if win is None:
                    result.append(False)
                    return
                w = win.get_width()
                h = win.get_height()
                if w <= 0 or h <= 0:
                    result.append(False)
                    return
                pb = Gdk.pixbuf_get_from_window(win, 0, 0, w, h)
                if pb is None:
                    result.append(False)
                    return
                scaled = pb.scale_simple(THUMB_W, THUMB_H, GdkPixbuf.InterpType.BILINEAR)
                scaled.savev(str(self.thumb_path(xid)), "png", [], [])
                result.append(True)
            except Exception:
                log.debug("GDK capture failed xid=%d", xid, exc_info=True)
                result.append(False)
            finally:
                done.set()

        GLib.idle_add(_on_main)
        done.wait(timeout=3.0)
        return bool(result and result[0])

    def _try_ffmpeg(self, xid: int) -> bool:
        """Capture via ffmpeg x11grab selecting the window by geometry."""
        try:
            # Get window geometry via xwininfo
            info = subprocess.run(
                ["xwininfo", "-id", hex(xid)],
                capture_output=True, text=True, timeout=3
            )
            x, y, w, h = _parse_xwininfo(info.stdout)
            if w <= 0 or h <= 0:
                return False

            display = os.environ.get("DISPLAY", ":0")
            out = str(self.thumb_path(xid))
            proc = subprocess.run([
                "ffmpeg", "-y",
                "-f", "x11grab",
                "-video_size", f"{w}x{h}",
                "-i", f"{display}+{x},{y}",
                "-vframes", "1",
                "-vf", f"scale={THUMB_W}:{THUMB_H}:force_original_aspect_ratio=increase,"
                       f"crop={THUMB_W}:{THUMB_H}",
                out,
            ], capture_output=True, timeout=5)
            return proc.returncode == 0
        except Exception:
            log.debug("ffmpeg capture failed xid=%d", xid, exc_info=True)
            return False


def _parse_xwininfo(text: str) -> tuple[int, int, int, int]:
    """Return (x, y, width, height) from xwininfo output."""
    x = y = w = h = 0
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("Absolute upper-left X:"):
            x = int(line.split(":")[-1].strip())
        elif line.startswith("Absolute upper-left Y:"):
            y = int(line.split(":")[-1].strip())
        elif line.startswith("Width:"):
            w = int(line.split(":")[-1].strip())
        elif line.startswith("Height:"):
            h = int(line.split(":")[-1].strip())
    return x, y, w, h
