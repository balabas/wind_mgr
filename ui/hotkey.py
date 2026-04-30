from __future__ import annotations

import logging
from typing import Callable

import configparser
from pathlib import Path
_CONFIG_PATH = Path(__file__).parent.parent / "config.ini"
cfg = configparser.ConfigParser()
cfg.read(_CONFIG_PATH)
_HOTKEY = cfg.get("capture", "hotkey", fallback="<Super>e")

log = logging.getLogger(__name__)



_keybinder_callback: Callable | None = None
_xlib_thread_started = False


def bind_hotkey(callback: Callable, accelerator: str | None = None) -> bool:
    """Bind Super+W global hotkey. Returns True if successful."""
    global _HOTKEY, _keybinder_callback, _xlib_thread_started
    if accelerator:
        _HOTKEY = accelerator
    try:
        import gi
        gi.require_version("Keybinder", "3.0")
        from gi.repository import Keybinder
        Keybinder.init()

        def _on_keybinder(*_args) -> None:
            log.info("Hotkey fired: %s via Keybinder", _HOTKEY)
            callback()

        _keybinder_callback = _on_keybinder
        try:
            Keybinder.unbind(_HOTKEY)
        except Exception:
            pass
        ok = Keybinder.bind(_HOTKEY, _keybinder_callback)
        if ok:
            log.info("Hotkey bound: %s via Keybinder", _HOTKEY)
            return True
        log.warning("Keybinder.bind failed for %s", _HOTKEY)
    except Exception:
        log.debug("Keybinder3 not available, trying python-xlib", exc_info=True)

    if _HOTKEY != "<Super>e":
        log.warning("python-xlib fallback only supports <Super>e, not %s", _HOTKEY)
        return False

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
                    log.info("Hotkey fired: Super+W via python-xlib")
                    callback()

        if not _xlib_thread_started:
            t = threading.Thread(target=_listen, daemon=True)
            t.start()
            _xlib_thread_started = True
        log.info("Hotkey bound: Super+W via python-xlib")
        return True
    except Exception:
        log.warning("Could not bind global hotkey (install gir1.2-keybinder-3.0): %s", _HOTKEY)
        return False
