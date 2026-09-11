"""Chốt duyệt — spec §8.2 và §8.3.

Chốt A đứng sau `translate` và TRƯỚC `tts`: đó là chỗ duy nhất sửa bản dịch mà
chưa tốn lượt gọi TTS nào và chưa render. Chốt B đứng sau `compose`.
"""
from dataclasses import replace
from pathlib import Path

import pytest

from reup.core.job import create_job
from reup.core.runner import atomic_write, blocking_gate, run_job
from reup.core.stage import StageSpec
from reup.core.store import Store
from reup.stages import stages_for


@pytest.fixture
def store(tmp_path: Path):
    s = Store(tmp_path / "reup.db")
    s.init_schema()
    yield s
    s.close()


def make(name: str, artifact: str, gate: str | None = None, log=None):
    def _run(job, cfg):
        if log is not None:
            log.append(name)
        atomic_write(job.root / artifact, "{}")

    return StageSpec(name, (artifact,), _run, gate=gate)


def test_pipeline_stops_at_an_unapproved_gate(tmp_path: Path, cfg_fixture, store):
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    store.upsert_job("j1", "https://a/1", "pending")
    ran: list[str] = []
    stages = [make("one", "a.json", gate="a", log=ran), make("two", "b.json", log=ran)]

    assert run_job(job, cfg_fixture, store, stages) == "needs_review"
    assert ran == ["one"]  # stage sau chốt KHÔNG được chạy
    assert store.get_job("j1")["stage"] == "gate_a"


def test_approving_lets_the_pipeline_continue(tmp_path: Path, cfg_fixture, store):
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    store.upsert_job("j1", "https://a/1", "pending")
    ran: list[str] = []
    stages = [make("one", "a.json", gate="a", log=ran), make("two", "b.json", log=ran)]

    run_job(job, cfg_fixture, store, stages)
    job.approve_gate("a")

    assert run_job(job, cfg_fixture, store, stages) == "done"
    assert ran == ["one", "two"]


def test_approval_survives_a_rerun(tmp_path: Path, cfg_fixture, store):
    """Duyệt xong mà chạy lại vẫn hỏi duyệt thì không bao giờ xong được."""
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    store.upsert_job("j1", "https://a/1", "pending")
    stages = [make("one", "a.json", gate="a"), make("two", "b.json")]

    run_job(job, cfg_fixture, store, stages)
    job.approve_gate("a")
    run_job(job, cfg_fixture, store, stages)

    assert run_job(job, cfg_fixture, store, stages) == "done"


def test_gate_marker_lives_in_the_job_folder(tmp_path: Path):
    """Mất reup.db thì dựng lại được; mất dấu duyệt thì phải ngồi duyệt lại."""
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    assert job.gate_approved("a") is False
    job.approve_gate("a")
    assert job.gate_approved("a") is True
    assert job.gate_marker("a").parent == job.root


def test_gates_are_independent(tmp_path: Path):
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    job.approve_gate("a")
    assert job.gate_approved("b") is False


def test_auto_approve_b_skips_the_second_gate(tmp_path: Path, cfg_fixture, store):
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    store.upsert_job("j1", "https://a/1", "pending")
    cfg = replace(cfg_fixture, review=replace(cfg_fixture.review, auto_approve_b=True))
    stages = [make("one", "a.json", gate="b"), make("two", "b.json")]

    assert run_job(job, cfg, store, stages) == "done"


def test_auto_approve_b_does_not_skip_gate_a(tmp_path: Path, cfg_fixture, store):
    """Chốt A là chỗ sửa bản dịch — bỏ qua nó thì mọi lỗi dịch đi thẳng ra video."""
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    store.upsert_job("j1", "https://a/1", "pending")
    cfg = replace(cfg_fixture, review=replace(cfg_fixture.review, auto_approve_b=True))
    stages = [make("one", "a.json", gate="a"), make("two", "b.json")]

    assert run_job(job, cfg, store, stages) == "needs_review"


def test_no_gate_blocks_before_its_stage_has_run(tmp_path: Path, cfg_fixture):
    """Chốt chỉ chặn khi stage đứng trước nó đã xong."""
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    stages = [make("one", "a.json", gate="a"), make("two", "b.json")]
    assert blocking_gate(job, stages, cfg_fixture) is None


def test_real_pipeline_puts_gate_a_before_tts(cfg_fixture):
    """Chốt A phải đứng trước tts: duyệt sau khi đã tổng hợp là duyệt muộn."""
    names = [s.name for s in stages_for(cfg_fixture)]
    gate_a_stage = next(s for s in stages_for(cfg_fixture) if s.gate == "a")
    assert names.index(gate_a_stage.name) < names.index("tts")


def test_real_pipeline_puts_gate_b_after_compose(cfg_fixture):
    gate_b_stage = next(s for s in stages_for(cfg_fixture) if s.gate == "b")
    assert gate_b_stage.name == "compose"


def test_exactly_two_gates_exist(cfg_fixture):
    gates = [s.gate for s in stages_for(cfg_fixture) if s.gate]
    assert sorted(gates) == ["a", "b"]
