import json
from pathlib import Path

from reup.core.job import create_job
from reup.media.audio import duration_ms
from reup.models import Segment, Transcript
from reup.stages import fit as fit_stage
from reup.stages import tts as tts_stage


def _seed(job, specs: list[tuple[int, int, str]]) -> None:
    """specs: danh sách (start_ms, end_ms, text)."""
    Transcript(
        source_lang="zh",
        segments=[
            Segment(id=i, start_ms=s, end_ms=e, text=t)
            for i, (s, e, t) in enumerate(specs, start=1)
        ],
    ).save(job.transcript_json)


def test_builds_dub_covering_whole_video(tmp_path: Path, cfg_fixture):
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    _seed(job, [(0, 3000, "今天教大家"), (3000, 6000, "先把肉切块")])
    tts_stage.run(job, cfg_fixture)

    fit_stage.run(job, cfg_fixture)

    assert job.dub_wav.exists()
    assert abs(duration_ms(job.dub_wav) - 6000) <= 150


def test_overlong_segment_is_flagged(tmp_path: Path, cfg_fixture):
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    # 20 ký tự Hán x 220ms = 4400ms nhét vào khe 1000ms → ratio 4.4 → overflow
    _seed(job, [(0, 1000, "今天教大家做红烧肉先把肉切块再下锅炒香")])
    tts_stage.run(job, cfg_fixture)

    fit_stage.run(job, cfg_fixture)

    t = Transcript.load(job.transcript_json)
    assert "overflow" in t.segments[0].flags


def test_well_fitting_segment_is_not_flagged(tmp_path: Path, cfg_fixture):
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    # 5 ký tự x 220ms = 1100ms trong khe 3000ms → pad, không cờ
    _seed(job, [(0, 3000, "今天教大家")])
    tts_stage.run(job, cfg_fixture)

    fit_stage.run(job, cfg_fixture)

    assert Transcript.load(job.transcript_json).segments[0].flags == []


def test_writes_fit_report(tmp_path: Path, cfg_fixture):
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    _seed(job, [(0, 3000, "今天教大家")])
    tts_stage.run(job, cfg_fixture)

    fit_stage.run(job, cfg_fixture)

    report = json.loads((job.tts_dir / "fit.json").read_text(encoding="utf-8"))
    assert report["segments"][0]["action"] in {"pad", "tempo", "tempo_capped", "overflow"}
    assert report["segments"][0]["id"] == 1


def test_spec_declares_its_artifacts():
    assert fit_stage.SPEC.name == "fit"
    assert set(fit_stage.SPEC.produces) == {"dub.wav"}
