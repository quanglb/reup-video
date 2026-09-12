"""Luồng nền chạy job cho Web UI."""
import threading
import time
from pathlib import Path

import pytest

from reup.core.job import create_job
from reup.core.store import Store
from reup.web.runner import BackgroundRunner


@pytest.fixture
def runner(tmp_path: Path, config_file: Path) -> BackgroundRunner:
    jobs = tmp_path / "jobs"
    jobs.mkdir()
    db = tmp_path / "reup.db"
    Store(db).init_schema()
    return BackgroundRunner(config_file, jobs, db)


def wait_until(cond, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


def test_a_job_that_cannot_be_loaded_is_marked_failed(runner):
    """`run_job` chỉ ghi "failed" cho lỗi TRONG stage. Lỗi trước đó không ai
    ghi, nên job sẽ đứng mãi ở "pending" mà không nói vì sao."""
    s = Store(runner.db_path)
    s.upsert_job("mo-coi", "https://a/1", "pending")
    s.close()

    assert runner.start("mo-coi") is True
    assert wait_until(lambda: not runner.is_running("mo-coi"))

    s = Store(runner.db_path)
    row = s.get_job("mo-coi")
    s.close()
    assert row["status"] == "failed"
    assert "không khởi động được" in row["error"]


def test_the_same_job_is_never_started_twice(runner, monkeypatch):
    """Hai luồng cùng một job là đua ghi trên cùng một file artifact."""
    gate = threading.Event()
    monkeypatch.setattr(
        "reup.web.runner.load_config", lambda p: gate.wait(5) or (_ for _ in ()).throw(RuntimeError("dừng"))
    )
    create_job(runner.jobs_dir, "https://a/1", "auto", job_id="j1")

    assert runner.start("j1") is True
    assert wait_until(lambda: runner.is_running("j1"))
    assert runner.start("j1") is False  # đang chạy
    assert runner.live() == {"j1"}

    gate.set()
    assert wait_until(lambda: not runner.is_running("j1"))
    assert runner.start("j1") is True  # xong rồi thì chạy lại được


def test_concurrency_comes_from_the_profile(runner, monkeypatch):
    """Web phải tôn trọng profile.concurrency như `reup run --all`."""
    import reup.web.runner as mod
    from reup.config import load_config

    # Để load_config chạy thật (semaphore mới dựng đúng), rồi gãy ngay sau đó.
    monkeypatch.setattr(
        mod, "load_job", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("dừng"))
    )
    runner.start("bat-ky")
    assert wait_until(lambda: not runner.is_running("bat-ky"))

    workers = load_config(runner.config_path).profile.concurrency
    slots = runner._semaphore()
    got = sum(1 for _ in range(workers + 3) if slots.acquire(blocking=False))
    assert got == workers


def test_a_broken_config_does_not_wedge_the_job(runner, monkeypatch):
    """Lỗi lúc dựng semaphore từng ném thẳng vào request và bỏ job kẹt trong
    sổ `_live`, không ai bấm chạy lại được nữa."""
    import reup.web.runner as mod

    monkeypatch.setattr(
        mod, "load_config", lambda p: (_ for _ in ()).throw(RuntimeError("config hỏng"))
    )
    assert runner.start("j1") is True
    assert wait_until(lambda: not runner.is_running("j1"))
    assert runner.live() == set()
