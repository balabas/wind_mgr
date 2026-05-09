from __future__ import annotations
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.window_record import WindowRecord


class Provider(ABC):
    priority: int = 50

    @abstractmethod
    def matches(self, record: "WindowRecord") -> bool: ...

    @abstractmethod
    def enrich(self, record: "WindowRecord") -> None: ...


class ProviderChain:
    def __init__(self) -> None:
        self._providers: list[Provider] = []

    def register(self, provider: Provider) -> None:
        self._providers.append(provider)
        self._providers.sort(key=lambda p: p.priority)

    _ENRICHMENT_KEYS = {"tab_title", "domain", "group_key", "app_type",
                        "project_name", "active_file", "active_directory",
                        "history_title", "history_url", "history_domain",
                        "history_profile", "history_visit_count",
                        "history_typed_count", "history_last_visit_time"}

    def run(self, record: "WindowRecord") -> None:
        matched = False
        for provider in self._providers:
            if provider.matches(record):
                provider.enrich(record)
                matched = True
        if not matched:
            for key in self._ENRICHMENT_KEYS:
                record.metadata.pop(key, None)
