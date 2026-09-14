# tests/test_ffmpeg.py
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from reup.media.ffmpeg import FFmpegError, probe, run_ffmpeg


def test_probe_reads_dimensions_and_duration(sample_video: Path):
    info = probe(sample_video)
    assert info.width == 540
    assert info.height == 960
    assert info.has_video is True
    assert info.has_audio is True
    assert abs(info.duration_ms - 6000) <= 100


def test_probe_detects_audio_only(sample_wav: Path):
    info = probe(sample_wav)
    assert info.has_video is False
    assert info.has_audio is True
    assert abs(info.duration_ms - 2000) <= 100
    assert info.width == 0
    assert info.height == 0


def test_probe_missing_file_raises(tmp_path: Path):
    with pytest.raises(FFmpegError):
        probe(tmp_path / "khong-co.mp4")


def test_run_ffmpeg_returns_stderr(sample_wav: Path, tmp_path: Path):
    out = tmp_path / "copy.wav"
    stderr = run_ffmpeg(["-i", str(sample_wav), "-c", "copy", str(out)])
    assert out.exists()
    assert isinstance(stderr, str)


def test_run_ffmpeg_raises_with_stderr_in_message(tmp_path: Path):
    with pytest.raises(FFmpegError) as err:
        run_ffmpeg(["-i", str(tmp_path / "khong-co.mp4"), str(tmp_path / "x.mp4")])
    assert "khong-co.mp4" in str(err.value)


def test_probe_reports_video_bitrate(sample_video: Path):
    info = probe(sample_video)
    assert info.video_bps > 0


def test_probe_reports_zero_bitrate_for_audio_only(sample_wav: Path):
    assert probe(sample_wav).video_bps == 0


def test_video_bps_excludes_audio_when_derived_from_container(sample_video: Path):
    """Bitrate suy ra từ container phải trừ phần audio, không tính gộp."""
    info = probe(sample_video)
    total_bps = sample_video.stat().st_size * 8 / (info.duration_ms / 1000)
    assert info.video_bps < total_bps


def test_run_ffmpeg_timeout_raises_ffmpeg_error_not_raw_exception():
    """ffmpeg treo (TimeoutExpired) phải lộ ra FFmpegError, không phải exception gốc."""
    with patch(
        "reup.media.ffmpeg.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="ffmpeg", timeout=60),
    ):
        with pytest.raises(FFmpegError) as err:
            run_ffmpeg(["-i", "in.mp4", "out.mp4"], timeout_s=60)
    assert "60" in str(err.value)


def test_run_ffmpeg_passes_timeout_to_subprocess_run():
    with patch("reup.media.ffmpeg.subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        mock_run.return_value.stderr = ""
        run_ffmpeg(["-i", "in.mp4", "out.mp4"], timeout_s=42.0)
    assert mock_run.call_args.kwargs["timeout"] == 42.0


def test_run_ffmpeg_default_timeout_is_none():
    with patch("reup.media.ffmpeg.subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        mock_run.return_value.stderr = ""
        run_ffmpeg(["-i", "in.mp4", "out.mp4"])
    assert mock_run.call_args.kwargs["timeout"] is None
