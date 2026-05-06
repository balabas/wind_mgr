from __future__ import annotations
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING

from providers.session import SessionProvider
from providers.session._shared import find_vscode_project_path

if TYPE_CHECKING:
    from core.window_record import WindowRecord

log = logging.getLogger(__name__)

_VSCODE_CLASSES = {"code", "code-oss", "vscodium", "code - oss"}
_VSCODE_SUFFIX = re.compile(
    r"\s+[-–]\s+(?:Visual Studio Code|VSCodium|Code - OSS)$", re.IGNORECASE
)


class Provider(SessionProvider):
    def matches(self, record: "WindowRecord") -> bool:
        return record.wm_class.lower() in _VSCODE_CLASSES

    def collect_args(self, record: "WindowRecord") -> list[str]:
        # Window title is the authoritative source for the current project.
        # Plain "Visual Studio Code" (no prefix) means no project is open.
        project_name = _project_name_from_title(record.title or "")
        if not project_name:
            return []

        path = find_vscode_project_path(project_name)
        if path:
            return [path]

        # Cmdline is a valid fallback only when the title confirms the same project
        path = _path_from_cmdline(record.pid)
        if path and Path(path).name == project_name:
            return [path]

        return []


def _project_name_from_title(title: str) -> str:
    if not _VSCODE_SUFFIX.search(title):
        return ""
    core = _VSCODE_SUFFIX.sub("", title).strip()
    return core.rsplit(" - ", 1)[-1].strip()


def _path_from_cmdline(pid: int) -> str:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\x00")
        for arg in [a.decode(errors="replace") for a in raw[1:] if a]:
            if arg.startswith("-"):
                continue
            p = Path(arg).expanduser().resolve()
            if p.is_dir():
                return str(p)
    except Exception:
        log.debug("vscode cmdline lookup failed pid=%s", pid, exc_info=True)
    return ""
