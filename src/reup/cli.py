"""Giao diện dòng lệnh. Phase 1: add, run, redo, status."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from reup.config import PLATFORMS, load_config
from reup.dotenv import load_dotenv
from reup.core.job import create_job, load_job
from reup.core.runner import run_job, run_jobs
from reup.core.store import Store
from reup.stages import stages_for


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="reup", description="Pipeline dịch và lồng tiếng video ngắn")
    p.add_argument("--config", type=Path, default=Path("config.toml"))
    p.add_argument("--jobs-dir", type=Path, default=Path("jobs"))
    p.add_argument("--db", type=Path, default=Path("reup.db"))
    p.add_argument("--env", type=Path, default=Path(".env"))
    sub = p.add_subparsers(dest="command", required=True)

    add = sub.add_parser("add", help="tạo job từ một link")
    add.add_argument("url")
    add.add_argument("--lang", default="auto", help="ngôn ngữ nguồn: zh, en, hoặc auto")

    disc = sub.add_parser("discover", help="quét video ngắn đang thịnh hành")
    disc.add_argument("--platform", default="youtube", choices=list(PLATFORMS))
    disc.add_argument("--limit", type=int, default=10)
    disc.add_argument("--region", default="VN")
    disc.add_argument(
        "--query", "--hashtag", dest="query", default="",
        help=(
            "nguồn quét. YouTube: chữ trần là tìm kiếm, #tag là hashtag, @tên là kênh, "
            "hoặc URL. Trống thì lấy từ [discover.<nền tảng>]"
        ),
    )
    disc.add_argument("--lang", default="auto")
    disc.add_argument("--add", action="store_true", help="tạo job luôn, không chỉ liệt kê")

    run_cmd = sub.add_parser("run", help="chạy job tới stage cuối")
    run_cmd.add_argument("job_id", nargs="?")
    run_cmd.add_argument(
        "--all", action="store_true",
        help="chạy mọi job đang chờ, song song theo profile.concurrency",
    )

    redo = sub.add_parser("redo", help="xóa artifact từ một stage trở đi để chạy lại")
    redo.add_argument("job_id")
    redo.add_argument("--from", dest="from_stage", required=True)

    approve = sub.add_parser("approve", help="duyệt một chốt để job chạy tiếp")
    approve.add_argument("job_id")
    approve.add_argument("--gate", choices=["a", "b"], default="a")

    sub.add_parser("status", help="liệt kê job")
    sub.add_parser("benchmark", help="đo thời gian từng stage trên máy này")

    web = sub.add_parser("web", help="bật giao diện duyệt")
    web.add_argument("--host", default="127.0.0.1")
    web.add_argument("--port", type=int, default=8765)
    return p


def _cmd_add(args, store: Store) -> int:
    job = create_job(args.jobs_dir, args.url, args.lang)
    store.upsert_job(job.id, job.source_url, "pending")
    print(job.id)
    return 0


def _cmd_discover(args, store: Store) -> int:
    from reup.adapters.crawl import DiscoverError
    from reup.adapters.registry import make_source

    try:
        source = make_source(load_config(args.config), args.platform, args.query)
        found = source.list_trending(args.region, args.limit)
    except (DiscoverError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    fresh = [c for c in found if not store.is_seen(c.platform, c.video_id)]
    if not fresh:
        print(f"quét được {len(found)} video, tất cả đều đã xử lý rồi")
        return 0

    for c in fresh:
        mark = ""
        if args.add:
            job = create_job(args.jobs_dir, c.url, args.lang)
            store.upsert_job(job.id, job.source_url, "pending")
            store.mark_seen(c.platform, c.video_id)
            mark = f"  -> {job.id}"
        print(f"{c.duration_ms // 1000:>4}s  {c.title[:56]:<58}{c.url}{mark}")

    if not args.add:
        print(f"\n{len(fresh)} video mới. Thêm `--add` để tạo job.")
    return 0


def _cmd_run_all(args, store: Store) -> int:
    cfg = load_config(args.config)
    rows = store.list_jobs("pending") + store.list_jobs("failed")
    jobs = []
    for row in rows:
        try:
            jobs.append(load_job(args.jobs_dir, row["id"]))
        except FileNotFoundError:
            # Thư mục job bị xoá tay nhưng dòng trong sổ cái còn — bỏ qua, đừng
            # để một dòng mồ côi chặn cả loạt.
            print(f"bỏ qua {row['id']}: không còn thư mục job", file=sys.stderr)

    if not jobs:
        print("không có job nào đang chờ")
        return 0

    workers = cfg.profile.concurrency
    print(f"chạy {len(jobs)} job, {workers} luồng")
    results = run_jobs(jobs, cfg, args.db, stages_for(cfg), workers)

    for job_id, status in results.items():
        print(f"{status:<13} {job_id}")
    failed = sum(1 for s in results.values() if s == "failed")
    if failed:
        print(f"\n{failed} job hỏng. Xem chi tiết: reup status", file=sys.stderr)
        return 1
    return 0


def _cmd_run(args, store: Store) -> int:
    if args.all:
        if args.job_id:
            print("chọn một trong hai: `run <job_id>` hoặc `run --all`", file=sys.stderr)
            return 2
        return _cmd_run_all(args, store)
    if not args.job_id:
        print("thiếu job_id. Chạy cả loạt thì thêm `--all`", file=sys.stderr)
        return 2
    try:
        job = load_job(args.jobs_dir, args.job_id)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    cfg = load_config(args.config)
    status = run_job(job, cfg, store, stages_for(cfg))
    if status == "failed":
        row = store.get_job(job.id)
        print(f"job {job.id} hỏng ở stage {row['stage']}: {row['error']}", file=sys.stderr)
        return 1
    if status == "needs_review":
        gate = (store.get_job(job.id)["stage"] or "gate_?").removeprefix("gate_")
        print(
            f"job {job.id} đang chờ duyệt chốt {gate.upper()}.\n"
            f"Xem và sửa: reup web   |   duyệt nhanh: reup approve {job.id}"
        )
        return 0
    print(job.final_mp4)
    return 0


def _cmd_redo(args, store: Store) -> int:
    stages = stages_for(load_config(args.config))
    names = [s.name for s in stages]
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
    for spec in stages[start:]:
        for rel in spec.produces:
            target = job.root / rel
            if target.exists():
                target.unlink()
    store.upsert_job(job.id, job.source_url, "pending", stage=None)
    print(f"đã xóa artifact từ stage {args.from_stage} trở đi")
    return 0


def _cmd_approve(args, store: Store) -> int:
    row = store.get_job(args.job_id)
    if row is None:
        print(f"không có job {args.job_id!r}", file=sys.stderr)
        return 1
    if row["status"] != "needs_review":
        print(
            f"job {args.job_id} đang ở trạng thái {row['status']!r}, "
            "không chờ duyệt",
            file=sys.stderr,
        )
        return 1
    try:
        job = load_job(args.jobs_dir, args.job_id)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    # Chốt đang chặn mới là chốt được duyệt; --gate chỉ để người dùng nói rõ ý.
    gate = (row["stage"] or "").removeprefix("gate_") or args.gate
    job.approve_gate(gate)
    store.upsert_job(args.job_id, row["url"], "pending", stage=row["stage"])
    print(f"đã duyệt chốt {gate.upper()} cho {args.job_id}")
    return 0


def _cmd_benchmark(args, store: Store) -> int:
    rows = store.stage_summary()
    if not rows:
        print("chưa có lần chạy nào để đo")
        return 0
    print(f"{'stage':<11}{'lần':>5}{'trung bình':>12}{'nhanh nhất':>12}"
          f"{'chậm nhất':>12}{'hỏng':>6}")
    for r in rows:
        print(
            f"{r['stage']:<11}{r['runs']:>5}{r['avg_ms'] / 1000:>11.1f}s"
            f"{r['min_ms'] / 1000:>11.1f}s{r['max_ms'] / 1000:>11.1f}s"
            f"{r['failures']:>6}"
        )
    total = sum(r["avg_ms"] for r in rows) / 1000
    print(f"{'TỔNG':<11}{'':>5}{total:>11.1f}s  (một video trung bình)")
    return 0


def _cmd_web(args, store: Store) -> int:
    try:
        import uvicorn
    except ImportError:
        print(
            "thiếu package web. Cài bằng "
            "`uv pip install fastapi uvicorn jinja2 python-multipart`",
            file=sys.stderr,
        )
        return 1
    from reup.web.app import create_app

    app = create_app(args.config, args.jobs_dir, args.db)
    print(f"giao diện ở http://{args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
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
    # Khoá API đọc từ .env (đã trong .gitignore) để không phải gõ lại mỗi phiên.
    load_dotenv(args.env)
    store = Store(args.db)
    store.init_schema()
    try:
        return {
            "add": _cmd_add,
            "run": _cmd_run,
            "redo": _cmd_redo,
            "status": _cmd_status,
            "approve": _cmd_approve,
            "benchmark": _cmd_benchmark,
            "web": _cmd_web,
            "discover": _cmd_discover,
        }[args.command](args, store)
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
