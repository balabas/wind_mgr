from __future__ import annotations
import re
from typing import TYPE_CHECKING

from .base import Provider

if TYPE_CHECKING:
    from core.window_record import WindowRecord

_VSCODE_CLASSES = {"code", "code-oss", "vscodium", "code - oss"}
_VSCODE_SUFFIX = re.compile(
    r"\s+[-–]\s+(?:Visual Studio Code|VSCodium|Code - OSS)$", re.IGNORECASE
)
_VSCODE_PARTS = re.compile(r"^[●\s]*(.+?)\s+[-–]\s+(.+?)$")


class VSCodeProvider(Provider):
    priority = 20

    def matches(self, record: "WindowRecord") -> bool:
        return record.wm_class.lower() in _VSCODE_CLASSES

    def enrich(self, record: "WindowRecord") -> None:
        record.metadata["app_type"] = "vscode"
        title = _VSCODE_SUFFIX.sub("", record.title).strip()
        m = _VSCODE_PARTS.match(title)
        if m:
            file_name = m.group(1).strip().lstrip("●").strip()
            project_name = m.group(2).strip()
        else:
            file_name = ""
            project_name = title.strip().lstrip("●").strip()
        record.metadata["file_name"] = file_name
        record.metadata["project_name"] = project_name
        record.metadata["group_key"] = project_name
