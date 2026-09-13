"""FastAPI + Jinja2 + JS thuần. Không có bước build, không có node_modules.

Chỉ đọc ghi trạng thái job; mọi logic xử lý nằm ở `core` và `stages`.
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from reup.adapters.crawl import DiscoverError
from reup.config import PLATFORMS, load_config
from reup.core.job import create_job, load_job
from reup.core.store import Store
from reup.web import service
from reup.web.runner import BackgroundRunner

HERE = Path(__file__).parent


def create_app(config_path: Path, jobs_dir: Path, db_path: Path) -> FastAPI:
    app = FastAPI(title="reup")
    templates = Jinja2Templates(directory=str(HERE / "templates"))
    templates.env.filters["duration"] = service.duration_label
    templates.env.filters["views"] = service.view_label
    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")

    app.state.config_path = Path(config_path)
    app.state.jobs_dir = Path(jobs_dir)
    app.state.db_path = Path(db_path)
    app.state.runner = BackgroundRunner(app.state.config_path, app.state.jobs_dir, app.state.db_path)

    def store() -> Store:
        s = Store(app.state.db_path)
        s.init_schema()
        return s

    def cfg():
        return load_config(app.state.config_path)

    def job_or_404(job_id: str):
        try:
            return load_job(app.state.jobs_dir, job_id)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail=f"không có job {job_id}")

    @app.get("/", response_class=HTMLResponse)
    def queue(request: Request, status: str | None = None):
        s = store()
        c = cfg()
        try:
            rows = service.list_jobs(s, status, c)
        finally:
            s.close()
        return templates.TemplateResponse(
            request,
            "queue.html",
            {
                "rows": rows,
                "status": status or "",
                "running": app.state.runner.live(),
                "tab": "queue",
                "title": "Hàng đợi",
            },
        )

    @app.get("/api/progress")
    def all_progress():
        s = store()
        c = cfg()
        try:
            jobs = s.list_jobs()
            live_running = app.state.runner.live()
        finally:
            s.close()
        out = {}
        for r in jobs:
            pct, label = service.calculate_progress(r["status"], r["stage"], c)
            out[r["id"]] = {
                "id": r["id"],
                "status": r["status"],
                "stage": r["stage"] or "-",
                "stage_label": label,
                "percent": pct,
                "is_running": r["id"] in live_running,
                "error": r["error"] or "",
            }
        return out

    @app.post("/jobs")
    def add_job(url: str = Form(...), lang: str = Form("auto")):
        job = create_job(app.state.jobs_dir, url.strip(), lang)
        s = store()
        try:
            s.upsert_job(job.id, job.source_url, "pending")
        finally:
            s.close()
        app.state.runner.start(job.id)
        return RedirectResponse(f"/jobs/{job.id}", status_code=303)

    @app.post("/jobs/{job_id}/run")
    def run(job_id: str):
        """Chạy job tới chốt gần nhất. Bấm lại lúc đang chạy là không làm gì."""
        job_or_404(job_id)
        app.state.runner.start(job_id)
        return RedirectResponse(f"/jobs/{job_id}", status_code=303)

    @app.post("/run-all")
    def run_all():
        """Chạy mọi job đang chờ hoặc đã hỏng, song song theo profile.concurrency."""
        s = store()
        try:
            ids = [r["id"] for r in s.list_jobs("pending") + s.list_jobs("failed")]
        finally:
            s.close()
        for job_id in ids:
            app.state.runner.start(job_id)
        return RedirectResponse("/", status_code=303)

    @app.get("/discover", response_class=HTMLResponse)
    def discover(
        request: Request,
        platform: str = "youtube",
        q: str = "",
        limit: int = 0,
        sort: str = "",
        hide_seen: int = 0,
        run: int = 0,
    ):
        """Tab quét nguồn. Mặc định KHÔNG quét — mở tab là mở ngay, không chờ.

        `run=1` (nút "Quét") mới thật sự gọi yt-dlp: mỗi lần quét mất vài giây
        tới vài chục giây, nên để nó nổ mỗi lần đổi tab thì tab nào cũng treo.
        """
        if platform not in PLATFORMS:
            raise HTTPException(
                status_code=404, detail=f"không có nền tảng {platform!r}"
            )
        c = cfg()
        opts = c.discover.for_platform(platform)
        result, error = None, ""
        if run:
            s = store()
            try:
                result = service.discover(
                    s, c, platform, q, limit or opts.limit, sort, bool(hide_seen)
                )
            except (DiscoverError, ValueError) as exc:
                error = str(exc)
            finally:
                s.close()
        return templates.TemplateResponse(
            request,
            "discover.html",
            {
                "platforms": [
                    {"id": p, "label": service.PLATFORM_LABELS[p]} for p in PLATFORMS
                ],
                "platform": platform,
                "tab": "discover",
                "q": q or opts.query,
                "limit": limit or opts.limit,
                "ran": bool(run),
                "result": result,
                "rows": result.rows if result else [],
                "sort": sort,
                "sorts": [
                    {"id": k, "label": v[0]} for k, v in service.SORTS.items()
                ],
                "hide_seen": bool(hide_seen),
                "error": error,
                "title": f"Quét {service.PLATFORM_LABELS[platform]}",
            },
        )

    @app.post("/discover/pick")
    def pick(
        url: str = Form(...),
        lang: str = Form("auto"),
        platform: str = Form(""),
        video_id: str = Form(""),
    ):
        """Chọn một video từ tab quét: tạo job, đánh dấu đã xử lý, và chạy luôn.

        Đánh dấu `seen` ở đây chứ không đợi chạy xong, để lần quét sau không
        hiện lại đúng video vừa chọn.

        Chạy luôn vì "chọn video này" nghĩa là "làm video này": tạo job rồi bắt
        người dùng nhảy ra terminal gõ `reup run` là cắt đôi một thao tác duy nhất.
        """
        job = create_job(app.state.jobs_dir, url.strip(), lang)
        s = store()
        try:
            s.upsert_job(job.id, job.source_url, "pending")
            if platform and video_id:
                s.mark_seen(platform, video_id)
        finally:
            s.close()
        app.state.runner.start(job.id)
        return RedirectResponse(f"/jobs/{job.id}", status_code=303)

    @app.get("/jobs/{job_id}", response_class=HTMLResponse)
    def review(request: Request, job_id: str):
        job = job_or_404(job_id)
        s = store()
        c = cfg()
        try:
            row = s.get_job(job_id)
        finally:
            s.close()
        progress = service.get_job_progress(job, row, c)
        return templates.TemplateResponse(
            request,
            "review.html",
            {
                "job": job,
                "row": row,
                "rows": service.review_rows(job),
                "voices": service.voices_for(c),
                "voice": service.pick_voice(job, c),
                "has_video": job.final_mp4.exists(),
                "gate_a": job.gate_approved("a"),
                "gate_b": job.gate_approved("b"),
                "running": app.state.runner.is_running(job_id),
                "progress": progress,
                "title": f"Duyệt {job_id}",
            },
        )

    @app.get("/jobs/{job_id}/progress")
    def job_progress_api(job_id: str):
        job = job_or_404(job_id)
        s = store()
        c = cfg()
        try:
            row = s.get_job(job_id)
            is_running = app.state.runner.is_running(job_id)
        finally:
            s.close()
        prog = service.get_job_progress(job, row, c)
        prog["is_running"] = is_running
        return prog

    @app.post("/jobs/{job_id}/segments")
    async def save_segments(job_id: str, request: Request):
        job = job_or_404(job_id)
        payload = await request.json()
        edits = {int(k): v for k, v in (payload.get("segments") or {}).items()}
        changed = service.save_edits(job, edits)
        return {"changed": changed}

    @app.post("/jobs/{job_id}/voice")
    def set_voice(job_id: str, voice: str = Form(...)):
        job = job_or_404(job_id)
        try:
            changed = service.set_voice(job, voice, cfg())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"voice": voice, "changed": changed}

    @app.post("/jobs/{job_id}/approve")
    def approve(job_id: str, gate: str = Form("a")):
        job = job_or_404(job_id)
        s = store()
        try:
            row = s.get_job(job_id)
            if row is None:
                raise HTTPException(status_code=404, detail="không có job")
            blocking = (row["stage"] or "").removeprefix("gate_") or gate
            job.approve_gate(blocking)
            s.upsert_job(job_id, row["url"], "pending", stage=row["stage"])
        finally:
            s.close()
        app.state.runner.start(job_id)
        return RedirectResponse(f"/jobs/{job_id}", status_code=303)

    @app.post("/jobs/{job_id}/rerender")
    def rerender(job_id: str):
        job = job_or_404(job_id)
        for path in (job.dub_wav, job.final_mp4, job.sub_ass, job.meta_json):
            if path.exists():
                path.unlink()
        c = cfg()
        out_mp4 = Path(c.review.output_dir) / f"{job.id}.mp4"
        if out_mp4.exists():
            out_mp4.unlink()
        out_json = Path(c.review.output_dir) / f"{job.id}.json"
        if out_json.exists():
            out_json.unlink()
        s = store()
        try:
            row = s.get_job(job_id)
            url = row["url"] if row else job.source_url
            s.upsert_job(job_id, url, "pending", stage=None)
        finally:
            s.close()
        app.state.runner.start(job_id)
        return RedirectResponse(f"/jobs/{job_id}", status_code=303)

    @app.post("/jobs/{job_id}/reveal")
    def reveal(job_id: str):
        job = job_or_404(job_id)
        c = cfg()
        out_mp4 = Path(c.review.output_dir) / f"{job.id}.mp4"
        target = None
        if out_mp4.exists():
            target = out_mp4
        elif job.final_mp4.exists():
            target = job.final_mp4
        elif job.root.exists():
            target = job.root

        if target and target.exists():
            import subprocess

            resolved = str(target.resolve())
            subprocess.run(["open", "-R", resolved])
            try:
                subprocess.run(
                    ["osascript", "-e", 'tell application "Finder" to activate'],
                    timeout=2.0,
                )
            except Exception:
                pass
            return {"ok": True, "path": resolved}
        raise HTTPException(status_code=404, detail="Chưa có file thành phẩm để mở")

    @app.get("/jobs/{job_id}/source")
    def source_video(job_id: str):
        job = job_or_404(job_id)
        if not job.source_video.exists():
            raise HTTPException(status_code=404, detail="chưa tải video nguồn")
        return FileResponse(job.source_video, media_type="video/mp4")

    @app.get("/jobs/{job_id}/final")
    def final_video(job_id: str):
        job = job_or_404(job_id)
        if not job.final_mp4.exists():
            raise HTTPException(status_code=404, detail="chưa render")
        return FileResponse(job.final_mp4, media_type="video/mp4")

    @app.get("/jobs/{job_id}/audio/{seg_id}")
    def segment_audio(job_id: str, seg_id: int):
        """Nghe thử một câu. Chưa chạy tts thì tổng hợp đúng câu đó (spec §8.2)."""
        job = job_or_404(job_id)
        try:
            path = service.preview_audio(job, cfg(), seg_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:  # engine TTS lỗi: nói rõ chứ không trả 404 mơ hồ
            raise HTTPException(
                status_code=502, detail=f"TTS lỗi: {exc}"
            ) from exc
        return FileResponse(path, media_type="audio/wav")

    @app.get("/jobs/{job_id}/meta")
    def meta(job_id: str):
        job = job_or_404(job_id)
        if not job.meta_json.exists():
            raise HTTPException(status_code=404, detail="chưa có metadata")
        return json.loads(job.meta_json.read_text(encoding="utf-8"))

    return app
