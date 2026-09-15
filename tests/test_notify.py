"""Báo cáo Telegram: bật đúng lúc, gửi đúng sự kiện, lỗi mạng không làm hỏng job."""
import json
from pathlib import Path

from reup.config import load_config
from reup.core.job import create_job
from reup.core.runner import run_job
from reup.core.stage import StageSpec
from reup.core.store import Store
from reup import notify


class RecordingBot(notify.TelegramNotifier):
    """Ghi lại lệnh gọi Bot API thay vì gửi thật."""

    def __init__(self, **kw):
        super().__init__("TOKEN", "42", **kw)
        self.calls: list[tuple[str, dict]] = []

    def call(self, method, payload):
        self.calls.append((method, payload))
        return {"message_id": len(self.calls)}

    def texts(self, method="sendMessage"):
        return [p["text"] for m, p in self.calls if m == method]


def stage(name, rel, fail=False):
    def run(job, cfg):
        if fail:
            raise RuntimeError("ffmpeg nổ <b>")
        (job.root / rel).write_text("x", encoding="utf-8")
    return StageSpec(name, (rel,), run)


def setup(tmp_path, config_file):
    cfg = load_config(config_file)
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    job.meta_json.write_text(json.dumps({"title": "Món chay", "hashtags": ["monchay"]}),
                             encoding="utf-8")
    store = Store(tmp_path / "db")
    store.init_schema()
    return cfg, job, store


def test_one_progress_message_is_edited_then_done_is_sent(tmp_path, config_file):
    cfg, job, store = setup(tmp_path, config_file)
    bot = RecordingBot()
    stages = [stage("fetch", "a.txt"), stage("demux", "b.txt")]

    assert run_job(job, cfg, store, stages, notifier=bot) == "done"

    methods = [m for m, _ in bot.calls]
    assert methods == ["sendMessage", "editMessageText", "editMessageText", "sendMessage"]
    assert "2/2" in bot.texts("editMessageText")[-1]
    assert "Tải video nguồn" in bot.texts("editMessageText")[-1]
    done = bot.texts()[-1]
    assert "Xong" in done and "Món chay" in done and "#monchay" in done
    assert "http://127.0.0.1:8765/jobs/j1" in done or "j1" in done


def test_rerunning_a_finished_job_sends_nothing(tmp_path, config_file):
    cfg, job, store = setup(tmp_path, config_file)
    stages = [stage("fetch", "a.txt")]
    run_job(job, cfg, store, stages, notifier=RecordingBot())
    bot = RecordingBot()
    run_job(job, cfg, store, stages, notifier=bot)
    assert bot.calls == []


def test_failure_is_reported_with_escaped_error(tmp_path, config_file):
    cfg, job, store = setup(tmp_path, config_file)
    bot = RecordingBot()
    assert run_job(job, cfg, store, [stage("compose", "c.txt", fail=True)], notifier=bot) == "failed"
    text = bot.texts()[-1]
    assert "Lỗi" in text and "Render video" in text
    assert "&lt;b&gt;" in text


def test_network_error_never_breaks_the_job(tmp_path, config_file, monkeypatch):
    cfg, job, store = setup(tmp_path, config_file)
    bot = notify.TelegramNotifier("T", "1", api="http://127.0.0.1:9", timeout=0.2)
    assert run_job(job, cfg, store, [stage("fetch", "a.txt")], notifier=bot) == "done"


def test_from_config_needs_switch_token_and_chat(config_file, monkeypatch):
    cfg = load_config(config_file)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    assert type(notify.from_config(cfg)) is notify.Notifier

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "99")
    bot = notify.from_config(cfg)
    assert isinstance(bot, notify.TelegramNotifier) == cfg.notify.telegram


def test_batch_summary_counts(tmp_path):
    bot = RecordingBot()
    bot.batch_finished({"done": 3, "failed": 1})
    assert "4 job" in bot.texts()[0] and "3 xong" in bot.texts()[0] and "1 lỗi" in bot.texts()[0]


def test_chat_id_config_accepts_a_bare_number(tmp_path, config_file):
    import re

    text = re.sub(r"(?m)^chat_id\s*=.*$", "chat_id = 12345",
                  config_file.read_text(encoding="utf-8"))
    text = re.sub(r"(?m)^topic_id\s*=.*$", "topic_id = 6789", text)
    config_file.write_text(text, encoding="utf-8")
    cfg = load_config(config_file)
    assert cfg.notify.chat_id == "12345"
    assert cfg.notify.topic_id == 6789


def test_topic_id_is_included_in_send_and_from_config(config_file, monkeypatch):
    import re

    text = re.sub(r"(?m)^chat_id\s*=.*$", 'chat_id = ""', config_file.read_text(encoding="utf-8"))
    text = re.sub(r"(?m)^topic_id\s*=.*$", "topic_id = 0", text)
    config_file.write_text(text, encoding="utf-8")
    cfg = load_config(config_file)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-100123")
    monkeypatch.setenv("TELEGRAM_TOPIC_ID", "555")
    bot = notify.from_config(cfg)
    assert isinstance(bot, notify.TelegramNotifier)
    assert bot.topic_id == 555
    assert bot.chat_id == "-100123"

    rec = RecordingBot(topic_id=555)
    rec.send("Xin chào topic")
    method, payload = rec.calls[-1]
    assert method == "sendMessage"
    assert payload.get("message_thread_id") == 555


def test_chat_ids_extracts_topics(monkeypatch):
    updates = [
        {
            "update_id": 1,
            "message": {
                "chat": {"id": -100123, "title": "Nhóm Reup", "type": "supergroup"},
                "message_thread_id": 88,
                "forum_topic_created": {"name": "Video Mới"},
            },
        },
        {
            "update_id": 2,
            "message": {
                "chat": {"id": 999, "first_name": "Quang", "type": "private"},
            },
        },
    ]
    monkeypatch.setattr(notify.TelegramNotifier, "call", lambda self, m, p: updates)
    targets = notify.chat_ids("dummy_token")
    assert len(targets) == 2
    topic_tgt = [t for t in targets if t.topic_id == 88][0]
    assert topic_tgt.chat_id == "-100123"
    assert topic_tgt.name == "Nhóm Reup"
    assert topic_tgt.topic_name == "Video Mới"

    # Test tuple unpacking backward-compatibility
    chat_id, name = topic_tgt
    assert chat_id == "-100123"
    assert name == "Nhóm Reup"
