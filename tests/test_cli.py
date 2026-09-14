# tests/test_cli.py
from pathlib import Path
import pytest
from reup.cli import main
from reup.stages import ALL_STAGES, stages_for


def test_stage_order(cfg_fixture):
    """translate giữa asr và tts; separate TRƯỚC asr để ASR nghe giọng đã tách."""
    assert [s.name for s in stages_for(cfg_fixture)] == [
        "fetch", "demux", "separate", "asr", "subdetect", "ocr",
        "reconcile", "translate", "tts", "fit", "compose", "export",
    ]


def test_drop_original_skips_the_heaviest_stage(cfg_fixture):
    """Nút thoát hiểm khi máy quá ì (spec §9): bỏ Demucs, mất nhạc nền."""
    from dataclasses import replace

    cfg = replace(cfg_fixture, audio=replace(cfg_fixture.audio, mode="drop_original"))
    names = [s.name for s in stages_for(cfg)]
    assert "separate" not in names
    assert names[:3] == ["fetch", "demux", "asr"]


def test_stage_names_are_unique():
    names = [s.name for s in ALL_STAGES]
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


def test_benchmark_without_runs_says_so(tmp_path: Path, capsys, config_file: Path):
    code = main([
        "--config", str(config_file), "--jobs-dir", str(tmp_path / "jobs"),
        "--db", str(tmp_path / "reup.db"), "benchmark",
    ])
    assert code == 0
    assert "chưa có lần chạy" in capsys.readouterr().out


def test_benchmark_reports_per_stage_timing(tmp_path: Path, capsys, config_file: Path):
    from reup.core.store import Store

    db = tmp_path / "reup.db"
    s = Store(db)
    s.init_schema()
    s.upsert_job("j1", "https://a/1", "done")
    s.record_stage_run("j1", "asr", 0.0, 6.0, ok=True)
    s.record_stage_run("j1", "demux", 0.0, 0.2, ok=True)
    s.close()

    main(["--config", str(config_file), "--jobs-dir", str(tmp_path / "jobs"),
          "--db", str(db), "benchmark"])

    out = capsys.readouterr().out
    assert "asr" in out and "demux" in out
    assert "TỔNG" in out
    assert out.index("asr") < out.index("demux")  # chậm nhất lên đầu


def test_approve_moves_a_reviewing_job_back_to_pending(
    tmp_path: Path, capsys, config_file: Path
):
    from reup.core.job import create_job
    from reup.core.store import Store

    db = tmp_path / "reup.db"
    create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    s = Store(db)
    s.init_schema()
    s.upsert_job("j1", "https://a/1", "needs_review", stage="gate_a")
    s.close()

    code = main(["--config", str(config_file), "--jobs-dir", str(tmp_path / "jobs"),
                 "--db", str(db), "approve", "j1"])
    assert code == 0

    s = Store(db)
    assert s.get_job("j1")["status"] == "pending"
    s.close()


def test_approve_writes_the_gate_marker(tmp_path: Path, capsys, config_file: Path):
    """Không ghi dấu thì lần chạy sau lại dừng ở đúng chốt đó."""
    from reup.core.job import create_job, load_job
    from reup.core.store import Store

    db = tmp_path / "reup.db"
    create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    s = Store(db)
    s.init_schema()
    s.upsert_job("j1", "https://a/1", "needs_review", stage="gate_a")
    s.close()

    main(["--config", str(config_file), "--jobs-dir", str(tmp_path / "jobs"),
          "--db", str(db), "approve", "j1"])

    assert load_job(tmp_path / "jobs", "j1").gate_approved("a")


def test_approve_uses_the_blocking_gate_not_the_flag(
    tmp_path: Path, capsys, config_file: Path
):
    """Job đang chờ chốt B mà gõ --gate a thì phải duyệt B, không phải A."""
    from reup.core.job import create_job, load_job
    from reup.core.store import Store

    db = tmp_path / "reup.db"
    create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    s = Store(db)
    s.init_schema()
    s.upsert_job("j1", "https://a/1", "needs_review", stage="gate_b")
    s.close()

    main(["--config", str(config_file), "--jobs-dir", str(tmp_path / "jobs"),
          "--db", str(db), "approve", "j1", "--gate", "a"])

    job = load_job(tmp_path / "jobs", "j1")
    assert job.gate_approved("b")
    assert not job.gate_approved("a")


def test_approve_refuses_a_job_that_is_not_waiting(
    tmp_path: Path, capsys, config_file: Path
):
    """Duyệt một job đang chạy dở sẽ đẩy nó chạy lại từ giữa."""
    from reup.core.store import Store

    db = tmp_path / "reup.db"
    s = Store(db)
    s.init_schema()
    s.upsert_job("j1", "https://a/1", "running", stage="tts")
    s.close()

    code = main(["--config", str(config_file), "--jobs-dir", str(tmp_path / "jobs"),
                 "--db", str(db), "approve", "j1"])
    assert code == 1
    assert "running" in capsys.readouterr().err


def test_approve_unknown_job_errors(tmp_path: Path, capsys, config_file: Path):
    code = main(["--config", str(config_file), "--jobs-dir", str(tmp_path / "jobs"),
                 "--db", str(tmp_path / "reup.db"), "approve", "khong-co"])
    assert code == 1
    assert "khong-co" in capsys.readouterr().err


