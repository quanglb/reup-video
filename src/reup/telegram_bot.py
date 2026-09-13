"""Bot Telegram nghe lệnh, chạy nền cùng `reup web`.

Long-polling (getUpdates) chứ không webhook: máy ở nhà không có IP public, và
không phải mở cổng nào. Luồng daemon nên Ctrl-C web là bot tắt theo.

Chỉ nghe đúng chat id trong config: token lộ ra ngoài thì người lạ vẫn không
ra lệnh `/lam` được.
"""
from __future__ import annotations

import html
import sys
import threading
import time
from pathlib import Path
from typing import Callable

from reup.notify import TelegramNotifier, job_title

COMMANDS = [
    ("status", "Tổng quan các dự án"),
    ("dangchay", "Job đang chạy và đang ở bước nào"),
    ("loi", "Các job bị lỗi gần đây"),
    ("hangcho", "Video trong hàng chờ"),
    ("lam", "Làm N video đầu hàng chờ, vd /lam 3"),
    ("help", "Danh sách lệnh"),
]

STATUS_ICONS = {"pending": "🕓", "running": "⚙️", "needs_review": "⏸", "done": "✅", "failed": "❌"}


class BotPoller:
    def __init__(
        self,
        bot: TelegramNotifier,
        jobs_dir: Path,
        db_path: Path,
        live: Callable[[], set] = set,
        run_first_saved: Callable[[int], int] | None = None,
        web_url: str = "",
        actions: dict[str, Callable[[str], str]] | None = None,
    ) -> None:
        self.bot = bot
        self.jobs_dir = Path(jobs_dir)
        self.db_path = Path(db_path)
        self.live = live
        self.run_first_saved = run_first_saved
        self.web_url = web_url.rstrip("/")
        self.actions = actions or {}
        self.offset: int | None = None
        self._stop = threading.Event()

    # --- vòng nghe ---------------------------------------------------------------

    def start(self) -> threading.Thread:
        t = threading.Thread(target=self.loop, daemon=True, name="reup-telegram-bot")
        t.start()
        return t

    def stop(self) -> None:
        self._stop.set()

    def loop(self) -> None:
        self.bot.call("setMyCommands", {"commands": [
            {"command": c, "description": d} for c, d in COMMANDS
        ]})
        # Bỏ tin cũ tồn từ lúc web tắt: không chạy lại một "/lam 5" của hôm qua.
        backlog = self.bot.call("getUpdates", {"offset": -1, "timeout": 0}) or []
        if backlog:
            self.offset = backlog[-1]["update_id"] + 1
        while not self._stop.is_set():
            payload = {"timeout": 25, "allowed_updates": ["message", "callback_query"]}
            if self.offset is not None:
                payload["offset"] = self.offset
            updates = self.bot.call("getUpdates", payload)
            if updates is None:  # mạng rớt, hoặc có tiến trình khác cũng đang nghe
                time.sleep(5)
                continue
            for upd in updates:
                self.offset = upd["update_id"] + 1
                try:
                    self.handle(upd)
                except Exception as exc:  # một lệnh hỏng không được giết cả bot
                    print(f"[telegram] lệnh hỏng: {exc}", file=sys.stderr)
                    self.bot.send(f"⚠️ Lệnh hỏng: <code>{html.escape(str(exc))[:300]}</code>")

    # --- xử lý lệnh --------------------------------------------------------------

    def handle(self, update: dict) -> None:
        if update.get("callback_query"):
            self.handle_button(update["callback_query"])
            return
        msg = update.get("message") or {}
        if str((msg.get("chat") or {}).get("id")) != self.bot.chat_id:
            return  # người lạ: im lặng, không lộ là bot có tồn tại
        text = (msg.get("text") or "").strip()
        if not text.startswith("/"):
            self.bot.send("Gõ /help để xem lệnh.")
            return
        head, _, arg = text.partition(" ")
        cmd = head[1:].split("@", 1)[0].lower()
        handler = {
            "start": self.cmd_help, "help": self.cmd_help, "status": self.cmd_status,
            "dangchay": self.cmd_running, "loi": self.cmd_failed,
            "hangcho": self.cmd_saved, "lam": self.cmd_run,
        }.get(cmd)
        if handler is None:
            self.bot.send(f"Không có lệnh /{html.escape(cmd)}. Gõ /help.")
            return
        handler(arg.strip())

    # --- nút bấm dưới tin nhắn --------------------------------------------------

    def _answer(self, cq: dict, text: str = "") -> None:
        self.bot.call("answerCallbackQuery", {"callback_query_id": cq["id"], "text": text[:180]})

    def _set_keyboard(self, message: dict, keyboard: list | None) -> None:
        self.bot.call("editMessageReplyMarkup", {
            "chat_id": self.bot.chat_id, "message_id": message["message_id"],
            "reply_markup": {"inline_keyboard": keyboard or []},
        })

    def handle_button(self, cq: dict) -> None:
        message = cq.get("message") or {}
        if str((message.get("chat") or {}).get("id")) != self.bot.chat_id:
            self._answer(cq)  # người lạ bấm vào tin bị chuyển tiếp: không làm gì
            return
        action, _, job_id = (cq.get("data") or "").partition(":")
        if not job_id:
            self._answer(cq)
            return
        msg_id = message.get("message_id")

        if action == "delask":
            # Xoá không hoàn tác được: đổi bàn phím thành bước xác nhận.
            self._set_keyboard(message, [[
                {"text": "⚠️ Xoá hẳn", "callback_data": f"delete:{job_id}"},
                {"text": "Huỷ", "callback_data": f"cancel:{job_id}"},
            ]])
            self._answer(cq, "Bấm ⚠️ Xoá hẳn để xác nhận")
            return
        if action == "cancel":
            self._set_keyboard(message, None)
            self._answer(cq, "Đã huỷ")
            return
        if action == "log":
            self._answer(cq)
            self.bot.send(self._error_detail(job_id), reply_to=msg_id)
            return
        if action == "video":
            self._answer(cq, "Đang gửi video…")
            self._send_video(job_id, msg_id)
            return

        act = self.actions.get(action)
        if act is None:
            self._answer(cq, "Nút này chỉ dùng được khi bot chạy cùng reup web")
            return
        try:
            result = act(job_id)
        except ValueError as exc:
            self._answer(cq, f"Không làm được: {exc}")
            return
        # Bỏ nút ở tin cũ: tránh bấm lại "Chạy lại" hai lần cho cùng một lỗi.
        self._set_keyboard(message, None)
        self._answer(cq, result)
        self.bot.send(f"{html.escape(result)}\n<i>{html.escape(self._title(job_id))}</i>", reply_to=msg_id)

    def _error_detail(self, job_id: str) -> str:
        from reup.core.job import load_job

        try:
            job = load_job(self.jobs_dir, job_id)
        except FileNotFoundError:
            return "Không còn job này."
        last = ""
        if job.log_jsonl.exists():
            for line in job.log_jsonl.read_text(encoding="utf-8").splitlines():
                if '"ok": false' in line:
                    last = line
        if not last:
            return "Không thấy chi tiết lỗi trong nhật ký."
        import json

        entry = json.loads(last)
        tail = (entry.get("error") or "").strip()[-3000:]
        return f"🔍 <b>Chi tiết lỗi</b> · bước {html.escape(entry.get('stage', '?'))}\n<pre>{html.escape(tail)}</pre>"

    def _send_video(self, job_id: str, reply_to: int | None) -> None:
        from reup.core.job import load_job
        from reup.notify import VIDEO_LIMIT

        try:
            job = load_job(self.jobs_dir, job_id)
        except FileNotFoundError:
            self.bot.send("Không còn job này.", reply_to=reply_to)
            return
        if not job.final_mp4.exists():
            self.bot.send("Chưa có video thành phẩm.", reply_to=reply_to)
            return
        size = job.final_mp4.stat().st_size
        if size > VIDEO_LIMIT:
            self.bot.send(
                f"Video nặng {size // (1024 * 1024)}MB, quá giới hạn 50MB của bot.",
                reply_to=reply_to,
            )
            return
        self.bot.upload_video(job.final_mp4, html.escape(self._title(job_id)))

    def _store(self):
        from reup.core.store import Store

        s = Store(self.db_path)
        s.init_schema()
        return s

    def _title(self, job_id: str) -> str:
        from reup.core.job import load_job

        try:
            return job_title(load_job(self.jobs_dir, job_id))
        except FileNotFoundError:
            return job_id

    def _link(self, job_id: str, text: str) -> str:
        text = html.escape(text[:60])
        return f'<a href="{self.web_url}/jobs/{job_id}">{text}</a>' if self.web_url else text

    def cmd_help(self, _arg: str = "") -> None:
        lines = ["🤖 <b>reup bot</b> — các lệnh:"]
        lines += [f"/{c} — {d}" for c, d in COMMANDS]
        self.bot.send("\n".join(lines))

    def cmd_status(self, _arg: str = "") -> None:
        s = self._store()
        try:
            jobs = s.list_jobs()
            saved = len(s.saved_keys())
        finally:
            s.close()
        counts: dict[str, int] = {}
        for j in jobs:
            counts[j["status"]] = counts.get(j["status"], 0) + 1
        from reup.web.service import STATUS_LABELS

        lines = [f"📊 <b>Tổng quan</b> · {len(jobs)} dự án"]
        for key, label in STATUS_LABELS.items():
            if counts.get(key):
                lines.append(f"{STATUS_ICONS[key]} {label}: <b>{counts[key]}</b>")
        lines.append(f"⭐ Hàng chờ: <b>{saved}</b> video")
        if self.web_url:
            lines.append(self.web_url)
        self.bot.send("\n".join(lines))

    def cmd_running(self, _arg: str = "") -> None:
        from reup.notify import _label

        s = self._store()
        try:
            rows = [j for j in s.list_jobs("running")]
            live = self.live()
            rows += [s.get_job(i) for i in live if i not in {r["id"] for r in rows} and s.get_job(i)]
        finally:
            s.close()
        if not rows:
            self.bot.send("😴 Không có job nào đang chạy.")
            return
        lines = [f"⚙️ <b>Đang chạy</b> · {len(rows)} job"]
        for r in rows[:10]:
            step = html.escape(_label(r["stage"])) if r["stage"] else "đang xếp lượt"
            lines.append(f"• {self._link(r['id'], self._title(r['id']))} — {step}")
        self.bot.send("\n".join(lines))

    def cmd_failed(self, _arg: str = "") -> None:
        s = self._store()
        try:
            rows = s.list_jobs("failed")[:5]
        finally:
            s.close()
        if not rows:
            self.bot.send("👌 Không có job lỗi.")
            return
        lines = [f"❌ <b>Job lỗi</b> · {len(rows)} gần nhất"]
        for r in rows:
            err = html.escape((r["error"] or "").splitlines()[0][:120]) if r["error"] else ""
            lines.append(f"• {self._link(r['id'], self._title(r['id']))}\n  <i>{err}</i>")
        self.bot.send("\n".join(lines))

    def cmd_saved(self, _arg: str = "") -> None:
        s = self._store()
        try:
            rows = s.saved_videos()
        finally:
            s.close()
        if not rows:
            self.bot.send("⭐ Hàng chờ trống.")
            return
        lines = [f"⭐ <b>Hàng chờ</b> · {len(rows)} video"]
        for i, v in enumerate(rows[:8], 1):
            lines.append(f"{i}. [{v['platform']}] {html.escape((v.get('title') or v['video_id'])[:60])}")
        if len(rows) > 8:
            lines.append(f"… và {len(rows) - 8} video nữa")
        lines.append("Gõ /lam 3 để làm 3 video đầu.")
        self.bot.send("\n".join(lines))

    def cmd_run(self, arg: str = "") -> None:
        if self.run_first_saved is None:
            self.bot.send("Lệnh /lam chỉ dùng được khi bot chạy cùng `reup web`.")
            return
        try:
            n = int(arg or "1")
        except ValueError:
            self.bot.send("Cách dùng: /lam 3 (số video, 1–20).")
            return
        n = max(1, min(20, n))
        started = self.run_first_saved(n)
        if not started:
            self.bot.send("⭐ Hàng chờ trống, không có gì để làm.")
            return
        self.bot.send(f"▶ Đã tạo <b>{started}</b> job từ hàng chờ. Bot sẽ báo tiến độ từng video.")


def start_for_app(app) -> BotPoller | None:
    """Bật bot nghe lệnh cho một web app. Không bật được thì trả None, không ném."""
    from reup import notify
    from reup.config import load_config

    try:
        cfg = load_config(app.state.config_path)
    except Exception:
        return None
    base = notify.from_config(cfg)
    if not isinstance(base, TelegramNotifier) or not cfg.notify.commands:
        return None
    bot = TelegramNotifier(base.token, base.chat_id, timeout=40.0)  # dài hơn long-poll 25s
    poller = BotPoller(
        bot, app.state.jobs_dir, app.state.db_path,
        live=app.state.runner.live,
        run_first_saved=getattr(app.state, "run_first_saved", None),
        web_url=cfg.notify.web_url,
        actions=getattr(app.state, "bot_actions", None),
    )
    poller.start()
    return poller
