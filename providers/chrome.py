from __future__ import annotations
import re
from typing import TYPE_CHECKING

from .base import Provider

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
