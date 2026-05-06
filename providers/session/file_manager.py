from __future__ import annotations
from typing import TYPE_CHECKING

from providers.session import SessionProvider
from providers.session._shared import proc_cwd

if TYPE_CHECKING:
    from core.window_record import WindowRecord

_FILE_MANAGER_CLASSES = {"nautilus", "dolphin", "thunar", "nemo", "pcmanfm"}


class Provider(SessionProvider):
    def matches(self, record: "WindowRecord") -> bool:
        return record.wm_class.lower() in _FILE_MANAGER_CLASSES

    def collect_args(self, record: "WindowRecord") -> list[str]:
        cwd = proc_cwd(record.pid)
        return [cwd] if cwd else []
