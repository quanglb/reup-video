import json
from dataclasses import replace
from pathlib import Path

import pytest

from reup.config import TranslateConfig
from reup.core.job import create_job
from reup.models import Segment, Transcript
from reup.stages import translate as translate_stage
from reup.translate_styles import GENRES, guess_genre, style_block, validate_genre


class AnalyzingLLM:
    """Lượt đầu trả phân tích bối cảnh, các lượt sau trả bản dịch."""

    def __init__(self, analysis: dict, by_id: dict[int, str]):
        self.analysis = analysis
        self.by_id = by_id
        self.prompts: list[str] = []

    def complete_json(self, prompt: str, schema: dict) -> dict:
        self.prompts.append(prompt)
        if "genre" in schema.get("properties", {}):
            return self.analysis
        return {"segments": [{"id": i, "text": t} for i, t in self.by_id.items()]}


def _job(tmp_path: Path):
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    Transcript(
        source_lang="zh",
        segments=[Segment(id=1, start_ms=0, end_ms=3000, text="你是老板吗")],
    ).save(job.transcript_json)
    return job


ANALYSIS = {
    "genre": "hai_tinh_huong",
    "summary": "Chàng trai đi sửa xe thì bị lừa",
    "characters": [{"name": "chàng trai", "role": "khách, trẻ"}],
    "address": "chàng trai ↔ thợ: em/anh",
}


def test_guess_genre_from_metadata():
    assert guess_genre({"tags": ["喜剧", "幽默"], "categories": ["Comedy"]}) == "hai_tinh_huong"
    assert guess_genre({"title": "红烧肉的做法"}) == "huong_dan"
    assert guess_genre({}) == "chung"


def test_validate_genre_rejects_unknown_key():
    assert validate_genre("", "x") == "auto"
    with pytest.raises(ValueError, match="không hợp lệ"):
        validate_genre("kinh_di", "x")


def test_style_block_has_base_and_genre_rules():
    block = style_block("huong_dan", "- Nội dung: nấu thịt kho")
    assert "nấu thịt kho" in block
    assert "xưng hô" in block.lower()
    assert GENRES["huong_dan"].label in block


def test_translate_prompt_carries_genre_rules_and_context(tmp_path, cfg_fixture):
    job = _job(tmp_path)
    llm = AnalyzingLLM(ANALYSIS, {1: "Anh là chủ tiệm à?"})

    translate_stage.run_with(job, cfg_fixture, llm)

    prompt = llm.prompts[-1]
    assert GENRES["hai_tinh_huong"].label in prompt
    assert "em/anh" in prompt
    style = json.loads((job.root / "translate_style.json").read_text(encoding="utf-8"))
    assert style["genre"] == "hai_tinh_huong" and style["genre_source"] == "llm"


def test_job_override_beats_llm_guess(tmp_path, cfg_fixture):
    job = _job(tmp_path)
    job.set_override("genre", "drama_tinh_cam")
    llm = AnalyzingLLM(ANALYSIS, {1: "Anh là chủ à?"})

    translate_stage.run_with(job, cfg_fixture, llm)

    assert GENRES["drama_tinh_cam"].label in llm.prompts[-1]


def test_config_genre_is_used_when_no_override(tmp_path, cfg_fixture):
    job = _job(tmp_path)
    cfg = replace(cfg_fixture, translate=TranslateConfig(genre="huong_dan"))
    llm = AnalyzingLLM(ANALYSIS, {1: "Anh là chủ à?"})

    translate_stage.run_with(job, cfg, llm)

    assert GENRES["huong_dan"].label in llm.prompts[-1]


def test_unknown_llm_genre_falls_back_to_metadata(tmp_path, cfg_fixture):
    job = _job(tmp_path)
    job.source_info.write_text(json.dumps({"tags": ["美食", "做法"]}), encoding="utf-8")
    llm = AnalyzingLLM({**ANALYSIS, "genre": "???"}, {1: "Chủ à?"})

    translate_stage.run_with(job, cfg_fixture, llm)

    style = json.loads((job.root / "translate_style.json").read_text(encoding="utf-8"))
    assert style == {**style, "genre": "huong_dan", "genre_source": "metadata"}


def test_rewrite_prompt_keeps_style(tmp_path):
    prompt = translate_stage.build_rewrite_batch_prompt([(1, "một hai ba", 2)], style="LUẬT X")
    assert "LUẬT X" in prompt
