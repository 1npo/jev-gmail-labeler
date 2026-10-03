"""
Tiny persistence layer, backed by SQLite, so the listener survives restarts
without losing its place or reprocessing messages.

Two things are tracked:
  - kv table: single-row key/value store, used for `last_history_id`.
  - processed_messages table: message IDs already handed to your tool,
    used for idempotency (Pub/Sub delivers at-least-once, so the same
    notification can arrive twice).
"""

import os
import sqlite3
import time
from typing import Optional

DB_FILE = os.environ.get("STATE_DB_FILE", "state.sqlite3")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS processed_messages (
    message_id TEXT PRIMARY KEY,
    processed_at REAL
);
"""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_FILE)
    conn.executescript(_SCHEMA)
    return conn


def get_last_history_id() -> Optional[str]:
    with _connect() as conn:
        row = conn.execute("SELECT value FROM kv WHERE key = 'last_history_id'").fetchone()
        return row[0] if row else None


def set_last_history_id(history_id: str) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO kv (key, value) VALUES ('last_history_id', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(history_id),),
        )


def already_processed(message_id: str) -> bool:
    with _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM processed_messages WHERE message_id = ?", (message_id,)
        ).fetchone()
        return row is not None


def mark_processed(message_id: str) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO processed_messages (message_id, processed_at) VALUES (?, ?)",
            (message_id, time.time()),
        )
        # Keep the table from growing forever -- drop entries older than 30 days.
        conn.execute(
            "DELETE FROM processed_messages WHERE processed_at < ?",
            (time.time() - 30 * 86400,),
        )
