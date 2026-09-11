# tests/test_models.py
from pathlib import Path
from reup.models import Segment, Transcript


def test_slot_ms_is_span():
    seg = Segment(id=1, start_ms=1000, end_ms=4200, text="xin chào")
    assert seg.slot_ms == 3200


def test_defaults():
    seg = Segment(id=1, start_ms=0, end_ms=1000, text="a")
    assert seg.text_source == "asr"
    assert seg.confidence == 1.0
    assert seg.flags == []


def test_flags_are_not_shared_between_instances():
    a = Segment(id=1, start_ms=0, end_ms=1, text="a")
    b = Segment(id=2, start_ms=0, end_ms=1, text="b")
    a.flags.append("overflow")
    assert b.flags == []


def test_total_ms_is_last_end():
    t = Transcript(
        source_lang="zh",
        segments=[
            Segment(id=1, start_ms=0, end_ms=3200, text="今天"),
            Segment(id=2, start_ms=3400, end_ms=7100, text="教大家"),
        ],
    )
    assert t.total_ms == 7100


def test_total_ms_of_empty_transcript_is_zero():
    assert Transcript(source_lang="zh", segments=[]).total_ms == 0


def test_roundtrip_through_json(tmp_path: Path):
    original = Transcript(
        source_lang="zh",
        segments=[
            Segment(
                id=1, start_ms=0, end_ms=3200, text="今天教大家做红烧肉",
                text_source="ocr", confidence=0.93, flags=["asr_ocr_mismatch"],
            )
        ],
    )
    path = tmp_path / "transcript.json"
    original.save(path)
    loaded = Transcript.load(path)
    assert loaded == original


def test_saved_json_is_readable_utf8(tmp_path: Path):
    path = tmp_path / "transcript.json"
    Transcript(
        source_lang="zh",
        segments=[Segment(id=1, start_ms=0, end_ms=100, text="红烧肉")],
    ).save(path)
    assert "红烧肉" in path.read_text(encoding="utf-8")
