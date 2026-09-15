"""Bot nghe lệnh: chỉ nghe đúng chat, trả lời đúng lệnh, /lam gọi hàng chờ."""
from pathlib import Path

from reup.core.job import create_job
from reup.core.store import Store
from reup.telegram_bot import BotPoller

from tests.test_notify import RecordingBot


def msg(text: str, chat: int = 42, uid: int = 1) -> dict:
    return {"update_id": uid, "message": {"chat": {"id": chat}, "text": text}}


def poller(tmp_path: Path, **kw):
    db = tmp_path / "db"
    s = Store(db)
    s.init_schema()
    s.close()
    bot = RecordingBot()
    return BotPoller(bot, tmp_path / "jobs", db, **kw), bot, db


def test_strangers_are_ignored(tmp_path):
    p, bot, _ = poller(tmp_path)
    p.handle(msg("/status", chat=999))
    assert bot.calls == []


def test_status_counts_jobs_and_queue(tmp_path):
    p, bot, db = poller(tmp_path)
    s = Store(db)
    s.upsert_job("a", "https://a", "done")
    s.upsert_job("b", "https://b", "failed", error="nổ")
    s.save_video("douyin", "v1", {"title": "菜"})
    s.close()
    p.handle(msg("/status"))
    text = bot.texts()[-1]
    assert "2 dự án" in text and "Xong: <b>1</b>" in text and "Hàng chờ: <b>1</b>" in text


def test_failed_lists_titles_and_errors(tmp_path):
    p, bot, db = poller(tmp_path)
    create_job(tmp_path / "jobs", "https://a", "zh", job_id="j1").update_settings(name="Món <chay>")
    s = Store(db)
    s.upsert_job("j1", "https://a", "failed", error="ffmpeg hỏng\nchi tiết")
    s.close()
    p.handle(msg("/loi@quang_reup_bot"))
    text = bot.texts()[-1]
    assert "Món &lt;chay&gt;" in text and "ffmpeg hỏng" in text and "chi tiết" not in text


def test_run_command_calls_the_queue(tmp_path):
    asked = []
    p, bot, _ = poller(tmp_path, run_first_saved=lambda n: asked.append(n) or n)
    p.handle(msg("/lam 3"))
    p.handle(msg("/lam 999"))
    p.handle(msg("/lam abc"))
    assert asked == [3, 20]
    assert "Đã tạo <b>3</b> job" in bot.texts()[0]
    assert "Cách dùng" in bot.texts()[-1]


def test_unknown_and_plain_text(tmp_path):
    p, bot, _ = poller(tmp_path)
    p.handle(msg("xin chào"))
    p.handle(msg("/abc"))
    assert "/help" in bot.texts()[0] and "Không có lệnh /abc" in bot.texts()[1]


def test_web_app_exposes_run_first_saved(tmp_path, config_file):
    from fastapi.testclient import TestClient

    from reup.web.app import create_app
    from tests.test_saved_queue import video
    from tests.test_web_app import FakeRunner

    jobs = tmp_path / "jobs"
    jobs.mkdir()
    db = tmp_path / "reup.db"
    Store(db).init_schema()
    app = create_app(config_file, jobs, db)
    app.state.runner = FakeRunner()
    c = TestClient(app)
    for vid in ("a", "b"):
        c.post("/saved", json={"video": video(vid)})
    assert app.state.run_first_saved(5) == 2
    assert len(app.state.runner.started) == 2


# --- nút bấm dưới tin nhắn ------------------------------------------------------------

def press(data: str, chat: int = 42, uid: int = 7) -> dict:
    return {"update_id": uid, "callback_query": {
        "id": "cb1", "data": data,
        "message": {"message_id": 99, "chat": {"id": chat}},
    }}


def methods(bot):
    return [m for m, _ in bot.calls]


def test_failed_message_has_retry_and_log_buttons_only_when_enabled(tmp_path):
    job = create_job(tmp_path / "jobs", "https://a", "zh", job_id="j1")
    bot = RecordingBot(buttons=True)
    bot.job_failed(job, "compose", "nổ")
    keyboard = bot.calls[-1][1]["reply_markup"]["inline_keyboard"]
    datas = [b["callback_data"] for row in keyboard for b in row]
    assert datas == ["run:j1", "log:j1", "archive:j1", "delask:j1"]

    plain = RecordingBot()
    plain.job_failed(job, "compose", "nổ")
    assert "reply_markup" not in plain.calls[-1][1]


