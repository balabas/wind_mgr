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

    def run(self, record: "WindowRecord") -> None:
        for provider in self._providers:
            if provider.matches(record):
                provider.enrich(record)
