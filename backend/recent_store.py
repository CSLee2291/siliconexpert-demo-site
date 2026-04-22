"""SQLite-backed recent-searches store.

Single table; one row per search event. Queries dedupe by PN at read time
so the Home screen shows each part once (most recent lookup wins).
"""
from __future__ import annotations

import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_DB_PATH = Path(
    os.getenv("RECENT_DB_PATH", "./recent_searches.db")
).resolve()
_lock = threading.Lock()
_initialized = False


def _connect() -> sqlite3.Connection:
    # Allow the same connection to be shared across threads when Flask runs
    # in multi-threaded mode (the module lock serialises writes).
    conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init() -> None:
    global _initialized  # noqa: PLW0603
    with _lock:
        if _initialized:
            return
        _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS recent_searches (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    pn            TEXT    NOT NULL,
                    mpn           TEXT    DEFAULT '',
                    manufacturer  TEXT    DEFAULT '',
                    com_id        TEXT    DEFAULT '',
                    lifecycle     TEXT    DEFAULT '',
                    yeol          REAL,
                    risk          REAL,
                    source        TEXT    DEFAULT '',
                    kind          TEXT    DEFAULT 'single',
                    searched_at   TEXT    NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_recent_pn ON recent_searches(pn)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_recent_time "
                "ON recent_searches(searched_at DESC)"
            )
            conn.commit()
        _initialized = True


def record(
    *,
    pn: str,
    mpn: str = "",
    manufacturer: str = "",
    com_id: str = "",
    lifecycle: str = "",
    yeol: float | None = None,
    risk: float | None = None,
    source: str = "",
    kind: str = "single",
) -> None:
    """Append a single search event. Fail-soft: never raises into the request."""
    try:
        init()
        pn = (pn or "").strip()
        if not pn:
            return
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with _lock, _connect() as conn:
            conn.execute(
                """
                INSERT INTO recent_searches
                    (pn, mpn, manufacturer, com_id, lifecycle, yeol, risk,
                     source, kind, searched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    pn,
                    mpn or "",
                    manufacturer or "",
                    com_id or "",
                    lifecycle or "",
                    yeol,
                    risk,
                    source or "",
                    kind or "single",
                    now,
                ),
            )
            conn.commit()
    except Exception:  # noqa: BLE001 — telemetry must never break the user's request
        pass


def list_recent(limit: int = 10) -> list[dict[str, Any]]:
    """Return the most recent unique PNs (limit rows). Newest first."""
    init()
    with _connect() as conn:
        # Window function would be ideal; stdlib sqlite3 supports it since 3.25.
        rows = conn.execute(
            """
            SELECT pn, mpn, manufacturer, com_id, lifecycle, yeol, risk,
                   source, kind, searched_at
            FROM (
                SELECT *,
                       ROW_NUMBER() OVER (PARTITION BY pn
                                          ORDER BY searched_at DESC) AS rn
                FROM recent_searches
            )
            WHERE rn = 1
            ORDER BY searched_at DESC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
    return [
        {
            "pn":            r["pn"],
            "mpn":           r["mpn"] or "",
            "manufacturer":  r["manufacturer"] or "",
            "comId":         r["com_id"] or "",
            "lifecycle":     r["lifecycle"] or "",
            "yeol":          r["yeol"],
            "risk":          r["risk"],
            "source":        r["source"] or "",
            "kind":          r["kind"] or "single",
            "searchedAt":    r["searched_at"],
        }
        for r in rows
    ]


def clear() -> int:
    init()
    with _lock, _connect() as conn:
        n = conn.execute("DELETE FROM recent_searches").rowcount
        conn.commit()
    return n