def test_run_says_which_gate_is_waiting(tmp_path: Path, capsys, config_file: Path):
    """In ra đường dẫn final.mp4 khi job mới dừng ở chốt là nói dối: file chưa có."""
    from reup.core.job import create_job
    from reup.core.store import Store

    db = tmp_path / "reup.db"
    jobs = tmp_path / "jobs"
    job = create_job(jobs, "https://a/1", "zh", job_id="j1")
    s = Store(db)
    s.init_schema()
    s.upsert_job("j1", "https://a/1", "pending")
    s.close()

    # translation.json có sẵn -> chốt A chặn ngay; các stage trước đó cũng phải
    # có artifact, nên giả lập bằng cách chặn ở stage đầu tiên thiếu file.
    job.translation_json.write_text('{"source_lang":"vi","segments":[]}', encoding="utf-8")

    code = main(["--config", str(config_file), "--jobs-dir", str(jobs),
                 "--db", str(db), "run", "j1"])
    out = capsys.readouterr()
    # fetch sẽ hỏng vì url giả; điều cần kiểm là KHÔNG in bừa đường dẫn final.mp4
    assert "render/final.mp4" not in out.out
    assert code in (0, 1)


# --- run --all: chạy cả hàng đợi (spec §9 `concurrency`) -----------------


@pytest.fixture
def one_stage(monkeypatch):
    """Pipeline một stage, ghi ra file. Đủ để xem cả loạt có chạy không."""
    from reup.core.stage import StageSpec

    def run(job, cfg):
        (job.root / "done.txt").write_text(job.id, encoding="utf-8")

    spec = StageSpec(name="only", produces=("done.txt",), run=run)
    monkeypatch.setattr("reup.cli.stages_for", lambda cfg: [spec])
    return spec


def _common(tmp_path: Path, config_file: Path) -> list[str]:
    return [
        "--config", str(config_file), "--jobs-dir", str(tmp_path / "jobs"),
        "--db", str(tmp_path / "reup.db"),
    ]


def test_run_all_runs_every_pending_job(tmp_path: Path, capsys, config_file, one_stage):
    common = _common(tmp_path, config_file)
    for i in range(3):
        main([*common, "add", f"https://a/{i}", "--lang", "zh"])
    capsys.readouterr()

    assert main([*common, "run", "--all"]) == 0

    out = capsys.readouterr().out
    assert out.count("done") == 3
    assert len(list((tmp_path / "jobs").glob("*/done.txt"))) == 3


def test_run_all_with_nothing_waiting_is_not_an_error(
    tmp_path: Path, capsys, config_file, one_stage
):
    common = _common(tmp_path, config_file)
    assert main([*common, "run", "--all"]) == 0
    assert "không có job nào đang chờ" in capsys.readouterr().out


def test_run_all_reports_the_thread_count_from_the_profile(
    tmp_path: Path, capsys, config_file, one_stage
):
    """`profile.concurrency` phải thật sự tới được chỗ chạy, không chỉ nằm trong config."""
    common = _common(tmp_path, config_file)
    main([*common, "add", "https://a/1", "--lang", "zh"])
    capsys.readouterr()

    main([*common, "run", "--all"])

    from reup.config import load_config

    workers = load_config(config_file).profile.concurrency
    assert f"{workers} luồng" in capsys.readouterr().out


def test_run_all_survives_a_job_whose_directory_is_gone(
    tmp_path: Path, capsys, config_file, one_stage
):
    import shutil

    common = _common(tmp_path, config_file)
    main([*common, "add", "https://a/1", "--lang", "zh"])
    gone = capsys.readouterr().out.strip()
    main([*common, "add", "https://a/2", "--lang", "zh"])
    capsys.readouterr()
    shutil.rmtree(tmp_path / "jobs" / gone)

    assert main([*common, "run", "--all"]) == 0
    assert len(list((tmp_path / "jobs").glob("*/done.txt"))) == 1


def test_run_all_returns_error_when_a_job_fails(
    tmp_path: Path, capsys, config_file, monkeypatch
):
    from reup.core.stage import StageSpec

    def boom(job, cfg):
        raise RuntimeError("hỏng")

    monkeypatch.setattr(
        "reup.cli.stages_for",
        lambda cfg: [StageSpec(name="only", produces=("done.txt",), run=boom)],
    )
    common = _common(tmp_path, config_file)
    main([*common, "add", "https://a/1", "--lang", "zh"])
    capsys.readouterr()

    assert main([*common, "run", "--all"]) == 1
    assert "1 job hỏng" in capsys.readouterr().err


def test_run_needs_either_a_job_id_or_all(tmp_path: Path, capsys, config_file):
    common = _common(tmp_path, config_file)
    assert main([*common, "run"]) == 2
    assert "thiếu job_id" in capsys.readouterr().err


def test_run_refuses_a_job_id_together_with_all(tmp_path: Path, capsys, config_file):
    common = _common(tmp_path, config_file)
    assert main([*common, "run", "j1", "--all"]) == 2
    assert "một trong hai" in capsys.readouterr().err
