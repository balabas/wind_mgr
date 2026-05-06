from __future__ import annotations
import importlib
import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.window_record import WindowRecord

log = logging.getLogger(__name__)

_PROVIDER_MODULES = [
    "providers.session.browser",
    "providers.session.vscode",
    "providers.session.jetbrains",
    "providers.session.terminal",
    "providers.session.file_manager",
]


class SessionProvider(ABC):
    @abstractmethod
    def matches(self, record: "WindowRecord") -> bool: ...

    @abstractmethod
    def collect_args(self, record: "WindowRecord") -> list[str]: ...


def load_all() -> list[SessionProvider]:
    providers: list[SessionProvider] = []
    for module_name in _PROVIDER_MODULES:
        try:
            mod = importlib.import_module(module_name)
            providers.append(mod.Provider())
            log.debug("session provider loaded: %s", module_name)
        except Exception:
            log.warning("session provider failed to load: %s", module_name, exc_info=True)
    return providers
