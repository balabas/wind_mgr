from __future__ import annotations
from typing import TYPE_CHECKING

from providers.session import SessionProvider
from providers.session._shared import find_jetbrains_project_path

if TYPE_CHECKING:
    from core.window_record import WindowRecord

_JETBRAINS_CLASSES = {
    "jetbrains-pycharm", "pycharm", "pycharm-professional", "pycharm-community",
    "jetbrains-idea", "idea", "intellij-idea",
    "jetbrains-webstorm", "webstorm",
    "jetbrains-goland", "goland",
    "jetbrains-clion", "clion",
    "jetbrains-rider", "rider",
    "jetbrains-phpstorm", "phpstorm",
    "jetbrains-rubymine", "rubymine",
    "jetbrains-datagrip", "datagrip",
}


class Provider(SessionProvider):
    def matches(self, record: "WindowRecord") -> bool:
        return record.wm_class.lower() in _JETBRAINS_CLASSES

    def collect_args(self, record: "WindowRecord") -> list[str]:
        project_name = record.metadata.get("active_directory") or ""
        if not project_name:
            return []
        path = find_jetbrains_project_path(project_name)
        return [path] if path else []
