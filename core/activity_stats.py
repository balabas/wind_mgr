from __future__ import annotations

import json
import logging
import math
import os
import time
from pathlib import Path


log = logging.getLogger(__name__)
DATA_DIR = Path.home() / ".local" / "share" / "wind_mgr"
STATS_PATH = DATA_DIR / "activity_stats.json"


class ActivityStats:
    def __init__(self, *, half_life_seconds: float = 900.0) -> None:
        self._half_life_seconds = max(1.0, float(half_life_seconds))
        self._stats: dict[str, dict] = {}
        self._active_xid: int | None = None
        self._active_since: float | None = None
        self._last_save_at = 0.0
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.load()

    def load(self) -> None:
        if not STATS_PATH.exists():
            return
        try:
            raw = json.loads(STATS_PATH.read_text())
            if isinstance(raw, dict):
                self._stats = {str(k): dict(v) for k, v in raw.items() if isinstance(v, dict)}
        except Exception:
            log.exception("Failed to load activity stats")

    def save(self, *, force: bool = False) -> None:
        now = time.time()
        if not force and now - self._last_save_at < 5.0:
            return
        tmp = STATS_PATH.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(self._stats, indent=2, sort_keys=True))
            os.replace(tmp, STATS_PATH)
            self._last_save_at = now
        except Exception:
            log.exception("Failed to save activity stats")

    def mark_focus_change(self, new_xid: int | None, old_xid: int | None) -> None:
        now = time.time()
        if old_xid is not None:
            self._finish_active(old_xid, now)
        if new_xid is not None:
            item = self._item(new_xid)
            item["last_active_at"] = now
            item["activation_count"] = int(item.get("activation_count", 0)) + 1
            self._active_xid = new_xid
            self._active_since = now
        else:
            self._active_xid = None
            self._active_since = None
        self.save()

    def mark_click(self, xid: int) -> None:
        now = time.time()
        item = self._item(xid)
        item["last_clicked_at"] = now
        item["click_count"] = int(item.get("click_count", 0)) + 1
        self.save()

    def mark_hover(self, xid: int) -> None:
        now = time.time()
        item = self._item(xid)
        item["last_hovered_at"] = now
        item["hover_count"] = int(item.get("hover_count", 0)) + 1
        self.save()

    def score(self, xid: int, *, active_xid: int | None, last_capture_at_ms: int = 0) -> float:
        now = time.time()
        item = self._stats.get(str(xid), {})
        score = 0.0
        if xid == active_xid:
            score += 1000.0
        score += 300.0 * self._decay(now - float(item.get("last_active_at", 0) or 0))
        score += 180.0 * self._decay(now - float(item.get("last_clicked_at", 0) or 0))
        score += 120.0 * self._decay(now - float(item.get("last_hovered_at", 0) or 0))
        score += 25.0 * math.log1p(float(item.get("activation_count", 0) or 0))
        score += 12.0 * math.log1p(float(item.get("click_count", 0) or 0))
        score += 6.0 * math.log1p(float(item.get("hover_count", 0) or 0))
        if last_capture_at_ms:
            age_s = max(0.0, (time.monotonic() * 1000.0 - last_capture_at_ms) / 1000.0)
            score += min(180.0, age_s * 2.0)
        else:
            score += 180.0
        return score

    def _finish_active(self, xid: int, now: float) -> None:
        if self._active_xid != xid or self._active_since is None:
            return
        duration = max(0.0, now - self._active_since)
        item = self._item(xid)
        item["active_duration_total"] = float(item.get("active_duration_total", 0.0) or 0.0) + duration
        self._active_xid = None
        self._active_since = None

    def _item(self, xid: int) -> dict:
        return self._stats.setdefault(str(xid), {})

    def _decay(self, age_seconds: float) -> float:
        if age_seconds <= 0:
            return 1.0
        return math.pow(0.5, age_seconds / self._half_life_seconds)
