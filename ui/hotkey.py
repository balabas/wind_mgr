from __future__ import annotations
import logging
from typing import Callable

log = logging.getLogger(__name__)

_HOTKEY = "<Super>w"


def bind_hotkey(callback: Callable) -> bool:
    """Bind Super+W global hotkey. Returns True if successful."""
    try:
        import gi
        gi.require_version("Keybinder", "3.0")
        from gi.repository import Keybinder
        Keybinder.init()
        ok = Keybinder.bind(_HOTKEY, lambda key: callback())
        if ok:
            log.info("Hotkey bound: %s via Keybinder", _HOTKEY)
            return True
        log.warning("Keybinder.bind failed for %s", _HOTKEY)
    except Exception:
        log.debug("Keybinder3 not available, trying python-xlib", exc_info=True)

    try:
        from Xlib import X, XK, display as xdisplay
        from Xlib.ext import record
        import threading

        dpy = xdisplay.Display()
        root = dpy.screen().root

        mod_map = {
            "Super": dpy.keysym_to_keycode(XK.XK_Super_L),
        }
        w_code = dpy.keysym_to_keycode(XK.XK_w)

        root.grab_key(w_code, X.Mod4Mask, True, X.GrabModeAsync, X.GrabModeAsync)
        dpy.flush()

        def _listen():
            while True:
                event = dpy.next_event()
                if event.type == X.KeyPress:
                    callback()

        t = threading.Thread(target=_listen, daemon=True)
        t.start()
        log.info("Hotkey bound: Super+W via python-xlib")
        return True
    except Exception:
        log.warning("Could not bind global hotkey (install gir1.2-keybinder-3.0): %s", _HOTKEY)
        return False
