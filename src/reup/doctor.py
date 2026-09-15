"""Kiểm tra môi trường trước khi chạy, thay vì hỏng ở giữa pipeline.

Mọi mục dưới đây đều là một lần hỏng có thật, và cái nào cũng nổ SAU khi Demucs
với Whisper đã chạy xong vài phút:

- thiếu JS runtime -> yt-dlp báo "This video is not available", nghe như video
  bị gỡ trong khi thật ra mọi video đều hỏng
- chạy bằng .venv/bin/python thay vì `uv run` -> không thấy yt-dlp
- Ollama chưa bật, hoặc chưa kéo model đã khai trong config
- hết hạn mức LLM

Mỗi mục trả về một dòng: trạng thái, điều đo được, và cách sửa nếu hỏng.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from reup.adapters.manual import JS_RUNTIMES
from reup.config import LLM_ROLES, Config

OK, WARN, BAD = "ok", "warn", "bad"


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    detail: str
    fix: str = ""

    @property
    def blocking(self) -> bool:
        return self.status == BAD


def _tool(name: str, fix: str) -> Check:
    path = shutil.which(name)
    if path:
        return Check(name, OK, path)
    return Check(name, BAD, "không có trong PATH", fix)


def check_js_runtime() -> Check:
    """YouTube bắt giải JS challenge; thiếu runtime là hỏng mọi video."""
    for runtime in JS_RUNTIMES:
        path = shutil.which(runtime)
        if path:
            return Check("JS runtime", OK, f"{runtime} ({path})")
    return Check(
        "JS runtime",
        BAD,
        f"không thấy {' / '.join(JS_RUNTIMES)}",
        "brew install node — thiếu thì MỌI video YouTube báo "
        '"This video is not available"',
    )


def check_ffmpeg() -> list[Check]:
    return [
        _tool("ffmpeg", "brew install ffmpeg"),
        _tool("ffprobe", "brew install ffmpeg"),
    ]


def check_ytdlp() -> Check:
    c = _tool("yt-dlp", "chạy bằng `uv run reup ...` thay vì .venv/bin/python")
    if c.status != OK:
        return c
    try:
        ver = subprocess.run(
            ["yt-dlp", "--version"], capture_output=True, text=True, timeout=20
        ).stdout.strip()
    except Exception:
        ver = ""
    return Check("yt-dlp", OK, ver or c.detail)


def check_gemini_key(cfg: Config) -> Check | None:
    roles = [r for r in LLM_ROLES if cfg.llm_for(r).provider == "gemini"]
    if not roles:
        return None
    if os.environ.get("GEMINI_API_KEY"):
        return Check("GEMINI_API_KEY", OK, f"dùng cho: {', '.join(roles)}")
    return Check(
        "GEMINI_API_KEY",
        BAD,
        f"chưa đặt, nhưng {', '.join(roles)} đang dùng gemini",
        "điền vào .env — lấy key ở https://aistudio.google.com/apikey",
    )


def _ollama_models(base_url: str, timeout: float = 5.0) -> list[str]:
    with urllib.request.urlopen(f"{base_url}/api/tags", timeout=timeout) as resp:
        import json

        return [m["name"] for m in json.loads(resp.read()).get("models", [])]


def check_ollama(cfg: Config) -> list[Check]:
    """Một dòng cho server, một dòng cho mỗi model được khai mà chưa kéo về."""
    wanted: dict[str, list[str]] = {}
    base_url = ""
    for role in LLM_ROLES:
        one = cfg.llm_for(role)
        if one.provider == "ollama":
            wanted.setdefault(one.model, []).append(role)
            base_url = one.base_url.rstrip("/")
    if not wanted:
        return []

    try:
        have = _ollama_models(base_url)
    except (urllib.error.URLError, OSError) as exc:
        return [
            Check(
                "Ollama",
                BAD,
                f"không gọi được {base_url} ({exc})",
                "ollama serve",
            )
        ]

    out = [Check("Ollama", OK, f"{base_url}, {len(have)} model")]
    for model, roles in sorted(wanted.items()):
        if model in have:
            out.append(Check(f"model {model}", OK, f"dùng cho: {', '.join(roles)}"))
        else:
            out.append(
                Check(
                    f"model {model}",
                    BAD,
                    f"chưa kéo về, nhưng {', '.join(roles)} đang khai nó",
                    f"ollama pull {model}",
                )
            )
    return out


def check_capcut(cfg: Config) -> Check | None:
    if cfg.tts.engine != "capcut":
        return None
    if not cfg.tts.capcut_dir:
        return Check(
            "capcut-tts-api", BAD, "tts.engine = capcut nhưng chưa đặt tts.capcut_dir",
            "điền đường dẫn vào config.toml",
        )
    path = Path(cfg.tts.capcut_dir)
    if path.is_dir():
        return Check("capcut-tts-api", OK, str(path))
    return Check(
        "capcut-tts-api", BAD, f"không có thư mục {path}",
        "kéo repo về đúng đường dẫn khai trong config.toml",
    )


def check_disk(jobs_dir: Path, min_gb: float = 5.0) -> Check:
    """Một job giữ nguồn, vocals, bgm, wav từng câu và bản render."""
    target = jobs_dir if jobs_dir.exists() else jobs_dir.parent
    free_gb = shutil.disk_usage(target).free / 1024**3
    if free_gb >= min_gb:
        return Check("dung lượng đĩa", OK, f"còn {free_gb:.0f}GB")
    return Check(
        "dung lượng đĩa", WARN, f"chỉ còn {free_gb:.1f}GB",
        f"dọn bớt jobs/ — nên có ít nhất {min_gb:.0f}GB",
    )


def check_openai(cfg: Config) -> list[Check]:
    roles: list[str] = []
    base_url = ""
    api_key = ""
    for r in LLM_ROLES:
        one = cfg.llm_for(r)
        if one.provider == "openai":
            roles.append(r)
            base_url = one.base_url.rstrip("/")
            if one.api_key:
                api_key = one.api_key
    if not roles:
        return []
    if not api_key:
        api_key = (
            os.environ.get("OPENAI_API_KEY")
            or os.environ.get("ROUTER_API_KEY")
            or os.environ.get("LLM_API_KEY")
            or ""
        )
    out = []
    if api_key:
        out.append(Check("OpenAI/Router API Key", OK, f"dùng cho: {', '.join(roles)}"))
    else:
        out.append(
            Check(
                "OpenAI/Router API Key",
                BAD,
                f"chưa đặt, nhưng {', '.join(roles)} đang dùng provider openai",
                "điền api_key vào config.toml hoặc đặt OPENAI_API_KEY trong .env",
            )
        )
    if base_url:
        out.append(Check("OpenAI/Router Base URL", OK, base_url))
    return out


def check_telegram(cfg: Config) -> Check | None:
    """Chỉ xét khi đã bật. Thiếu token/chat id không chặn chạy: chỉ mất báo cáo."""
    if not cfg.notify.telegram:
        return None
    if not os.environ.get("TELEGRAM_BOT_TOKEN"):
        return Check("Telegram", WARN, "đã bật nhưng thiếu TELEGRAM_BOT_TOKEN",
                     "tạo bot ở @BotFather rồi điền token vào .env")
    if not (cfg.notify.chat_id or os.environ.get("TELEGRAM_CHAT_ID")):
        return Check("Telegram", WARN, "thiếu chat id",
                     "nhắn cho bot một tin rồi chạy `uv run reup telegram chat-id`")
    topic_info = f" (topic #{cfg.notify.topic_id})" if cfg.notify.topic_id else ""
    return Check("Telegram", OK, f"bật{topic_info} — kiểm thử bằng `uv run reup telegram test`")


def check_web_password() -> Check:
    """Mật khẩu web không bắt buộc khi chạy local, nhưng nên có để bảo vệ từ xa."""
    if os.environ.get("REUP_WEB_PASSWORD"):
        return Check("REUP_WEB_PASSWORD", OK, "đặt")
    return Check("REUP_WEB_PASSWORD", WARN, "chưa đặt",
                 "thiết lập để bảo vệ giao diện web khi chạy từ xa")


def check_pronunciation_rules(cfg: Config) -> Check | None:
    rules_path = getattr(cfg.tts, "pronunciation_rules_path", "")
    if not rules_path:
        return None
    p = Path(rules_path)
    if not p.is_absolute():
        p = Path.cwd() / p
    if p.is_file():
        return Check("luật phiên âm TTS", OK, str(p))
    return Check(
        "luật phiên âm TTS",
        BAD,
        f"không thấy file {p}",
        "tạo file quy tắc phiên âm hoặc sửa `tts.pronunciation_rules_path` trong config.toml",
    )


def run_checks(cfg: Config, jobs_dir: Path) -> list[Check]:
    checks = [*check_ffmpeg(), check_ytdlp(), check_js_runtime()]
    for maybe in (
        check_gemini_key(cfg),
        check_capcut(cfg),
        check_pronunciation_rules(cfg),
        check_telegram(cfg),
    ):
        if maybe is not None:
            checks.append(maybe)
    checks.extend(check_ollama(cfg))
    checks.extend(check_openai(cfg))
    checks.append(check_disk(jobs_dir))
    checks.append(check_web_password())
    return checks


MARK = {OK: "✓", WARN: "!", BAD: "✗"}


def format_report(checks: list[Check]) -> str:
    width = max(len(c.name) for c in checks)
    lines = []
    for c in checks:
        lines.append(f"{MARK[c.status]} {c.name:<{width}}  {c.detail}")
        if c.fix:
            lines.append(f"{' ' * (width + 4)}-> {c.fix}")
    bad = [c for c in checks if c.blocking]
    lines.append("")
    lines.append(
        "sẵn sàng chạy" if not bad else f"{len(bad)} thứ phải sửa trước khi chạy"
    )
    return "\n".join(lines)
