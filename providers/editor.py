from __future__ import annotations
import re
from typing import TYPE_CHECKING

from .base import Provider

if TYPE_CHECKING:
    from core.window_record import WindowRecord

_EDITOR_CLASSES = {
    "gedit", "kate", "mousepad", "xed", "pluma",
    "dbeaver", "dbeaverlauncher",
    "libreoffice", "soffice",
    "sublime_text", "atom",
    "jetbrains-pycharm", "pycharm", "pycharm-professional", "pycharm-community",
}
_JETBRAINS_CLASSES = {
    "jetbrains-pycharm", "pycharm", "pycharm-professional", "pycharm-community",
}
_JETBRAINS_TITLE_PARTS = re.compile(r"^(.+?)\s+[-–]\s+(.+?)$")
_TERMINAL_EDITOR_RE = re.compile(
    r"^(?:n?vim?|emacs|nano|micro|helix|hx)\b", re.IGNORECASE
)
_TERMINAL_CLASSES = {"gnome-terminal", "konsole", "xterm", "alacritty",
                     "tilix", "kitty", "terminator", "xfce4-terminal"}


class EditorProvider(Provider):
    priority = 30

    def matches(self, record: "WindowRecord") -> bool:
        cls = record.wm_class.lower()
        if cls in _EDITOR_CLASSES:
            return True
        if cls in _TERMINAL_CLASSES and _TERMINAL_EDITOR_RE.match(record.title):
            return True
        return False

    def enrich(self, record: "WindowRecord") -> None:
        cls = record.wm_class.lower()
        if cls in _TERMINAL_CLASSES:
            record.metadata["app_type"] = "editor"
            m = _TERMINAL_EDITOR_RE.match(record.title)
            record.metadata["editor_name"] = m.group(0) if m else cls
            record.metadata["group_key"] = record.metadata["editor_name"]
        elif cls in _JETBRAINS_CLASSES:
            record.metadata["app_type"] = "editor"
            record.metadata["editor_name"] = "PyCharm"
            project_name, active_file = _jetbrains_title_parts(record.title)
            record.metadata["project_name"] = f"PyCharm: {project_name}" if project_name else "PyCharm"
            record.metadata["active_file"] = active_file
            record.metadata["active_directory"] = project_name
            record.metadata["group_key"] = record.metadata["project_name"]
        elif "dbeaver" in cls:
            record.metadata["app_type"] = "editor"
            record.metadata["editor_name"] = "DBeaver"
            record.metadata["group_key"] = "DBeaver"
        elif "libreoffice" in cls or "soffice" in cls:
            record.metadata["app_type"] = "editor"
            record.metadata["editor_name"] = "LibreOffice"
            record.metadata["group_key"] = "LibreOffice"
        else:
            record.metadata["app_type"] = "editor"
            record.metadata["editor_name"] = record.app_name or record.wm_class
            record.metadata["group_key"] = record.metadata["editor_name"]


def _jetbrains_title_parts(title: str) -> tuple[str, str]:
    title = title.strip()
    m = _JETBRAINS_TITLE_PARTS.match(title)
    if not m:
        return title, ""
    return m.group(1).strip(), m.group(2).strip()


class TerminalProvider(Provider):
    priority = 35

    def matches(self, record: "WindowRecord") -> bool:
        return record.wm_class.lower() in _TERMINAL_CLASSES

    def enrich(self, record: "WindowRecord") -> None:
        if record.metadata.get("app_type") not in ("editor",):
            record.metadata["app_type"] = "terminal"
            record.metadata["group_key"] = record.app_name or "Terminal"
