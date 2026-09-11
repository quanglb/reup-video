import json
from pathlib import Path

import pytest

from reup.core.job import create_job
from reup.models import Segment, Transcript
from reup.stages import translate as translate_stage


class ScriptedLLM:
    """Trả sẵn bản dịch theo id đoạn; ghi lại prompt để soi."""

    def __init__(self, by_id: dict[int, str]):
        self.by_id = by_id
        self.prompts: list[str] = []

    def complete_json(self, prompt: str, schema: dict) -> dict:
        self.prompts.append(prompt)
        return {
            "segments": [
                {"id": i, "text": t} for i, t in sorted(self.by_id.items())
            ]
        }


def _seed(job, specs):
    Transcript(
        source_lang="zh",
        segments=[
            Segment(id=i, start_ms=s, end_ms=e, text=t)
            for i, (s, e, t) in enumerate(specs, start=1)
        ],
    ).save(job.transcript_json)


def test_writes_translation_json(tmp_path: Path, cfg_fixture):
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    _seed(job, [(0, 3000, "今天教大家做红烧肉"), (3000, 6000, "先把肉切块")])
    llm = ScriptedLLM({1: "Hôm nay dạy làm thịt kho", 2: "Trước hết thái thịt"})

    translate_stage.run_with(job, cfg_fixture, llm)

    data = json.loads(job.translation_json.read_text(encoding="utf-8"))
    assert data["target_lang"] == "vi"
    assert [s["text"] for s in data["segments"]] == [
        "Hôm nay dạy làm thịt kho",
        "Trước hết thái thịt",
    ]


def test_keeps_timing_from_transcript(tmp_path: Path, cfg_fixture):
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    _seed(job, [(500, 3700, "今天")])
    translate_stage.run_with(job, cfg_fixture, ScriptedLLM({1: "Hôm nay"}))

    seg = json.loads(job.translation_json.read_text(encoding="utf-8"))["segments"][0]
    assert seg["start_ms"] == 500
    assert seg["end_ms"] == 3700
    assert seg["slot_ms"] == 3200


def test_records_budget_and_actual_syllables(tmp_path: Path, cfg_fixture):
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    _seed(job, [(0, 2000, "今天")])  # khe 2s -> ngân sách 9
    translate_stage.run_with(job, cfg_fixture, ScriptedLLM({1: "Hôm nay trời đẹp"}))

    seg = json.loads(job.translation_json.read_text(encoding="utf-8"))["segments"][0]
    assert seg["syllable_budget"] == 9
    assert seg["syllables"] == 4
    assert seg["revision"] == 0


def test_flags_segment_that_blows_the_budget(tmp_path: Path, cfg_fixture):
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    _seed(job, [(0, 1000, "今天")])  # khe 1s -> ngân sách 4
    llm = ScriptedLLM({1: "một hai ba bốn năm sáu bảy tám"})  # 8 âm tiết

    translate_stage.run_with(job, cfg_fixture, llm)

    seg = json.loads(job.translation_json.read_text(encoding="utf-8"))["segments"][0]
    assert "over_budget" in seg["flags"]
    assert seg["text"] == "một hai ba bốn năm sáu bảy tám"  # KHÔNG tự cắt


def test_within_budget_is_not_flagged(tmp_path: Path, cfg_fixture):
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    _seed(job, [(0, 3000, "今天")])
    translate_stage.run_with(job, cfg_fixture, ScriptedLLM({1: "Hôm nay"}))

    seg = json.loads(job.translation_json.read_text(encoding="utf-8"))["segments"][0]
    assert seg["flags"] == []


def test_prompt_carries_neighbour_context(tmp_path: Path, cfg_fixture):
    """Spec §7.6: Gemini phải thấy câu trước và câu sau để đại từ nhất quán."""
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    _seed(job, [(0, 2000, "第一句"), (2000, 4000, "第二句"), (4000, 6000, "第三句")])
    llm = ScriptedLLM({1: "câu một", 2: "câu hai", 3: "câu ba"})

    translate_stage.run_with(job, cfg_fixture, llm)

    prompt = llm.prompts[0]
    assert "第一句" in prompt and "第二句" in prompt and "第三句" in prompt


def test_prompt_states_the_syllable_budget(tmp_path: Path, cfg_fixture):
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    _seed(job, [(0, 2000, "今天")])
    llm = ScriptedLLM({1: "Hôm nay"})
    translate_stage.run_with(job, cfg_fixture, llm)
    assert "9" in llm.prompts[0]


def test_missing_segment_in_answer_is_an_error(tmp_path: Path, cfg_fixture):
    """Thiếu đoạn mà vẫn chạy tiếp là mất câu — phải gãy."""
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    _seed(job, [(0, 2000, "một"), (2000, 4000, "hai")])
    llm = ScriptedLLM({1: "chỉ có một"})

    with pytest.raises(ValueError, match="thiếu"):
        translate_stage.run_with(job, cfg_fixture, llm)


def test_empty_transcript_is_an_error(tmp_path: Path, cfg_fixture):
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    Transcript(source_lang="zh", segments=[]).save(job.transcript_json)
    with pytest.raises(ValueError, match="không có câu nào"):
        translate_stage.run_with(job, cfg_fixture, ScriptedLLM({}))


def test_translation_roundtrips_as_transcript(tmp_path: Path, cfg_fixture):
    """translation.json phải đọc lại được bằng Transcript — stage tts dùng nó."""
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    _seed(job, [(0, 3000, "今天")])
    translate_stage.run_with(job, cfg_fixture, ScriptedLLM({1: "Hôm nay"}))

    t = Transcript.load(job.translation_json)
    assert t.source_lang == "vi"
    assert t.segments[0].text == "Hôm nay"


def test_spec_declares_its_artifacts():
    assert translate_stage.SPEC.name == "translate"
    assert set(translate_stage.SPEC.produces) == {"translation.json"}
