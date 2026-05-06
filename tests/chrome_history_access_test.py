#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from pathlib import Path


CHROME_ROOT = Path.home() / ".config" / "google-chrome"


@dataclass(frozen=True)
class HistoryEntry:
    profile: str
    title: str
    url: str
    visit_count: int
    typed_count: int
    last_visit_time: str


def chrome_profiles(root: Path) -> list[Path]:
    if not root.exists():
        return []

    candidates: list[Path] = []
    for profile_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        history = profile_dir / "History"
        if history.exists():
            candidates.append(history)
    return candidates


def recent_history(history_path: Path, limit: int) -> list[HistoryEntry]:
    conn = None
    try:
        conn = sqlite3.connect(f"file:{history_path}?mode=ro", uri=True, timeout=1.0)
        try:
            conn.execute("PRAGMA query_only = ON")
            rows = conn.execute(
                """
                SELECT
                    title,
                    url,
                    visit_count,
                    typed_count,
                    last_visit_time
                FROM urls
                ORDER BY last_visit_time DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        tmp_path = copy_history_db_with_wal(history_path)
        try:
            conn = sqlite3.connect(tmp_path, timeout=1.0)
            try:
                conn.execute("PRAGMA query_only = ON")
                rows = conn.execute(
                    """
                    SELECT
                        title,
                        url,
                        visit_count,
                        typed_count,
                        last_visit_time
                    FROM urls
                    ORDER BY last_visit_time DESC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
            finally:
                conn.close()
        finally:
            try:
                tmp_path.unlink()
            except FileNotFoundError:
                pass

    entries: list[HistoryEntry] = []
    profile_name = history_path.parent.name
    for title, url, visit_count, typed_count, last_visit_time in rows:
        human_time = chrome_time_to_iso8601(last_visit_time)
        entries.append(
            HistoryEntry(
                profile=profile_name,
                title=title or "",
                url=url or "",
                visit_count=int(visit_count or 0),
                typed_count=int(typed_count or 0),
                last_visit_time=human_time,
            )
        )
    return entries


def copy_history_db_with_wal(history_path: Path) -> Path:
    fd, tmp_name = tempfile.mkstemp(prefix="wind_mgr_chrome_history_", suffix=".sqlite3")
    os.close(fd)
    tmp_path = Path(tmp_name)

    shutil.copy2(history_path, tmp_path)
    for suffix in ("-wal", "-shm"):
        sidecar = Path(str(history_path) + suffix)
        if sidecar.exists():
            shutil.copy2(sidecar, Path(str(tmp_path) + suffix))
    return tmp_path


def chrome_time_to_iso8601(value: object) -> str:
    try:
        micros = int(value)
    except (TypeError, ValueError):
        return ""
    if micros <= 0:
        return ""

    epoch = datetime(1601, 1, 1, tzinfo=timezone.utc)
    dt = epoch + timedelta(microseconds=micros)
    return dt.isoformat()


def main() -> int:
    parser = argparse.ArgumentParser(description="Read recent Chrome history entries for access testing.")
    parser.add_argument("--limit", type=int, default=10, help="Number of recent entries to print.")
    args = parser.parse_args()

    profiles = chrome_profiles(CHROME_ROOT)
    if not profiles:
        print(f"No Chrome History database found under {CHROME_ROOT}")
        return 1

    print(f"Found {len(profiles)} Chrome profile database(s):")
    for history_path in profiles:
        print(f"- {history_path}")

    print()
    any_entries = False
    for history_path in profiles:
        entries = recent_history(history_path, args.limit)
        if not entries:
            print(f"[{history_path.parent.name}] no rows returned")
            continue

        any_entries = True
        print(f"[{history_path.parent.name}] recent history:")
        for entry in entries:
            print(
                f"- {entry.title!r} | {entry.url} | visits={entry.visit_count} "
                f"typed={entry.typed_count} last_visit_time={entry.last_visit_time}"
            )
        print()

    return 0 if any_entries else 2


if __name__ == "__main__":
    raise SystemExit(main())
