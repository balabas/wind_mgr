from __future__ import annotations
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .window_registry import DATA_DIR

log = logging.getLogger(__name__)

SESSIONS_DIR = DATA_DIR / "sessions"


class SessionStore:
    def __init__(self) -> None:
        SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

    def save(self, name: str, project_id: str, apps: list[dict]) -> dict:
        """Save a new session and return it."""
        session_id = str(uuid.uuid4())
        session = {
            "id": session_id,
            "name": name,
            "project_id": project_id,
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "apps": apps,
        }
        self._write(session)
        log.info("session saved: id=%s name=%r apps=%d", session_id, name, len(apps))
        return session

    def load_all(self) -> list[dict]:
        """Return all sessions sorted newest first."""
        sessions = []
        for path in SESSIONS_DIR.glob("*.json"):
            try:
                s = json.loads(path.read_text())
                sessions.append(s)
            except Exception:
                log.warning("Failed to load session %s", path, exc_info=True)
        sessions.sort(key=lambda s: s.get("saved_at", ""), reverse=True)
        return sessions

    def delete(self, session_id: str) -> None:
        path = SESSIONS_DIR / f"{session_id}.json"
        try:
            path.unlink()
            log.info("session deleted: %s", session_id)
        except FileNotFoundError:
            pass

    def rename(self, session_id: str, name: str) -> bool:
        path = SESSIONS_DIR / f"{session_id}.json"
        if not path.exists():
            return False
        try:
            s = json.loads(path.read_text())
            s["name"] = name
            self._write(s)
            return True
        except Exception:
            log.warning("Failed to rename session %s", session_id, exc_info=True)
            return False

    def _write(self, session: dict) -> None:
        path = SESSIONS_DIR / f"{session['id']}.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(session, indent=2))
        os.replace(tmp, path)
