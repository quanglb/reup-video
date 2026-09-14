"""Quét lại kênh qua Chrome: AppleScript giả, không mở trình duyệt thật."""
import json
import subprocess
import threading
import time

import pytest

from reup.adapters import douyin_chrome as dch
from reup.web import douyin_channels as dc
from reup.web.douyin_rescan import Rescanner

from tests.test_douyin_export import export, video


class FakeTab:
    """Giả tab Douyin: đang tải, rồi quét 2 nhịp, rồi xong với JSON dài nhiều đoạn."""

    def __init__(self, raw, polls=2, error=""):
        self.body = json.dumps(raw, ensure_ascii=False)
        self.polls, self.error = polls, error
        self.needle = None
        self.calls = []
        self.closed = False
        self.loading = 1

    def launch(self, url, profile):
        assert profile == "Default"
        self.needle = url.split("?", 1)[1]
        self.url = url

    def run(self, needle, js):
        assert needle == self.needle
        self.calls.append(js[:40])
        if js == dch.CLOSE:
            self.closed = True
            return "closed"
        if js == "document.readyState":
            self.loading -= 1
            return "loading" if self.loading >= 0 else "complete"
        if js.startswith("window.__reupExport.json.slice("):
            a, b = js[len("window.__reupExport.json.slice("):-1].split(",")
            return self.body[int(a):int(b)]
        # chính script quét: trả trạng thái
        if self.error:
            return json.dumps({"status": "error", "videos": 0, "error": self.error, "size": 0})
        self.polls -= 1
        if self.polls >= 0:
            return json.dumps({"status": "running", "videos": 1, "error": "", "size": 0})
        return json.dumps({"status": "done", "videos": 2, "error": "", "size": len(self.body)})


def raw_export(uid="uid123"):
    return {**export(video("a", 1, 5), video("b", 2, 9)), "sec_user_id": uid, "author": "厨师"}


def test_export_reads_json_back_in_chunks_and_closes_the_tab(monkeypatch):
    monkeypatch.setattr(dch, "CHUNK", 50)
    tab = FakeTab(raw_export())
    seen = []
    raw = dch.export_channel(
        "uid123", run=tab.run, launch=tab.launch, sleep=lambda s: None, on_progress=seen.append
    )
    assert raw["sec_user_id"] == "uid123" and len(raw["videos"]) == 2
    assert tab.url.startswith("https://www.douyin.com/user/uid123?reup_rescan=")
    assert sum(c.startswith("window.__reupExport.json.slice(") for c in tab.calls) > 1
    assert seen == [1, 1, 2]
    assert tab.closed


def test_douyin_error_is_raised_and_tab_still_closed():
    tab = FakeTab(raw_export(), error="Douyin trả rỗng 5 lần liền")
    with pytest.raises(dch.ChromeError, match="trả rỗng"):
        dch.export_channel("uid123", run=tab.run, launch=tab.launch, sleep=lambda s: None)
    assert tab.closed


def test_tab_that_never_opens_times_out():
    t = iter(range(0, 1000, 10))
    with pytest.raises(dch.ChromeError, match="không mở được tab"):
        dch.export_channel(
            "uid123", run=lambda n, js: dch.NO_TAB, launch=lambda u, p: None,
            sleep=lambda s: None, clock=lambda: next(t),
        )


@pytest.mark.parametrize(
    "stderr, hint",
    [
        ("execution error: Google Chrome got an error: Executing JavaScript through "
         "AppleScript is turned off. (12)", "Allow JavaScript from Apple Events"),
        ("execution error: Not authorized to send Apple events to Google Chrome. (-1743)",
         "Automation"),
    ],
)
def test_applescript_failures_explain_the_fix(monkeypatch, stderr, hint):
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 1, stdout="", stderr=stderr),
    )
    with pytest.raises(dch.ChromeError, match=hint):
        dch.osascript("x", "1")


def wait_done(rs, uid):
    for _ in range(200):
        s = rs.status(uid)
        if s["status"] != "running":
            return s
        time.sleep(0.01)
    raise AssertionError("quét lại không xong")


def test_rescanner_writes_a_new_export_that_becomes_the_channel_file(tmp_path):
    rs = Rescanner(tmp_path, export=lambda uid, on_progress: raw_export(uid))
    assert rs.status("uid123") == {"status": "idle"}
    rs.start("uid123")
    s = wait_done(rs, "uid123")
    assert (s["status"], s["videos"]) == ("done", 2)
    assert [e["path"] for e in dc.exports(tmp_path)] == [s["path"]]


def test_rescanner_runs_one_channel_at_a_time_and_reports_errors(tmp_path):
    gate = threading.Event()

    def slow(uid, on_progress):
        gate.wait(2)
        raise dch.ChromeError("Chrome đang tắt chạy JavaScript")

    rs = Rescanner(tmp_path, export=slow)
    rs.start("u1")
    assert rs.start("u1")["status"] == "running"  # bấm lại cùng kênh: không quét hai lần
    with pytest.raises(RuntimeError, match="kênh khác"):
        rs.start("u2")
    gate.set()
    s = wait_done(rs, "u1")
    assert s["status"] == "error" and "JavaScript" in s["error"]
