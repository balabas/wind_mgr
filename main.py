#!/usr/bin/env python3
"""wind_mgr — window manager companion with graph UI."""
from __future__ import annotations
import logging
import sys

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Wnck", "3.0")
from gi.repository import Gtk, GLib, Wnck

from core.events import bus, EVT_WINDOW_OPENED, EVT_TITLE_CHANGED
from core.window_registry import WindowRegistry
from core.activity_watcher import ActivityWatcher
from core.relationship import RelationshipTree
from capture.screenshot import ScreenshotCapture
from providers.base import ProviderChain
from providers.chrome import ChromeProvider
from providers.vscode import VSCodeProvider
from providers.editor import EditorProvider, TerminalProvider
from bridge.js_bridge import JSBridge
from ui.main_window import MainWindow

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("wind_mgr")


def main() -> None:
    # ── Core components ──────────────────────────────────────────────────
    registry = WindowRegistry()
    tree     = RelationshipTree(registry)
    capture  = ScreenshotCapture()
    chain    = ProviderChain()

    # Register providers
    chain.register(ChromeProvider())
    chain.register(VSCodeProvider())
    chain.register(EditorProvider())
    chain.register(TerminalProvider())

    # Wire providers into event bus
    def _enrich_on_open(record, **_):
        chain.run(record)

    def _enrich_on_title(xid, new_title, **_):
        record = registry.get(xid)
        if record:
            chain.run(record)

    bus.subscribe(EVT_WINDOW_OPENED, lambda record, **kw: chain.run(record))
    bus.subscribe(EVT_TITLE_CHANGED, lambda xid, new_title, **kw: _enrich_on_title(xid, new_title))

    # Debounced save: re-arm a 2s timer on every graph update
    _save_timer: list[int] = []

    def _schedule_save(**_):
        if _save_timer:
            try:
                GLib.source_remove(_save_timer[0])
            except Exception:
                pass
            _save_timer.clear()
        _save_timer.append(GLib.timeout_add(2000, _do_save))

    def _do_save() -> bool:
        registry.save()
        _save_timer.clear()
        return False

    from core.events import EVT_GRAPH_UPDATED
    bus.subscribe(EVT_GRAPH_UPDATED, _schedule_save)

    # ── Activity watcher ─────────────────────────────────────────────────
    watcher = ActivityWatcher(registry)

    # ── Load persisted registry ──────────────────────────────────────────
    registry.load()

    # ── Bridge & UI ──────────────────────────────────────────────────────
    bridge = JSBridge(registry, tree, capture)
    window = MainWindow(bridge)
    window.build()

    # ── Start watcher after GTK loop begins ──────────────────────────────
    def _start_watcher() -> bool:
        watcher.start()

        # Reconcile persisted records against live windows
        live = {w.get_xid() for w in Wnck.Screen.get_default().get_windows()}
        registry.reconcile(live)

        # Enrich all existing records through providers
        alive = registry.all_alive()
        for record in alive:
            chain.run(record)

        # Push initial graph immediately (placeholders), then re-push after captures
        bridge.push_graph()

        if alive:
            remaining = [len(alive)]

            def _on_initial_done(success: bool) -> None:
                remaining[0] -= 1
                if remaining[0] <= 0:
                    log.info("Initial captures done — refreshing graph")
                    bridge.push_graph()

            for record in alive:
                capture.capture_async(record.xid, callback=_on_initial_done)
                GLib.idle_add(capture.capture_icon, record.xid)

        from core.events import EVT_REGISTRY_READY
        bus.emit(EVT_REGISTRY_READY)
        return False  # one-shot

    GLib.idle_add(_start_watcher)

    # ── Run ──────────────────────────────────────────────────────────────
    log.info("Starting wind_mgr")
    try:
        window.run()
    except KeyboardInterrupt:
        pass
    finally:
        registry.save()
        log.info("wind_mgr stopped")


if __name__ == "__main__":
    main()
