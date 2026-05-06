from __future__ import annotations
import logging
from collections import defaultdict
from typing import Callable

log = logging.getLogger(__name__)

EVT_WINDOW_CLOSING = "window_closing"   # fires before registry.remove(); tree still intact
EVT_WINDOW_OPENED  = "window_opened"
EVT_WINDOW_CLOSED  = "window_closed"
EVT_FOCUS_CHANGED  = "focus_changed"
EVT_TITLE_CHANGED  = "title_changed"
EVT_GRAPH_UPDATED  = "graph_updated"
EVT_REGISTRY_READY = "registry_ready"


class EventBus:
    def __init__(self) -> None:
        self._listeners: dict[str, list[Callable]] = defaultdict(list)

    def subscribe(self, event: str, callback: Callable) -> None:
        self._listeners[event].append(callback)

    def unsubscribe(self, event: str, callback: Callable) -> None:
        try:
            self._listeners[event].remove(callback)
        except ValueError:
            pass

    def emit(self, event: str, **kwargs) -> None:
        for cb in list(self._listeners[event]):
            try:
                cb(**kwargs)
            except Exception:
                log.exception("Error in listener for event %r", event)


bus: EventBus = EventBus()
