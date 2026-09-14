import json
from pathlib import Path

import pytest

from reup.adapters.stub_tts import StubTTS
from reup.core.job import create_job
from reup.media.audio import duration_ms
from reup.models import Segment, Transcript
from reup.stages import fit as fit_stage
from reup.stages import tts as tts_stage


class NoLLM:
    """Không đoạn nào được phép cần viết lại trong các test này."""

    def complete_json(self, prompt, schema):
        raise AssertionError("không mong đợi gọi LLM ở đây")


def _seed(job, specs: list[tuple[int, int, str]]) -> None:
    """specs: danh sách (start_ms, end_ms, text tiếng Việt)."""
    Transcript(
        source_lang="vi",
        segments=[
            Segment(id=i, start_ms=s, end_ms=e, text=t)
            for i, (s, e, t) in enumerate(specs, start=1)
        ],
    ).save(job.translation_json)


def _prepare(job, cfg, specs):
    _seed(job, specs)
    tts_stage.run_with(job, cfg, StubTTS())


def test_builds_dub_covering_whole_video(tmp_path: Path, cfg_fixture):
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    _prepare(job, cfg_fixture, [(0, 3000, "Hôm nay dạy làm"), (3000, 6000, "Trước thái thịt")])

    fit_stage.run_with(job, cfg_fixture, StubTTS(), NoLLM())

    assert job.dub_wav.exists()
    assert abs(duration_ms(job.dub_wav) - 6000) <= 150


def test_well_fitting_segment_is_not_flagged(tmp_path: Path, cfg_fixture):
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    # 3 âm tiết x 220ms = 660ms trong khe 3000ms -> pad, không cờ
    _prepare(job, cfg_fixture, [(0, 3000, "Hôm nay nắng")])

    fit_stage.run_with(job, cfg_fixture, StubTTS(), NoLLM())

    assert Transcript.load(job.translation_json).segments[0].flags == []


def test_writes_fit_report(tmp_path: Path, cfg_fixture):
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    _prepare(job, cfg_fixture, [(0, 3000, "Hôm nay nắng")])

    fit_stage.run_with(job, cfg_fixture, StubTTS(), NoLLM())

    report = json.loads((job.tts_dir / "fit.json").read_text(encoding="utf-8"))
    assert report["segments"][0]["action"] in {"pad", "tempo", "tempo_capped", "overflow"}
    assert report["segments"][0]["id"] == 1


def test_fails_on_empty_translation(tmp_path: Path, cfg_fixture):
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    Transcript(source_lang="vi", segments=[]).save(job.translation_json)
    with pytest.raises(ValueError, match="không có câu nào"):
        fit_stage.run_with(job, cfg_fixture, StubTTS(), NoLLM())


def test_subtitles_are_generated_after_the_text_is_final(tmp_path: Path, cfg_fixture):
    """Sinh sub ở translate thì sub hiện câu cũ còn giọng đọc câu đã viết lại."""
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    _prepare(job, cfg_fixture, [(0, 3000, "Hôm nay nắng")])

    fit_stage.run_with(job, cfg_fixture, StubTTS(), NoLLM())

    ass = job.sub_ass.read_text(encoding="utf-8")
    assert "Hôm nay nắng" in ass
    assert "Dialogue:" in ass


def test_spec_declares_its_artifacts():
    assert fit_stage.SPEC.name == "fit"
    assert set(fit_stage.SPEC.produces) == {"dub.wav", "sub.ass"}
