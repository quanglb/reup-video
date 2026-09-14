"""Kiểm tra môi trường trước khi chạy.

Mỗi mục ở đây là một lần hỏng có thật, và cái nào cũng từng nổ SAU khi Demucs
với Whisper đã chạy xong vài phút.
"""
import shutil
import urllib.error
from dataclasses import replace
from pathlib import Path

import pytest

from reup import doctor
from reup.config import LLMConfig, LLMRoles


def only(name: str):
    """PATH chỉ có đúng một công cụ."""
    return lambda n: f"/bin/{n}" if n == name else None


def roles(**by_role) -> LLMRoles:
    base = LLMConfig()
    return LLMRoles(
        **{
            r: by_role.get(r, base)
            for r in ("reconcile", "translate", "fit", "export")
        }
    )


# --- công cụ ngoài ----------------------------------------------------------

def test_a_missing_tool_says_how_to_install_it(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda n: None)
    ffmpeg = doctor.check_ffmpeg()[0]
    assert ffmpeg.blocking
    assert "brew install ffmpeg" in ffmpeg.fix


def test_missing_ytdlp_blames_the_launcher_not_the_package(monkeypatch):
    """yt-dlp có trong .venv/bin; nó "mất" là vì chạy sai cách."""
    monkeypatch.setattr(shutil, "which", lambda n: None)
    assert "uv run" in doctor.check_ytdlp().fix


def test_any_js_runtime_counts(monkeypatch):
    monkeypatch.setattr(shutil, "which", only("bun"))
    c = doctor.check_js_runtime()
    assert not c.blocking
    assert "bun" in c.detail


def test_no_js_runtime_explains_the_misleading_youtube_error(monkeypatch):
    """Thông báo của YouTube nghe như video bị gỡ, nên phải nói rõ."""
    monkeypatch.setattr(shutil, "which", lambda n: None)
    c = doctor.check_js_runtime()
    assert c.blocking
    assert "This video is not available" in c.fix


# --- LLM --------------------------------------------------------------------

def test_the_api_key_is_only_required_when_a_stage_uses_gemini(cfg_fixture, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    local = LLMConfig(provider="ollama", model="qwen3:8b")
    cfg = replace(cfg_fixture, llm_roles=roles(
        reconcile=local, translate=local, fit=local, export=local))
    assert doctor.check_gemini_key(cfg) is None


def test_the_key_check_names_which_stages_need_it(cfg_fixture, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    local = LLMConfig(provider="ollama", model="qwen3:8b")
    cfg = replace(cfg_fixture, llm_roles=roles(reconcile=local, fit=local, export=local))
    c = doctor.check_gemini_key(cfg)
    assert c.blocking
    assert "translate" in c.detail
    assert "fit" not in c.detail


def test_ollama_is_not_checked_when_nothing_uses_it(cfg_fixture):
    assert doctor.check_ollama(replace(cfg_fixture, llm_roles=roles())) == []


def test_a_stopped_ollama_says_to_start_it(cfg_fixture, monkeypatch):
    monkeypatch.setattr(
        doctor, "_ollama_models",
        lambda url, timeout=5.0: (_ for _ in ()).throw(urllib.error.URLError("refused")),
    )
    local = LLMConfig(provider="ollama", model="qwen3:8b")
    cfg = replace(cfg_fixture, llm_roles=roles(fit=local))
    out = doctor.check_ollama(cfg)
    assert out[0].blocking
    assert out[0].fix == "ollama serve"


def test_a_model_that_is_not_pulled_gives_the_pull_command(cfg_fixture, monkeypatch):
    monkeypatch.setattr(doctor, "_ollama_models", lambda url, timeout=5.0: ["khac:7b"])
    local = LLMConfig(provider="ollama", model="qwen3:8b")
    cfg = replace(cfg_fixture, llm_roles=roles(fit=local, export=local))
    out = doctor.check_ollama(cfg)
    missing = [c for c in out if c.blocking]
    assert len(missing) == 1
    assert missing[0].fix == "ollama pull qwen3:8b"
    assert "fit" in missing[0].detail and "export" in missing[0].detail


def test_a_pulled_model_passes(cfg_fixture, monkeypatch):
    monkeypatch.setattr(doctor, "_ollama_models", lambda url, timeout=5.0: ["qwen3:8b"])
    cfg = replace(cfg_fixture, llm_roles=roles(fit=LLMConfig(provider="ollama", model="qwen3:8b")))
    assert not any(c.blocking for c in doctor.check_ollama(cfg))


def test_check_openai_missing_key_blocks(cfg_fixture, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ROUTER_API_KEY", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    cfg = replace(
        cfg_fixture,
        llm_roles=roles(fit=LLMConfig(provider="openai", model="ag/gemini-3.7-flash-medium", api_key="")),
    )
    checks = doctor.check_openai(cfg)
    assert any(c.blocking and "chưa đặt" in c.detail for c in checks)


def test_check_openai_with_key_passes(cfg_fixture):
    cfg = replace(
        cfg_fixture,
        llm_roles=roles(fit=LLMConfig(provider="openai", model="ag/gemini-3.7-flash-medium", api_key="sk-test")),
    )
    checks = doctor.check_openai(cfg)
    assert not any(c.blocking for c in checks)
    assert any("OpenAI/Router API Key" in c.name for c in checks)


# --- CapCut, đĩa, báo cáo ---------------------------------------------------

def test_capcut_is_skipped_for_the_stub_engine(cfg_fixture):
    from reup.config import TTSConfig

    assert doctor.check_capcut(replace(cfg_fixture, tts=TTSConfig(engine="stub"))) is None


def test_a_missing_capcut_folder_blocks(cfg_fixture, tmp_path):
    from reup.config import TTSConfig

    cfg = replace(cfg_fixture, tts=TTSConfig(engine="capcut", capcut_dir=str(tmp_path / "khong-co")))
    assert doctor.check_capcut(cfg).blocking


def test_low_disk_warns_but_does_not_block(tmp_path):
    c = doctor.check_disk(tmp_path, min_gb=10**6)
    assert c.status == doctor.WARN
    assert not c.blocking


def test_the_report_prints_a_fix_under_every_failure():
    checks = [
        doctor.Check("a", doctor.OK, "ổn"),
        doctor.Check("b", doctor.BAD, "hỏng", "sửa thế này"),
    ]
    out = doctor.format_report(checks)
    assert "sửa thế này" in out
    assert "1 thứ phải sửa" in out


def test_the_report_says_ready_when_nothing_blocks():
    out = doctor.format_report([doctor.Check("a", doctor.OK, "ổn")])
    assert "sẵn sàng chạy" in out


def test_the_cli_exit_code_reflects_blockers(tmp_path, config_file, monkeypatch, capsys):
    """Mã thoát khác 0 để cắm được vào script trước khi chạy cả loạt."""
    from reup.cli import main

    monkeypatch.setattr(shutil, "which", lambda n: None)
    args = ["--config", str(config_file), "--jobs-dir", str(tmp_path / "jobs"),
            "--db", str(tmp_path / "reup.db"), "doctor"]
    assert main(args) == 1
    assert "phải sửa" in capsys.readouterr().out
