import json
from pathlib import Path

import pytest

from reup.adapters.cassette_llm import CassetteLLM, CassetteMiss

SCHEMA = {"type": "object", "properties": {"text": {"type": "string"}}}


def test_replays_a_recorded_answer(tmp_path: Path):
    cassette = tmp_path / "c.json"
    llm = CassetteLLM(cassette, recorder=lambda p, s: {"text": "xin chào"})
    first = llm.complete_json("dịch câu này", SCHEMA)

    replayed = CassetteLLM(cassette).complete_json("dịch câu này", SCHEMA)
    assert replayed == first == {"text": "xin chào"}


def test_miss_without_recorder_raises_instead_of_calling_network(tmp_path: Path):
    """Thiếu bản ghi phải gãy to, không được âm thầm gọi mạng trong test."""
    llm = CassetteLLM(tmp_path / "c.json")
    with pytest.raises(CassetteMiss, match="chưa có bản ghi"):
        llm.complete_json("prompt lạ", SCHEMA)


def test_different_prompts_get_different_entries(tmp_path: Path):
    cassette = tmp_path / "c.json"
    calls = []

    def rec(prompt, schema):
        calls.append(prompt)
        return {"text": prompt.upper()}

    llm = CassetteLLM(cassette, recorder=rec)
    assert llm.complete_json("một", SCHEMA) == {"text": "MỘT"}
    assert llm.complete_json("hai", SCHEMA) == {"text": "HAI"}
    assert len(calls) == 2

    # lần hai không gọi recorder nữa
    llm2 = CassetteLLM(cassette, recorder=rec)
    assert llm2.complete_json("một", SCHEMA) == {"text": "MỘT"}
    assert len(calls) == 2


def test_cassette_file_is_readable_utf8_json(tmp_path: Path):
    cassette = tmp_path / "c.json"
    CassetteLLM(cassette, recorder=lambda p, s: {"text": "thịt kho tàu"}).complete_json(
        "x", SCHEMA
    )
    raw = cassette.read_text(encoding="utf-8")
    assert "thịt kho tàu" in raw
    assert json.loads(raw)


def test_recording_is_atomic(tmp_path: Path):
    """Ghi giữa chừng chết thì không để lại cassette hỏng."""
    cassette = tmp_path / "c.json"
    CassetteLLM(cassette, recorder=lambda p, s: {"text": "a"}).complete_json("x", SCHEMA)
    assert list(tmp_path.glob("*.tmp")) == []


def test_key_ignores_nothing_relevant(tmp_path: Path):
    """Prompt khác một ký tự là bản ghi khác — không được trộn lẫn."""
    cassette = tmp_path / "c.json"
    llm = CassetteLLM(cassette, recorder=lambda p, s: {"text": p})
    a = llm.complete_json("câu a", SCHEMA)
    b = llm.complete_json("câu b", SCHEMA)
    assert a != b
