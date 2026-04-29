"""SQLite storage — thread-safe, WAL mode, generic schema."""
from __future__ import annotations
import sqlite3
import threading
import logging

logger = logging.getLogger(__name__)

_lock: threading.Lock = threading.Lock()
_conn: sqlite3.Connection | None = None
_db_path: str = "engram.db"


def configure(path: str) -> None:
    global _db_path, _conn
    _db_path = path
    _conn = None  # force reconnect on next use


def _get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(_db_path, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA synchronous=NORMAL")
    return _conn


def init() -> None:
    with _lock:
        conn = _get_conn()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS decisions (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                ts           TEXT NOT NULL,
                decision_id  TEXT UNIQUE NOT NULL,
                decision     TEXT NOT NULL,
                context      TEXT,
                outcome      TEXT,
                outcome_ts   TEXT,
                outcome_raw  TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_dec_ts  ON decisions(ts);
            CREATE INDEX IF NOT EXISTS idx_dec_did ON decisions(decision_id);

            CREATE TABLE IF NOT EXISTS lessons (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                written_ts        TEXT NOT NULL,
                expires_ts        TEXT,
                type              TEXT DEFAULT 'lesson',
                text              TEXT NOT NULL,
                source_data       TEXT,
                baseline_accuracy REAL
            );
            CREATE INDEX IF NOT EXISTS idx_lessons ON lessons(type, written_ts);

            CREATE TABLE IF NOT EXISTS proposals (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                written_ts     TEXT NOT NULL,
                analysis_date  TEXT NOT NULL,
                category       TEXT NOT NULL,
                priority       TEXT NOT NULL DEFAULT 'medium',
                title          TEXT NOT NULL,
                problem        TEXT NOT NULL,
                evidence       TEXT NOT NULL,
                proposal       TEXT NOT NULL,
                affected_files TEXT,
                code_change    TEXT,
                status         TEXT NOT NULL DEFAULT 'pending',
                user_notes     TEXT,
                implemented_ts TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_proposals_status ON proposals(status);
            CREATE INDEX IF NOT EXISTS idx_proposals_date   ON proposals(analysis_date);
        """)
        conn.commit()

        # Migrations — safe to run on existing databases
        for col, typedef in [
            ("baseline_accuracy", "REAL"),
            ("source_data",       "TEXT"),
            ("code_change",       "TEXT"),
        ]:
            try:
                conn.execute(f"ALTER TABLE lessons ADD COLUMN {col} {typedef}")
                conn.commit()
            except Exception:
                pass  # column already exists

        # Proposal provenance — JSON arrays of lesson/decision IDs that motivated each proposal
        for col, typedef in [
            ("source_lesson_ids",   "TEXT"),
            ("source_decision_ids", "TEXT"),
        ]:
            try:
                conn.execute(f"ALTER TABLE proposals ADD COLUMN {col} {typedef}")
                conn.commit()
            except Exception:
                pass

        logger.debug("Engram DB initialised: %s", _db_path)


def execute(sql: str, params: tuple = ()) -> sqlite3.Cursor:
    with _lock:
        conn = _get_conn()
        cur = conn.execute(sql, params)
        conn.commit()
        return cur


def fetchall(sql: str, params: tuple = ()) -> list[dict]:
    with _lock:
        conn = _get_conn()
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def fetchone(sql: str, params: tuple = ()) -> dict | None:
    with _lock:
        conn = _get_conn()
        row = conn.execute(sql, params).fetchone()
        return dict(row) if row else None
