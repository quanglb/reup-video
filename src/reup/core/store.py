"""Sổ cái SQLite: job, lần chạy stage, và video đã xử lý."""
from __future__ import annotations

import sqlite3
from pathlib import Path

JOB_STATUSES = ("pending", "running", "needs_review", "done", "failed")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id         TEXT PRIMARY KEY,
    url        TEXT NOT NULL,
    status     TEXT NOT NULL,
    stage      TEXT,
    error      TEXT,
    updated_at REAL NOT NULL DEFAULT (unixepoch('subsec'))
);
CREATE TABLE IF NOT EXISTS stage_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id      TEXT NOT NULL,
    stage       TEXT NOT NULL,
    started_at  REAL NOT NULL,
    finished_at REAL NOT NULL,
    duration_ms INTEGER NOT NULL,
    ok          INTEGER NOT NULL,
    error       TEXT
);
CREATE INDEX IF NOT EXISTS idx_stage_runs_stage ON stage_runs(stage);
CREATE TABLE IF NOT EXISTS seen (
    platform TEXT NOT NULL,
    video_id TEXT NOT NULL,
    phash    TEXT,
    seen_at  REAL NOT NULL DEFAULT (unixepoch('subsec')),
    PRIMARY KEY (platform, video_id)
);
CREATE TABLE IF NOT EXISTS saved_videos (
    platform TEXT NOT NULL,
    video_id TEXT NOT NULL,
    data     TEXT NOT NULL,
    saved_at REAL NOT NULL DEFAULT (unixepoch('subsec')),
    PRIMARY KEY (platform, video_id)
);
"""


class Store:
    def __init__(self, db_path: Path) -> None:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        # timeout: `run --all` chạy nhiều job song song, mỗi job một connection.
        # Gặp lúc người khác đang ghi thì chờ chứ đừng ném "database is locked".
        self._conn = sqlite3.connect(db_path, timeout=30.0)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")

    def init_schema(self) -> None:
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def upsert_job(
        self,
        job_id: str,
        url: str,
        status: str,
        stage: str | None = None,
        error: str | None = None,
    ) -> None:
        if status not in JOB_STATUSES:
            raise ValueError(
                f"status {status!r} không hợp lệ. Chọn một trong {JOB_STATUSES}"
            )
        self._conn.execute(
            """
            INSERT INTO jobs (id, url, status, stage, error, updated_at)
            VALUES (?, ?, ?, ?, ?, unixepoch('subsec'))
            ON CONFLICT(id) DO UPDATE SET
                url=excluded.url, status=excluded.status,
                stage=excluded.stage, error=excluded.error,
                updated_at=excluded.updated_at
            """,
            (job_id, url, status, stage, error),
        )
        self._conn.commit()

    def delete_job(self, job_id: str) -> None:
        """Xoá job khỏi sổ cái. Giữ bảng `seen` để lần quét sau không hiện lại."""
        self._conn.execute("DELETE FROM stage_runs WHERE job_id = ?", (job_id,))
        self._conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        self._conn.commit()

    def get_job(self, job_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        return dict(row) if row else None

    def list_jobs(self, status: str | None = None) -> list[dict]:
        if status is None:
            rows = self._conn.execute(
                "SELECT * FROM jobs ORDER BY updated_at DESC"
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM jobs WHERE status = ? ORDER BY updated_at DESC",
                (status,),
            ).fetchall()
        return [dict(r) for r in rows]

    def record_stage_run(
        self,
        job_id: str,
        stage: str,
        started_at: float,
        finished_at: float,
        ok: bool,
        error: str | None = None,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO stage_runs
                (job_id, stage, started_at, finished_at, duration_ms, ok, error)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                stage,
                started_at,
                finished_at,
                round((finished_at - started_at) * 1000),
                1 if ok else 0,
                error,
            ),
        )
        self._conn.commit()

    def stage_durations(self, stage: str | None = None) -> list[dict]:
        if stage is None:
            rows = self._conn.execute(
                "SELECT * FROM stage_runs ORDER BY id"
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM stage_runs WHERE stage = ? ORDER BY id", (stage,)
            ).fetchall()
        return [dict(r) for r in rows]

    def mark_seen(
        self, platform: str, video_id: str, phash: str | None = None
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO seen (platform, video_id, phash) VALUES (?, ?, ?)
            ON CONFLICT(platform, video_id) DO UPDATE SET phash=excluded.phash
            """,
            (platform, video_id, phash),
        )
        self._conn.commit()

    # --- hàng chờ: video đánh dấu để làm sau, chưa tạo job ---------------------

    def save_video(self, platform: str, video_id: str, data: dict) -> None:
        """Lưu lại thì giữ nguyên thời điểm lưu đầu, chỉ cập nhật số liệu."""
        import json

        self._conn.execute(
            """
            INSERT INTO saved_videos (platform, video_id, data) VALUES (?, ?, ?)
            ON CONFLICT(platform, video_id) DO UPDATE SET data=excluded.data
            """,
            (platform, video_id, json.dumps(data, ensure_ascii=False)),
        )
        self._conn.commit()

    def unsave_video(self, platform: str, video_id: str) -> None:
        self._conn.execute(
            "DELETE FROM saved_videos WHERE platform = ? AND video_id = ?",
            (platform, video_id),
        )
        self._conn.commit()

    def saved_videos(self, platform: str | None = None) -> list[dict]:
        """Cũ nhất trước: hàng chờ làm dần theo thứ tự đã lưu."""
        import json

        sql = "SELECT * FROM saved_videos"
        args: tuple = ()
        if platform:
            sql += " WHERE platform = ?"
            args = (platform,)
        rows = self._conn.execute(sql + " ORDER BY saved_at, rowid", args).fetchall()
        out = []
        for r in rows:
            data = json.loads(r["data"])
            data.update(platform=r["platform"], video_id=r["video_id"], saved_at=r["saved_at"])
            out.append(data)
        return out

    def saved_keys(self) -> set[tuple[str, str]]:
        rows = self._conn.execute("SELECT platform, video_id FROM saved_videos").fetchall()
        return {(r["platform"], r["video_id"]) for r in rows}

    def is_seen(self, platform: str, video_id: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM seen WHERE platform = ? AND video_id = ?",
            (platform, video_id),
        ).fetchone()
        return row is not None

    def stage_summary(self) -> list[dict]:
        """Thống kê thời gian từng stage, để lệnh `benchmark` đo máy hiện tại."""
        rows = self._conn.execute(
            """
            SELECT stage,
                   COUNT(*)            AS runs,
                   AVG(duration_ms)    AS avg_ms,
                   MIN(duration_ms)    AS min_ms,
                   MAX(duration_ms)    AS max_ms,
                   SUM(ok = 0)         AS failures
            FROM stage_runs
            GROUP BY stage
            ORDER BY avg_ms DESC
            """
        ).fetchall()
        return [dict(r) for r in rows]
