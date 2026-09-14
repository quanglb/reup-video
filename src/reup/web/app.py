"""FastAPI + Jinja2 + JS thuần. Không có bước build, không có node_modules.

Chỉ đọc ghi trạng thái job; mọi logic xử lý nằm ở `core` và `stages`.
"""
from __future__ import annotations

import hmac
import json
import os
import re
import time
from pathlib import Path
from urllib.parse import urlencode

from fastapi import Body, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from reup.adapters.crawl import DiscoverError
from reup.config import PLATFORMS, load_config
from reup.core.job import create_job, load_job
from reup.core.store import Store
from reup.web import auth, service
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

    app.state.password = os.environ.get("REUP_WEB_PASSWORD", "")
    if app.state.password:
        app.state.session_secret = auth.secret_for(
            app.state.password, explicit=os.environ.get("REUP_WEB_SECRET", "")
        )

        @app.middleware("http")
        async def require_login(request: Request, call_next):
            path = request.url.path
            if path == "/login" or path.startswith("/static/"):
                return await call_next(request)
            token = request.cookies.get(auth.COOKIE, "")
            if auth.verify_token(app.state.session_secret, token, time.time()):
                return await call_next(request)
            wants_page = (
                request.method == "GET"
                and "text/html" in request.headers.get("accept", "")
            )
            if wants_page:
                url = f"/login?{urlencode({'next': path})}"
                return RedirectResponse(url, status_code=303)
            return JSONResponse({"detail": "cần đăng nhập"}, status_code=401)

        def is_https(request: Request) -> bool:
            return (
                request.url.scheme == "https"
                or request.headers.get("x-forwarded-proto", "") == "https"
            )

        def render_login(request: Request, next_: str, error: str = "", status_code: int = 200):
            return templates.TemplateResponse(
                request,
                "login.html",
                {"next": auth.safe_next(next_), "error": error},
                status_code=status_code,
            )

        @app.get("/login", response_class=HTMLResponse)
        def login_form(request: Request, next: str = "/"):
            return render_login(request, next)

        @app.post("/login", response_class=HTMLResponse)
        def login_submit(
            request: Request, password: str = Form(...), next: str = Form("/")
        ):
            if not hmac.compare_digest(password.encode(), app.state.password.encode()):
                time.sleep(1)  # làm chậm dò mật khẩu
                return render_login(request, next, "Sai mật khẩu")
            token = auth.make_token(app.state.session_secret, time.time())
            resp = RedirectResponse(auth.safe_next(next), status_code=303)
            resp.set_cookie(
                auth.COOKIE, token, max_age=auth.MAX_AGE, httponly=True,
                samesite="lax", secure=is_https(request),
            )
            return resp

        @app.post("/logout")
        def logout(request: Request):
            resp = RedirectResponse("/login", status_code=303)
            resp.delete_cookie(auth.COOKIE)
            return resp

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
    def queue(
        request: Request, status: str | None = None, view: str = "", platform: str = ""
    ):
        """Danh sách dự án. `view=archived` là kho lưu trữ, còn lại là đang làm.

        Đếm trước khi lọc, để các nút lọc luôn hiện đủ số. Kết quả chia nhóm
        theo nền tảng.
        """
        s = store()
        c = cfg()
        try:
            every = service.list_jobs(s, None, c, app.state.jobs_dir)
        finally:
            s.close()
        archived = view == "archived"
        in_view = [r for r in every if r.archived == archived]
        by_status = [r for r in in_view if not status or r.status == status]
        rows = [r for r in by_status if not platform or r.platform == platform]
        order = ["youtube", "tiktok", "douyin", "bilibili", ""]
        groups = [
            {
                "key": key or "other",
                "label": service.PLATFORM_LABELS.get(key, key.capitalize() or "Khác"),
                "rows": [r for r in rows if r.platform == key],
            }
            for key in order + sorted({r.platform for r in rows} - set(order))
        ]
        platform_counts = {"": len(by_status)}
        for r in by_status:
            platform_counts[r.platform] = platform_counts.get(r.platform, 0) + 1
        return templates.TemplateResponse(
            request,
            "queue.html",
            {
                "rows": rows,
                "status": status or "",
                "view": "archived" if archived else "",
                "counts": service.status_counts(in_view),
                "platform": platform,
                "platform_counts": platform_counts,
                "groups": [g for g in groups if g["rows"]],
                "archived_count": sum(1 for r in every if r.archived),
                "statuses": service.STATUS_LABELS,
                "running": app.state.runner.live(),
                "tab": "queue",
                "title": "Lưu trữ" if archived else "Dự án",
            },
        )

    @app.get("/jobs/{job_id}/thumb")
    def job_thumb(job_id: str):
        job = job_or_404(job_id)
        try:
            path = service.thumbnail(job)
        except Exception as exc:  # chưa có video, hoặc ffmpeg không cắt được
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return FileResponse(path, media_type="image/jpeg")

    @app.post("/jobs/{job_id}/rename")
    def rename(job_id: str, name: str = Form("")):
        job = job_or_404(job_id)
        name = service.rename_job(job, name)
        title = service.job_details(app.state.jobs_dir, job_id).get("title") or job_id
        return {"name": name, "title": title}

    @app.post("/jobs/{job_id}/archive")
    def archive(job_id: str, archived: int = Form(1)):
        job = job_or_404(job_id)
        service.set_archived(job, bool(archived))
        return {"archived": bool(archived)}

    def delete_one(s: Store, c, job_id: str) -> None:
        """Xoá hẳn một job. Đang chạy thì từ chối: stage vẫn đang ghi vào thư mục đó."""
        if app.state.runner.is_running(job_id):
            raise HTTPException(status_code=409, detail="job đang chạy, chưa xoá được")
        try:
            job = load_job(app.state.jobs_dir, job_id)
        except FileNotFoundError:
            if s.get_job(job_id) is None:
                raise HTTPException(status_code=404, detail=f"không có job {job_id}")
            s.delete_job(job_id)  # chỉ còn dòng mồ côi trong sổ cái
            return
        service.delete_job(job, s, c)

    def delete_many(job_ids: list[str]) -> dict:
        """Xoá lần lượt; job nào đang chạy hoặc không còn thì bỏ qua, không dừng cả loạt."""
        s = store()
        c = cfg()
        deleted, skipped = [], []
        try:
            for job_id in dict.fromkeys(job_ids):
                try:
                    delete_one(s, c, job_id)
                    deleted.append(job_id)
                except HTTPException as exc:
                    skipped.append({"id": job_id, "reason": exc.detail})
        finally:
            s.close()
        return {"deleted": deleted, "skipped": skipped}

    @app.post("/jobs/{job_id}/delete")
    def delete(job_id: str):
        s = store()
        try:
            delete_one(s, cfg(), job_id)
        finally:
            s.close()
        return {"deleted": job_id}

    @app.post("/jobs/delete-many")
    def delete_selected(job_ids: list[str] = Form([])):
        return delete_many(job_ids)

    @app.post("/archive/empty")
    def empty_archive():
        """Dọn sạch Lưu trữ: xoá vĩnh viễn mọi job đã cất."""
        s = store()
        try:
            ids = [r.id for r in service.list_jobs(s, None, cfg(), app.state.jobs_dir, archived=True)]
        finally:
            s.close()
        return delete_many(ids)

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
        # Job đã cất vào lưu trữ là người dùng gác lại, đừng tự chạy.
        ids = [
            i for i in ids
            if not service.job_details(app.state.jobs_dir, i).get("archived")
        ]
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
        start: int = 1,
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
                    s, c, platform, q, limit or opts.limit, sort, bool(hide_seen),
                    start=start,
                )
            except (DiscoverError, ValueError) as exc:
                error = str(exc)
            finally:
                s.close()
        return render_discover(
            request, platform, q or opts.query, limit or opts.limit, sort,
            bool(hide_seen), bool(run), start, result, error,
        )

    def exports_dir() -> Path:
        # Cạnh thư mục jobs chứ không bên trong: jobs/ chỉ chứa thư mục job.
        return app.state.jobs_dir.parent / "douyin-exports"

    from reup.web.douyin_rescan import Rescanner

    app.state.douyin_rescanner = Rescanner(exports_dir())

    @app.post("/discover/douyin/import", response_class=HTMLResponse)
    async def douyin_import(
        request: Request,
        file: UploadFile = File(...),
        save: int = Form(0),
        name: str = Form(""),
    ):
        """Nạp file JSON xuất từ console Douyin, lưu lại, rồi mở kết quả.

        Lưu ra đĩa thay vì giữ trong bộ nhớ để URL kết quả (sắp theo like, đổi
        vị trí bắt đầu) bấm lại được mà không phải nạp lại file.
        """
        from reup.adapters.douyin_export import parse_export

        body = await file.read()
        c = cfg()
        opts = c.discover.for_platform("douyin")
        try:
            raw = json.loads(body.decode("utf-8"))
            parse_export(raw)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            return render_discover(
                request, "douyin", "", opts.limit, "", False, True, 1, None,
                f"file {file.filename or ''} không nạp được: {exc}",
                status_code=400,
            )
        from reup.web import douyin_channels

        out = douyin_channels.store_export(exports_dir(), body, raw)
        if save:
            douyin_channels.save_channel(
                exports_dir(), douyin_channels.channel_id(raw, out), name
            )
        query = urlencode({"platform": "douyin", "run": 1, "q": str(out.resolve())})
        return RedirectResponse(f"/discover?{query}", status_code=303)

    @app.post("/discover/douyin/channels")
    def douyin_save_channel(uid: str = Form(...), name: str = Form("")):
        """Lưu kênh từ một file đã nạp, hoặc đổi tên kênh đã lưu."""
        from reup.web import douyin_channels

        try:
            douyin_channels.save_channel(exports_dir(), uid, name)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return RedirectResponse("/discover?platform=douyin", status_code=303)

    @app.post("/discover/douyin/channels/delete")
    def douyin_remove_channel(uid: str = Form(...)):
        from reup.web import douyin_channels

        douyin_channels.remove_channel(exports_dir(), uid)
        return RedirectResponse("/discover?platform=douyin", status_code=303)

    @app.post("/discover/douyin/channels/rescan")
    def douyin_rescan(uid: str = Form(...)):
        """Nút Quét lại: mở kênh trong Chrome đã đăng nhập và xuất file mới.

        Chạy nền vì kênh vài trăm video mất 1–2 phút; UI hỏi tiến độ ở GET cùng đường.
        """
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", uid):
            raise HTTPException(status_code=400, detail="sec_uid không hợp lệ")
        try:
            return app.state.douyin_rescanner.start(uid)
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/discover/douyin/channels/rescan")
    def douyin_rescan_status(uid: str):
        return app.state.douyin_rescanner.status(uid)

    def douyin_suggestions() -> dict:
        from reup.adapters import douyin_export as dx
        from reup.web import douyin_channels

        return {
            "douyin_channels": douyin_channels.saved_channels(exports_dir()),
            "douyin_recent": douyin_channels.recent_unsaved(exports_dir()),
            "douyin_exports": douyin_channels.exports(exports_dir()),
            "douyin_topics": [
                {"q": t["q"], "label": t["vi"], "url": dx.search_url(t["q"])}
                for t in discover_tags("douyin")["tags_all"]
            ],
        }

    def render_discover(
        request, platform, q, limit, sort, hide_seen, ran, start, result, error,
        status_code: int = 200,
    ):
        return templates.TemplateResponse(
            request,
            "discover.html",
            {
                "platforms": [
                    {"id": p, "label": service.PLATFORM_LABELS[p]} for p in PLATFORMS
                ],
                "platform": platform,
                "tab": "discover",
                "q": q,
                "limit": limit,
                "start": start,
                "ran": ran,
                "result": result,
                "rows": result.rows if result else [],
                "sort": sort,
                "sorts": [
                    {"id": k, "label": v[0]} for k, v in service.SORTS.items()
                ],
                # Bấm đổi cách sắp ngay trên kết quả, giữ nguyên mọi tham số quét.
                "sort_links": [
                    {
                        "id": k,
                        "label": v[0],
                        "url": "/discover?" + urlencode({
                            "platform": platform, "run": 1, "q": q, "limit": limit,
                            "start": start, "sort": k, "hide_seen": int(bool(hide_seen)),
                        }),
                    }
                    for k, v in service.SORTS.items()
                ],
                "hide_seen": hide_seen,
                "error": error,
                **(douyin_suggestions() if platform == "douyin" else {}),
                "douyin_script": (
                    (HERE / "static" / "douyin_export.js").read_text(encoding="utf-8")
                    if platform == "douyin"
                    else ""
                ),
                **discover_tags(platform),
                "title": f"Quét {service.PLATFORM_LABELS[platform]}",
            },
            status_code=status_code,
        )

    def tags_path() -> Path:
        return app.state.jobs_dir.parent / "tags.json"

    def tag_llm():
        from reup.adapters.registry import make_llm

        return make_llm(cfg(), "export")

    def discover_tags(platform: str) -> dict:
        from reup.web import tags as tag_store

        every = tag_store.for_platform(tag_store.load(tags_path()), platform)
        return {
            "tags_all": every,
            "tags_topic": [t for t in every if t["group"] == "topic"],
            "tags_hashtag": [t for t in every if t["group"] == "hashtag"],
        }

    def render_tags(request, platform="", form=None, error="", status_code=200):
        from reup.web import tags as tag_store

        platform = platform if platform in tag_store.PLATFORMS else ""
        every = tag_store.load(tags_path())
        counts = {"": len(every)}
        for t in every:
            counts[t["platform"]] = counts.get(t["platform"], 0) + 1
        start = platform or "youtube"
        return templates.TemplateResponse(
            request,
            "tags.html",
            {
                "rows": [t for t in every if not platform or t["platform"] == platform],
                "platform": platform,
                "counts": counts,
                "platforms": tag_store.PLATFORMS,
                "platform_labels": service.PLATFORM_LABELS,
                "groups": tag_store.GROUPS,
                "langs": tag_store.LANGS,
                "form": form or {
                    "platform": start, "group": "topic", "vi": "", "q": "",
                    "lang": tag_store.default_lang(start),
                },
                "error": error,
                "tab": "tags",
                "title": "Tag gợi ý",
            },
            status_code=status_code,
        )

    @app.get("/tags", response_class=HTMLResponse)
    def tags_page(request: Request, platform: str = ""):
        return render_tags(request, platform)

    @app.post("/tags", response_class=HTMLResponse)
    def tags_create(
        request: Request,
        platform: str = Form(""),
        group: str = Form("topic"),
        vi: str = Form(""),
        q: str = Form(""),
        lang: str = Form(""),
    ):
        """Thêm tag. Từ khoá trống thì AI dịch từ tên tiếng Việt trước khi lưu."""
        from reup.web import tags as tag_store

        form = {"platform": platform, "group": group, "vi": vi, "q": q, "lang": lang}
        try:
            tag = tag_store.create(tags_path(), form, tag_llm)
        except ValueError as exc:
            return render_tags(request, platform, form, str(exc), status_code=400)
        return RedirectResponse(f"/tags?platform={tag['platform']}#tag-{tag['id']}", status_code=303)

    @app.post("/tags/{tag_id}", response_class=HTMLResponse)
    def tags_update(
        request: Request,
        tag_id: str,
        platform: str = Form(""),
        group: str = Form("topic"),
        vi: str = Form(""),
        q: str = Form(""),
        lang: str = Form(""),
        back: str = Form(""),
    ):
        from reup.web import tags as tag_store

        form = {"platform": platform, "group": group, "vi": vi, "q": q, "lang": lang}
        try:
            tag_store.update(tags_path(), tag_id, form, tag_llm)
        except ValueError as exc:
            return render_tags(request, back, None, str(exc), status_code=400)
        return RedirectResponse(f"/tags?{urlencode({'platform': back})}#tag-{tag_id}", status_code=303)

    @app.post("/tags/{tag_id}/delete")
    def tags_delete(tag_id: str, back: str = Form("")):
        from reup.web import tags as tag_store

        try:
            tag_store.delete(tags_path(), tag_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return RedirectResponse(f"/tags?{urlencode({'platform': back})}", status_code=303)

    @app.post("/api/tags/translate")
    def tags_translate(payload: dict = Body(...)):
        """Nút ✨ Dịch: xem trước từ khoá AI dịch, chưa lưu gì."""
        from reup.web import tags as tag_store

        try:
            q = tag_store.suggest(
                str(payload.get("vi") or ""), str(payload.get("lang") or "en"),
                str(payload.get("group") or "topic"), tag_llm,
            )
        except ValueError as exc:
            return {"q": "", "error": str(exc)}
        return {"q": q}

    @app.post("/api/translate-titles")
    def translate_titles(payload: dict = Body(...)):
        """Tiêu đề ở tab quét → tiếng Việt. Hàm thường nên chạy trong threadpool."""
        from reup.adapters.registry import make_llm
        from reup.web import title_translate

        titles = [str(t) for t in (payload.get("titles") or [])][:300]
        try:
            llm = make_llm(cfg(), "export")
        except Exception as exc:
            return {"translations": {}, "error": f"không dựng được LLM: {exc}"}
        cache = app.state.jobs_dir.parent / "title-vi-cache.json"
        found, error = title_translate.translate(titles, cache, llm)
        return {"translations": found, "error": error}

    def start_candidate(
        url: str, lang: str, platform: str, video_id: str, media_url: str, title: str
    ) -> str:
        """Tạo job từ một video đã quét, đánh dấu đã xử lý, bỏ khỏi hàng chờ, chạy."""
        if media_url and not media_url.startswith(("https://", "http://")):
            raise HTTPException(status_code=400, detail="link tải phải là http(s)")
        job = create_job(app.state.jobs_dir, url.strip(), lang)
        if media_url:
            from reup.adapters.douyin_export import save_direct

            save_direct(job.root, media_url, video_id=video_id, url=url.strip(), title=title)
        s = store()
        try:
            s.upsert_job(job.id, job.source_url, "pending")
            if platform and video_id:
                s.mark_seen(platform, video_id)
                s.unsave_video(platform, video_id)
        finally:
            s.close()
        app.state.runner.start(job.id)
        return job.id

    @app.post("/discover/pick")
    def pick(
        url: str = Form(...),
        lang: str = Form("auto"),
        platform: str = Form(""),
        video_id: str = Form(""),
        media_url: str = Form(""),
        title: str = Form(""),
    ):
        """Chọn một video từ tab quét: tạo job, đánh dấu đã xử lý, và chạy luôn.

        Đánh dấu `seen` ở đây chứ không đợi chạy xong, để lần quét sau không
        hiện lại đúng video vừa chọn.

        Chạy luôn vì "chọn video này" nghĩa là "làm video này": tạo job rồi bắt
        người dùng nhảy ra terminal gõ `reup run` là cắt đôi một thao tác duy nhất.
        """
        job_id = start_candidate(url, lang, platform, video_id, media_url, title)
        return RedirectResponse(f"/jobs/{job_id}", status_code=303)

    # --- hàng chờ ----------------------------------------------------------------

    SAVED_FIELDS = (
        "url", "embed_url", "title", "uploader", "thumbnail", "duration_ms",
        "view_count", "like_count", "share_count", "published_at", "media_url",
    )

    def saved_count() -> int:
        s = store()
        try:
            return len(s.saved_keys())
        finally:
            s.close()

    templates.env.globals["saved_count"] = saved_count

    @app.post("/saved")
    def toggle_saved(payload: dict = Body(...)):
        """Thêm/bỏ một video ở tab quét khỏi hàng chờ. Không tạo job."""
        video = payload.get("video") or {}
        platform = str(video.get("platform") or "")
        video_id = str(video.get("video_id") or "")
        url = str(video.get("url") or "")
        if not (platform and video_id and url.startswith(("https://", "http://"))):
            raise HTTPException(status_code=400, detail="thiếu nền tảng, id hoặc link video")
        s = store()
        try:
            if payload.get("saved", True):
                s.save_video(platform, video_id, {k: video.get(k) for k in SAVED_FIELDS})
            else:
                s.unsave_video(platform, video_id)
            count = len(s.saved_keys())
        finally:
            s.close()
        return {"saved": bool(payload.get("saved", True)), "count": count}

    @app.get("/saved", response_class=HTMLResponse)
    def saved_page(request: Request, platform: str = ""):
        s = store()
        try:
            every = s.saved_videos()
        finally:
            s.close()
        counts = {"": len(every)}
        for v in every:
            counts[v["platform"]] = counts.get(v["platform"], 0) + 1
        return templates.TemplateResponse(
            request,
            "saved.html",
            {
                "rows": [v for v in every if not platform or v["platform"] == platform],
                "platform": platform,
                "counts": counts,
                "auto_approve": bool(
                    getattr(cfg().review, "auto_approve", False)
                    or getattr(cfg().review, "auto_approve_a", False)
                ),
                "tab": "saved",
                "title": "Hàng chờ",
            },
        )

    def run_saved(platform: str, video_id: str, lang: str) -> str:
        s = store()
        try:
            found = [v for v in s.saved_videos(platform) if v["video_id"] == video_id]
        finally:
            s.close()
        if not found:
            raise HTTPException(status_code=404, detail="video không còn trong hàng chờ")
        v = found[0]
        return start_candidate(
            v["url"], lang, platform, video_id, v.get("media_url") or "", v.get("title") or ""
        )

    @app.post("/saved/run")
    def saved_run(
        platform: str = Form(...), video_id: str = Form(...), lang: str = Form("auto")
    ):
        job_id = run_saved(platform, video_id, lang)
        return RedirectResponse(f"/jobs/{job_id}", status_code=303)

    @app.post("/saved/next")
    def saved_next(platform: str = Form("")):
        """Làm video lưu lâu nhất. Ở lại trang hàng chờ để bấm tiếp nếu muốn."""
        s = store()
        try:
            queue = s.saved_videos(platform or None)
        finally:
            s.close()
        if not queue:
            raise HTTPException(status_code=404, detail="hàng chờ trống")
        v = queue[0]
        lang = "zh" if v["platform"] == "douyin" else "auto"
        run_saved(v["platform"], v["video_id"], lang)
        back = f"/saved?platform={platform}" if platform else "/saved"
        return RedirectResponse(back, status_code=303)

    @app.post("/saved/run-batch")
    def saved_run_batch(
        keys: list[str] = Form(default=[]),
        first: int = Form(0),
        platform: str = Form(""),
    ):
        """Làm hàng loạt: các video đã tick, hoặc `first` video đầu hàng chờ.

        Tạo job cho tất cả ngay, còn chạy thì runner tự xếp lượt theo
        `profile.concurrency` — bấm 20 video không có nghĩa 20 job chạy cùng lúc.
        """
        s = store()
        try:
            queue = s.saved_videos(platform or None)
        finally:
            s.close()
        if first > 0:
            picked = queue[:first]
        else:
            wanted = {tuple(k.split(":", 1)) for k in keys if ":" in k}
            picked = [v for v in queue if (v["platform"], v["video_id"]) in wanted]
        if not picked:
            raise HTTPException(status_code=400, detail="chưa chọn video nào")
        run_picked(picked)
        return RedirectResponse(f"/?batch={len(picked)}", status_code=303)

    def run_picked(picked: list[dict]) -> int:
        for v in picked:
            lang = "zh" if v["platform"] == "douyin" else "auto"
            run_saved(v["platform"], v["video_id"], lang)
        return len(picked)

    def run_first_saved(n: int, platform: str = "") -> int:
        """Làm `n` video đầu hàng chờ. Bot Telegram gọi qua `app.state`."""
        s = store()
        try:
            queue = s.saved_videos(platform or None)
        finally:
            s.close()
        return run_picked(queue[: max(0, n)])

    app.state.run_first_saved = run_first_saved

    @app.post("/saved/remove")
    def saved_remove(platform: str = Form(...), video_id: str = Form(...)):
        s = store()
        try:
            s.unsave_video(platform, video_id)
        finally:
            s.close()
        return RedirectResponse("/saved", status_code=303)

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
        rows = service.review_rows(job)
        return templates.TemplateResponse(
            request,
            "review.html",
            {
                "job": job,
                "details": service.job_details(app.state.jobs_dir, job_id),
                "row": row,
                "rows": rows,
                "tts_ready": sum(
                    1 for r in rows if r["tts"] and r["tts"]["state"] == "tts"
                ),
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
        prog["tts"] = service.tts_status(
            job, [r["id"] for r in service.review_rows(job)]
        )
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

    def bot_actions() -> dict:
        """Hành động cho nút bấm trong tin nhắn Telegram.

        Bọc đúng các route của web, nên bấm nút trên Telegram và bấm nút trên
        web đi cùng một đường. Mỗi hàm trả câu báo kết quả, lỗi thì ném ValueError.
        """

        def wrap(fn, done: str):
            def act(job_id: str) -> str:
                try:
                    fn(job_id)
                except HTTPException as exc:
                    raise ValueError(str(exc.detail)) from exc
                return done
            return act

        def run_again(job_id: str) -> None:
            if app.state.runner.is_running(job_id):
                raise HTTPException(status_code=409, detail="job đang chạy rồi")
            run(job_id)

        return {
            "run": wrap(run_again, "↻ Đã cho chạy lại. Bot sẽ báo tiến độ."),
            "rerender": wrap(rerender, "🔄 Đang dựng lại video. Bot sẽ báo khi xong."),
            "approve": wrap(lambda j: approve(j, "a"), "✅ Đã duyệt, đang chạy tiếp."),
            "archive": wrap(lambda j: archive(j, 1), "🗄 Đã cất vào Lưu trữ."),
            "delete": wrap(delete, "🗑 Đã xoá dự án."),
        }

    app.state.bot_actions = bot_actions()

    return app
