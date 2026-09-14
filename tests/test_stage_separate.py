import subprocess
from pathlib import Path

import pytest

from reup.core.job import create_job
from reup.media.demucs import DemucsError, separate
from reup.media.ffmpeg import probe
from reup.stages import asr as asr_stage
from reup.stages import separate as separate_stage


def _fake_demucs(monkeypatch, sample_wav: Path, *, oom_times: int = 0, fail: bool = False):
    """Giả Demucs: ghi ra hai stem thật bằng ffmpeg, không nạp model."""
    state = {"calls": 0, "segments": [], "src": ""}
    real_run = subprocess.run

    def fake_run(cmd, **kwargs):
        if "demucs" not in cmd:
            return real_run(cmd, **kwargs)
        state["calls"] += 1
        state["segments"].append(int(cmd[cmd.index("--segment") + 1]))
        state["src"] = cmd[-1]
        if fail:
            return subprocess.CompletedProcess(cmd, 1, "", "lỗi gì đó không phải bộ nhớ")
        if state["calls"] <= oom_times:
            return subprocess.CompletedProcess(cmd, 1, "", "MPS backend out of memory")
        out_dir = Path(cmd[cmd.index("-o") + 1])
        src = Path(cmd[-1])
        model = cmd[cmd.index("-n") + 1]
        stem = out_dir / model / src.stem
        stem.mkdir(parents=True, exist_ok=True)
        for name in ("vocals.wav", "no_vocals.wav"):
            real_run(
                ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src),
                 "-ar", "44100", "-ac", "2", str(stem / name)],
                capture_output=True, check=True,
            )
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    return state


def test_separate_returns_both_stems(tmp_path: Path, monkeypatch, sample_wav: Path):
    _fake_demucs(monkeypatch, sample_wav)
    vocals, bgm = separate(sample_wav, tmp_path / "work", segment=7)
    assert vocals.exists() and bgm.exists()


def test_out_of_memory_retries_with_a_smaller_segment(
    tmp_path: Path, monkeypatch, sample_wav: Path
):
    """spec §12: hết bộ nhớ thì giảm segment rồi thử lại một lần."""
    state = _fake_demucs(monkeypatch, sample_wav, oom_times=1)
    separate(sample_wav, tmp_path / "work", segment=8)
    assert state["segments"] == [8, 4]


def test_gives_up_after_one_retry(tmp_path: Path, monkeypatch, sample_wav: Path):
    state = _fake_demucs(monkeypatch, sample_wav, oom_times=99)
    with pytest.raises(DemucsError, match="hết bộ nhớ"):
        separate(sample_wav, tmp_path / "work", segment=8)
    assert state["calls"] == 2  # không thử mãi


def test_non_memory_error_does_not_retry(tmp_path: Path, monkeypatch, sample_wav: Path):
    """Lỗi không phải bộ nhớ thì thử lại cũng vô ích — gãy ngay cho nhanh."""
    state = _fake_demucs(monkeypatch, sample_wav, fail=True)
    with pytest.raises(DemucsError, match="thoát với mã"):
        separate(sample_wav, tmp_path / "work", segment=7)
    assert state["calls"] == 1


def test_stage_writes_vocals_at_16k_mono_for_asr(
    tmp_path: Path, monkeypatch, sample_wav: Path, cfg_fixture
):
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    job.full_48k.parent.mkdir(parents=True, exist_ok=True)
    job.full_48k.write_bytes(sample_wav.read_bytes())
    _fake_demucs(monkeypatch, sample_wav)

    separate_stage.run(job, cfg_fixture)

    assert job.vocals.exists()
    info = probe(job.vocals)
    assert info.has_audio is True


def test_stage_writes_bgm_at_48k_stereo_for_mixing(
    tmp_path: Path, monkeypatch, sample_wav: Path, cfg_fixture
):
    """Demucs ra 44.1kHz; timeline và amix cần 48k, nên phải resample."""
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    job.full_48k.parent.mkdir(parents=True, exist_ok=True)
    job.full_48k.write_bytes(sample_wav.read_bytes())
    _fake_demucs(monkeypatch, sample_wav)

    separate_stage.run(job, cfg_fixture)

    import json

    raw = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries",
         "stream=sample_rate,channels", "-of", "json", str(job.bgm)],
        capture_output=True, text=True,
    ).stdout
    stream = json.loads(raw)["streams"][0]
    assert stream["sample_rate"] == "48000"
    assert stream["channels"] == 2


def test_stage_reads_the_48k_source_not_the_16k_one(
    tmp_path: Path, monkeypatch, sample_wav: Path, cfg_fixture
):
    """Đưa Demucs bản 16k mono thì vừa tách kém vừa cho nhạc nền không dùng được."""
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    job.full_48k.parent.mkdir(parents=True, exist_ok=True)
    job.full_48k.write_bytes(sample_wav.read_bytes())
    state = _fake_demucs(monkeypatch, sample_wav)

    separate_stage.run(job, cfg_fixture)

    assert state["src"].endswith("full_48k.wav")


def test_spec_declares_its_artifacts():
    assert separate_stage.SPEC.name == "separate"
    assert set(separate_stage.SPEC.produces) == {"audio/vocals.wav", "audio/bgm.wav"}


# --- asr chọn nguồn audio ---------------------------------------------------

def test_asr_prefers_separated_vocals(tmp_path: Path):
    """Nhạc nền to là nguyên nhân chính làm ASR sai — nghe giọng đã tách thì đỡ."""
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    job.full_16k.parent.mkdir(parents=True, exist_ok=True)
    job.full_16k.write_bytes(b"")
    job.vocals.write_bytes(b"")
    assert asr_stage.pick_audio(job) == job.vocals


def test_asr_falls_back_to_mixed_audio_without_vocals(tmp_path: Path):
    """drop_original bỏ stage separate nên không có vocals.wav."""
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    job.full_16k.parent.mkdir(parents=True, exist_ok=True)
    job.full_16k.write_bytes(b"")
    assert asr_stage.pick_audio(job) == job.full_16k
