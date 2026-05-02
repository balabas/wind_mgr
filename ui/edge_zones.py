from __future__ import annotations

import configparser
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import gi
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib

log = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).parent.parent / "config.ini"


@dataclass
class EdgeZoneConfig:
    enabled: bool = False
    edge_size: int = 2
    poll_interval_ms: int = 80
    cooldown_ms: int = 700
    bottom_action: str = "toggle"
    top_action: str = "hide"


@dataclass(frozen=True)
class EdgeZoneContext:
    monitor_index: int
    edge: str
    x: int
    y: int
    width: int
    height: int


class EdgeZoneWatcher:
    """Poll global pointer position and fire actions on monitor-edge entry."""

    def __init__(
        self,
        *,
        on_toggle: Callable[[EdgeZoneContext | None], None],
        on_show: Callable[[EdgeZoneContext | None], None],
        on_hide: Callable[[], None],
        config: EdgeZoneConfig | None = None,
    ) -> None:
        self._on_toggle = on_toggle
        self._on_show = on_show
        self._on_hide = on_hide
        self._config = config or read_edge_zone_config()
        self._timer_id: int | None = None
        self._last_zone: tuple[int, str] | None = None
        self._last_context: EdgeZoneContext | None = None
        self._last_fire_ms = 0
        self._suppressed_until_ms = 0
        self._suppress_until_edge_exit = False

    def start(self) -> None:
        if not self._config.enabled:
            log.info("edge zones disabled")
            return
        if self._timer_id is not None:
            return
        interval = max(20, self._config.poll_interval_ms)
        self._timer_id = GLib.timeout_add(interval, self._poll)
        log.info(
            "edge zones enabled: edge=%spx poll=%sms cooldown=%sms top=%s bottom=%s",
            self._config.edge_size,
            interval,
            self._config.cooldown_ms,
            self._config.top_action,
            self._config.bottom_action,
        )

    def stop(self) -> None:
        if self._timer_id is not None:
            GLib.source_remove(self._timer_id)
            self._timer_id = None

    def suppress(self, duration_ms: int) -> None:
        """Ignore the current edge touch after programmatic hides.

        Suppression ends as soon as the pointer leaves all edge zones. The time
        limit is only a safety fallback, so moving away and back remains fast.
        """
        self._suppressed_until_ms = max(
            self._suppressed_until_ms,
            GLib.get_monotonic_time() // 1000 + max(0, duration_ms),
        )
        self._suppress_until_edge_exit = True
        self._last_zone = None
        self._last_context = None

    def _poll(self) -> bool:
        now_ms = GLib.get_monotonic_time() // 1000
        zone = self._current_zone()
        if self._suppress_until_edge_exit:
            if zone is None or self._last_context is None:
                self._suppress_until_edge_exit = False
                self._suppressed_until_ms = 0
            elif now_ms < self._suppressed_until_ms:
                self._last_zone = None
                return True
            else:
                self._suppress_until_edge_exit = False
                self._suppressed_until_ms = 0
        if zone is None or self._last_context is None:
            self._last_zone = None
            self._last_context = None
            return True
        if zone == self._last_zone:
            return True
        if now_ms - self._last_fire_ms < self._config.cooldown_ms:
            self._last_zone = zone
            return True
        self._last_zone = zone
        self._last_fire_ms = now_ms
        monitor_idx, edge = zone
        action = self._config.bottom_action if edge == "bottom" else self._config.top_action
        log.info("edge zone entered: monitor=%s edge=%s action=%s", monitor_idx, edge, action)
        self._run_action(action, self._last_context)
        return True

    def _current_zone(self) -> tuple[int, str] | None:
        self._last_context = None
        display = Gdk.Display.get_default()
        if display is None:
            return None
        seat = display.get_default_seat()
        if seat is None:
            return None
        pointer = seat.get_pointer()
        if pointer is None:
            return None
        _screen, x, y = pointer.get_position()
        edge = max(1, self._config.edge_size)
        for idx in range(display.get_n_monitors()):
            monitor = display.get_monitor(idx)
            if monitor is None:
                continue
            rect = monitor.get_geometry()
            if x < rect.x or x >= rect.x + rect.width:
                continue
            if y < rect.y or y >= rect.y + rect.height:
                continue
            if y < rect.y + edge:
                self._last_context = EdgeZoneContext(
                    monitor_index=idx,
                    edge="top",
                    x=rect.x,
                    y=rect.y,
                    width=rect.width,
                    height=rect.height,
                )
                return (idx, "top")
            if y >= rect.y + rect.height - edge:
                self._last_context = EdgeZoneContext(
                    monitor_index=idx,
                    edge="bottom",
                    x=rect.x,
                    y=rect.y,
                    width=rect.width,
                    height=rect.height,
                )
                return (idx, "bottom")
        return None

    def _run_action(self, action: str, context: EdgeZoneContext | None) -> None:
        normalized = (action or "").strip().lower()
        if normalized == "toggle":
            self._on_toggle(context)
        elif normalized == "show":
            self._on_show(context)
        elif normalized == "hide":
            self._on_hide()
        elif normalized in {"none", "off", "disabled"}:
            return
        else:
            log.warning("unknown edge zone action: %s", action)


def read_edge_zone_config() -> EdgeZoneConfig:
    cfg = configparser.ConfigParser()
    cfg.read(CONFIG_PATH)
    section = "edge_zones"
    return EdgeZoneConfig(
        enabled=cfg.getboolean(section, "enabled", fallback=False),
        edge_size=max(1, cfg.getint(section, "edge_size", fallback=2)),
        poll_interval_ms=max(20, cfg.getint(section, "poll_interval_ms", fallback=80)),
        cooldown_ms=max(0, cfg.getint(section, "toggle_cooldown_ms", fallback=700)),
        bottom_action=cfg.get(section, "bottom_action", fallback="toggle"),
        top_action=cfg.get(section, "top_action", fallback="hide"),
    )
