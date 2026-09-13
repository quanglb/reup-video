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
