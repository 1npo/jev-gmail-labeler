"""SQLite state: history cursor, watch info and per-message processing status."""

import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from jev_gmail_labeler import files

SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS messages (
  message_id TEXT PRIMARY KEY,
  status TEXT NOT NULL CHECK (status IN ('done','retry','failed')),
  result_status TEXT, category TEXT, label_name TEXT,
  attempts INTEGER NOT NULL DEFAULT 0,
  last_error TEXT, next_retry_at REAL, updated_at REAL NOT NULL);
"""


@dataclass(frozen=True, slots=True)
class MessageRecord:
    """The stored processing status of one message."""

    message_id: str
    status: str
    attempts: int
    next_retry_at: float | None
    last_error: str | None


class StateStore:
    """Small key-value and per-message store backed by SQLite."""

    def __init__(
        self, path: Path | str, *, clock: Callable[[], float] = time.time
    ) -> None:
        if path != ':memory:':
            files.ensure_dir(Path(path).parent)
            path = str(path)
        self._clock = clock
        self._db = sqlite3.connect(path)
        self._db.executescript(SCHEMA)
        self._db.commit()

    def get(self, key: str) -> str | None:
        """Return the value stored under ``key``, or None."""
        row = self._db.execute('SELECT value FROM kv WHERE key = ?', (key,)).fetchone()
        return row[0] if row else None

    def set(self, key: str, value: str) -> None:
        """Store ``value`` under ``key``."""
        self._db.execute(
            'INSERT INTO kv (key, value) VALUES (?, ?) '
            'ON CONFLICT(key) DO UPDATE SET value = excluded.value',
            (key, value),
        )
        self._db.commit()

    def delete(self, key: str) -> None:
        """Remove ``key`` if present."""
        self._db.execute('DELETE FROM kv WHERE key = ?', (key,))
        self._db.commit()

    def get_cursor(self) -> int | None:
        """Return the Gmail history cursor, or None if not yet seeded."""
        value = self.get('history_cursor')
        return int(value) if value is not None else None

    def advance_cursor(self, history_id: int) -> None:
        """Move the cursor forward; it never moves backwards."""
        current = self.get_cursor()
        if current is None or history_id > current:
            self.set('history_cursor', str(history_id))

    def message(self, message_id: str) -> MessageRecord | None:
        """Return the record for ``message_id``, or None."""
        row = self._db.execute(
            'SELECT message_id, status, attempts, next_retry_at, last_error '
            'FROM messages WHERE message_id = ?',
            (message_id,),
        ).fetchone()
        return MessageRecord(*row) if row else None

    def _upsert(
        self,
        message_id: str,
        status: str,
        *,
        result_status: str | None = None,
        category: str | None = None,
        label_name: str | None = None,
        attempts: int = 0,
        error: str | None = None,
        next_retry_at: float | None = None,
    ) -> None:
        self._db.execute(
            'INSERT OR REPLACE INTO messages (message_id, status, result_status, '
            'category, label_name, attempts, last_error, next_retry_at, updated_at) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (
                message_id,
                status,
                result_status,
                category,
                label_name,
                attempts,
                error,
                next_retry_at,
                self._clock(),
            ),
        )
        self._db.commit()

    def mark_done(
        self,
        message_id: str,
        *,
        result_status: str,
        category: str | None,
        label_name: str | None,
    ) -> None:
        """Record that a message was handled."""
        self._upsert(
            message_id,
            'done',
            result_status=result_status,
            category=category,
            label_name=label_name,
        )

    def mark_retry(
        self, message_id: str, *, attempts: int, error: str, next_retry_at: float
    ) -> None:
        """Record a failed attempt that should be retried later."""
        self._upsert(
            message_id,
            'retry',
            attempts=attempts,
            error=error,
            next_retry_at=next_retry_at,
        )

    def mark_failed(self, message_id: str, *, attempts: int, error: str) -> None:
        """Record a message that will not be retried."""
        self._upsert(message_id, 'failed', attempts=attempts, error=error)

    def due_retries(self, now: float) -> list[str]:
        """Return ids whose retry time has come, oldest first."""
        rows = self._db.execute(
            "SELECT message_id FROM messages WHERE status = 'retry' "
            'AND next_retry_at <= ? ORDER BY next_retry_at, message_id',
            (now,),
        ).fetchall()
        return [r[0] for r in rows]

    def prune(self, older_than: float) -> int:
        """Delete finished rows last updated before ``older_than``."""
        cur = self._db.execute(
            "DELETE FROM messages WHERE status IN ('done','failed') AND updated_at < ?",
            (older_than,),
        )
        self._db.commit()
        return cur.rowcount

    def clear_watch(self) -> None:
        """Forget the stored watch expiration and renewal time."""
        self.delete('watch_expiration_ms')
        self.delete('watch_renewed_at')

    def close(self) -> None:
        """Close the database."""
        self._db.close()
