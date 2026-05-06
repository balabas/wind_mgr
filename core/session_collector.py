from __future__ import annotations
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.window_record import WindowRecord

log = logging.getLogger(__name__)

_providers = None


def _get_providers():
    global _providers
    if _providers is None:
        from providers.session import load_all
        _providers = load_all()
    return _providers


def collect_launch_info(record: "WindowRecord", launcher) -> dict:
    """Return {app_id, args, title, icon_url, wm_class, app_name} for relaunching this window."""
    app_id = launcher.find_app_for_window(
        record.wm_class, record.wm_class_group, record.app_name
    )
    title = (
        record.metadata.get("card_name")
        or record.metadata.get("tab_title")
        or record.title
        or record.app_name
    )
    icon_url = ""
    if app_id:
        app = launcher.get_app(app_id)
        if app:
            from core.app_launcher import _find_icon
            icon_path = _find_icon(app.get("icon", ""))
            icon_url = f"file://{icon_path}" if icon_path else ""

    args: list[str] = []
    for provider in _get_providers():
        try:
            if provider.matches(record):
                args = provider.collect_args(record)
                break
        except Exception:
            log.warning(
                "session provider %s failed for xid=%s",
                type(provider).__module__, record.xid, exc_info=True,
            )

    return {
        "app_id": app_id,
        "args": args,
        "title": title,
        "icon_url": icon_url,
        "wm_class": record.wm_class,
        "app_name": record.app_name,
    }
