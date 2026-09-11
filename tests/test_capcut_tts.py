import json
import subprocess
from pathlib import Path

import pytest

from reup.adapters.capcut_tts import CapCutTTS, CapCutError
from reup.media.audio import duration_ms


@pytest.fixture
def capcut_dir(tmp_path: Path) -> Path:
    """Thư mục CapCut giả: chỉ cần Voice.json và một .venv/bin/python."""
    d = tmp_path / "capcut"
    (d / ".venv" / "bin").mkdir(parents=True)
    (d / ".venv" / "bin" / "python").write_text("#!/bin/sh\n", encoding="utf-8")
    (d / "Voice.json").write_text(
        json.dumps(
            [
                {
                    "lan": "vi", "lang": "vi-VN", "voice_type": "BV074_streaming",
                    "display_name": "Cô Gái Hoạt Ngôn", "resource_id": "7102355709945188865",
                },
                {
                    "lan": "vi", "lang": "vi-VN", "voice_type": "vi_female_huong",
                    "display_name": "Giọng Nữ Phổ Thông", "resource_id": "7264854897953083905",
                },
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return d


def _fake_driver(monkeypatch, mp3_source: Path, *, fail_times: int = 0):
    """Giả subprocess: chép sẵn một mp3 thật rồi in JSON như driver."""
    state = {"calls": 0, "cmds": []}
    real_run = subprocess.run

    def fake_run(cmd, **kwargs):
        # chỉ chặn driver; ffmpeg/ffprobe phải chạy thật để đo được wav
        if "capcut_driver.py" not in " ".join(str(c) for c in cmd):
            return real_run(cmd, **kwargs)
        state["calls"] += 1
        state["cmds"].append(cmd)
        if state["calls"] <= fail_times:
            return subprocess.CompletedProcess(cmd, 1, "", "CapCut báo lỗi: quá tải")
        out = Path(cmd[cmd.index("--out") + 1])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(mp3_source.read_bytes())
        payload = json.dumps(
            {"path": str(out), "bytes": out.stat().st_size,
             "duration_ms": 2000, "hit_cache": False}
        )
        return subprocess.CompletedProcess(cmd, 0, payload, "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    return state


@pytest.fixture
def sample_mp3(tmp_path: Path) -> Path:
    """mp3 24kHz mono 2 giây — đúng thứ CapCut trả về."""
    out = tmp_path / "voice.mp3"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
         "-i", "sine=frequency=300:duration=2:sample_rate=24000",
         "-ac", "1", "-c:a", "libmp3lame", str(out)],
        check=True, capture_output=True,
    )
    return out


def test_lists_vietnamese_voices_from_voice_json(capcut_dir: Path):
    voices = CapCutTTS(capcut_dir).voices("vi")
    assert len(voices) == 2
    assert {v.id for v in voices} == {"BV074_streaming", "vi_female_huong"}
    assert all(v.lang == "vi" for v in voices)


def test_no_english_voices_exist(capcut_dir: Path):
    """Voice.json thật chỉ có 22 giọng vi — đầu ra của pipeline luôn tiếng Việt."""
    assert CapCutTTS(capcut_dir).voices("en") == []


def test_synthesize_converts_mp3_to_48k_stereo_wav(
    capcut_dir: Path, tmp_path: Path, monkeypatch, sample_mp3: Path
):
    """Pipeline dựng timeline bằng wav 48k stereo; CapCut trả mp3 24k mono."""
    _fake_driver(monkeypatch, sample_mp3)
    out = tmp_path / "seg_0001.wav"

    result = CapCutTTS(capcut_dir).synthesize("Hôm nay", "vi", "BV074_streaming", out)

    assert out.exists()
    assert result.path == out
    from reup.media.ffmpeg import probe
    info = probe(out)
    assert info.has_audio is True
    assert abs(duration_ms(out) - 2000) <= 100


def test_actual_ms_is_measured_from_the_wav_not_trusted_from_api(
    capcut_dir: Path, tmp_path: Path, monkeypatch, sample_mp3: Path
):
    """fit quyết định dựa trên actual_ms, nên phải là số đo thật của file."""
    _fake_driver(monkeypatch, sample_mp3)
    result = CapCutTTS(capcut_dir).synthesize(
        "x", "vi", "BV074_streaming", tmp_path / "a.wav"
    )
    assert abs(result.actual_ms - duration_ms(result.path)) <= 1


def test_rejects_unknown_voice(capcut_dir: Path, tmp_path: Path):
    with pytest.raises(ValueError, match="khong-co"):
        CapCutTTS(capcut_dir).synthesize("x", "vi", "khong-co", tmp_path / "a.wav")


def test_passes_resource_id_matching_the_voice(
    capcut_dir: Path, tmp_path: Path, monkeypatch, sample_mp3: Path
):
    state = _fake_driver(monkeypatch, sample_mp3)
    CapCutTTS(capcut_dir).synthesize("x", "vi", "vi_female_huong", tmp_path / "a.wav")
    cmd = state["cmds"][0]
    assert cmd[cmd.index("--resource-id") + 1] == "7264854897953083905"
    assert cmd[cmd.index("--voice") + 1] == "vi_female_huong"


def test_never_sends_a_rate_other_than_one(
    capcut_dir: Path, tmp_path: Path, monkeypatch, sample_mp3: Path
):
    """rate của CapCut không có tác dụng (spec §7.7) — đừng giả vờ là có."""
    state = _fake_driver(monkeypatch, sample_mp3)
    CapCutTTS(capcut_dir).synthesize("x", "vi", "BV074_streaming", tmp_path / "a.wav")
    cmd = state["cmds"][0]
    assert cmd[cmd.index("--rate") + 1] == "1.0"


def test_retries_on_transient_failure(
    capcut_dir: Path, tmp_path: Path, monkeypatch, sample_mp3: Path
):
    """API gãy sau ~15 request liên tiếp — phải thử lại, không bỏ cuộc ngay."""
    state = _fake_driver(monkeypatch, sample_mp3, fail_times=2)
    tts = CapCutTTS(capcut_dir, sleep=lambda s: None)
    result = tts.synthesize("x", "vi", "BV074_streaming", tmp_path / "a.wav")
    assert state["calls"] == 3
    assert result.actual_ms > 0


def test_gives_up_after_retry_budget(
    capcut_dir: Path, tmp_path: Path, monkeypatch, sample_mp3: Path
):
    _fake_driver(monkeypatch, sample_mp3, fail_times=99)
    tts = CapCutTTS(capcut_dir, sleep=lambda s: None, retries=3)
    with pytest.raises(CapCutError, match="quá tải|3 lần"):
        tts.synthesize("x", "vi", "BV074_streaming", tmp_path / "a.wav")


def test_backoff_grows(capcut_dir: Path, tmp_path: Path, monkeypatch, sample_mp3: Path):
    waits: list[float] = []
    _fake_driver(monkeypatch, sample_mp3, fail_times=2)
    CapCutTTS(capcut_dir, sleep=waits.append).synthesize(
        "x", "vi", "BV074_streaming", tmp_path / "a.wav"
    )
    assert len(waits) == 2
    assert waits[1] > waits[0]


def test_missing_capcut_dir_fails_with_a_useful_message(tmp_path: Path):
    with pytest.raises(CapCutError, match="không thấy"):
        CapCutTTS(tmp_path / "khong-co").voices("vi")
