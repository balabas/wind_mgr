from __future__ import annotations

import logging
import math
import ctypes

import cairo
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkX11", "3.0")
from gi.repository import Gdk, GdkX11, GLib, Gtk

log = logging.getLogger(__name__)

SHAPE_INPUT = 2

try:
    _libx11 = ctypes.CDLL("libX11.so.6")
    _libxfixes = ctypes.CDLL("libXfixes.so.3")
except OSError:
    _libx11 = None
    _libxfixes = None

_XFIXES_READY = False


def _setup_xfixes() -> bool:
    global _XFIXES_READY
    if _XFIXES_READY:
        return True
    if _libx11 is None or _libxfixes is None:
        return False
    display_p = ctypes.c_void_p
    window = ctypes.c_ulong
    region = ctypes.c_ulong
    _libx11.XOpenDisplay.argtypes = [ctypes.c_char_p]
    _libx11.XOpenDisplay.restype = display_p
    _libx11.XCloseDisplay.argtypes = [display_p]
    _libx11.XFlush.argtypes = [display_p]
    _libxfixes.XFixesCreateRegion.argtypes = [display_p, ctypes.c_void_p, ctypes.c_int]
    _libxfixes.XFixesCreateRegion.restype = region
    _libxfixes.XFixesSetWindowShapeRegion.argtypes = [
        display_p, window, ctypes.c_int, ctypes.c_int, ctypes.c_int, region
    ]
    _libxfixes.XFixesDestroyRegion.argtypes = [display_p, region]
    _XFIXES_READY = True
    return True


class WindowFlashOverlay:
    """Temporary border overlay used to identify a real activated window."""

    def __init__(self, x: int, y: int, width: int, height: int) -> None:
        self._x = x
        self._y = y
        self._width = max(1, width)
        self._height = max(1, height)
        self._started_ms = GLib.get_monotonic_time() // 1000
        self._duration_ms = 1300
        self._timer_id: int | None = None

        self._win = Gtk.Window(type=Gtk.WindowType.POPUP)
        self._win.set_app_paintable(True)
        self._win.set_decorated(False)
        self._win.set_keep_above(True)
        self._win.set_skip_taskbar_hint(True)
        self._win.set_skip_pager_hint(True)
        self._win.set_accept_focus(False)
        self._win.set_focus_on_map(False)
        self._win.set_visual(self._rgba_visual())
        self._win.connect("draw", self._draw)
        self._win.connect("realize", self._make_input_transparent)

    def show(self) -> None:
        self._win.move(self._x, self._y)
        self._win.resize(self._width, self._height)
        self._win.show_all()
        self._make_input_transparent()
        GLib.idle_add(self._make_input_transparent)
        GLib.timeout_add(80, self._make_input_transparent)
        self._timer_id = GLib.timeout_add(45, self._tick)

    def _rgba_visual(self):
        screen = Gdk.Screen.get_default()
        return screen.get_rgba_visual() if screen is not None else None

    def _make_input_transparent(self, *_args) -> bool:
        gdk_window = self._win.get_window()
        if gdk_window is None:
            return False
        try:
            gdk_window.input_shape_combine_region(cairo.Region(), 0, 0)
            self._set_xfixes_empty_input_region(gdk_window)
        except Exception:
            log.debug("could not make window flash overlay input-transparent", exc_info=True)
        return False

    def _set_xfixes_empty_input_region(self, gdk_window: GdkX11.X11Window) -> None:
        if not _setup_xfixes() or not hasattr(gdk_window, "get_xid"):
            return
        display = _libx11.XOpenDisplay(None)
        if not display:
            return
        region = 0
        try:
            region = _libxfixes.XFixesCreateRegion(display, None, 0)
            _libxfixes.XFixesSetWindowShapeRegion(
                display,
                int(gdk_window.get_xid()),
                SHAPE_INPUT,
                0,
                0,
                region,
            )
            _libx11.XFlush(display)
        finally:
            if region:
                _libxfixes.XFixesDestroyRegion(display, region)
            _libx11.XCloseDisplay(display)

    def _tick(self) -> bool:
        elapsed = (GLib.get_monotonic_time() // 1000) - self._started_ms
        if elapsed >= self._duration_ms:
            self._win.destroy()
            self._timer_id = None
            return False
        self._win.queue_draw()
        return True

    def _draw(self, _widget, cr) -> bool:
        elapsed = (GLib.get_monotonic_time() // 1000) - self._started_ms
        t = max(0.0, min(1.0, elapsed / self._duration_ms))
        pulse = 0.5 + 0.5 * math.sin(t * math.pi * 6)
        alpha = (1.0 - t) * (0.35 + 0.55 * pulse)

        cr.set_operator(cairo.OPERATOR_CLEAR)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)

        inset = 8
        line_width = 8
        cr.set_line_width(line_width)
        cr.set_source_rgba(0.0, 0.85, 1.0, alpha)
        cr.rectangle(
            inset,
            inset,
            max(1, self._width - inset * 2),
            max(1, self._height - inset * 2),
        )
        cr.stroke()
        return False


def flash_window_rect(x: int, y: int, width: int, height: int) -> bool:
    overlay = WindowFlashOverlay(x, y, width, height)
    overlay.show()
    # The GLib timeout callback owned by the overlay keeps it alive long enough.
    return False
