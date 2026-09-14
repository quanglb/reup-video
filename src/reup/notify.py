"""Báo cáo tình hình qua Telegram khi làm video.

Gửi thẳng Bot API bằng `urllib`, không thêm thư viện. Mọi lỗi mạng đều nuốt và
in ra stderr: bot sập không bao giờ được làm hỏng một job đang render.

Mỗi job có MỘT tin nhắn tiến trình, sửa tại chỗ sau từng stage (không spam 13
tin). Còn xong / lỗi / chờ duyệt thì gửi tin mới để điện thoại có thông báo.

Token để trong `.env` (TELEGRAM_BOT_TOKEN), không bao giờ trong config.toml.
"""
from __future__ import annotations

import html
import json
import os
import sys
import threading
import time
import urllib.request
import uuid
from pathlib import Path

API = "https://api.telegram.org"
VIDEO_LIMIT = 49 * 1024 * 1024  # Bot API chỉ nhận file tới 50MB


def _label(stage: str) -> str:
    try:
        from reup.web.service import STAGE_LABELS
    except Exception:  # web không cài: dùng tên stage
        return stage
    return STAGE_LABELS.get(stage, stage)


def _read(path: Path) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def job_title(job) -> str:
    """Tên đặt > tiêu đề Việt đã sinh > tiêu đề gốc > id."""
    return (
        str(_read(job.job_json).get("name") or "").strip()
        or str(_read(job.meta_json).get("title") or "").strip()
        or str(_read(job.source_info).get("title") or "").strip()
        or job.id
    )


def elapsed_label(seconds: float) -> str:
    s = int(round(seconds))
    if s < 60:
        return f"{s}s"
    return f"{s // 60} phút {s % 60:02d}s"


class Notifier:
    """Không làm gì. Dùng khi chưa bật Telegram."""

    def job_started(self, job, total: int) -> None: ...
    def stage_done(self, job, stage: str, seconds: float, done: int, total: int) -> None: ...
    def job_needs_review(self, job, gate: str) -> None: ...
    def job_done(self, job, output_dir: str) -> None: ...
    def job_failed(self, job, stage: str, error: str) -> None: ...
    def tts_fallback_warning(self, job, ids: list[int | str]) -> None: ...
    def batch_finished(self, counts: dict[str, int]) -> None: ...