def test_done_message_offers_video_and_rerender(tmp_path):
    job = create_job(tmp_path / "jobs", "https://a", "zh", job_id="j1")
    bot = RecordingBot(buttons=True)
    bot.job_done(job, str(tmp_path / "out"))
    datas = [b["callback_data"] for row in bot.calls[-1][1]["reply_markup"]["inline_keyboard"] for b in row]
    assert "video:j1" in datas and "rerender:j1" in datas


def test_pressing_retry_runs_the_action_and_removes_the_buttons(tmp_path):
    ran = []
    p, bot, _ = poller(tmp_path, actions={"run": lambda j: ran.append(j) or "↻ Đã cho chạy lại."})
    p.handle(press("run:j1"))
    assert ran == ["j1"]
    assert methods(bot) == ["editMessageReplyMarkup", "answerCallbackQuery", "sendMessage"]
    assert bot.calls[0][1]["reply_markup"] == {"inline_keyboard": []}
    assert bot.calls[2][1]["reply_to_message_id"] == 99


def test_delete_asks_for_confirmation_first(tmp_path):
    deleted = []
    p, bot, _ = poller(tmp_path, actions={"delete": lambda j: deleted.append(j) or "🗑 Đã xoá dự án."})
    p.handle(press("delask:j1"))
    assert deleted == []
    confirm = bot.calls[0][1]["reply_markup"]["inline_keyboard"][0]
    assert [b["callback_data"] for b in confirm] == ["delete:j1", "cancel:j1"]

    p.handle(press("cancel:j1"))
    assert deleted == []
    p.handle(press("delete:j1"))
    assert deleted == ["j1"]


def test_action_errors_are_shown_not_raised(tmp_path):
    def boom(job_id):
        raise ValueError("job đang chạy rồi")

    p, bot, _ = poller(tmp_path, actions={"run": boom})
    p.handle(press("run:j1"))
    assert methods(bot) == ["answerCallbackQuery"]
    assert "đang chạy rồi" in bot.calls[0][1]["text"]


def test_strangers_cannot_press_buttons(tmp_path):
    ran = []
    p, bot, _ = poller(tmp_path, actions={"delete": lambda j: ran.append(j) or "x"})
    p.handle(press("delete:j1", chat=999))
    assert ran == [] and methods(bot) == ["answerCallbackQuery"]


def test_log_button_sends_the_last_error(tmp_path):
    job = create_job(tmp_path / "jobs", "https://a", "zh", job_id="j1")
    job.log_jsonl.write_text(
        '{"stage": "fetch", "ok": true}\n{"stage": "compose", "ok": false, "error": "Traceback\\nffmpeg <lỗi>"}\n',
        encoding="utf-8",
    )
    p, bot, _ = poller(tmp_path)
    p.handle(press("log:j1"))
    text = bot.texts()[-1]
    assert "compose" in text and "ffmpeg &lt;lỗi&gt;" in text


def test_web_app_exposes_bot_actions(tmp_path, config_file):
    from fastapi.testclient import TestClient

    from reup.web.app import create_app
    from tests.test_web_app import FakeRunner

    jobs = tmp_path / "jobs"
    jobs.mkdir()
    db = tmp_path / "reup.db"
    Store(db).init_schema()
    app = create_app(config_file, jobs, db)
    app.state.runner = FakeRunner()
    TestClient(app)
    create_job(jobs, "https://a", "zh", job_id="j1")
    s = Store(db); s.upsert_job("j1", "https://a", "failed"); s.close()

    acts = app.state.bot_actions
    assert "chạy lại" in acts["run"]("j1") and app.state.runner.started == ["j1"]
    assert "Lưu trữ" in acts["archive"]("j1")
    import pytest
    with pytest.raises(ValueError):
        acts["run"]("khong-co")


def test_forum_topic_filtering(tmp_path):
    bot = RecordingBot(topic_id=123)
    db = tmp_path / "db"
    s = Store(db)
    s.init_schema()
    s.close()
    p = BotPoller(bot, tmp_path / "jobs", db)

    # Tin nhắn ở topic khác (ví dụ topic 999) -> bị bỏ qua
    p.handle({
        "update_id": 1,
        "message": {"chat": {"id": 42}, "message_thread_id": 999, "text": "/status"},
    })
    assert bot.calls == []

    # Tin nhắn ở đúng topic 123 -> được xử lý và gửi về đúng topic 123
    p.handle({
        "update_id": 2,
        "message": {"chat": {"id": 42}, "message_thread_id": 123, "text": "/status"},
    })
    assert len(bot.calls) == 1
    method, payload = bot.calls[0]
    assert method == "sendMessage"
    assert payload["message_thread_id"] == 123
    assert "Tổng quan" in payload["text"]
