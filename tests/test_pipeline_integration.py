# tests/test_pipeline_integration.py
from pathlib import Path
import pytest
from reup.core.job import create_job
from reup.core.runner import run_job
from reup.core.stage import StageSpec
from reup.core.store import Store
from reup.media.ffmpeg import probe
from reup.models import Segment
from reup.stages import PHASE2_STAGES
from reup.stages import asr as asr_stage
from reup.stages import translate as translate_stage


class FakeLLM:
    """Dịch giả lập: trả bản tiếng Việt cố định, và rút ngắn khi bị bảo viết lại."""

    def complete_json(self, prompt, schema):
        if "segments" in schema.get("properties", {}):
            return {
                "segments": [
                    {"id": 1, "text": "Hôm nay dạy làm thịt kho"},
                    {"id": 2, "text": "Trước hết thái thịt ba chỉ"},
                ]
            }
        return {"text": "Câu ngắn"}


@pytest.fixture
def offline_stages(monkeypatch, sample_video: Path):
    """Thay fetch bằng copy file local, whisper và Gemini bằng hàm giả.

    Không stage nào trong test được chạm vào mạng.
    """

    def fake_fetch_run(job, cfg):
        job.source_video.write_bytes(sample_video.read_bytes())
        job.source_info.write_text('{"title": "clip thử"}', encoding="utf-8")

    def fake_transcribe(audio, model, language):
        return "zh", [
            Segment(id=1, start_ms=0, end_ms=3000, text="今天教大家做红烧肉"),
            Segment(id=2, start_ms=3000, end_ms=6000, text="先把五花肉切成小块"),
        ]

    monkeypatch.setattr(asr_stage, "transcribe", fake_transcribe)
    monkeypatch.setattr(
        "reup.adapters.registry.make_llm", lambda cfg: FakeLLM()
    )
    stages = [
        StageSpec("fetch", ("source.mp4", "source.info.json"), fake_fetch_run)
        if s.name == "fetch" else s
        for s in PHASE2_STAGES
    ]
    return stages


def test_full_pipeline_produces_playable_video(
    tmp_path: Path, cfg_fixture, offline_stages
):
    store = Store(tmp_path / "reup.db")
    store.init_schema()
    job = create_job(tmp_path / "jobs", "https://douyin.com/v/1", "zh", job_id="j1")
    store.upsert_job(job.id, job.source_url, "pending")

    assert run_job(job, cfg_fixture, store, offline_stages) == "done"

    info = probe(job.final_mp4)
    assert info.has_video is True
    assert info.has_audio is True
    assert info.width == 540
    assert info.height == 960
    assert abs(info.duration_ms - 6000) <= 200
    store.close()


def test_every_intermediate_artifact_exists(tmp_path: Path, cfg_fixture, offline_stages):
    store = Store(tmp_path / "reup.db")
    store.init_schema()
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    store.upsert_job(job.id, job.source_url, "pending")

    run_job(job, cfg_fixture, store, offline_stages)

    for path in (
        job.source_video, job.source_info, job.full_16k, job.full_48k,
        job.asr_json, job.transcript_json, job.translation_json,
        job.tts_dir / "manifest.json",
        job.tts_dir / "fit.json", job.dub_wav, job.final_mp4,
    ):
        assert path.exists(), f"thiếu {path}"
    store.close()


def test_resume_skips_completed_stages(tmp_path: Path, cfg_fixture, offline_stages):
    store = Store(tmp_path / "reup.db")
    store.init_schema()
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    store.upsert_job(job.id, job.source_url, "pending")
    run_job(job, cfg_fixture, store, offline_stages)
    before = len(store.stage_durations())

    run_job(job, cfg_fixture, store, offline_stages)

    assert len(store.stage_durations()) == before  # không stage nào chạy lại
    store.close()


def test_deleting_one_artifact_reruns_only_from_there(
    tmp_path: Path, cfg_fixture, offline_stages
):
    store = Store(tmp_path / "reup.db")
    store.init_schema()
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    store.upsert_job(job.id, job.source_url, "pending")
    run_job(job, cfg_fixture, store, offline_stages)

    job.final_mp4.unlink()
    run_job(job, cfg_fixture, store, offline_stages)

    ran = [r["stage"] for r in store.stage_durations()]
    assert ran[-1] == "compose"
    assert ran.count("asr") == 1  # asr không chạy lại
    store.close()


def test_log_jsonl_has_one_line_per_stage(tmp_path: Path, cfg_fixture, offline_stages):
    store = Store(tmp_path / "reup.db")
    store.init_schema()
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    store.upsert_job(job.id, job.source_url, "pending")

    run_job(job, cfg_fixture, store, offline_stages)

    lines = job.log_jsonl.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == len(offline_stages)
    store.close()
