from __future__ import annotations
import logging
from typing import TYPE_CHECKING

from providers.session import SessionProvider

if TYPE_CHECKING:
    from core.window_record import WindowRecord

log = logging.getLogger(__name__)

_BROWSER_CLASSES = {
    "google-chrome", "google-chrome-stable", "chrome",
    "chromium", "chromium-browser",
    "brave-browser",
}
_BROWSER_NAME_MAP = {
    "google-chrome": "chrome",
    "google-chrome-stable": "chrome",
    "chrome": "chrome",
    "chromium": "chromium",
    "chromium-browser": "chromium",
    "brave-browser": "brave",
}


class Provider(SessionProvider):
    def matches(self, record: "WindowRecord") -> bool:
        return record.wm_class.lower() in _BROWSER_CLASSES

    def collect_args(self, record: "WindowRecord") -> list[str]:
        wm = record.wm_class.lower()
        browser = _BROWSER_NAME_MAP.get(wm, "chrome")

        # Prefer URL already resolved by ChromeProvider at window-open time
        url = record.metadata.get("history_url", "")
        if url:
            return [url]

        tab_title = record.metadata.get("tab_title") or record.title or ""
        domain = record.metadata.get("domain") or ""
        try:
            from providers.browser_history import match_history_visit
            visit = match_history_visit(browser=browser, title=tab_title, domain=domain)
            if visit and visit.url:
                return [visit.url]
        except Exception:
            log.debug("browser history lookup failed", exc_info=True)

        if domain:
            return [f"https://{domain}"]
        return []
