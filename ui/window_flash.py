from __future__ import annotations

import logging
import math

import cairo
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk

log = logging.getLogger(__name__)


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
        self._timer_id = GLib.timeout_add(45, self._tick)

    def _rgba_visual(self):
        screen = Gdk.Screen.get_default()
        return screen.get_rgba_visual() if screen is not None else None

    def _make_input_transparent(self, _widget) -> None:
        gdk_window = self._win.get_window()
        if gdk_window is None:
            return
        try:
            gdk_window.input_shape_combine_region(cairo.Region(), 0, 0)
        except Exception:
            log.debug("could not make window flash overlay input-transparent", exc_info=True)

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
