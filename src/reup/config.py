"""Đọc config.toml thành các dataclass bất biến."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

AUDIO_MODES = ("separate", "drop_original")
TTS_ENGINES = ("capcut", "stub")
ASR_ENGINES = ("whisper", "capcut")
LLM_PROVIDERS = ("gemini", "ollama", "cassette")
PLATFORMS = ("youtube", "tiktok", "douyin")
# Bốn stage gọi LLM. Mỗi cái chịu được một model khác nhau: chỉ `translate`
# thật sự cần model giỏi, ba cái còn lại không phải suy luận gì nhiều.
LLM_ROLES = ("reconcile", "translate", "fit", "export")


def parse_bitrate(text: str) -> int:
    """Đổi cách viết của ffmpeg ("8M", "2500k", "800000") thành bit/giây."""
    s = str(text).strip()
    if not s:
        raise ValueError("bitrate rỗng")
    factor = 1
    if s[-1] in "kK":
        factor, s = 1_000, s[:-1]
    elif s[-1] in "mM":
        factor, s = 1_000_000, s[:-1]
    try:
        value = float(s)
    except ValueError:
        raise ValueError(f"bitrate {text!r} không đọc được. Ví dụ hợp lệ: 8M, 2500k, 800000")
    if value <= 0:
        raise ValueError(f"bitrate {text!r} phải dương")
    return round(value * factor)


@dataclass(frozen=True)
class ProfileConfig:
    concurrency: int
    whisper_model: str
    demucs_segment: int
    encoder: str
    video_bitrate: str = "8M"  # trần, không phải mức cố định — xem compose.pick_bitrate
    prefer_h264: bool = False  # chỉ bật trên máy không có hardware AV1 decode
    demucs_model: str = "htdemucs"
    demucs_device: str = "mps"  # "mps" | "cpu" — MPS gần như không sinh nhiệt


@dataclass(frozen=True)
class AudioConfig:
    mode: str
    bgm_gain: float


@dataclass(frozen=True)
class TransformConfig:
    hflip: bool
    zoom: float
    speed: float


@dataclass(frozen=True)
class SubtitleConfig:
    font: str
    size: int
    outline: int
    position: str


@dataclass(frozen=True)
class TTSConfig:
    engine: str = "stub"
    voice: str = "BV074_streaming"
    capcut_dir: str = ""


@dataclass(frozen=True)
class ASRConfig:
    engine: str = "whisper"


@dataclass(frozen=True)
class LLMConfig:
    provider: str = "gemini"
    model: str = "gemini-3.6-flash"
    cassette: str = ""
    # Chỉ dùng cho provider "ollama".
    base_url: str = "http://localhost:11434"
    timeout_s: float = 600.0


@dataclass(frozen=True)
class FetchConfig:
    """Cách yt-dlp bóc link tải.

    `js_runtime` rỗng = tự dò deno, node, bun theo thứ tự đó.
    `remote_components` rỗng = không tải script EJS từ GitHub; YouTube sẽ hỏng,
    nhưng không có mã ngoài nào chạy trên máy.
    """

    js_runtime: str = ""
    remote_components: str = "ejs:github"


@dataclass(frozen=True)
class LLMRoles:
    """Cấu hình LLM đã giải xong cho từng stage.

    Tách theo stage vì hạn mức đếm theo số request: đẩy ba việc nhẹ sang model
    local thì hạn mức miễn phí của nhà cung cấp chỉ còn phải gánh `translate`,
    tức một request mỗi video.
    """

    reconcile: "LLMConfig"
    translate: "LLMConfig"
    fit: "LLMConfig"
    export: "LLMConfig"

    def for_role(self, name: str) -> "LLMConfig":
        if name not in LLM_ROLES:
            raise ValueError(
                f"không có vai LLM {name!r}. Chọn một trong {LLM_ROLES}"
            )
        return getattr(self, name)


@dataclass(frozen=True)
class PlatformDiscoverConfig:
    """Cách quét một nền tảng. `query` là hashtag, @user, hoặc nguyên một URL.

    Cookie để rỗng là hợp lệ: YouTube không cần, TikTok và Douyin thì gần như
    luôn cần — crawler tự nói ra khi quét hỏng vì thiếu.
    """

    query: str = ""
    cookies_from_browser: str = ""
    cookie_file: str = ""
    limit: int = 12


@dataclass(frozen=True)
class DiscoverConfig:
    youtube: PlatformDiscoverConfig = PlatformDiscoverConfig(query="#shorts")
    tiktok: PlatformDiscoverConfig = PlatformDiscoverConfig(query="xuhuong")
    douyin: PlatformDiscoverConfig = PlatformDiscoverConfig()

    def for_platform(self, name: str) -> PlatformDiscoverConfig:
        if name not in PLATFORMS:
            raise ValueError(
                f"không có nền tảng {name!r}. Chọn một trong {PLATFORMS}"
            )
        return getattr(self, name)


@dataclass(frozen=True)
class ReviewConfig:
    auto_approve_b: bool
    output_dir: str = "output"


@dataclass(frozen=True)
class Config:
    profile_name: str
    profile: ProfileConfig
    audio: AudioConfig
    transform: TransformConfig
    subtitle: SubtitleConfig
    review: ReviewConfig
    tts: TTSConfig = TTSConfig()

    def output_dir_path(self) -> Path:
        return Path(self.review.output_dir)

    asr: ASRConfig = ASRConfig()
    llm: LLMConfig = LLMConfig()
    discover: DiscoverConfig = DiscoverConfig()
    fetch: FetchConfig = FetchConfig()
    llm_roles: LLMRoles | None = None

    def llm_for(self, role: str) -> LLMConfig:
        """Cấu hình LLM cho một stage. Không khai riêng thì dùng [llm] chung."""
        if self.llm_roles is None:
            return self.llm
        return self.llm_roles.for_role(role)


def load_config(path: Path) -> Config:
    raw = tomllib.loads(Path(path).read_text(encoding="utf-8"))

    name = raw["profile"]["active"]
    profiles = {k: v for k, v in raw["profile"].items() if k != "active"}
    if name not in profiles:
        raise ValueError(
            f"profile.active = {name!r} nhưng không có mục [profile.{name}]. "
            f"Có sẵn: {sorted(profiles)}"
        )

    profile = ProfileConfig(**profiles[name])
    parse_bitrate(profile.video_bitrate)  # sai cú pháp thì gãy ngay lúc đọc config

    audio = raw["audio"]
    if audio["mode"] not in AUDIO_MODES:
        raise ValueError(
            f"audio.mode = {audio['mode']!r} không hợp lệ. Chọn một trong {AUDIO_MODES}"
        )

    tts = TTSConfig(**raw.get("tts", {}))
    _one_of("tts.engine", tts.engine, TTS_ENGINES)
    asr = ASRConfig(**raw.get("asr", {}))
    _one_of("asr.engine", asr.engine, ASR_ENGINES)
    llm, llm_roles = _llm(raw.get("llm", {}))
    discover = _discover(raw.get("discover", {}))
    fetch = FetchConfig(**raw.get("fetch", {}))

    return Config(
        profile_name=name,
        profile=profile,
        audio=AudioConfig(**audio),
        transform=TransformConfig(**raw["transform"]),
        subtitle=SubtitleConfig(**raw["subtitle"]),
        review=ReviewConfig(**raw["review"]),
        tts=tts,
        asr=asr,
        llm=llm,
        llm_roles=llm_roles,
        discover=discover,
        fetch=fetch,
    )


def _llm(raw: dict) -> tuple[LLMConfig, LLMRoles]:
    """Tách [llm] thành cấu hình chung và phần ghi đè theo stage.

    Khoá vô hướng trong [llm] là mặc định chung; mỗi bảng con [llm.<stage>] ghi
    đè lên nó, và khoá nào không khai thì thừa kế.
    """
    base_raw = {k: v for k, v in raw.items() if not isinstance(v, dict)}
    per_role = {k: v for k, v in raw.items() if isinstance(v, dict)}

    unknown = sorted(set(per_role) - set(LLM_ROLES))
    if unknown:
        raise ValueError(
            f"[llm] có bảng con lạ: {unknown}. Chỉ nhận {list(LLM_ROLES)}"
        )

    base = LLMConfig(**base_raw)
    _one_of("llm.provider", base.provider, LLM_PROVIDERS)

    resolved = {}
    for role in LLM_ROLES:
        cfg = LLMConfig(**{**vars(base), **per_role.get(role, {})})
        _one_of(f"llm.{role}.provider", cfg.provider, LLM_PROVIDERS)
        resolved[role] = cfg
    return base, LLMRoles(**resolved)


def _discover(raw: dict) -> DiscoverConfig:
    unknown = sorted(set(raw) - set(PLATFORMS))
    if unknown:
        raise ValueError(
            f"[discover] có mục lạ: {unknown}. Chỉ nhận {list(PLATFORMS)}"
        )
    default = DiscoverConfig()
    return DiscoverConfig(
        **{
            name: PlatformDiscoverConfig(
                **{**vars(getattr(default, name)), **raw.get(name, {})}
            )
            for name in PLATFORMS
        }
    )


def _one_of(field: str, value: str, allowed: tuple[str, ...]) -> None:
    if value not in allowed:
        raise ValueError(f"{field} = {value!r} không hợp lệ. Chọn một trong {allowed}")
