from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import urlsplit


@dataclass(frozen=True)
class HistoryVisit:
    browser: str
    profile: str
    title: str
    url: str
    domain: str
    visit_count: int
    typed_count: int
    last_visit_time: datetime | None


_ROOTS = {
    "chrome": Path.home() / ".config" / "google-chrome",
    "chromium": Path.home() / ".config" / "chromium",
    "brave": Path.home() / ".config" / "BraveSoftware" / "Brave-Browser",
}

_CACHE: dict[str, tuple[tuple[tuple[str, int], ...], list[HistoryVisit]]] = {}


def recent_browser_history(limit_per_profile: int = 200) -> list[HistoryVisit]:
    rows: list[HistoryVisit] = []
    for browser, root in _ROOTS.items():
        rows.extend(_recent_history_for_root(browser, root, limit_per_profile))
    rows.sort(key=lambda row: row.last_visit_time or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return rows


def match_history_visit(
    *,
    browser: str,
    title: str,
    domain: str,
    limit_per_profile: int = 200,
) -> HistoryVisit | None:
    browser = browser.lower().strip()
    title_norm = normalize_text(title)
    domain_norm = normalize_domain(domain)
    if not title_norm and not domain_norm:
        return None

    query_domain = domain if looks_like_host(domain) else ""
    candidates = [row for row in recent_browser_history(limit_per_profile) if row.browser == browser]
    best: tuple[float, HistoryVisit] | None = None
    for row in candidates:
        score = score_history_visit(
            title=title_norm,
            domain=query_domain,
            candidate=row,
        )
        if score <= 0:
            continue
        if best is None or score > best[0]:
            best = (score, row)
    return best[1] if best is not None else None


def score_history_visit(*, title: str, domain: str, candidate: HistoryVisit) -> float:
    score = 0.0
    candidate_title = normalize_text(candidate.title)
    candidate_domain = normalize_domain(candidate.domain)

    if domain and candidate_domain:
        if domain == candidate_domain:
            score += 60.0
        elif domain in candidate_domain or candidate_domain in domain:
            score += 30.0

    if title and candidate_title:
        if title == candidate_title:
            score += 45.0
        else:
            ratio = SequenceMatcher(None, title, candidate_title).ratio()
            score += ratio * 35.0
            if title in candidate_title or candidate_title in title:
                score += 15.0

    if candidate.last_visit_time is not None:
        age_hours = max(0.0, (datetime.now(timezone.utc) - candidate.last_visit_time).total_seconds() / 3600.0)
        score += max(0.0, 20.0 - age_hours * 0.5)

    if candidate.url:
        url_domain = normalize_domain(url_domain_from_url(candidate.url))
        if domain and url_domain:
            if domain == url_domain:
                score += 20.0
            elif domain in url_domain or url_domain in domain:
                score += 8.0
    return score


def normalize_text(value: str | None) -> str:
    text = (value or "").strip().lower()
    if not text:
        return ""
    text = " ".join(text.replace("•", " ").split())
    return text


def normalize_domain(value: str | None) -> str:
    domain = normalize_text(value)
    domain = domain.removeprefix("www.")
    domain = domain.removeprefix("m.")
    domain = domain.split("/", 1)[0]
    return domain


def looks_like_host(value: str | None) -> bool:
    host = normalize_domain(value)
    if not host or " " in host:
        return False
    if "." not in host:
        return False
    return True


def url_domain_from_url(url: str) -> str:
    try:
        parts = urlsplit(url)
    except Exception:
        return ""
    if not parts.netloc:
        return ""
    return parts.netloc


def chrome_time_to_datetime(value: object) -> datetime | None:
    try:
        micros = int(value)
    except (TypeError, ValueError):
        return None
    if micros <= 0:
        return None
    epoch = datetime(1601, 1, 1, tzinfo=timezone.utc)
    return epoch + timedelta(microseconds=micros)


def _recent_history_for_root(browser: str, root: Path, limit_per_profile: int) -> list[HistoryVisit]:
    if not root.exists():
        return []

    profile_history = [p / "History" for p in sorted(root.iterdir()) if p.is_dir() and (p / "History").exists()]
    if not profile_history:
        return []

    cache_key = browser
    fingerprint = tuple((str(path), _stat_mtime_ns(path)) for path in profile_history)
    cached = _CACHE.get(cache_key)
    if cached and cached[0] == fingerprint:
        return cached[1]

    rows: list[HistoryVisit] = []
    for history_path in profile_history:
        rows.extend(_read_history_rows(browser, history_path, limit_per_profile))

    _CACHE[cache_key] = (fingerprint, rows)
    return rows


def _read_history_rows(browser: str, history_path: Path, limit: int) -> list[HistoryVisit]:
    try:
        rows = _read_history_rows_ro(history_path, limit)
    except sqlite3.Error:
        rows = _read_history_rows_copy(history_path, limit)

    profile = history_path.parent.name
    result: list[HistoryVisit] = []
    for title, url, visit_count, typed_count, last_visit_time in rows:
        result.append(
            HistoryVisit(
                browser=browser,
                profile=profile,
                title=title or "",
                url=url or "",
                domain=url_domain_from_url(url or ""),
                visit_count=int(visit_count or 0),
                typed_count=int(typed_count or 0),
                last_visit_time=chrome_time_to_datetime(last_visit_time),
            )
        )
    return result


def _read_history_rows_ro(history_path: Path, limit: int) -> list[tuple]:
    conn = sqlite3.connect(f"file:{history_path}?mode=ro", uri=True, timeout=1.0)
    try:
        conn.execute("PRAGMA query_only = ON")
        return conn.execute(
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


def _read_history_rows_copy(history_path: Path, limit: int) -> list[tuple]:
    fd, tmp_name = tempfile.mkstemp(prefix="wind_mgr_browser_history_", suffix=".sqlite3")
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        shutil.copy2(history_path, tmp_path)
        for suffix in ("-wal", "-shm"):
            sidecar = Path(str(history_path) + suffix)
            if sidecar.exists():
                shutil.copy2(sidecar, Path(str(tmp_path) + suffix))
        conn = sqlite3.connect(tmp_path, timeout=1.0)
        try:
            conn.execute("PRAGMA query_only = ON")
            return conn.execute(
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


def _stat_mtime_ns(path: Path) -> int:
    try:
        return path.stat().st_mtime_ns
    except FileNotFoundError:
        return 0
