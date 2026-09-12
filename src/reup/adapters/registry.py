"""Chọn implement theo config. Chỗ duy nhất biết tên engine nào ứng với class nào.

Stage chỉ gọi `make_tts(cfg)` / `make_llm(cfg)` / `make_asr(cfg)`, nên thêm nhà
cung cấp mới là thêm một nhánh ở đây, không đụng stage.
"""
from __future__ import annotations

from pathlib import Path

from reup.config import Config


def make_tts(cfg: Config):
    engine = cfg.tts.engine
    if engine == "stub":
        from reup.adapters.stub_tts import StubTTS

        return StubTTS()
    if engine == "capcut":
        from reup.adapters.capcut_tts import CapCutError, CapCutTTS

        if not cfg.tts.capcut_dir:
            raise CapCutError(
                "tts.engine = \"capcut\" nhưng chưa đặt tts.capcut_dir trong config.toml"
            )
        return CapCutTTS(Path(cfg.tts.capcut_dir))
    raise ValueError(f"không có TTS engine {engine!r}")


def make_llm(cfg: Config):
    provider = cfg.llm.provider
    if provider == "gemini":
        from reup.adapters.gemini import GeminiLLM

        return GeminiLLM(model=cfg.llm.model)
    if provider == "cassette":
        from reup.adapters.cassette_llm import CassetteLLM

        if not cfg.llm.cassette:
            raise ValueError(
                "llm.provider = \"cassette\" nhưng chưa đặt llm.cassette trong config.toml"
            )
        return CassetteLLM(Path(cfg.llm.cassette))
    raise ValueError(f"không có LLM provider {provider!r}")


def make_asr(cfg: Config):
    engine = cfg.asr.engine
    if engine == "whisper":
        from reup.adapters.whisper_asr import WhisperASR

        return WhisperASR(model=cfg.profile.whisper_model)
    if engine == "capcut":
        from reup.adapters.capcut_stt import CapCutSTT

        if not cfg.tts.capcut_dir:
            raise ValueError(
                "asr.engine = \"capcut\" nhưng chưa đặt tts.capcut_dir trong config.toml"
            )
        return CapCutSTT(Path(cfg.tts.capcut_dir))
    raise ValueError(f"không có ASR engine {engine!r}")


def make_source(cfg: Config, platform: str, query: str | None = None):
    """Crawler cho một nền tảng, dựng theo mục [discover.<platform>].

    `query` truyền vào (từ CLI hoặc ô tìm trong Web UI) đè lên config, để thử
    một hashtag khác mà không phải sửa file.
    """
    opts = cfg.discover.for_platform(platform)
    q = (query or "").strip() or opts.query
    cookies = opts.cookies_from_browser or None
    cookie_file = opts.cookie_file or None

    if platform == "youtube":
        from reup.adapters.youtube import YouTubeSource

        return YouTubeSource(q)
    if platform == "tiktok":
        from reup.adapters.tiktok import TikTokSource

        return TikTokSource(q, cookies_from_browser=cookies, cookie_file=cookie_file)
    if platform == "douyin":
        from reup.adapters.douyin import DouyinSource

        return DouyinSource(q, cookies_from_browser=cookies, cookie_file=cookie_file)
    raise ValueError(f"không có crawler cho nền tảng {platform!r}")
