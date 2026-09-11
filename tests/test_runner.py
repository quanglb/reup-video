from pathlib import Path

import pytest

from reup.config import (
    AudioConfig, Config, ProfileConfig, ReviewConfig, SubtitleConfig, TransformConfig,
)
from reup.core.job import create_job
from reup.core.runner import artifacts_present, atomic_write, next_stage, run_job, run_stage
from reup.core.stage import StageSpec
from reup.core.store import Store


@pytest.fixture
def cfg():
    return Config(
        profile_name="test",
        profile=ProfileConfig(1, "tiny", 7, "h264_videotoolbox"),
        audio=AudioConfig("separate", 0.35),
        transform=TransformConfig(False, 1.0, 1.0),
        subtitle=SubtitleConfig("X", 64, 4, "bottom"),
        review=ReviewConfig(False),
    )


@pytest.fixture
def store(tmp_path: Path):
    s = Store(tmp_path / "reup.db")
    s.init_schema()
    yield s
    s.close()


def test_atomic_write_leaves_no_tmp_file(tmp_path: Path):
    target = tmp_path / "a.json"
    atomic_write(target, '{"x": 1}')
    assert target.read_text(encoding="utf-8") == '{"x": 1}'
    assert list(tmp_path.glob("*.tmp")) == []


def test_atomic_write_creates_parent_dirs(tmp_path: Path):
    target = tmp_path / "deep" / "nested" / "a.txt"
    atomic_write(target, "hi")
    assert target.read_text(encoding="utf-8") == "hi"


def test_artifacts_present_false_when_missing(tmp_path: Path):
    job = create_job(tmp_path, "https://a/1", "zh", job_id="j1")
    spec = StageSpec("s1", ("out.json",), lambda j, c: None)
    assert artifacts_present(job, spec) is False


def test_artifacts_present_true_when_all_exist(tmp_path: Path):
    job = create_job(tmp_path, "https://a/1", "zh", job_id="j1")
    (job.root / "out.json").write_text("{}", encoding="utf-8")
    spec = StageSpec("s1", ("out.json",), lambda j, c: None)
    assert artifacts_present(job, spec) is True


def test_artifacts_present_false_when_only_some_exist(tmp_path: Path):
    job = create_job(tmp_path, "https://a/1", "zh", job_id="j1")
    (job.root / "a.json").write_text("{}", encoding="utf-8")
    spec = StageSpec("s1", ("a.json", "b.json"), lambda j, c: None)
    assert artifacts_present(job, spec) is False


def test_next_stage_skips_completed(tmp_path: Path):
    job = create_job(tmp_path, "https://a/1", "zh", job_id="j1")
    (job.root / "a.json").write_text("{}", encoding="utf-8")
    stages = [
        StageSpec("first", ("a.json",), lambda j, c: None),
        StageSpec("second", ("b.json",), lambda j, c: None),
    ]
    assert next_stage(job, stages).name == "second"


def test_next_stage_returns_none_when_all_done(tmp_path: Path):
    job = create_job(tmp_path, "https://a/1", "zh", job_id="j1")
    (job.root / "a.json").write_text("{}", encoding="utf-8")
    stages = [StageSpec("first", ("a.json",), lambda j, c: None)]
    assert next_stage(job, stages) is None


def test_run_stage_records_success(tmp_path: Path, cfg, store: Store):
    job = create_job(tmp_path, "https://a/1", "zh", job_id="j1")
    store.upsert_job("j1", "https://a/1", "pending")
    spec = StageSpec(
        "writer", ("a.json",), lambda j, c: atomic_write(j.root / "a.json", "{}")
    )
    run_stage(job, cfg, spec, store)
    runs = store.stage_durations(stage="writer")
    assert len(runs) == 1
    assert runs[0]["ok"] == 1


def test_run_stage_raises_and_records_failure(tmp_path: Path, cfg, store: Store):
    job = create_job(tmp_path, "https://a/1", "zh", job_id="j1")
    store.upsert_job("j1", "https://a/1", "pending")

    def boom(j, c):
        raise RuntimeError("ffmpeg chết")

    spec = StageSpec("boom", ("a.json",), boom)
    with pytest.raises(RuntimeError, match="ffmpeg chết"):
        run_stage(job, cfg, spec, store)
    runs = store.stage_durations(stage="boom")
    assert runs[0]["ok"] == 0
    assert "ffmpeg chết" in runs[0]["error"]


def test_run_stage_errors_if_artifact_not_produced(tmp_path: Path, cfg, store: Store):
    job = create_job(tmp_path, "https://a/1", "zh", job_id="j1")
    store.upsert_job("j1", "https://a/1", "pending")
    spec = StageSpec("lazy", ("a.json",), lambda j, c: None)
    with pytest.raises(RuntimeError, match="a.json"):
        run_stage(job, cfg, spec, store)


def test_run_job_runs_all_stages_in_order(tmp_path: Path, cfg, store: Store):
    job = create_job(tmp_path, "https://a/1", "zh", job_id="j1")
    store.upsert_job("j1", "https://a/1", "pending")
    order: list[str] = []

    def make(name: str, artifact: str):
        def _run(j, c):
            order.append(name)
            atomic_write(j.root / artifact, "{}")

        return StageSpec(name, (artifact,), _run)

    stages = [make("one", "a.json"), make("two", "b.json")]
    assert run_job(job, cfg, store, stages) == "done"
    assert order == ["one", "two"]
    assert store.get_job("j1")["status"] == "done"


def test_run_job_resumes_from_middle(tmp_path: Path, cfg, store: Store):
    job = create_job(tmp_path, "https://a/1", "zh", job_id="j1")
    store.upsert_job("j1", "https://a/1", "pending")
    (job.root / "a.json").write_text("{}", encoding="utf-8")
    order: list[str] = []

    def make(name: str, artifact: str):
        def _run(j, c):
            order.append(name)
            atomic_write(j.root / artifact, "{}")

        return StageSpec(name, (artifact,), _run)

    run_job(job, cfg, store, [make("one", "a.json"), make("two", "b.json")])
    assert order == ["two"]


def test_run_job_marks_failed_and_stops(tmp_path: Path, cfg, store: Store):
    job = create_job(tmp_path, "https://a/1", "zh", job_id="j1")
    store.upsert_job("j1", "https://a/1", "pending")
    reached_second = []

    def boom(j, c):
        raise RuntimeError("gãy")

    def second(j, c):
        reached_second.append(True)
        atomic_write(j.root / "b.json", "{}")

    stages = [StageSpec("one", ("a.json",), boom), StageSpec("two", ("b.json",), second)]
    assert run_job(job, cfg, store, stages) == "failed"
    assert reached_second == []
    row = store.get_job("j1")
    assert row["status"] == "failed"
    assert row["stage"] == "one"
    assert "gãy" in row["error"]
