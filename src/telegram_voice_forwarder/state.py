from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass(frozen=True, slots=True)
class PendingJob:
    source_id: int
    message_id: int
    attempts: int


class StateStore:
    """Durable cursors and forwarding jobs used for restart-safe processing."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path)
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS cursors (
                source_id INTEGER PRIMARY KEY,
                message_id INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS forwarding_jobs (
                source_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('pending', 'failed', 'forwarded', 'ignored')),
                attempts INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (source_id, message_id)
            );
            """
        )
        self._connection.commit()

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    def close(self) -> None:
        self._connection.close()

    def cursor(self, source_id: int) -> int:
        row = self._connection.execute(
            "SELECT message_id FROM cursors WHERE source_id = ?", (source_id,)
        ).fetchone()
        return int(row[0]) if row else 0

    def advance_cursor(self, source_id: int, message_id: int) -> None:
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO cursors(source_id, message_id) VALUES (?, ?)
                ON CONFLICT(source_id) DO UPDATE SET
                    message_id = MAX(cursors.message_id, excluded.message_id)
                """,
                (source_id, message_id),
            )

    def reset(self) -> tuple[int, int]:
        """Remove all scan positions and known-message history."""
        with self._connection:
            cursor_result = self._connection.execute("DELETE FROM cursors")
            jobs_result = self._connection.execute("DELETE FROM forwarding_jobs")
        return cursor_result.rowcount, jobs_result.rowcount

    def is_complete(self, source_id: int, message_id: int) -> bool:
        row = self._connection.execute(
            """
            SELECT 1 FROM forwarding_jobs
            WHERE source_id = ? AND message_id = ? AND status IN ('forwarded', 'ignored')
            """,
            (source_id, message_id),
        ).fetchone()
        return row is not None

    def mark_pending(self, source_id: int, message_id: int) -> None:
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO forwarding_jobs(source_id, message_id, status, updated_at)
                VALUES (?, ?, 'pending', ?)
                ON CONFLICT(source_id, message_id) DO UPDATE SET
                    status = 'pending',
                    updated_at = excluded.updated_at
                WHERE forwarding_jobs.status NOT IN ('forwarded', 'ignored')
                """,
                (source_id, message_id, self._now()),
            )

    def mark_forwarded(self, source_id: int, message_id: int) -> None:
        with self._connection:
            self._connection.execute(
                """
                UPDATE forwarding_jobs
                SET status = 'forwarded', attempts = attempts + 1,
                    last_error = NULL, updated_at = ?
                WHERE source_id = ? AND message_id = ?
                """,
                (self._now(), source_id, message_id),
            )
            self._connection.execute(
                """
                INSERT INTO cursors(source_id, message_id) VALUES (?, ?)
                ON CONFLICT(source_id) DO UPDATE SET
                    message_id = MAX(cursors.message_id, excluded.message_id)
                """,
                (source_id, message_id),
            )

    def mark_failed(self, source_id: int, message_id: int, error: str) -> None:
        with self._connection:
            self._connection.execute(
                """
                UPDATE forwarding_jobs
                SET status = 'failed', attempts = attempts + 1,
                    last_error = ?, updated_at = ?
                WHERE source_id = ? AND message_id = ?
                """,
                (error[:1000], self._now(), source_id, message_id),
            )

    def mark_ignored(self, source_id: int, message_id: int, reason: str) -> None:
        self.mark_pending(source_id, message_id)
        with self._connection:
            self._connection.execute(
                """
                UPDATE forwarding_jobs
                SET status = 'ignored', last_error = ?, updated_at = ?
                WHERE source_id = ? AND message_id = ?
                """,
                (reason[:1000], self._now(), source_id, message_id),
            )
            self._connection.execute(
                """
                INSERT INTO cursors(source_id, message_id) VALUES (?, ?)
                ON CONFLICT(source_id) DO UPDATE SET
                    message_id = MAX(cursors.message_id, excluded.message_id)
                """,
                (source_id, message_id),
            )

    def pending_jobs(self) -> list[PendingJob]:
        rows = self._connection.execute(
            """
            SELECT source_id, message_id, attempts
            FROM forwarding_jobs
            WHERE status IN ('pending', 'failed')
            ORDER BY updated_at ASC
            """
        ).fetchall()
        return [PendingJob(int(row[0]), int(row[1]), int(row[2])) for row in rows]
