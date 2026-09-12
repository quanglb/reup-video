"""Hai implement của ASRAdapter. Không gọi mạng, không nạp model."""
import json
import subprocess
from pathlib import Path

import pytest

from reup.adapters.capcut_stt import CapCutSTT, merge_utterances
from reup.adapters.capcut_tts import CapCutError
from reup.adapters.whisper_asr import WhisperASR
from reup.models import Segment


@pytest.fixture
def capcut_dir(tmp_path: Path) -> Path:
    d = tmp_path / "capcut"
    (d / ".venv" / "bin").mkdir(parents=True)
    (d / ".venv" / "bin" / "python").write_text("#!/bin/sh\n", encoding="utf-8")
    return d


@pytest.fixture
def sample_wav(tmp_path: Path) -> Path:
    """wav 16k mono — đúng thứ pipeline đưa vào ASR."""
    out = tmp_path / "full_16k.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
         "-i", "sine=frequency=300:duration=2:sample_rate=16000",
         "-ac", "1", str(out)],
        check=True, capture_output=True,
    )
    return out


DEFAULT_PAYLOAD = {
    "language": "zh-CN",
    "duration_ms": 6000,
    "utterances": [
        {"text": "今天教大家", "start_ms": 0, "end_ms": 1800},
        {"text": "做红烧肉", "start_ms": 1800, "end_ms": 3200},
        {"text": "  ", "start_ms": 3200, "end_ms": 3300},
        {"text": "先把肉切块", "start_ms": 4400, "end_ms": 6000},
    ],
}