class TelegramNotifier(Notifier):
    def __init__(
        self,
        token: str,
        chat_id: str,
        stage_updates: bool = True,
        send_video: bool = False,
        web_url: str = "",
        buttons: bool = False,
        api: str = API,
        timeout: float = 15.0,
    ) -> None:
        self.token = token
        self.chat_id = str(chat_id)
        self.stage_updates = stage_updates
        self.send_video = send_video
        self.web_url = web_url.rstrip("/")
        # Nút bấm dưới tin Xong/Lỗi chỉ có nghĩa khi bot đang nghe (reup web).
        self.buttons = buttons
        self.api = api
        self.timeout = timeout
        self._lock = threading.Lock()
        self._jobs: dict[str, dict] = {}  # job_id -> message_id, lúc bắt đầu, dòng stage

    # --- gửi --------------------------------------------------------------------

    def call(self, method: str, payload: dict) -> dict | None:
        req = urllib.request.Request(
            f"{self.api}/bot{self.token}/{method}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        return self._open(req, method)

    def _open(self, req, method: str) -> dict | None:
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:  # mạng, 400 do HTML sai, bot bị chặn...
            print(f"[telegram] {method} hỏng: {exc}", file=sys.stderr)
            return None
        return data.get("result") if data.get("ok") else None

    def send(self, text: str, keyboard: list | None = None, reply_to: int | None = None) -> int | None:
        payload = {
            "chat_id": self.chat_id, "text": text[:4000], "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if keyboard:
            payload["reply_markup"] = {"inline_keyboard": keyboard}
        if reply_to:
            payload["reply_to_message_id"] = reply_to
        result = self.call("sendMessage", payload)
        return result.get("message_id") if isinstance(result, dict) else None

    def _keyboard(self, rows: list[list[tuple[str, str]]], job_id: str) -> list | None:
        if not self.buttons:
            return None
        return [[{"text": t, "callback_data": f"{act}:{job_id}"} for t, act in row] for row in rows]

    def edit(self, message_id: int, text: str) -> None:
        self.call("editMessageText", {
            "chat_id": self.chat_id, "message_id": message_id, "text": text[:4000],
            "parse_mode": "HTML", "disable_web_page_preview": True,
        })

    def upload_video(self, path: Path, caption: str) -> None:
        boundary = uuid.uuid4().hex
        parts = []
        for name, value in (("chat_id", self.chat_id), ("caption", caption[:1000]),
                            ("parse_mode", "HTML"), ("supports_streaming", "true")):
            parts.append(
                f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'
                .encode("utf-8")
            )
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="video"; '
            f'filename="{path.name}"\r\nContent-Type: video/mp4\r\n\r\n'.encode("utf-8")
        )
        body = b"".join(parts) + path.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
        req = urllib.request.Request(
            f"{self.api}/bot{self.token}/sendVideo", data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        old, self.timeout = self.timeout, max(self.timeout, 300.0)
        try:
            self._open(req, "sendVideo")
        finally:
            self.timeout = old

    # --- sự kiện ----------------------------------------------------------------

    def _head(self, job, icon: str, verb: str) -> str:
        return f"{icon} <b>{verb}</b> · {html.escape(job_title(job))}"

    def _link(self, job) -> str:
        if not self.web_url:
            return f"<code>{job.id}</code>"
        return f'<a href="{self.web_url}/jobs/{job.id}">Mở job {job.id}</a>'

    def _progress_text(self, job, state: dict) -> str:
        done, total = state["done"], state["total"]
        filled = round(10 * done / total) if total else 0
        bar = "▓" * filled + "░" * (10 - filled)
        lines = [self._head(job, "⚙️", "Đang làm"), f"{bar} {done}/{total}"]
        lines += state["lines"][-6:]
        lines.append(self._link(job))
        return "\n".join(lines)

    def job_started(self, job, total: int) -> None:
        state = {"started": time.time(), "done": 0, "total": total, "lines": [], "msg": None}
        with self._lock:
            self._jobs[job.id] = state
        if self.stage_updates:
            state["msg"] = self.send(self._progress_text(job, state))
        else:
            self.send(f"{self._head(job, '🎬', 'Bắt đầu')}\n{self._link(job)}")

    def stage_done(self, job, stage: str, seconds: float, done: int, total: int) -> None:
        with self._lock:
            state = self._jobs.get(job.id)
        if state is None:
            return
        state["done"], state["total"] = done, total
        state["lines"].append(f"✓ {html.escape(_label(stage))} · {elapsed_label(seconds)}")
        if self.stage_updates and state["msg"]:
            self.edit(state["msg"], self._progress_text(job, state))

    def _finish(self, job) -> float:
        with self._lock:
            state = self._jobs.pop(job.id, None)
        return time.time() - state["started"] if state else 0.0

    def job_needs_review(self, job, gate: str) -> None:
        self._finish(job)
        what = "bản dịch (Chốt A)" if gate == "a" else "thành phẩm (Chốt B)"
        self.send(
            f"{self._head(job, '⏸', 'Chờ duyệt')}\nCần duyệt {what}.\n{self._link(job)}",
            self._keyboard([[("✅ Duyệt & chạy tiếp", "approve")],
                            [("🗄 Lưu trữ", "archive"), ("🗑 Xoá", "delask")]], job.id),
        )

    def job_done(self, job, output_dir: str) -> None:
        took = self._finish(job)
        meta = _read(job.meta_json)
        tags = " ".join(f"#{t}" for t in (meta.get("hashtags") or [])[:8])
        out = Path(output_dir) / f"{job.id}.mp4"
        lines = [self._head(job, "✅", "Xong")]
        if took:
            lines.append(f"⏱ {elapsed_label(took)}")
        lines.append(f"📁 <code>{html.escape(str(out if out.exists() else job.final_mp4))}</code>")
        if tags:
            lines.append(html.escape(tags))
        lines.append(self._link(job))
        text = "\n".join(lines)
        video = out if out.exists() else job.final_mp4
        if self.send_video and video.exists() and video.stat().st_size <= VIDEO_LIMIT:
            self.upload_video(video, text)
        keyboard = self._keyboard(
            [[("🔄 Dựng lại", "rerender")] if self.send_video else
             [("📹 Gửi video", "video"), ("🔄 Dựng lại", "rerender")],
             [("🗄 Lưu trữ", "archive"), ("🗑 Xoá", "delask")]],
            job.id,
        )
        if not (self.send_video and video.exists() and video.stat().st_size <= VIDEO_LIMIT):
            self.send(text, keyboard)
        elif keyboard:
            self.send("Làm gì tiếp với video này?", keyboard)

    def job_failed(self, job, stage: str, error: str) -> None:
        self._finish(job)
        self.send(
            f"{self._head(job, '❌', 'Lỗi')}\nỞ bước: {html.escape(_label(stage))}\n"
            f"<pre>{html.escape((error or '')[:600])}</pre>\n{self._link(job)}",
            self._keyboard([[("↻ Chạy lại", "run"), ("🔍 Xem lỗi", "log")],
                            [("🗄 Lưu trữ", "archive"), ("🗑 Xoá", "delask")]], job.id),
        )

    def tts_fallback_warning(self, job, ids: list[int | str]) -> None:
        ids_str = ", ".join(f"#{i}" for i in ids)
        self.send(
            f"{self._head(job, '⚠️', 'Cảnh báo TTS')}\n"
            f"CapCut lỗi, đã đổi sang edge-tts cho các câu: {html.escape(ids_str)}.\n"
            f"Audio các câu này có thể khác giọng.\n{self._link(job)}"
        )

    def batch_finished(self, counts: dict[str, int]) -> None:
        total = sum(counts.values())
        parts = [
            f"✅ {counts.get('done', 0)} xong",
            f"⏸ {counts.get('needs_review', 0)} chờ duyệt",
            f"❌ {counts.get('failed', 0)} lỗi",
        ]
        link = f"\n{self.web_url}" if self.web_url else ""
        self.send(f"🏁 <b>Hết lượt chạy</b> · {total} job\n" + " · ".join(parts) + link)


def token_from_env() -> str:
    return os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()


def from_config(cfg) -> Notifier:
    """Bật khi config bật VÀ có đủ token + chat id. Thiếu gì cũng im lặng."""
    notify = getattr(cfg, "notify", None)
    if notify is None or not notify.telegram:
        return Notifier()
    token = token_from_env()
    chat = str(notify.chat_id or os.environ.get("TELEGRAM_CHAT_ID", "")).strip()
    if not (token and chat):
        return Notifier()
    return TelegramNotifier(
        token, chat, stage_updates=notify.stage_updates,
        send_video=notify.send_video, web_url=notify.web_url,
        buttons=notify.commands,
    )


def chat_ids(token: str) -> list[tuple[str, str]]:
    """Chat đã nhắn cho bot gần đây: (id, tên). Để người dùng khỏi phải tự mò."""
    bot = TelegramNotifier(token, "")
    found: dict[str, str] = {}
    for upd in bot.call("getUpdates", {"limit": 50}) or []:
        msg = upd.get("message") or upd.get("channel_post") or upd.get("my_chat_member") or {}
        chat = msg.get("chat") or {}
        if "id" in chat:
            name = chat.get("title") or " ".join(
                x for x in (chat.get("first_name"), chat.get("last_name")) if x
            ) or chat.get("username") or ""
            found[str(chat["id"])] = name
    return list(found.items())
