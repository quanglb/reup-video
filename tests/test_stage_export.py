import json
from dataclasses import replace
from pathlib import Path

import pytest

from reup.core.job import create_job
from reup.models import Segment, Transcript
from reup.stages import export as export_stage


class MetaLLM:
    def __init__(self, payload: dict):
        self.payload = payload
        self.prompts: list[str] = []

    def complete_json(self, prompt, schema):
        self.prompts.append(prompt)
        return self.payload


GOOD = {
    "title": "Con bò AI trong Minecraft",
    "description": "Một khoảnh khắc lạ trong Minecraft. Xem tới cuối nhé.",
    "hashtags": ["minecraft", "ai", "gaming"],
}


@pytest.fixture
def ready_job(tmp_path: Path, sample_video: Path):
    job = create_job(tmp_path / "jobs", "https://a/1", "en", job_id="j1")
    job.final_mp4.parent.mkdir(parents=True, exist_ok=True)
    job.final_mp4.write_bytes(sample_video.read_bytes())
    job.source_info.write_text('{"title": "AI Minecraft cow"}', encoding="utf-8")
    Transcript(
        source_lang="vi",
        segments=[Segment(id=1, start_ms=0, end_ms=3000, text="Con bò AI Minecraft")],
    ).save(job.translation_json)
    return job


def _cfg(cfg_fixture, tmp_path: Path):
    return replace(
        cfg_fixture,
        review=replace(cfg_fixture.review, output_dir=str(tmp_path / "output")),
    )


def test_writes_meta_json(ready_job, cfg_fixture, tmp_path: Path):
    export_stage.run_with(ready_job, _cfg(cfg_fixture, tmp_path), MetaLLM(GOOD))

    meta = json.loads(ready_job.meta_json.read_text(encoding="utf-8"))
    assert meta["title"] == "Con bò AI trong Minecraft"
    assert meta["hashtags"] == ["minecraft", "ai", "gaming"]
    assert meta["source_url"] == "https://a/1"


def test_copies_video_into_the_output_folder(ready_job, cfg_fixture, tmp_path: Path):
    export_stage.run_with(ready_job, _cfg(cfg_fixture, tmp_path), MetaLLM(GOOD))

    out = tmp_path / "output" / "j1.mp4"
    assert out.exists()
    assert out.stat().st_size == ready_job.final_mp4.stat().st_size


def test_writes_metadata_next_to_the_video(ready_job, cfg_fixture, tmp_path: Path):
    """Đăng bài làm tay, nên tiêu đề phải nằm ngay cạnh file để chép dán."""
    export_stage.run_with(ready_job, _cfg(cfg_fixture, tmp_path), MetaLLM(GOOD))
    assert (tmp_path / "output" / "j1.json").exists()


def test_prompt_carries_the_vietnamese_script(ready_job, cfg_fixture, tmp_path: Path):
    llm = MetaLLM(GOOD)
    export_stage.run_with(ready_job, _cfg(cfg_fixture, tmp_path), llm)
    assert "Con bò AI Minecraft" in llm.prompts[0]


def test_long_title_is_trimmed(ready_job, cfg_fixture, tmp_path: Path):
    """YouTube cắt tiêu đề quá dài; cắt ở đây để thấy trước cái sẽ đăng."""
    llm = MetaLLM({**GOOD, "title": "x" * 300})
    export_stage.run_with(ready_job, _cfg(cfg_fixture, tmp_path), llm)
    meta = json.loads(ready_job.meta_json.read_text(encoding="utf-8"))
    assert len(meta["title"]) == export_stage.MAX_TITLE_CHARS


def test_hashtags_are_normalised():
    assert export_stage.clean_hashtags(
        ["#Minecraft", " AI ", "reup video", "MINECRAFT"]
    ) == ["minecraft", "ai", "reupvideo"]


def test_hashtag_count_is_capped():
    assert len(export_stage.clean_hashtags([f"tag{i}" for i in range(50)])) == (
        export_stage.MAX_HASHTAGS
    )


def test_empty_hashtags_are_allowed():
    assert export_stage.clean_hashtags([]) == []
    assert export_stage.clean_hashtags(None) == []


def test_missing_final_video_is_an_error(tmp_path: Path, cfg_fixture):
    job = create_job(tmp_path / "jobs", "https://a/1", "en", job_id="j1")
    Transcript(source_lang="vi", segments=[]).save(job.translation_json)
    with pytest.raises(FileNotFoundError, match="compose"):
        export_stage.run_with(job, _cfg(cfg_fixture, tmp_path), MetaLLM(GOOD))


def test_spec_declares_its_artifacts():
    assert export_stage.SPEC.name == "export"
    assert set(export_stage.SPEC.produces) == {"meta.json"}
