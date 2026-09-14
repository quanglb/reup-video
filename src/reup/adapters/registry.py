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
        return CapCutTTS(
            Path(cfg.tts.capcut_dir),
            poll_interval=getattr(cfg.tts, "poll_interval", 1.0),
            max_polls=getattr(cfg.tts, "max_polls", 10),
            pause_every=cfg.tts.pause_every,
            pause_seconds=cfg.tts.pause_seconds,
            allow_edge_fallback=cfg.tts.allow_edge_fallback,
        )
    raise ValueError(f"không có TTS engine {engine!r}")


def make_llm(cfg: Config, role: str | None = None):
    """`role` là tên stage gọi tới: mỗi stage cấu hình được model riêng.

    Bỏ trống thì dùng [llm] chung — giữ cho chỗ gọi cũ và test không phải biết
    về vai.
    """
    llm = cfg.llm_for(role) if role else cfg.llm
    provider = llm.provider
    if provider == "gemini":
        from reup.adapters.gemini import GeminiLLM

        return GeminiLLM(model=llm.model)
    if provider == "ollama":
        from reup.adapters.ollama import OllamaLLM

        return OllamaLLM(
            model=llm.model,
            base_url=llm.base_url,
            timeout_s=llm.timeout_s,
        )
    if provider == "openai":
        from reup.adapters.openai import OpenAILLM

        return OpenAILLM(
            model=llm.model,
            base_url=llm.base_url,
            api_key=llm.api_key,
            timeout_s=llm.timeout_s,
        )
    if provider == "cassette":
        from reup.adapters.cassette_llm import CassetteLLM

        if not llm.cassette:
            raise ValueError(
                "llm.provider = \"cassette\" nhưng chưa đặt llm.cassette trong config.toml"
            )
        return CassetteLLM(Path(llm.cassette))
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
