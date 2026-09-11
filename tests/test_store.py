from pathlib import Path

import pytest

from reup.core.store import Store


@pytest.fixture
def store(tmp_path: Path):
    s = Store(tmp_path / "reup.db")
    s.init_schema()
    yield s
    s.close()


def test_init_schema_is_idempotent(tmp_path: Path):
    s = Store(tmp_path / "reup.db")
    s.init_schema()
    s.init_schema()
    s.close()


def test_upsert_then_get(store: Store):
    store.upsert_job("j1", "https://a/1", "pending")
    row = store.get_job("j1")
    assert row["id"] == "j1"
    assert row["url"] == "https://a/1"
    assert row["status"] == "pending"
    assert row["stage"] is None


def test_upsert_overwrites_status_and_stage(store: Store):
    store.upsert_job("j1", "https://a/1", "pending")
    store.upsert_job("j1", "https://a/1", "running", stage="asr")
    row = store.get_job("j1")
    assert row["status"] == "running"
    assert row["stage"] == "asr"


def test_get_missing_job_returns_none(store: Store):
    assert store.get_job("khong-co") is None


def test_rejects_unknown_status(store: Store):
    with pytest.raises(ValueError, match="lung-tung"):
        store.upsert_job("j1", "https://a/1", "lung-tung")


def test_list_jobs_filters_by_status(store: Store):
    store.upsert_job("j1", "https://a/1", "done")
    store.upsert_job("j2", "https://a/2", "failed")
    assert [r["id"] for r in store.list_jobs(status="failed")] == ["j2"]
    assert len(store.list_jobs()) == 2


def test_record_stage_run_and_read_durations(store: Store):
    store.upsert_job("j1", "https://a/1", "running")
    store.record_stage_run("j1", "asr", 100.0, 104.5, ok=True)
    store.record_stage_run("j1", "demux", 90.0, 91.0, ok=True)
    rows = store.stage_durations(stage="asr")
    assert len(rows) == 1
    assert rows[0]["stage"] == "asr"
    assert rows[0]["duration_ms"] == 4500


def test_failed_stage_run_keeps_error(store: Store):
    store.upsert_job("j1", "https://a/1", "running")
    store.record_stage_run("j1", "asr", 0.0, 1.0, ok=False, error="hết bộ nhớ")
    rows = store.stage_durations()
    assert rows[0]["ok"] == 0
    assert rows[0]["error"] == "hết bộ nhớ"


def test_seen_ledger(store: Store):
    assert store.is_seen("douyin", "v123") is False
    store.mark_seen("douyin", "v123", phash="abc")
    assert store.is_seen("douyin", "v123") is True
    assert store.is_seen("tiktok", "v123") is False


def test_mark_seen_twice_does_not_raise(store: Store):
    store.mark_seen("douyin", "v123")
    store.mark_seen("douyin", "v123", phash="abc")
    assert store.is_seen("douyin", "v123") is True