def _fake_driver(monkeypatch, payload=None, *, fail_times: int = 0, stdout=None):
    state = {"calls": 0, "cmds": []}
    real_run = subprocess.run

    def fake_run(cmd, **kwargs):
        # ffmpeg phải chạy thật để có mp3 thật; chỉ chặn driver
        if "capcut_stt_driver.py" not in " ".join(str(c) for c in cmd):
            return real_run(cmd, **kwargs)
        state["calls"] += 1
        state["cmds"].append(cmd)
        state["audio_exists"] = Path(cmd[cmd.index("--audio") + 1]).exists()
        if state["calls"] <= fail_times:
            return subprocess.CompletedProcess(cmd, 1, "", "CapCut báo lỗi: quá tải")
        text = stdout if stdout is not None else json.dumps(
            payload if payload is not None else DEFAULT_PAYLOAD, ensure_ascii=False
        )
        return subprocess.CompletedProcess(cmd, 0, text, "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    return state


# --- WhisperASR ---------------------------------------------------------


def test_whisper_adapter_only_forwards_to_media_whisper(monkeypatch):
    seen = {}

    def fake(audio, model, language):
        seen.update(audio=audio, model=model, language=language)
        return "zh", [Segment(id=1, start_ms=0, end_ms=100, text="x")]

    monkeypatch.setattr("reup.adapters.whisper_asr.transcribe", fake)

    lang, segments = WhisperASR(model="tiny").transcribe(Path("a.wav"), "zh")

    assert (lang, len(segments)) == ("zh", 1)
    assert seen == {"audio": Path("a.wav"), "model": "tiny", "language": "zh"}


def test_whisper_adapter_names_itself():
    assert WhisperASR(model="tiny").name == "whisper"


# --- CapCutSTT ----------------------------------------------------------


def test_capcut_stt_turns_utterances_into_segments(
    capcut_dir: Path, sample_wav: Path, monkeypatch
):
    _fake_driver(monkeypatch)

    lang, segments = CapCutSTT(capcut_dir).transcribe(sample_wav, "zh")

    assert lang == "zh-CN"
    assert [(s.id, s.start_ms, s.end_ms) for s in segments] == [
        (1, 0, 3200),
        (2, 4400, 6000),
    ]
    assert segments[0].text == "今天教大家做红烧肉"
    assert all(s.text_source == "asr" for s in segments)


def test_capcut_stt_drops_blank_utterances(
    capcut_dir: Path, sample_wav: Path, monkeypatch
):
    """Đoạn rỗng lọt vào transcript thì translate gọi LLM cho một chuỗi trắng."""
    _fake_driver(monkeypatch)
    _, segments = CapCutSTT(capcut_dir).transcribe(sample_wav, "zh")
    assert all(s.text.strip() for s in segments)


def test_capcut_stt_uploads_mp3_not_the_16k_wav(
    capcut_dir: Path, sample_wav: Path, monkeypatch
):
    """Dịch vụ upload của CapCut đọc mp3/m4a/mp4, không chắc đọc được wav."""
    state = _fake_driver(monkeypatch)
    CapCutSTT(capcut_dir).transcribe(sample_wav, "zh")
    sent = Path(state["cmds"][0][state["cmds"][0].index("--audio") + 1])
    assert sent.suffix == ".mp3"
    assert state["audio_exists"] is True


def test_capcut_stt_cleans_up_the_temporary_mp3(
    capcut_dir: Path, sample_wav: Path, monkeypatch
):
    state = _fake_driver(monkeypatch)
    CapCutSTT(capcut_dir).transcribe(sample_wav, "zh")
    sent = Path(state["cmds"][0][state["cmds"][0].index("--audio") + 1])
    assert not sent.exists()


def test_capcut_stt_cleans_up_even_when_the_driver_fails(
    capcut_dir: Path, sample_wav: Path, monkeypatch
):
    state = _fake_driver(monkeypatch, fail_times=99)
    with pytest.raises(CapCutError):
        CapCutSTT(capcut_dir, sleep=lambda s: None).transcribe(sample_wav, "zh")
    sent = Path(state["cmds"][0][state["cmds"][0].index("--audio") + 1])
    assert not sent.exists()


def test_capcut_stt_maps_two_letter_language_codes(
    capcut_dir: Path, sample_wav: Path, monkeypatch
):
    state = _fake_driver(monkeypatch)
    CapCutSTT(capcut_dir).transcribe(sample_wav, "zh")
    cmd = state["cmds"][0]
    assert cmd[cmd.index("--language") + 1] == "zh-CN"


def test_capcut_stt_refuses_auto_language():
    """API bắt khai ngôn ngữ. Đoán bừa thì cả transcript thành rác."""
    with pytest.raises(ValueError, match="không tự nhận ngôn ngữ"):
        CapCutSTT(Path("/khong-quan-trong")).capcut_lang("auto")
    with pytest.raises(ValueError, match="không tự nhận ngôn ngữ"):
        CapCutSTT(Path("/khong-quan-trong")).capcut_lang(None)


def test_capcut_stt_rejects_unknown_language():
    with pytest.raises(ValueError, match="chưa biết ngôn ngữ"):
        CapCutSTT(Path("/khong-quan-trong")).capcut_lang("xx")


def test_capcut_stt_accepts_a_full_code_as_given():
    assert CapCutSTT(Path("/x")).capcut_lang("zh-Hant") == "zh-Hant"


def test_capcut_stt_retries_transient_failure(
    capcut_dir: Path, sample_wav: Path, monkeypatch
):
    state = _fake_driver(monkeypatch, fail_times=2)
    _, segments = CapCutSTT(capcut_dir, sleep=lambda s: None).transcribe(
        sample_wav, "zh"
    )
    assert state["calls"] == 3
    assert len(segments) == 2


def test_capcut_stt_backoff_grows(capcut_dir: Path, sample_wav: Path, monkeypatch):
    waits: list[float] = []
    _fake_driver(monkeypatch, fail_times=2)
    CapCutSTT(capcut_dir, sleep=waits.append).transcribe(sample_wav, "zh")
    assert len(waits) == 2
    assert waits[1] > waits[0]


def test_capcut_stt_gives_up_after_retry_budget(
    capcut_dir: Path, sample_wav: Path, monkeypatch
):
    _fake_driver(monkeypatch, fail_times=99)
    with pytest.raises(CapCutError, match="quá tải|3 lần"):
        CapCutSTT(capcut_dir, sleep=lambda s: None, retries=3).transcribe(
            sample_wav, "zh"
        )


def test_capcut_stt_reports_unreadable_driver_output(
    capcut_dir: Path, sample_wav: Path, monkeypatch
):
    _fake_driver(monkeypatch, stdout="Upload complete: 42%\n")
    with pytest.raises(CapCutError, match="không đọc được"):
        CapCutSTT(capcut_dir).transcribe(sample_wav, "zh")


def test_capcut_stt_reads_the_last_line_of_stdout(
    capcut_dir: Path, sample_wav: Path, monkeypatch
):
    """Client của CapCut in tiến trình upload trước; JSON là dòng cuối."""
    _fake_driver(
        monkeypatch,
        stdout="dang upload...\n" + json.dumps(DEFAULT_PAYLOAD, ensure_ascii=False),
    )
    _, segments = CapCutSTT(capcut_dir).transcribe(sample_wav, "zh")
    assert len(segments) == 2


def test_capcut_stt_missing_dir_fails_with_a_useful_message(
    tmp_path: Path, sample_wav: Path
):
    with pytest.raises(CapCutError, match="không thấy"):
        CapCutSTT(tmp_path / "khong-co").transcribe(sample_wav, "zh")


def test_capcut_stt_names_itself():
    assert CapCutSTT(Path("/x")).name == "capcut"


# --- ghép mảnh phụ đề thành câu ------------------------------------------


def _u(text: str, start: int, end: int) -> dict:
    return {"text": text, "start_ms": start, "end_ms": end}


def test_merge_rebuilds_the_sentence_measured_from_the_real_api():
    """Mẫu thật: một câu tiếng Việt 2.4 giây bị CapCut cắt thành năm mảnh."""
    got = merge_utterances([
        _u("hôm nay", 40, 440),
        _u("tôi sẽ dạy", 440, 960),
        _u("các bạn", 960, 1160),
        _u("làm thịt heo", 1160, 1840),
        _u("kho tàu", 1840, 2402),
    ])
    assert got == [_u("hôm nay tôi sẽ dạy các bạn làm thịt heo kho tàu", 40, 2402)]


def test_merge_splits_on_a_long_silence():
    got = merge_utterances([_u("câu một", 0, 1000), _u("câu hai", 2000, 3000)])
    assert [u["text"] for u in got] == ["câu một", "câu hai"]


def test_merge_keeps_a_short_gap_together():
    got = merge_utterances([_u("câu", 0, 1000), _u("một", 1200, 1500)])
    assert got == [_u("câu một", 0, 1500)]


def test_merge_splits_after_sentence_punctuation():
    got = merge_utterances([_u("xong rồi.", 0, 1000), _u("bắt đầu", 1000, 2000)])
    assert [u["text"] for u in got] == ["xong rồi.", "bắt đầu"]


def test_merge_caps_the_length_of_one_segment():
    """Người nói không nghỉ vẫn phải cắt, nếu không phụ đề thành một khối chữ."""
    stream = [_u("chữ", i * 1000, (i + 1) * 1000) for i in range(12)]
    got = merge_utterances(stream)
    assert len(got) > 1
    assert all(u["end_ms"] - u["start_ms"] <= 7000 for u in got)


def test_merge_writes_chinese_without_spaces():
    """Tiếng Trung viết liền; chèn khoảng trắng làm hỏng cả đếm âm tiết lẫn OCR."""
    got = merge_utterances([_u("今天教大家", 0, 1800), _u("做红烧肉", 1800, 3200)])
    assert got[0]["text"] == "今天教大家做红烧肉"


def test_merge_drops_blank_fragments():
    got = merge_utterances([_u("một", 0, 500), _u("   ", 500, 600)])
    assert got == [_u("một", 0, 500)]


def test_merge_of_nothing_is_nothing():
    assert merge_utterances([]) == []
