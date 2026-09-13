"""Chọn stage kế tiếp, chạy nó, ghi nhật ký. Không biết stage nào làm gì."""
from __future__ import annotations

import json
import os
import time
import traceback
from pathlib import Path

from reup.config import Config
from reup.core.job import Job
from reup.core.stage import StageSpec
from reup.core.store import Store


def atomic_write(path: Path, data: bytes | str) -> None:
    """Ghi ra file tạm rồi đổi tên, để stage chết giữa chừng không để lại rác."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    mode, encoding = ("wb", None) if isinstance(data, bytes) else ("w", "utf-8")
    with open(tmp, mode, encoding=encoding) as fh:
        fh.write(data)
    os.replace(tmp, path)


def artifacts_present(job: Job, spec: StageSpec) -> bool:
    for rel in spec.produces:
        p = job.root / rel
        if not p.exists():
            return False
        if p.is_file() and p.stat().st_size == 0:
            return False
    return True


def next_stage(job: Job, stages: list[StageSpec]) -> StageSpec | None:
    for spec in stages:
        if not artifacts_present(job, spec):
            return spec
    return None


def _append_log(job: Job, entry: dict) -> None:
    with open(job.log_jsonl, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def run_stage(job: Job, cfg: Config, spec: StageSpec, store: Store) -> None:
    started = time.time()
    store.upsert_job(job.id, job.source_url, "running", stage=spec.name)
    try:
        spec.run(job, cfg)
        missing = [rel for rel in spec.produces if not (job.root / rel).exists()]
        if missing:
            raise RuntimeError(
                f"stage {spec.name!r} chạy xong nhưng thiếu artifact: {missing}"
            )
    except Exception as exc:
        finished = time.time()
        detail = traceback.format_exc()
        store.record_stage_run(
            job.id, spec.name, started, finished, ok=False, error=str(exc)
        )
        _append_log(
            job,
            {"stage": spec.name, "ok": False, "started": started,
             "finished": finished, "error": detail},
        )
        raise
    finished = time.time()
    store.record_stage_run(job.id, spec.name, started, finished, ok=True)
    _append_log(
        job, {"stage": spec.name, "ok": True, "started": started, "finished": finished}
    )


def blocking_gate(job: Job, stages: list[StageSpec], cfg: Config) -> str | None:
    """Chốt gần nhất đã tới nơi mà chưa được duyệt.

    Một chốt "tới nơi" khi stage đứng trước nó đã sinh xong artifact. Xét theo
    artifact chứ không theo biến đếm, để `reup run` chạy lại vẫn dừng đúng chỗ.
    """
    for spec in stages:
        if not artifacts_present(job, spec):
            return None  # chưa chạy tới đây
        if spec.gate is None:
            continue
        if spec.gate == "a" and (cfg.review.auto_approve_a or cfg.review.auto_approve):
            continue
        if spec.gate == "b" and (cfg.review.auto_approve_b or cfg.review.auto_approve):
            continue
        if not job.gate_approved(spec.gate):
            return spec.gate
    return None


def run_job(
    job: Job, cfg: Config, store: Store, stages: list[StageSpec], notifier=None
) -> str:
    """Chạy tới chốt hoặc tới cuối. `notifier` trống thì dựng từ [notify].

    Chỉ báo khi lần gọi này thật sự chạy stage nào đó: bấm "chạy" một job đã
    xong hay đang kẹt ở chốt thì không gửi lại tin cũ.
    """
    if notifier is None:
        from reup.notify import from_config

        notifier = from_config(cfg)
    ran = 0
    while True:
        gate = blocking_gate(job, stages, cfg)
        if gate is not None:
            store.upsert_job(
                job.id, job.source_url, "needs_review", stage=f"gate_{gate}"
            )
            if ran:
                notifier.job_needs_review(job, gate)
            return "needs_review"

        spec = next_stage(job, stages)
        if spec is None:
            break
        if not ran:
            notifier.job_started(job, len(stages))
        started = time.time()
        try:
            run_stage(job, cfg, spec, store)
        except Exception as exc:
            store.upsert_job(
                job.id, job.source_url, "failed", stage=spec.name, error=str(exc)
            )
            notifier.job_failed(job, spec.name, str(exc))
            return "failed"
        ran += 1
        done = sum(1 for s in stages if artifacts_present(job, s))
        notifier.stage_done(job, spec.name, time.time() - started, done, len(stages))

    store.upsert_job(job.id, job.source_url, "done", stage=None)
    if ran:
        notifier.job_done(job, cfg.review.output_dir)
    return "done"


def run_jobs(
    jobs: list[Job],
    cfg: Config,
    db_path: Path,
    stages: list[StageSpec],
    workers: int = 1,
) -> dict[str, str]:
    """Chạy cả loạt job, tối đa `workers` cùng lúc. Trả {job_id: trạng thái}.

    `discover --add` tạo hàng chục job một lúc, nên phải có đường chạy cả loạt;
    `profile.concurrency` là chỗ khai chạy mấy job song song (1 trên Air, 2 trên
    Studio — spec §9, R3).

    Mỗi worker mở `Store` riêng: `sqlite3.Connection` không dùng chung giữa
    thread được. Dữ liệu vẫn là một file, WAL lo phần ghi xen kẽ.

    Job hỏng không làm đổ cả loạt — `run_job` trả `"failed"` chứ không ném.
    """
    workers = max(1, int(workers))
    results: dict[str, str] = {}

    def one(job: Job) -> tuple[str, str]:
        store = Store(db_path)
        try:
            return job.id, run_job(job, cfg, store, stages)
        finally:
            store.close()

    if workers == 1 or len(jobs) <= 1:
        for job in jobs:
            job_id, status = one(job)
            results[job_id] = status
        return results

    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for job_id, status in pool.map(one, jobs):
            results[job_id] = status
    return results
