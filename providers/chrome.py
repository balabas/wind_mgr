from __future__ import annotations
import re
from typing import TYPE_CHECKING

from .base import Provider
from .browser_history import match_history_visit

if TYPE_CHECKING:
    from core.window_record import WindowRecord

_CHROME_CLASSES = {"google-chrome", "chromium", "chromium-browser", "brave-browser",
                   "google-chrome-stable", "chrome"}
_CHROME_SUFFIX = re.compile(
    r"\s+[-–]\s+(?:Google Chrome|Chromium|Brave|Brave Browser)$", re.IGNORECASE
)
_TITLE_PARTS = re.compile(r"^(.+?)\s+[-–]\s+(.+?)$")


class ChromeProvider(Provider):
    priority = 10

    def matches(self, record: "WindowRecord") -> bool:
        return record.wm_class.lower() in _CHROME_CLASSES

    def enrich(self, record: "WindowRecord") -> None:
        record.metadata["app_type"] = "chrome"
        title = _CHROME_SUFFIX.sub("", record.title).strip()
        m = _TITLE_PARTS.match(title)
        if m:
            tab_title = m.group(1).strip()
            domain = m.group(2).strip()
        else:
            tab_title = title
            domain = title
        record.metadata["tab_title"] = tab_title
        record.metadata["domain"] = domain
        record.metadata["group_key"] = domain

        match = match_history_visit(
            browser=_browser_type(record.wm_class),
            title=tab_title or record.title,
            domain=_history_domain_hint(domain),
        )
        if match is not None:
            record.metadata["history_title"] = match.title
            record.metadata["history_url"] = match.url
            record.metadata["history_domain"] = match.domain
            record.metadata["history_profile"] = match.profile
            record.metadata["history_visit_count"] = match.visit_count
            record.metadata["history_typed_count"] = match.typed_count
            record.metadata["history_last_visit_time"] = (
                match.last_visit_time.isoformat() if match.last_visit_time else ""
            )


def _browser_type(wm_class: str) -> str:
    cls = (wm_class or "").lower()
    if cls in {"chromium", "chromium-browser"}:
        return "chromium"
    if cls in {"brave-browser"}:
        return "brave"
    return "chrome"


def _history_domain_hint(value: str) -> str:
    value = (value or "").strip().lower()
    if not value:
        return ""
    if " " in value:
        return ""
    if "." not in value:
        return ""
    return value
