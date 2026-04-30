from __future__ import annotations
import json
import logging
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING

from .window_record import WindowRecord

if TYPE_CHECKING:
    pass

log = logging.getLogger(__name__)

DATA_DIR = Path.home() / ".local" / "share" / "wind_mgr"
REGISTRY_PATH = DATA_DIR / "registry.json"


class WindowRegistry:
    def __init__(self) -> None:
        self._records: dict[int, WindowRecord] = {}
        DATA_DIR.mkdir(parents=True, exist_ok=True)

    # ── CRUD ──────────────────────────────────────────────────────────────

    def add(self, record: WindowRecord) -> None:
        self._records[record.xid] = record
        if record.parent_xid is not None:
            parent = self._records.get(record.parent_xid)
            if parent and record.xid not in parent.children_xids:
                parent.children_xids.append(record.xid)

    def remove(self, xid: int) -> None:
        record = self._records.get(xid)
        if record:
            record.is_alive = False

    def get(self, xid: int) -> WindowRecord | None:
        return self._records.get(xid)

    def all_alive(self) -> list[WindowRecord]:
        return [r for r in self._records.values() if r.is_alive]

    def all_records(self) -> list[WindowRecord]:
        return list(self._records.values())

    def live_xids(self) -> set[int]:
        return {r.xid for r in self._records.values() if r.is_alive}

    # ── Persistence ───────────────────────────────────────────────────────

    def save(self) -> None:
        tmp = REGISTRY_PATH.with_suffix(".tmp")
        try:
            data = [r.to_dict() for r in self._records.values()]
            tmp.write_text(json.dumps(data, indent=2))
            os.replace(tmp, REGISTRY_PATH)
        except Exception:
            log.exception("Failed to save registry")

    def load(self) -> None:
        if not REGISTRY_PATH.exists():
            return
        try:
            data = json.loads(REGISTRY_PATH.read_text())
            for d in data:
                r = WindowRecord.from_dict(d)
                self._records[r.xid] = r
        except Exception:
            log.exception("Failed to load registry")

    def reconcile(self, live_xids: set[int]) -> None:
        """Cross-reference persisted records against currently open windows."""
        for xid, record in self._records.items():
            if _is_self_record(record):
                record.is_alive = False
            elif xid in live_xids:
                record.is_alive = True
            else:
                record.is_alive = False
        for record in self._records.values():
            parent = self._records.get(record.parent_xid) if record.parent_xid is not None else None
            if parent is not None and _is_self_record(parent):
                record.parent_xid = None
        # Rebuild children back-links in case they're missing
        for record in self._records.values():
            if record.parent_xid is not None:
                parent = self._records.get(record.parent_xid)
                if parent and record.xid not in parent.children_xids:
                    parent.children_xids.append(record.xid)


def _is_self_record(record: WindowRecord) -> bool:
    values = {
        record.title,
        record.app_name,
        record.wm_class,
        record.wm_class_group,
    }
    return any((v or "").strip().lower() == "wind_mgr" for v in values)
