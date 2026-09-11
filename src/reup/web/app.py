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

from reup.config import load_config
from reup.core.job import create_job, load_job
from reup.core.store import Store
from reup.web import service

HERE = Path(__file__).parent


def create_app(config_path: Path, jobs_dir: Path, db_path: Path) -> FastAPI:
    app = FastAPI(title="reup")
    templates = Jinja2Templates(directory=str(HERE / "templates"))
    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")

    app.state.config_path = Path(config_path)
    app.state.jobs_dir = Path(jobs_dir)
    app.state.db_path = Path(db_path)

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
        try:
            rows = service.list_jobs(s, status)
        finally:
            s.close()
        return templates.TemplateResponse(
            request,
            "queue.html",
            {"rows": rows, "status": status or "", "title": "Hàng đợi"},
        )

    @app.post("/jobs")
    def add_job(url: str = Form(...), lang: str = Form("auto")):
        job = create_job(app.state.jobs_dir, url.strip(), lang)
        s = store()
        try:
            s.upsert_job(job.id, job.source_url, "pending")
        finally:
            s.close()
        return RedirectResponse(f"/jobs/{job.id}", status_code=303)

    @app.get("/jobs/{job_id}", response_class=HTMLResponse)
    def review(request: Request, job_id: str):
        job = job_or_404(job_id)
        s = store()
        try:
            row = s.get_job(job_id)
        finally:
            s.close()
        return templates.TemplateResponse(
            request,
            "review.html",
            {
                "job": job,
                "row": row,
                "rows": service.review_rows(job),
                "voices": service.voices_for(cfg()),
                "has_video": job.final_mp4.exists(),
                "gate_a": job.gate_approved("a"),
                "gate_b": job.gate_approved("b"),
                "title": f"Duyệt {job_id}",
            },
        )

    @app.post("/jobs/{job_id}/segments")
    async def save_segments(job_id: str, request: Request):
        job = job_or_404(job_id)
        payload = await request.json()
        edits = {int(k): v for k, v in (payload.get("segments") or {}).items()}
        changed = service.save_edits(job, edits)
        return {"changed": changed}

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
        return RedirectResponse(f"/jobs/{job_id}", status_code=303)

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
        job = job_or_404(job_id)
        path = job.tts_segment(seg_id)
        if not path.exists():
            raise HTTPException(status_code=404, detail="chưa có giọng đọc cho câu này")
        return FileResponse(path, media_type="audio/wav")

    @app.get("/jobs/{job_id}/meta")
    def meta(job_id: str):
        job = job_or_404(job_id)
        if not job.meta_json.exists():
            raise HTTPException(status_code=404, detail="chưa có metadata")
        return json.loads(job.meta_json.read_text(encoding="utf-8"))

    return app
