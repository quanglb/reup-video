# tests/test_cli.py
from pathlib import Path
import pytest
from reup.cli import main
from reup.stages import PHASE2_STAGES


def test_phase2_stage_order():
    """translate phải đứng giữa asr và tts: tts đọc bản dịch, không đọc bản gốc."""
    assert [s.name for s in PHASE2_STAGES] == [
        "fetch", "demux", "asr", "translate", "tts", "fit", "compose",
    ]


def test_stage_names_are_unique():
    names = [s.name for s in PHASE2_STAGES]
    assert len(names) == len(set(names))


def test_add_creates_job_and_prints_id(tmp_path: Path, capsys, config_file: Path):
    code = main([
        "--config", str(config_file), "--jobs-dir", str(tmp_path / "jobs"),
        "--db", str(tmp_path / "reup.db"),
        "add", "https://douyin.com/video/123", "--lang", "zh",
    ])
    assert code == 0
    job_id = capsys.readouterr().out.strip()
    assert (tmp_path / "jobs" / job_id / "job.json").exists()


def test_status_lists_the_job(tmp_path: Path, capsys, config_file: Path):
    common = [
        "--config", str(config_file), "--jobs-dir", str(tmp_path / "jobs"),
        "--db", str(tmp_path / "reup.db"),
    ]
    main([*common, "add", "https://a/1", "--lang", "zh"])
    capsys.readouterr()
    assert main([*common, "status"]) == 0
    out = capsys.readouterr().out
    assert "pending" in out
    assert "https://a/1" in out


def test_redo_removes_artifacts_from_named_stage(tmp_path: Path, capsys, config_file: Path):
    common = [
        "--config", str(config_file), "--jobs-dir", str(tmp_path / "jobs"),
        "--db", str(tmp_path / "reup.db"),
    ]
    main([*common, "add", "https://a/1", "--lang", "zh"])
    job_id = capsys.readouterr().out.strip()
    job_root = tmp_path / "jobs" / job_id
    (job_root / "asr.json").write_text("{}", encoding="utf-8")
    (job_root / "transcript.json").write_text("{}", encoding="utf-8")
    (job_root / "dub.wav").write_bytes(b"")

    assert main([*common, "redo", job_id, "--from", "asr"]) == 0

    assert not (job_root / "asr.json").exists()
    assert not (job_root / "transcript.json").exists()
    assert not (job_root / "dub.wav").exists()


def test_redo_rejects_unknown_stage(tmp_path: Path, capsys, config_file: Path):
    common = [
        "--config", str(config_file), "--jobs-dir", str(tmp_path / "jobs"),
        "--db", str(tmp_path / "reup.db"),
    ]
    main([*common, "add", "https://a/1", "--lang", "zh"])
    job_id = capsys.readouterr().out.strip()
    assert main([*common, "redo", job_id, "--from", "khong-co"]) == 2
    assert "khong-co" in capsys.readouterr().err


def test_run_on_missing_job_returns_error(tmp_path: Path, capsys, config_file: Path):
    code = main([
        "--config", str(config_file), "--jobs-dir", str(tmp_path / "jobs"),
        "--db", str(tmp_path / "reup.db"),
        "run", "khong-co",
    ])
    assert code == 1
    assert "khong-co" in capsys.readouterr().err
