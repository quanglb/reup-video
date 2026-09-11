"""Giao diện dòng lệnh. Phase 1: add, run, redo, status."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from reup.config import load_config
from reup.core.job import create_job, load_job
from reup.core.runner import run_job
from reup.core.store import Store
from reup.stages import PHASE2_STAGES


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="reup", description="Pipeline dịch và lồng tiếng video ngắn")
    p.add_argument("--config", type=Path, default=Path("config.toml"))
    p.add_argument("--jobs-dir", type=Path, default=Path("jobs"))
    p.add_argument("--db", type=Path, default=Path("reup.db"))
    sub = p.add_subparsers(dest="command", required=True)

    add = sub.add_parser("add", help="tạo job từ một link")
    add.add_argument("url")
    add.add_argument("--lang", default="auto", help="ngôn ngữ nguồn: zh, en, hoặc auto")

    run_cmd = sub.add_parser("run", help="chạy job tới stage cuối")
    run_cmd.add_argument("job_id")

    redo = sub.add_parser("redo", help="xóa artifact từ một stage trở đi để chạy lại")
    redo.add_argument("job_id")
    redo.add_argument("--from", dest="from_stage", required=True)

    sub.add_parser("status", help="liệt kê job")
    return p


def _cmd_add(args, store: Store) -> int:
    job = create_job(args.jobs_dir, args.url, args.lang)
    store.upsert_job(job.id, job.source_url, "pending")
    print(job.id)
    return 0


def _cmd_run(args, store: Store) -> int:
    try:
        job = load_job(args.jobs_dir, args.job_id)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    cfg = load_config(args.config)
    status = run_job(job, cfg, store, PHASE2_STAGES)
    if status == "failed":
        row = store.get_job(job.id)
        print(f"job {job.id} hỏng ở stage {row['stage']}: {row['error']}", file=sys.stderr)
        return 1
    print(job.final_mp4)
    return 0


def _cmd_redo(args, store: Store) -> int:
    names = [s.name for s in PHASE2_STAGES]
    if args.from_stage not in names:
        print(
            f"không có stage {args.from_stage!r}. Có: {', '.join(names)}",
            file=sys.stderr,
        )
        return 2
    try:
        job = load_job(args.jobs_dir, args.job_id)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    start = names.index(args.from_stage)
    for spec in PHASE2_STAGES[start:]:
        for rel in spec.produces:
            target = job.root / rel
            if target.exists():
                target.unlink()
    store.upsert_job(job.id, job.source_url, "pending", stage=None)
    print(f"đã xóa artifact từ stage {args.from_stage} trở đi")
    return 0


def _cmd_status(args, store: Store) -> int:
    rows = store.list_jobs()
    if not rows:
        print("chưa có job nào")
        return 0
    for row in rows:
        stage = row["stage"] or "-"
        print(f"{row['id']}  {row['status']:<13} {stage:<10} {row['url']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    store = Store(args.db)
    store.init_schema()
    try:
        return {
            "add": _cmd_add,
            "run": _cmd_run,
            "redo": _cmd_redo,
            "status": _cmd_status,
        }[args.command](args, store)
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
