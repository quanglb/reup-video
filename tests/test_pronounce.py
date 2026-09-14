import json
from pathlib import Path

import pytest

from reup.config import Config
from reup.core.job import create_job
from reup.models import Segment, Transcript
from reup.stages.pronounce import (
    SPEC,
    load_rules,
    pronounce_all,
    run_with,
)


class FakePronounceLLM:
    def __init__(self, response: dict | list):
        self.response = response
        self.prompts: list[str] = []

    def complete_json(self, prompt: str, schema: dict) -> dict:
        self.prompts.append(prompt)
        return self.response


def test_pronounce_spec_properties():
    assert SPEC.name == "pronounce"
    assert SPEC.produces == ("tts/pronunciation.json",)


def test_load_rules_ok(tmp_path: Path):
    rules = tmp_path / "rules.md"
    rules.write_text("# Luật phiên âm\nHello -> Hê lô", encoding="utf-8")
    content = load_rules(rules)
    assert "Hello -> Hê lô" in content


def test_load_rules_missing_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError, match="Không tìm thấy"):
        load_rules(tmp_path / "non_existent.md")


def test_pronounce_all_with_segments_array():
    segments = [
        Segment(id=1, start_ms=0, end_ms=2000, text="Hello các bạn"),
        Segment(id=2, start_ms=2000, end_ms=4000, text="Hôm nay trời đẹp"),
    ]
    fake_llm = FakePronounceLLM({
        "segments": [
            {"id": 1, "tts_text": "Hê lô các bạn"},
            {"id": 2, "tts_text": "Hôm nay trời đẹp"},
        ]
    })
    result = pronounce_all(segments, "Hello -> Hê lô", fake_llm)
    assert result[1] == "Hê lô các bạn"
    assert result[2] == "Hôm nay trời đẹp"
    assert len(fake_llm.prompts) == 1


def test_pronounce_all_with_dict_response():
    segments = [
        Segment(id=1, start_ms=0, end_ms=2000, text="Gọi 0912345678"),
    ]
    fake_llm = FakePronounceLLM({"1": "Gọi không chín một hai ba bốn năm sáu bảy tám"})
    result = pronounce_all(segments, "Số điện thoại đọc rời", fake_llm)
    assert result[1] == "Gọi không chín một hai ba bốn năm sáu bảy tám"


def test_pronounce_all_missing_id_defaults_to_original_text():
    segments = [
        Segment(id=1, start_ms=0, end_ms=2000, text="Hello"),
        Segment(id=2, start_ms=2000, end_ms=4000, text="Thịt kho tàu"),
    ]
    # LLM chỉ trả về id 1, bỏ sót id 2
    fake_llm = FakePronounceLLM({"segments": [{"id": 1, "tts_text": "Hê lô"}]})
    result = pronounce_all(segments, "Hello -> Hê lô", fake_llm)
    assert result[1] == "Hê lô"
    assert result[2] == "Thịt kho tàu"


def test_pronounce_all_llm_failure_falls_back_to_original_text():
    segments = [
        Segment(id=1, start_ms=0, end_ms=2000, text="Hello các bạn"),
    ]
    class FailingLLM:
        def complete_json(self, prompt, schema):
            raise RuntimeError("LLM error")

    result = pronounce_all(segments, "Hello -> Hê lô", FailingLLM())
    assert result[1] == "Hello các bạn"


def test_run_with_creates_pronunciation_json_and_preserves_translation_json(
    tmp_path: Path, cfg_fixture: Config
):
    job = create_job(tmp_path / "jobs", "https://example.com", "vi", job_id="test-pronounce")
    trans = Transcript(
        source_lang="vi",
        segments=[
            Segment(id=1, start_ms=0, end_ms=2000, text="Hello mọi người"),
            Segment(id=2, start_ms=2000, end_ms=4000, text="Đây là AI voice"),
        ],
    )
    trans.save(job.translation_json)

    fake_llm = FakePronounceLLM({
        "segments": [
            {"id": 1, "tts_text": "Hê lô mọi người"},
            {"id": 2, "tts_text": "Đây là Ây Ai voice"},
        ]
    })

    run_with(job, cfg_fixture, fake_llm)

    pron_path = job.tts_dir / "pronunciation.json"
    assert pron_path.exists()
    data = json.loads(pron_path.read_text(encoding="utf-8"))
    assert data == {
        "1": "Hê lô mọi người",
        "2": "Đây là Ây Ai voice",
    }

    # translation.json không bị thay đổi chữ
    trans_after = Transcript.load(job.translation_json)
    assert trans_after.segments[0].text == "Hello mọi người"
    assert trans_after.segments[1].text == "Đây là AI voice"
