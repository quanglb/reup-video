# reup-video Phase 1 — Xương sống pipeline

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Dán một link video vào là ra file `.mp4` đã thay audio, chạy qua đúng kiến trúc stage rời nhau — chưa có dịch, chưa có phụ đề, giọng đọc là giả.

**Architecture:** Chuỗi stage rời nhau. Mỗi stage đọc file trong thư mục job, ghi file mới vào đó, cập nhật SQLite. Không stage nào gọi trực tiếp stage khác; runner quyết định stage nào chạy tiếp bằng cách xem artifact nào đã tồn tại. Phase 1 hiện thực 6 trong 13 stage: `fetch`, `demux`, `asr`, `tts`, `fit`, `compose`.

**Tech Stack:** Python 3.12 (venv qua `uv`), ffmpeg 8.x, yt-dlp, mlx-whisper, SQLite (stdlib), pytest. CLI bằng `argparse` (stdlib) — không thêm dependency.

**Spec:** `docs/superpowers/specs/2026-09-11-reup-video-design.md`

## Tiến độ

Cập nhật 2026-09-11.

| Task | Trạng thái | Ghi chú |
|------|-----------|---------|
| 1. Scaffold + config | ✅ xong | venv 3.12, `pyproject.toml`, `config.toml`, `src/reup/config.py` |
| 2. Đếm âm tiết | ✅ xong | `src/reup/text.py` |
| 3. Segment / Transcript | ✅ xong | `src/reup/models.py` |
| 4. Thư mục job | ✅ xong | `src/reup/core/job.py` |
| 5. Sổ cái SQLite | ✅ xong | `src/reup/core/store.py` |
| 6. StageSpec + runner | ✅ xong | `src/reup/core/stage.py`, `core/runner.py` — có resume và atomic_write |
| 7. Bọc ffmpeg/ffprobe | ✅ xong | `src/reup/media/ffmpeg.py`, fixture media trong `tests/conftest.py` |
| 8. Thao tác audio | ✅ xong | `src/reup/media/audio.py` — extract, atempo, silence, build_timeline |
| 9. Adapter nguồn + stage fetch | ✅ xong | `adapters/source.py`, `adapters/manual.py`, `stages/fetch.py` |
| 10. Stage demux | ✅ xong | `src/reup/stages/demux.py` |
| 11. Whisper + stage asr | ✅ xong | `media/whisper.py`, `stages/asr.py` |
| 12. TTSAdapter + StubTTS | ✅ xong | `adapters/tts.py`, `adapters/stub_tts.py` |
| 13. Stage tts | ✅ xong | `src/reup/stages/tts.py` + `tts/manifest.json` |
| 14. Luật khớp thời lượng | ✅ xong | `src/reup/fit.py` — `decide_fit` |
| 15. Stage fit | ✅ xong | `src/reup/stages/fit.py` + `tts/fit.json` |
| 16. Stage compose | ✅ xong | `src/reup/stages/compose.py` |
| 17. Đăng ký stage + CLI | ⬜ chưa | **điểm tiếp tục** — `core/stage.py` thêm `STAGES`, viết `src/reup/cli.py` |
| 18. Test tích hợp + README | ⬜ chưa | chạy thông `add` → `run` đầu cuối |

**Test hiện tại:** 126 pass — chạy bằng `.venv/bin/python -m pytest -q` (hoặc `uv run pytest`).

**Môi trường đã dựng sẵn:** `.venv` Python 3.12 với `yt-dlp`, `mlx-whisper`, `pytest`. `ffmpeg` có ở `/opt/homebrew/bin/ffmpeg`. Không cần cài lại.

**Sai sót đã sửa trong chính plan này:** fixture Task 2 ghi câu `"Hôm nay mình dạy mọi người làm thịt kho tàu"` là 9 âm tiết, đếm thật là 10. Đã sửa cả trong plan lẫn trong `tests/test_text.py`. Các task sau nếu gặp con số kỳ vọng lệch thì kiểm lại fixture trước khi sửa code.

**Chưa làm:** `pyproject.toml` khai `reup = "reup.cli:main"` nhưng `src/reup/cli.py` chưa tồn tại (Task 17). Vì vậy chưa chạy được `uv pip install -e .`; test dùng `pythonpath = ["src"]` nên không ảnh hưởng.


## Global Constraints

- Python **3.12** trong venv riêng tạo bằng `uv`. Không dùng `python3` hệ thống (đang là 3.14, nhiều package ML chưa có wheel).
- Dependency phase 1 **chỉ gồm**: `yt-dlp`, `mlx-whisper`, `pytest`. Mọi thứ khác dùng stdlib. YAGNI.
- Encoder video: `h264_videotoolbox`, bitrate `8M`. Không dùng `libx264` (máy Air không quạt).
- Quy tắc phụ thuộc module: `web` → `core` → `adapters` / `media`. **`media` không được import `core`.** `core` không được import `stages`.
- Mọi artifact ghi **nguyên tử**: ghi ra `<tên>.tmp` rồi `os.replace`. Stage chết giữa chừng không để lại file nửa vời.
- Tên file audio: `full_16k.wav` (16kHz mono) và `full_48k.wav` (48kHz stereo). Không có file nào tên `full.wav`.
- Test **không** so khớp pixel. Kiểm tra: file tồn tại, độ dài ±100ms, đúng độ phân giải, có đủ stream video và audio.
- Fixture phase 1 **sinh bằng ffmpeg lúc chạy test**, không commit file nhị phân. Clip thật commit vào repo bắt đầu từ phase 3 khi OCR cần hardsub thật.
- Source CapCut nằm ở `capcut-tts-api/` (Python thuần, CLI, có `requests`). Thư mục
  này là git repo riêng và đã vào `.gitignore` — **không** commit nó vào repo này.
  Phase 1 không đụng tới; phase 2 mới bọc nó lại.
- Whisper nằm ở `media/` chứ không phải `adapters/`, vì nó là thư viện chạy local
  như ffmpeg, không phải dịch vụ mạng. Phase 2 mới dựng `ASRAdapter` khi có
  implement thứ hai (CapCut STT) để so.
- Commit sau mỗi task.

---

## File Structure

```
pyproject.toml                     metadata + dependency
config.toml                        cấu hình mặc định
src/reup/
  __init__.py
  config.py                        đọc config.toml → Config (frozen dataclass)
  text.py                          đếm âm tiết — StubTTS và phase 2 cùng dùng
  models.py                        Segment / Transcript + đọc ghi JSON
  cli.py                           argparse: add / run / redo / status
  core/
    __init__.py
    job.py                         Job dataclass + đường dẫn artifact
    store.py                       SQLite: bảng jobs, stage_runs, seen
    stage.py                       StageSpec + đăng ký thứ tự stage
    runner.py                      chọn stage kế, chạy, ghi nhật ký, atomic_write
  adapters/
    __init__.py
    source.py                      SourceAdapter Protocol + Candidate/FetchResult
    manual.py                      ManualSource — bọc yt-dlp
    tts.py                         TTSAdapter Protocol + Voice/TTSResult
    stub_tts.py                    StubTTS — wav im lặng theo số âm tiết
  media/
    __init__.py
    ffmpeg.py                      run_ffmpeg, probe, MediaInfo, FFmpegError
    audio.py                       extract_audio, atempo_filter, build_timeline
    whisper.py                     transcribe — bọc mlx-whisper
  stages/
    __init__.py
    fetch.py  demux.py  asr.py  tts.py  fit.py  compose.py
tests/
  conftest.py                      fixture sinh video/audio bằng ffmpeg
  test_config.py  test_text.py  test_models.py
  test_store.py  test_job.py  test_runner.py
  test_ffmpeg.py  test_audio.py
  test_stub_tts.py  test_fit_decision.py
  test_stage_fetch.py  test_stage_demux.py  test_stage_asr.py
  test_stage_tts.py  test_stage_fit.py  test_stage_compose.py
  test_cli.py
  test_pipeline_integration.py
```

**Trách nhiệm từng file** — mỗi file một việc:

- `media/*` là hàm thuần bọc công cụ ngoài. Không biết Job là gì. Nhận `Path` vào, ghi `Path` ra.
- `core/*` điều phối. Không gọi ffmpeg, không biết stage cụ thể nào làm gì.
- `stages/*` là chỗ duy nhất nối `core` với `media`. Mỗi stage một hàm `run(job, cfg)`.
- `adapters/*` là biên giới ra thế giới ngoài. Đổi nhà cung cấp = thêm file, không sửa file cũ.

---

## Task 1: Scaffold dự án và bộ đọc cấu hình

**Files:**
- Create: `pyproject.toml`
- Create: `config.toml`
- Create: `src/reup/__init__.py`
- Create: `src/reup/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: — (task đầu tiên)
- Produces: `load_config(path: Path) -> Config`; `Config` với các thuộc tính `profile_name: str`, `profile: ProfileConfig`, `audio: AudioConfig`, `transform: TransformConfig`, `subtitle: SubtitleConfig`, `review: ReviewConfig`. `ProfileConfig(concurrency: int, whisper_model: str, demucs_segment: int, encoder: str)`. `AudioConfig(mode: str, bgm_gain: float)`. `TransformConfig(hflip: bool, zoom: float, speed: float)`. `SubtitleConfig(font: str, size: int, outline: int, position: str)`. `ReviewConfig(auto_approve_b: bool)`.

- [x] **Step 1: Dựng venv và khung dự án**

```bash
cd /Users/admin/Projects/ai-learning/reup-video
uv venv --python 3.12
uv pip install yt-dlp mlx-whisper pytest
mkdir -p src/reup/core src/reup/adapters src/reup/media src/reup/stages tests
touch src/reup/__init__.py src/reup/core/__init__.py src/reup/adapters/__init__.py \
      src/reup/media/__init__.py src/reup/stages/__init__.py
```

- [x] **Step 2: Viết `pyproject.toml`**

```toml
[project]
name = "reup"
version = "0.1.0"
requires-python = ">=3.12,<3.13"
dependencies = ["yt-dlp>=2024.1.1", "mlx-whisper>=0.4.0"]

[project.scripts]
reup = "reup.cli:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/reup"]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]
```

- [x] **Step 3: Viết `config.toml`**

```toml
[profile]
active = "air-16"

[profile.air-16]
concurrency    = 1
whisper_model  = "mlx-community/whisper-large-v3-turbo-q4"
demucs_segment = 7
encoder        = "h264_videotoolbox"

[profile.studio-24]
concurrency    = 2
whisper_model  = "mlx-community/whisper-large-v3-turbo"
demucs_segment = 12
encoder        = "h264_videotoolbox"

[audio]
mode     = "separate"
bgm_gain = 0.35

[transform]
hflip = false
zoom  = 1.0
speed = 1.0

[subtitle]
font     = "Be Vietnam Pro"
size     = 64
outline  = 4
position = "bottom"

[review]
auto_approve_b = false
```

- [x] **Step 4: Viết test thất bại**

```python
# tests/test_config.py
from pathlib import Path
import pytest
from reup.config import load_config


def test_loads_active_profile(tmp_path: Path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(
        '[profile]\nactive = "air-16"\n\n'
        '[profile.air-16]\nconcurrency = 1\nwhisper_model = "tiny"\n'
        'demucs_segment = 7\nencoder = "h264_videotoolbox"\n\n'
        '[profile.studio-24]\nconcurrency = 2\nwhisper_model = "big"\n'
        'demucs_segment = 12\nencoder = "h264_videotoolbox"\n\n'
        '[audio]\nmode = "separate"\nbgm_gain = 0.35\n\n'
        '[transform]\nhflip = false\nzoom = 1.0\nspeed = 1.0\n\n'
        '[subtitle]\nfont = "Be Vietnam Pro"\nsize = 64\noutline = 4\nposition = "bottom"\n\n'
        '[review]\nauto_approve_b = false\n',
        encoding="utf-8",
    )
    cfg = load_config(cfg_file)
    assert cfg.profile_name == "air-16"
    assert cfg.profile.concurrency == 1
    assert cfg.profile.whisper_model == "tiny"
    assert cfg.audio.bgm_gain == 0.35
    assert cfg.transform.hflip is False
    assert cfg.subtitle.font == "Be Vietnam Pro"
    assert cfg.review.auto_approve_b is False


def test_rejects_unknown_active_profile(tmp_path: Path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(
        '[profile]\nactive = "khong-co"\n\n'
        '[profile.air-16]\nconcurrency = 1\nwhisper_model = "tiny"\n'
        'demucs_segment = 7\nencoder = "h264_videotoolbox"\n\n'
        '[audio]\nmode = "separate"\nbgm_gain = 0.35\n\n'
        '[transform]\nhflip = false\nzoom = 1.0\nspeed = 1.0\n\n'
        '[subtitle]\nfont = "X"\nsize = 64\noutline = 4\nposition = "bottom"\n\n'
        '[review]\nauto_approve_b = false\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="khong-co"):
        load_config(cfg_file)


def test_rejects_unknown_audio_mode(tmp_path: Path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(
        '[profile]\nactive = "air-16"\n\n'
        '[profile.air-16]\nconcurrency = 1\nwhisper_model = "tiny"\n'
        'demucs_segment = 7\nencoder = "h264_videotoolbox"\n\n'
        '[audio]\nmode = "lung-tung"\nbgm_gain = 0.35\n\n'
        '[transform]\nhflip = false\nzoom = 1.0\nspeed = 1.0\n\n'
        '[subtitle]\nfont = "X"\nsize = 64\noutline = 4\nposition = "bottom"\n\n'
        '[review]\nauto_approve_b = false\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="lung-tung"):
        load_config(cfg_file)
```

- [x] **Step 5: Chạy test, xác nhận thất bại**

Run: `uv run pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'reup.config'`

- [x] **Step 6: Viết `src/reup/config.py`**

```python
"""Đọc config.toml thành các dataclass bất biến."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

AUDIO_MODES = ("separate", "drop_original")


@dataclass(frozen=True)
class ProfileConfig:
    concurrency: int
    whisper_model: str
    demucs_segment: int
    encoder: str


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
class ReviewConfig:
    auto_approve_b: bool


@dataclass(frozen=True)
class Config:
    profile_name: str
    profile: ProfileConfig
    audio: AudioConfig
    transform: TransformConfig
    subtitle: SubtitleConfig
    review: ReviewConfig


def load_config(path: Path) -> Config:
    raw = tomllib.loads(Path(path).read_text(encoding="utf-8"))

    name = raw["profile"]["active"]
    profiles = {k: v for k, v in raw["profile"].items() if k != "active"}
    if name not in profiles:
        raise ValueError(
            f"profile.active = {name!r} nhưng không có mục [profile.{name}]. "
            f"Có sẵn: {sorted(profiles)}"
        )

    audio = raw["audio"]
    if audio["mode"] not in AUDIO_MODES:
        raise ValueError(
            f"audio.mode = {audio['mode']!r} không hợp lệ. Chọn một trong {AUDIO_MODES}"
        )

    return Config(
        profile_name=name,
        profile=ProfileConfig(**profiles[name]),
        audio=AudioConfig(**audio),
        transform=TransformConfig(**raw["transform"]),
        subtitle=SubtitleConfig(**raw["subtitle"]),
        review=ReviewConfig(**raw["review"]),
    )
```

- [x] **Step 7: Chạy test, xác nhận pass**

Run: `uv run pytest tests/test_config.py -v`
Expected: PASS — 3 test

- [x] **Step 8: Commit**

```bash
printf '%s\n' 'jobs/' 'output/' '.venv/' '__pycache__/' '*.pyc' 'reup.db' '.env' '.pytest_cache/' > .gitignore
git add pyproject.toml config.toml .gitignore src/reup tests/test_config.py
git commit -m "feat: khung dự án và bộ đọc config.toml"
```

---

## Task 2: Đếm âm tiết

**Files:**
- Create: `src/reup/text.py`
- Test: `tests/test_text.py`

**Interfaces:**
- Consumes: —
- Produces: `count_syllables(text: str, lang: str) -> int`

Tiếng Việt viết tách rời từng âm tiết nên đếm theo khoảng trắng. Tiếng Trung không có khoảng trắng nên đếm theo ký tự Hán. Tiếng Anh đếm theo cụm nguyên âm. StubTTS dùng hàm này để bịa độ dài; phase 2 dùng lại để tính ngân sách âm tiết.

- [x] **Step 1: Viết test thất bại**

```python
# tests/test_text.py
import pytest
from reup.text import count_syllables


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Hôm nay mình dạy mọi người làm thịt kho tàu", 10),
        ("Xin chào!", 2),
        ("  nhiều   khoảng   trắng  ", 3),
        ("", 0),
        ("---", 0),
    ],
)
def test_vietnamese_counts_whitespace_tokens(text, expected):
    assert count_syllables(text, "vi") == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("今天教大家做红烧肉", 9),
        ("你好，世界", 4),
        ("", 0),
    ],
)
def test_chinese_counts_han_characters(text, expected):
    assert count_syllables(text, "zh") == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("hello world", 3),
        ("a", 1),
        ("", 0),
    ],
)
def test_english_counts_vowel_groups(text, expected):
    assert count_syllables(text, "en") == expected


def test_unknown_language_falls_back_to_whitespace():
    assert count_syllables("mot hai ba", "xx") == 3
```

- [x] **Step 2: Chạy test, xác nhận thất bại**

Run: `uv run pytest tests/test_text.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'reup.text'`

- [x] **Step 3: Viết `src/reup/text.py`**

```python
"""Đếm âm tiết theo ngôn ngữ.

Dùng cho hai việc: StubTTS bịa độ dài, và stage translate (phase 2)
tính trần âm tiết cho mỗi khe thời gian.
"""
from __future__ import annotations

import re
import unicodedata

_HAN = re.compile(r"[㐀-䶿一-鿿]")
_EN_VOWEL_GROUP = re.compile(r"[aeiouy]+")


def _has_letter(token: str) -> bool:
    return any(unicodedata.category(ch).startswith("L") for ch in token)


def count_syllables(text: str, lang: str) -> int:
    if lang == "zh":
        return len(_HAN.findall(text))
    if lang == "en":
        total = 0
        for token in text.lower().split():
            groups = len(_EN_VOWEL_GROUP.findall(token))
            total += groups if groups else (1 if _has_letter(token) else 0)
        return total
    # vi và mọi ngôn ngữ khác: âm tiết viết tách rời bằng khoảng trắng
    return sum(1 for token in text.split() if _has_letter(token))
```

- [x] **Step 4: Chạy test, xác nhận pass**

Run: `uv run pytest tests/test_text.py -v`
Expected: PASS — 12 test

- [x] **Step 5: Commit**

```bash
git add src/reup/text.py tests/test_text.py
git commit -m "feat: đếm âm tiết cho tiếng Việt, Trung, Anh"
```

---

## Task 3: Mô hình Segment và Transcript

**Files:**
- Create: `src/reup/models.py`
- Test: `tests/test_models.py`

**Interfaces:**
- Consumes: —
- Produces: `Segment(id: int, start_ms: int, end_ms: int, text: str, text_source: str = "asr", confidence: float = 1.0, flags: list[str] = [])` với thuộc tính `slot_ms -> int`; `Transcript(source_lang: str, segments: list[Segment])` với `Transcript.load(path: Path) -> Transcript` và `.save(path: Path) -> None`; `Transcript.total_ms -> int`.

- [x] **Step 1: Viết test thất bại**

```python
# tests/test_models.py
from pathlib import Path
from reup.models import Segment, Transcript


def test_slot_ms_is_span():
    seg = Segment(id=1, start_ms=1000, end_ms=4200, text="xin chào")
    assert seg.slot_ms == 3200


def test_defaults():
    seg = Segment(id=1, start_ms=0, end_ms=1000, text="a")
    assert seg.text_source == "asr"
    assert seg.confidence == 1.0
    assert seg.flags == []


def test_flags_are_not_shared_between_instances():
    a = Segment(id=1, start_ms=0, end_ms=1, text="a")
    b = Segment(id=2, start_ms=0, end_ms=1, text="b")
    a.flags.append("overflow")
    assert b.flags == []


def test_total_ms_is_last_end():
    t = Transcript(
        source_lang="zh",
        segments=[
            Segment(id=1, start_ms=0, end_ms=3200, text="今天"),
            Segment(id=2, start_ms=3400, end_ms=7100, text="教大家"),
        ],
    )
    assert t.total_ms == 7100


def test_total_ms_of_empty_transcript_is_zero():
    assert Transcript(source_lang="zh", segments=[]).total_ms == 0


def test_roundtrip_through_json(tmp_path: Path):
    original = Transcript(
        source_lang="zh",
        segments=[
            Segment(
                id=1, start_ms=0, end_ms=3200, text="今天教大家做红烧肉",
                text_source="ocr", confidence=0.93, flags=["asr_ocr_mismatch"],
            )
        ],
    )
    path = tmp_path / "transcript.json"
    original.save(path)
    loaded = Transcript.load(path)
    assert loaded == original


def test_saved_json_is_readable_utf8(tmp_path: Path):
    path = tmp_path / "transcript.json"
    Transcript(
        source_lang="zh",
        segments=[Segment(id=1, start_ms=0, end_ms=100, text="红烧肉")],
    ).save(path)
    assert "红烧肉" in path.read_text(encoding="utf-8")
```

- [x] **Step 2: Chạy test, xác nhận thất bại**

Run: `uv run pytest tests/test_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'reup.models'`

- [x] **Step 3: Viết `src/reup/models.py`**

```python
"""Cấu trúc dữ liệu đi qua pipeline, kèm đọc ghi JSON."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class Segment:
    id: int
    start_ms: int
    end_ms: int
    text: str
    text_source: str = "asr"
    confidence: float = 1.0
    flags: list[str] = field(default_factory=list)

    @property
    def slot_ms(self) -> int:
        return self.end_ms - self.start_ms


@dataclass
class Transcript:
    source_lang: str
    segments: list[Segment]

    @property
    def total_ms(self) -> int:
        return max((s.end_ms for s in self.segments), default=0)

    def save(self, path: Path) -> None:
        payload = {
            "source_lang": self.source_lang,
            "segments": [asdict(s) for s in self.segments],
        }
        Path(path).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    @classmethod
    def load(cls, path: Path) -> Transcript:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(
            source_lang=raw["source_lang"],
            segments=[Segment(**s) for s in raw["segments"]],
        )
```

- [x] **Step 4: Chạy test, xác nhận pass**

Run: `uv run pytest tests/test_models.py -v`
Expected: PASS — 7 test

- [x] **Step 5: Commit**

```bash
git add src/reup/models.py tests/test_models.py
git commit -m "feat: mô hình Segment và Transcript"
```

---

## Task 4: Thư mục job và đường dẫn artifact

**Files:**
- Create: `src/reup/core/job.py`
- Test: `tests/test_job.py`

**Interfaces:**
- Consumes: —
- Produces: `Job(id: str, root: Path, source_url: str, source_lang: str)` với các thuộc tính đường dẫn `source_video`, `source_info`, `audio_dir`, `full_16k`, `full_48k`, `vocals`, `bgm`, `asr_json`, `transcript_json`, `translation_json`, `tts_dir`, `dub_wav`, `render_dir`, `final_mp4`, `meta_json`, `log_jsonl`, `job_json` — tất cả kiểu `Path`; `create_job(jobs_dir: Path, url: str, source_lang: str, job_id: str | None = None) -> Job`; `load_job(jobs_dir: Path, job_id: str) -> Job`; `new_job_id(url: str, now: datetime) -> str`.

- [x] **Step 1: Viết test thất bại**

```python
# tests/test_job.py
from datetime import datetime, timezone
from pathlib import Path
import pytest
from reup.core.job import Job, create_job, load_job, new_job_id


def test_job_id_is_sortable_and_stable():
    now = datetime(2026, 9, 11, 14, 30, 5, tzinfo=timezone.utc)
    a = new_job_id("https://douyin.com/video/123", now)
    b = new_job_id("https://douyin.com/video/123", now)
    assert a == b
    assert a.startswith("20260911-143005-")


def test_job_id_differs_by_url():
    now = datetime(2026, 9, 11, 14, 30, 5, tzinfo=timezone.utc)
    assert new_job_id("https://a/1", now) != new_job_id("https://a/2", now)


def test_create_job_makes_directories(tmp_path: Path):
    job = create_job(tmp_path, "https://a/1", "zh")
    assert job.root.is_dir()
    assert job.audio_dir.is_dir()
    assert job.tts_dir.is_dir()
    assert job.render_dir.is_dir()


def test_artifact_paths_match_spec(tmp_path: Path):
    job = create_job(tmp_path, "https://a/1", "zh", job_id="j1")
    assert job.source_video == tmp_path / "j1" / "source.mp4"
    assert job.full_16k == tmp_path / "j1" / "audio" / "full_16k.wav"
    assert job.full_48k == tmp_path / "j1" / "audio" / "full_48k.wav"
    assert job.vocals == tmp_path / "j1" / "audio" / "vocals.wav"
    assert job.bgm == tmp_path / "j1" / "audio" / "bgm.wav"
    assert job.asr_json == tmp_path / "j1" / "asr.json"
    assert job.final_mp4 == tmp_path / "j1" / "render" / "final.mp4"


def test_create_then_load_roundtrip(tmp_path: Path):
    created = create_job(tmp_path, "https://a/1", "zh", job_id="j1")
    loaded = load_job(tmp_path, "j1")
    assert loaded == created


def test_load_missing_job_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError, match="khong-co"):
        load_job(tmp_path, "khong-co")


def test_tts_segment_path_is_zero_padded(tmp_path: Path):
    job = create_job(tmp_path, "https://a/1", "zh", job_id="j1")
    assert job.tts_segment(7) == tmp_path / "j1" / "tts" / "seg_0007.wav"
    assert job.tts_segment(1234) == tmp_path / "j1" / "tts" / "seg_1234.wav"
```

- [x] **Step 2: Chạy test, xác nhận thất bại**

Run: `uv run pytest tests/test_job.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'reup.core.job'`

- [x] **Step 3: Viết `src/reup/core/job.py`**

```python
"""Thư mục làm việc của một job và toàn bộ đường dẫn artifact trong đó."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


def new_job_id(url: str, now: datetime) -> str:
    stamp = now.strftime("%Y%m%d-%H%M%S")
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:8]
    return f"{stamp}-{digest}"


@dataclass(frozen=True)
class Job:
    id: str
    root: Path
    source_url: str
    source_lang: str

    @property
    def job_json(self) -> Path:
        return self.root / "job.json"

    @property
    def source_video(self) -> Path:
        return self.root / "source.mp4"

    @property
    def source_info(self) -> Path:
        return self.root / "source.info.json"

    @property
    def audio_dir(self) -> Path:
        return self.root / "audio"

    @property
    def full_16k(self) -> Path:
        return self.audio_dir / "full_16k.wav"

    @property
    def full_48k(self) -> Path:
        return self.audio_dir / "full_48k.wav"

    @property
    def vocals(self) -> Path:
        return self.audio_dir / "vocals.wav"

    @property
    def bgm(self) -> Path:
        return self.audio_dir / "bgm.wav"

    @property
    def asr_json(self) -> Path:
        return self.root / "asr.json"

    @property
    def transcript_json(self) -> Path:
        return self.root / "transcript.json"

    @property
    def translation_json(self) -> Path:
        return self.root / "translation.json"

    @property
    def tts_dir(self) -> Path:
        return self.root / "tts"

    def tts_segment(self, seg_id: int) -> Path:
        return self.tts_dir / f"seg_{seg_id:04d}.wav"

    @property
    def dub_wav(self) -> Path:
        return self.root / "dub.wav"

    @property
    def render_dir(self) -> Path:
        return self.root / "render"

    @property
    def final_mp4(self) -> Path:
        return self.render_dir / "final.mp4"

    @property
    def meta_json(self) -> Path:
        return self.root / "meta.json"

    @property
    def log_jsonl(self) -> Path:
        return self.root / "log.jsonl"


def create_job(
    jobs_dir: Path, url: str, source_lang: str, job_id: str | None = None
) -> Job:
    job_id = job_id or new_job_id(url, datetime.now())
    root = Path(jobs_dir) / job_id
    job = Job(id=job_id, root=root, source_url=url, source_lang=source_lang)
    for d in (job.root, job.audio_dir, job.tts_dir, job.render_dir):
        d.mkdir(parents=True, exist_ok=True)
    job.job_json.write_text(
        json.dumps(
            {"id": job_id, "source_url": url, "source_lang": source_lang},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return job


def load_job(jobs_dir: Path, job_id: str) -> Job:
    root = Path(jobs_dir) / job_id
    meta = root / "job.json"
    if not meta.exists():
        raise FileNotFoundError(f"Không có job {job_id!r} trong {jobs_dir}")
    raw = json.loads(meta.read_text(encoding="utf-8"))
    return Job(
        id=raw["id"],
        root=root,
        source_url=raw["source_url"],
        source_lang=raw["source_lang"],
    )
```

- [x] **Step 4: Chạy test, xác nhận pass**

Run: `uv run pytest tests/test_job.py -v`
Expected: PASS — 7 test

- [x] **Step 5: Commit**

```bash
git add src/reup/core/job.py tests/test_job.py
git commit -m "feat: thư mục job và đường dẫn artifact"
```

---

## Task 5: Sổ cái SQLite

**Files:**
- Create: `src/reup/core/store.py`
- Test: `tests/test_store.py`

**Interfaces:**
- Consumes: —
- Produces: `Store(db_path: Path)` với `init_schema() -> None`, `upsert_job(job_id: str, url: str, status: str, stage: str | None = None, error: str | None = None) -> None`, `get_job(job_id: str) -> dict | None`, `list_jobs(status: str | None = None) -> list[dict]`, `record_stage_run(job_id: str, stage: str, started_at: float, finished_at: float, ok: bool, error: str | None = None) -> None`, `stage_durations(stage: str | None = None) -> list[dict]`, `mark_seen(platform: str, video_id: str, phash: str | None = None) -> None`, `is_seen(platform: str, video_id: str) -> bool`, `close() -> None`.

Trạng thái job hợp lệ: `pending`, `running`, `needs_review`, `done`, `failed`.

- [x] **Step 1: Viết test thất bại**

```python
# tests/test_store.py
from pathlib import Path
import pytest
from reup.core.store import Store


@pytest.fixture
def store(tmp_path: Path):
    s = Store(tmp_path / "reup.db")
    s.init_schema()
    yield s
    s.close()


def test_init_schema_is_idempotent(tmp_path: Path):
    s = Store(tmp_path / "reup.db")
    s.init_schema()
    s.init_schema()
    s.close()


def test_upsert_then_get(store: Store):
    store.upsert_job("j1", "https://a/1", "pending")
    row = store.get_job("j1")
    assert row["id"] == "j1"
    assert row["url"] == "https://a/1"
    assert row["status"] == "pending"
    assert row["stage"] is None


def test_upsert_overwrites_status_and_stage(store: Store):
    store.upsert_job("j1", "https://a/1", "pending")
    store.upsert_job("j1", "https://a/1", "running", stage="asr")
    row = store.get_job("j1")
    assert row["status"] == "running"
    assert row["stage"] == "asr"


def test_get_missing_job_returns_none(store: Store):
    assert store.get_job("khong-co") is None


def test_rejects_unknown_status(store: Store):
    with pytest.raises(ValueError, match="lung-tung"):
        store.upsert_job("j1", "https://a/1", "lung-tung")


def test_list_jobs_filters_by_status(store: Store):
    store.upsert_job("j1", "https://a/1", "done")
    store.upsert_job("j2", "https://a/2", "failed")
    assert [r["id"] for r in store.list_jobs(status="failed")] == ["j2"]
    assert len(store.list_jobs()) == 2


def test_record_stage_run_and_read_durations(store: Store):
    store.upsert_job("j1", "https://a/1", "running")
    store.record_stage_run("j1", "asr", 100.0, 104.5, ok=True)
    store.record_stage_run("j1", "demux", 90.0, 91.0, ok=True)
    rows = store.stage_durations(stage="asr")
    assert len(rows) == 1
    assert rows[0]["stage"] == "asr"
    assert rows[0]["duration_ms"] == 4500


def test_failed_stage_run_keeps_error(store: Store):
    store.upsert_job("j1", "https://a/1", "running")
    store.record_stage_run("j1", "asr", 0.0, 1.0, ok=False, error="hết bộ nhớ")
    rows = store.stage_durations()
    assert rows[0]["ok"] == 0
    assert rows[0]["error"] == "hết bộ nhớ"


def test_seen_ledger(store: Store):
    assert store.is_seen("douyin", "v123") is False
    store.mark_seen("douyin", "v123", phash="abc")
    assert store.is_seen("douyin", "v123") is True
    assert store.is_seen("tiktok", "v123") is False


def test_mark_seen_twice_does_not_raise(store: Store):
    store.mark_seen("douyin", "v123")
    store.mark_seen("douyin", "v123", phash="abc")
    assert store.is_seen("douyin", "v123") is True
```

- [x] **Step 2: Chạy test, xác nhận thất bại**

Run: `uv run pytest tests/test_store.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'reup.core.store'`

- [x] **Step 3: Viết `src/reup/core/store.py`**

```python
"""Sổ cái SQLite: job, lần chạy stage, và video đã xử lý."""
from __future__ import annotations

import sqlite3
from pathlib import Path

JOB_STATUSES = ("pending", "running", "needs_review", "done", "failed")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id         TEXT PRIMARY KEY,
    url        TEXT NOT NULL,
    status     TEXT NOT NULL,
    stage      TEXT,
    error      TEXT,
    updated_at REAL NOT NULL DEFAULT (unixepoch('subsec'))
);
CREATE TABLE IF NOT EXISTS stage_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id      TEXT NOT NULL,
    stage       TEXT NOT NULL,
    started_at  REAL NOT NULL,
    finished_at REAL NOT NULL,
    duration_ms INTEGER NOT NULL,
    ok          INTEGER NOT NULL,
    error       TEXT
);
CREATE INDEX IF NOT EXISTS idx_stage_runs_stage ON stage_runs(stage);
CREATE TABLE IF NOT EXISTS seen (
    platform TEXT NOT NULL,
    video_id TEXT NOT NULL,
    phash    TEXT,
    seen_at  REAL NOT NULL DEFAULT (unixepoch('subsec')),
    PRIMARY KEY (platform, video_id)
);
"""


class Store:
    def __init__(self, db_path: Path) -> None:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")

    def init_schema(self) -> None:
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def upsert_job(
        self,
        job_id: str,
        url: str,
        status: str,
        stage: str | None = None,
        error: str | None = None,
    ) -> None:
        if status not in JOB_STATUSES:
            raise ValueError(
                f"status {status!r} không hợp lệ. Chọn một trong {JOB_STATUSES}"
            )
        self._conn.execute(
            """
            INSERT INTO jobs (id, url, status, stage, error, updated_at)
            VALUES (?, ?, ?, ?, ?, unixepoch('subsec'))
            ON CONFLICT(id) DO UPDATE SET
                url=excluded.url, status=excluded.status,
                stage=excluded.stage, error=excluded.error,
                updated_at=excluded.updated_at
            """,
            (job_id, url, status, stage, error),
        )
        self._conn.commit()

    def get_job(self, job_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        return dict(row) if row else None

    def list_jobs(self, status: str | None = None) -> list[dict]:
        if status is None:
            rows = self._conn.execute(
                "SELECT * FROM jobs ORDER BY updated_at DESC"
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM jobs WHERE status = ? ORDER BY updated_at DESC",
                (status,),
            ).fetchall()
        return [dict(r) for r in rows]

    def record_stage_run(
        self,
        job_id: str,
        stage: str,
        started_at: float,
        finished_at: float,
        ok: bool,
        error: str | None = None,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO stage_runs
                (job_id, stage, started_at, finished_at, duration_ms, ok, error)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                stage,
                started_at,
                finished_at,
                round((finished_at - started_at) * 1000),
                1 if ok else 0,
                error,
            ),
        )
        self._conn.commit()

    def stage_durations(self, stage: str | None = None) -> list[dict]:
        if stage is None:
            rows = self._conn.execute(
                "SELECT * FROM stage_runs ORDER BY id"
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM stage_runs WHERE stage = ? ORDER BY id", (stage,)
            ).fetchall()
        return [dict(r) for r in rows]

    def mark_seen(
        self, platform: str, video_id: str, phash: str | None = None
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO seen (platform, video_id, phash) VALUES (?, ?, ?)
            ON CONFLICT(platform, video_id) DO UPDATE SET phash=excluded.phash
            """,
            (platform, video_id, phash),
        )
        self._conn.commit()

    def is_seen(self, platform: str, video_id: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM seen WHERE platform = ? AND video_id = ?",
            (platform, video_id),
        ).fetchone()
        return row is not None
```

- [x] **Step 4: Chạy test, xác nhận pass**

Run: `uv run pytest tests/test_store.py -v`
Expected: PASS — 10 test

- [x] **Step 5: Commit**

```bash
git add src/reup/core/store.py tests/test_store.py
git commit -m "feat: sổ cái SQLite cho job, lần chạy stage, video đã xử lý"
```

---

## Task 6: Định nghĩa stage và runner

**Files:**
- Create: `src/reup/core/stage.py`
- Create: `src/reup/core/runner.py`
- Test: `tests/test_runner.py`

**Interfaces:**
- Consumes: `Job` (Task 4), `Store` (Task 5), `Config` (Task 1)
- Produces:
  - `StageSpec(name: str, produces: tuple[str, ...], run: Callable[[Job, Config], None])` — `produces` là đường dẫn tương đối so với `job.root`.
  - `atomic_write(path: Path, data: bytes | str) -> None`
  - `artifacts_present(job: Job, spec: StageSpec) -> bool`
  - `next_stage(job: Job, stages: list[StageSpec]) -> StageSpec | None`
  - `run_stage(job: Job, cfg: Config, spec: StageSpec, store: Store) -> None` — ném lại lỗi sau khi ghi nhật ký
  - `run_job(job: Job, cfg: Config, store: Store, stages: list[StageSpec]) -> str` — trả về status cuối (`done` hoặc `failed`)

Runner quyết định stage kế bằng cách tìm stage đầu tiên chưa có đủ artifact. Nhờ vậy chạy lại là an toàn và `redo` chỉ cần xóa artifact.

- [x] **Step 1: Viết test thất bại**

```python
# tests/test_runner.py
from pathlib import Path
import pytest
from reup.config import (
    AudioConfig, Config, ProfileConfig, ReviewConfig, SubtitleConfig, TransformConfig,
)
from reup.core.job import create_job
from reup.core.runner import artifacts_present, atomic_write, next_stage, run_job, run_stage
from reup.core.stage import StageSpec
from reup.core.store import Store


@pytest.fixture
def cfg():
    return Config(
        profile_name="test",
        profile=ProfileConfig(1, "tiny", 7, "h264_videotoolbox"),
        audio=AudioConfig("separate", 0.35),
        transform=TransformConfig(False, 1.0, 1.0),
        subtitle=SubtitleConfig("X", 64, 4, "bottom"),
        review=ReviewConfig(False),
    )


@pytest.fixture
def store(tmp_path: Path):
    s = Store(tmp_path / "reup.db")
    s.init_schema()
    yield s
    s.close()


def test_atomic_write_leaves_no_tmp_file(tmp_path: Path):
    target = tmp_path / "a.json"
    atomic_write(target, '{"x": 1}')
    assert target.read_text(encoding="utf-8") == '{"x": 1}'
    assert list(tmp_path.glob("*.tmp")) == []


def test_atomic_write_creates_parent_dirs(tmp_path: Path):
    target = tmp_path / "deep" / "nested" / "a.txt"
    atomic_write(target, "hi")
    assert target.read_text(encoding="utf-8") == "hi"


def test_artifacts_present_false_when_missing(tmp_path: Path):
    job = create_job(tmp_path, "https://a/1", "zh", job_id="j1")
    spec = StageSpec("s1", ("out.json",), lambda j, c: None)
    assert artifacts_present(job, spec) is False


def test_artifacts_present_true_when_all_exist(tmp_path: Path):
    job = create_job(tmp_path, "https://a/1", "zh", job_id="j1")
    (job.root / "out.json").write_text("{}", encoding="utf-8")
    spec = StageSpec("s1", ("out.json",), lambda j, c: None)
    assert artifacts_present(job, spec) is True


def test_artifacts_present_false_when_only_some_exist(tmp_path: Path):
    job = create_job(tmp_path, "https://a/1", "zh", job_id="j1")
    (job.root / "a.json").write_text("{}", encoding="utf-8")
    spec = StageSpec("s1", ("a.json", "b.json"), lambda j, c: None)
    assert artifacts_present(job, spec) is False


def test_next_stage_skips_completed(tmp_path: Path):
    job = create_job(tmp_path, "https://a/1", "zh", job_id="j1")
    (job.root / "a.json").write_text("{}", encoding="utf-8")
    stages = [
        StageSpec("first", ("a.json",), lambda j, c: None),
        StageSpec("second", ("b.json",), lambda j, c: None),
    ]
    assert next_stage(job, stages).name == "second"


def test_next_stage_returns_none_when_all_done(tmp_path: Path):
    job = create_job(tmp_path, "https://a/1", "zh", job_id="j1")
    (job.root / "a.json").write_text("{}", encoding="utf-8")
    stages = [StageSpec("first", ("a.json",), lambda j, c: None)]
    assert next_stage(job, stages) is None


def test_run_stage_records_success(tmp_path: Path, cfg, store: Store):
    job = create_job(tmp_path, "https://a/1", "zh", job_id="j1")
    store.upsert_job("j1", "https://a/1", "pending")
    spec = StageSpec(
        "writer", ("a.json",), lambda j, c: atomic_write(j.root / "a.json", "{}")
    )
    run_stage(job, cfg, spec, store)
    runs = store.stage_durations(stage="writer")
    assert len(runs) == 1
    assert runs[0]["ok"] == 1


def test_run_stage_raises_and_records_failure(tmp_path: Path, cfg, store: Store):
    job = create_job(tmp_path, "https://a/1", "zh", job_id="j1")
    store.upsert_job("j1", "https://a/1", "pending")

    def boom(j, c):
        raise RuntimeError("ffmpeg chết")

    spec = StageSpec("boom", ("a.json",), boom)
    with pytest.raises(RuntimeError, match="ffmpeg chết"):
        run_stage(job, cfg, spec, store)
    runs = store.stage_durations(stage="boom")
    assert runs[0]["ok"] == 0
    assert "ffmpeg chết" in runs[0]["error"]


def test_run_stage_errors_if_artifact_not_produced(tmp_path: Path, cfg, store: Store):
    job = create_job(tmp_path, "https://a/1", "zh", job_id="j1")
    store.upsert_job("j1", "https://a/1", "pending")
    spec = StageSpec("lazy", ("a.json",), lambda j, c: None)
    with pytest.raises(RuntimeError, match="a.json"):
        run_stage(job, cfg, spec, store)


def test_run_job_runs_all_stages_in_order(tmp_path: Path, cfg, store: Store):
    job = create_job(tmp_path, "https://a/1", "zh", job_id="j1")
    store.upsert_job("j1", "https://a/1", "pending")
    order: list[str] = []

    def make(name: str, artifact: str):
        def _run(j, c):
            order.append(name)
            atomic_write(j.root / artifact, "{}")

        return StageSpec(name, (artifact,), _run)

    stages = [make("one", "a.json"), make("two", "b.json")]
    assert run_job(job, cfg, store, stages) == "done"
    assert order == ["one", "two"]
    assert store.get_job("j1")["status"] == "done"


def test_run_job_resumes_from_middle(tmp_path: Path, cfg, store: Store):
    job = create_job(tmp_path, "https://a/1", "zh", job_id="j1")
    store.upsert_job("j1", "https://a/1", "pending")
    (job.root / "a.json").write_text("{}", encoding="utf-8")
    order: list[str] = []

    def make(name: str, artifact: str):
        def _run(j, c):
            order.append(name)
            atomic_write(j.root / artifact, "{}")

        return StageSpec(name, (artifact,), _run)

    run_job(job, cfg, store, [make("one", "a.json"), make("two", "b.json")])
    assert order == ["two"]


def test_run_job_marks_failed_and_stops(tmp_path: Path, cfg, store: Store):
    job = create_job(tmp_path, "https://a/1", "zh", job_id="j1")
    store.upsert_job("j1", "https://a/1", "pending")
    reached_second = []

    def boom(j, c):
        raise RuntimeError("gãy")

    def second(j, c):
        reached_second.append(True)
        atomic_write(j.root / "b.json", "{}")

    stages = [StageSpec("one", ("a.json",), boom), StageSpec("two", ("b.json",), second)]
    assert run_job(job, cfg, store, stages) == "failed"
    assert reached_second == []
    row = store.get_job("j1")
    assert row["status"] == "failed"
    assert row["stage"] == "one"
    assert "gãy" in row["error"]
```

- [x] **Step 2: Chạy test, xác nhận thất bại**

Run: `uv run pytest tests/test_runner.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'reup.core.runner'`

- [x] **Step 3: Viết `src/reup/core/stage.py`**

```python
"""Mô tả một stage: tên, artifact nó sinh ra, và hàm chạy."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from reup.config import Config
    from reup.core.job import Job


@dataclass(frozen=True)
class StageSpec:
    name: str
    produces: tuple[str, ...]
    run: Callable[["Job", "Config"], None]
```

- [x] **Step 4: Viết `src/reup/core/runner.py`**

```python
"""Chọn stage kế tiếp, chạy nó, ghi nhật ký. Không biết stage nào làm gì."""
from __future__ import annotations

import json
import os
import time
import traceback
from pathlib import Path

from reup.config import Config
from reup.core.job import Job
from reup.core.stage import StageSpec
from reup.core.store import Store


def atomic_write(path: Path, data: bytes | str) -> None:
    """Ghi ra file tạm rồi đổi tên, để stage chết giữa chừng không để lại rác."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    mode, encoding = ("wb", None) if isinstance(data, bytes) else ("w", "utf-8")
    with open(tmp, mode, encoding=encoding) as fh:
        fh.write(data)
    os.replace(tmp, path)


def artifacts_present(job: Job, spec: StageSpec) -> bool:
    return all((job.root / rel).exists() for rel in spec.produces)


def next_stage(job: Job, stages: list[StageSpec]) -> StageSpec | None:
    for spec in stages:
        if not artifacts_present(job, spec):
            return spec
    return None


def _append_log(job: Job, entry: dict) -> None:
    with open(job.log_jsonl, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def run_stage(job: Job, cfg: Config, spec: StageSpec, store: Store) -> None:
    started = time.time()
    store.upsert_job(job.id, job.source_url, "running", stage=spec.name)
    try:
        spec.run(job, cfg)
        missing = [rel for rel in spec.produces if not (job.root / rel).exists()]
        if missing:
            raise RuntimeError(
                f"stage {spec.name!r} chạy xong nhưng thiếu artifact: {missing}"
            )
    except Exception as exc:
        finished = time.time()
        detail = traceback.format_exc()
        store.record_stage_run(
            job.id, spec.name, started, finished, ok=False, error=str(exc)
        )
        _append_log(
            job,
            {"stage": spec.name, "ok": False, "started": started,
             "finished": finished, "error": detail},
        )
        raise
    finished = time.time()
    store.record_stage_run(job.id, spec.name, started, finished, ok=True)
    _append_log(
        job, {"stage": spec.name, "ok": True, "started": started, "finished": finished}
    )


def run_job(job: Job, cfg: Config, store: Store, stages: list[StageSpec]) -> str:
    while (spec := next_stage(job, stages)) is not None:
        try:
            run_stage(job, cfg, spec, store)
        except Exception as exc:
            store.upsert_job(
                job.id, job.source_url, "failed", stage=spec.name, error=str(exc)
            )
            return "failed"
    store.upsert_job(job.id, job.source_url, "done", stage=None)
    return "done"
```

- [x] **Step 5: Chạy test, xác nhận pass**

Run: `uv run pytest tests/test_runner.py -v`
Expected: PASS — 13 test

- [x] **Step 6: Commit**

```bash
git add src/reup/core/stage.py src/reup/core/runner.py tests/test_runner.py
git commit -m "feat: stage runner với resume và ghi nhật ký"
```

---

## Task 7: Bọc ffmpeg và ffprobe

**Files:**
- Create: `src/reup/media/ffmpeg.py`
- Create: `tests/conftest.py`
- Test: `tests/test_ffmpeg.py`

**Interfaces:**
- Consumes: —
- Produces: `FFmpegError(RuntimeError)`; `run_ffmpeg(args: list[str]) -> str` (trả stderr, ném `FFmpegError` khi mã thoát khác 0); `MediaInfo(duration_ms: int, width: int, height: int, has_video: bool, has_audio: bool)`; `probe(path: Path) -> MediaInfo`.
- Fixture dùng chung cho mọi test sau: `sample_video(tmp_path) -> Path` (6 giây, 540×960, có audio) và `sample_wav(tmp_path) -> Path` (2 giây, 48kHz stereo).

Dùng 540×960 thay vì 1080×1920 cho fixture: cùng tỉ lệ 9:16 nhưng encode nhanh hơn bốn lần, test chạy nhanh hơn hẳn.

- [x] **Step 1: Viết `tests/conftest.py`**

```python
"""Fixture media sinh bằng ffmpeg lúc chạy test — không commit file nhị phân."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

FIXTURE_W, FIXTURE_H = 540, 960


@pytest.fixture(scope="session", autouse=True)
def require_ffmpeg():
    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            pytest.skip(f"cần {tool} trong PATH", allow_module_level=True)


def _run(args: list[str]) -> None:
    subprocess.run(args, check=True, capture_output=True)


@pytest.fixture
def sample_video(tmp_path: Path) -> Path:
    """6 giây, 540x960, có cả video lẫn audio."""
    out = tmp_path / "sample.mp4"
    _run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", f"testsrc=size={FIXTURE_W}x{FIXTURE_H}:rate=30:duration=6",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=6:sample_rate=48000",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest", str(out),
    ])
    return out


@pytest.fixture
def sample_wav(tmp_path: Path) -> Path:
    """2 giây, 48kHz stereo."""
    out = tmp_path / "sample.wav"
    _run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", "sine=frequency=880:duration=2:sample_rate=48000",
        "-ac", "2", "-c:a", "pcm_s16le", str(out),
    ])
    return out
```

- [x] **Step 2: Viết test thất bại**

```python
# tests/test_ffmpeg.py
from pathlib import Path
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
```

- [x] **Step 3: Chạy test, xác nhận thất bại**

Run: `uv run pytest tests/test_ffmpeg.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'reup.media.ffmpeg'`

- [x] **Step 4: Viết `src/reup/media/ffmpeg.py`**

```python
"""Bọc ffmpeg và ffprobe. Hàm thuần — không biết Job hay Config là gì."""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path


class FFmpegError(RuntimeError):
    pass


@dataclass(frozen=True)
class MediaInfo:
    duration_ms: int
    width: int
    height: int
    has_video: bool
    has_audio: bool


def run_ffmpeg(args: list[str]) -> str:
    """Chạy ffmpeg với -y và -loglevel error. Trả stderr, ném FFmpegError khi lỗi."""
    cmd = ["ffmpeg", "-y", "-loglevel", "error", *args]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise FFmpegError(
            f"ffmpeg thoát với mã {proc.returncode}\n"
            f"lệnh: {' '.join(cmd)}\n"
            f"stderr:\n{proc.stderr}"
        )
    return proc.stderr


def probe(path: Path) -> MediaInfo:
    cmd = [
        "ffprobe", "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise FFmpegError(
            f"ffprobe thoát với mã {proc.returncode} khi đọc {path}\n"
            f"stderr:\n{proc.stderr}"
        )
    raw = json.loads(proc.stdout)
    streams = raw.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    duration = float(raw.get("format", {}).get("duration", 0.0))
    return MediaInfo(
        duration_ms=round(duration * 1000),
        width=int(video["width"]) if video else 0,
        height=int(video["height"]) if video else 0,
        has_video=video is not None,
        has_audio=audio is not None,
    )
```

- [x] **Step 5: Chạy test, xác nhận pass**

Run: `uv run pytest tests/test_ffmpeg.py -v`
Expected: PASS — 5 test

- [x] **Step 6: Commit**

```bash
git add src/reup/media/ffmpeg.py tests/conftest.py tests/test_ffmpeg.py
git commit -m "feat: bọc ffmpeg và ffprobe, fixture media sinh lúc chạy test"
```

---

## Task 8: Thao tác audio

**Files:**
- Create: `src/reup/media/audio.py`
- Test: `tests/test_audio.py`

**Interfaces:**
- Consumes: `run_ffmpeg`, `probe` (Task 7)
- Produces:
  - `extract_audio(src: Path, out: Path, sample_rate: int, channels: int) -> None`
  - `duration_ms(path: Path) -> int`
  - `atempo_filter(ratio: float) -> str` — ghép chuỗi `atempo` khi ratio ngoài khoảng 0.5–2.0
  - `apply_tempo(src: Path, out: Path, ratio: float) -> None`
  - `silence(out: Path, ms: int, sample_rate: int = 48000, channels: int = 2) -> None`
  - `build_timeline(placements: list[tuple[int, Path]], total_ms: int, out: Path) -> None` — đặt mỗi wav vào đúng mốc `start_ms` trên nền im lặng dài `total_ms`

- [x] **Step 1: Viết test thất bại**

```python
# tests/test_audio.py
from pathlib import Path
import pytest
from reup.media.audio import (
    apply_tempo, atempo_filter, build_timeline, duration_ms, extract_audio, silence,
)
from reup.media.ffmpeg import probe


@pytest.mark.parametrize(
    "ratio,expected",
    [
        (1.0, "atempo=1.000000"),
        (1.25, "atempo=1.250000"),
        (0.5, "atempo=0.500000"),
        (2.0, "atempo=2.000000"),
    ],
)
def test_atempo_single_filter_in_range(ratio, expected):
    assert atempo_filter(ratio) == expected


def test_atempo_chains_above_two():
    assert atempo_filter(4.0) == "atempo=2.0,atempo=2.000000"


def test_atempo_chains_below_half():
    assert atempo_filter(0.25) == "atempo=0.5,atempo=0.500000"


def test_atempo_rejects_non_positive():
    with pytest.raises(ValueError):
        atempo_filter(0.0)


def test_extract_audio_makes_16k_mono(sample_video: Path, tmp_path: Path):
    out = tmp_path / "full_16k.wav"
    extract_audio(sample_video, out, sample_rate=16000, channels=1)
    assert out.exists()
    assert abs(duration_ms(out) - 6000) <= 100


def test_extract_audio_makes_48k_stereo(sample_video: Path, tmp_path: Path):
    out = tmp_path / "full_48k.wav"
    extract_audio(sample_video, out, sample_rate=48000, channels=2)
    info = probe(out)
    assert info.has_audio is True
    assert abs(info.duration_ms - 6000) <= 100


def test_silence_has_requested_duration(tmp_path: Path):
    out = tmp_path / "sil.wav"
    silence(out, ms=1500)
    assert abs(duration_ms(out) - 1500) <= 100


def test_apply_tempo_shortens_audio(sample_wav: Path, tmp_path: Path):
    out = tmp_path / "fast.wav"
    apply_tempo(sample_wav, out, 2.0)
    assert abs(duration_ms(out) - 1000) <= 100


def test_apply_tempo_ratio_one_keeps_duration(sample_wav: Path, tmp_path: Path):
    out = tmp_path / "same.wav"
    apply_tempo(sample_wav, out, 1.0)
    assert abs(duration_ms(out) - 2000) <= 100


def test_build_timeline_has_requested_total_duration(sample_wav: Path, tmp_path: Path):
    out = tmp_path / "dub.wav"
    build_timeline([(0, sample_wav), (3000, sample_wav)], total_ms=6000, out=out)
    assert abs(duration_ms(out) - 6000) <= 100


def test_build_timeline_with_no_placements_is_pure_silence(tmp_path: Path):
    out = tmp_path / "dub.wav"
    build_timeline([], total_ms=4000, out=out)
    assert abs(duration_ms(out) - 4000) <= 100
```

- [x] **Step 2: Chạy test, xác nhận thất bại**

Run: `uv run pytest tests/test_audio.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'reup.media.audio'`

- [x] **Step 3: Viết `src/reup/media/audio.py`**

```python
"""Thao tác audio bằng ffmpeg. Hàm thuần — nhận Path vào, ghi Path ra."""
from __future__ import annotations

from pathlib import Path

from reup.media.ffmpeg import probe, run_ffmpeg


def duration_ms(path: Path) -> int:
    return probe(path).duration_ms


def extract_audio(src: Path, out: Path, sample_rate: int, channels: int) -> None:
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg([
        "-i", str(src), "-vn",
        "-ac", str(channels), "-ar", str(sample_rate),
        "-c:a", "pcm_s16le", str(out),
    ])


def atempo_filter(ratio: float) -> str:
    """ffmpeg atempo chỉ nhận 0.5–2.0 nên phải ghép chuỗi khi ra ngoài khoảng đó."""
    if ratio <= 0:
        raise ValueError(f"ratio phải dương, nhận {ratio}")
    parts: list[str] = []
    remaining = ratio
    while remaining > 2.0:
        parts.append("atempo=2.0")
        remaining /= 2.0
    while remaining < 0.5:
        parts.append("atempo=0.5")
        remaining /= 0.5
    parts.append(f"atempo={remaining:.6f}")
    return ",".join(parts)


def apply_tempo(src: Path, out: Path, ratio: float) -> None:
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg([
        "-i", str(src), "-filter:a", atempo_filter(ratio),
        "-c:a", "pcm_s16le", str(out),
    ])


def silence(out: Path, ms: int, sample_rate: int = 48000, channels: int = 2) -> None:
    layout = "mono" if channels == 1 else "stereo"
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg([
        "-f", "lavfi", "-i", f"anullsrc=r={sample_rate}:cl={layout}",
        "-t", f"{ms / 1000:.3f}", "-c:a", "pcm_s16le", str(out),
    ])


def build_timeline(
    placements: list[tuple[int, Path]], total_ms: int, out: Path
) -> None:
    """Đặt mỗi wav vào mốc start_ms của nó trên nền im lặng dài total_ms."""
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    if not placements:
        silence(out, total_ms)
        return

    args: list[str] = ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
    for _, path in placements:
        args += ["-i", str(path)]

    chains: list[str] = []
    labels: list[str] = ["[0:a]"]
    for idx, (start_ms, _) in enumerate(placements, start=1):
        label = f"[d{idx}]"
        chains.append(f"[{idx}:a]adelay={start_ms}|{start_ms},aresample=48000{label}")
        labels.append(label)

    chains.append(
        f"{''.join(labels)}amix=inputs={len(labels)}:duration=first:normalize=0[out]"
    )
    args += [
        "-filter_complex", ";".join(chains),
        "-map", "[out]", "-t", f"{total_ms / 1000:.3f}",
        "-c:a", "pcm_s16le", str(out),
    ]
    run_ffmpeg(args)
```

- [x] **Step 4: Chạy test, xác nhận pass**

Run: `uv run pytest tests/test_audio.py -v`
Expected: PASS — 14 test

- [x] **Step 5: Commit**

```bash
git add src/reup/media/audio.py tests/test_audio.py
git commit -m "feat: thao tác audio — trích, co giãn, dựng timeline lồng tiếng"
```

---

## Task 9: Adapter nguồn và stage `fetch`

**Files:**
- Create: `src/reup/adapters/source.py`
- Create: `src/reup/adapters/manual.py`
- Create: `src/reup/stages/fetch.py`
- Test: `tests/test_stage_fetch.py`

**Interfaces:**
- Consumes: `Job` (Task 4), `Config` (Task 1), `atomic_write` (Task 6)
- Produces:
  - `Candidate(platform: str, video_id: str, url: str, title: str, duration_ms: int, view_count: int, published_at: str)`
  - `FetchResult(video_path: Path, info_path: Path, duration_ms: int, width: int, height: int)`
  - `SourceAdapter` Protocol: `name: str`, `list_trending(region: str, limit: int) -> list[Candidate]`, `fetch(url: str, dest: Path) -> FetchResult`
  - `ManualSource()` — implement `SourceAdapter`; `list_trending` ném `NotImplementedError`
  - `run(job: Job, cfg: Config) -> None` trong `stages/fetch.py`, và `SPEC: StageSpec`

`ManualSource.fetch` gọi `yt-dlp` bằng subprocess chứ không import làm thư viện: giữ biên giới hẹp, và đổi sang bản `yt-dlp` cài riêng chỉ là đổi đường dẫn.

- [x] **Step 1: Viết test thất bại**

```python
# tests/test_stage_fetch.py
from pathlib import Path
import pytest
from reup.adapters.manual import ManualSource
from reup.adapters.source import Candidate, FetchResult
from reup.core.job import create_job
from reup.stages import fetch as fetch_stage


def test_manual_source_has_name():
    assert ManualSource().name == "manual"


def test_manual_source_cannot_list_trending():
    with pytest.raises(NotImplementedError, match="manual"):
        ManualSource().list_trending("VN", 10)


def test_fetch_stage_writes_video_and_info(tmp_path: Path, monkeypatch, sample_video: Path, cfg_fixture):
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")

    def fake_fetch(self, url: str, dest: Path) -> FetchResult:
        dest.write_bytes(sample_video.read_bytes())
        info = dest.parent / "source.info.json"
        info.write_text('{"title": "thịt kho tàu"}', encoding="utf-8")
        return FetchResult(dest, info, 6000, 540, 960)

    monkeypatch.setattr(ManualSource, "fetch", fake_fetch)
    fetch_stage.run(job, cfg_fixture)

    assert job.source_video.exists()
    assert job.source_info.exists()
    assert "thịt kho tàu" in job.source_info.read_text(encoding="utf-8")


def test_fetch_stage_rejects_video_without_audio(tmp_path: Path, monkeypatch, cfg_fixture):
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")

    def fake_fetch(self, url: str, dest: Path) -> FetchResult:
        import subprocess
        subprocess.run([
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc=size=540x960:rate=30:duration=2",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(dest),
        ], check=True, capture_output=True)
        info = dest.parent / "source.info.json"
        info.write_text("{}", encoding="utf-8")
        return FetchResult(dest, info, 2000, 540, 960)

    monkeypatch.setattr(ManualSource, "fetch", fake_fetch)
    with pytest.raises(ValueError, match="không có audio"):
        fetch_stage.run(job, cfg_fixture)


def test_spec_declares_its_artifacts():
    assert fetch_stage.SPEC.name == "fetch"
    assert set(fetch_stage.SPEC.produces) == {"source.mp4", "source.info.json"}


def test_candidate_is_hashable():
    c = Candidate("douyin", "v1", "https://a/1", "tiêu đề", 30000, 5000, "2026-09-01")
    assert {c, c} == {c}
```

Thêm fixture dùng chung vào `tests/conftest.py`:

```python
# thêm vào cuối tests/conftest.py
from reup.config import (
    AudioConfig, Config, ProfileConfig, ReviewConfig, SubtitleConfig, TransformConfig,
)


@pytest.fixture
def cfg_fixture() -> Config:
    return Config(
        profile_name="test",
        profile=ProfileConfig(1, "tiny", 7, "h264_videotoolbox"),
        audio=AudioConfig("separate", 0.35),
        transform=TransformConfig(False, 1.0, 1.0),
        subtitle=SubtitleConfig("Be Vietnam Pro", 64, 4, "bottom"),
        review=ReviewConfig(False),
    )
```

- [x] **Step 2: Chạy test, xác nhận thất bại**

Run: `uv run pytest tests/test_stage_fetch.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'reup.adapters.manual'`

- [x] **Step 3: Viết `src/reup/adapters/source.py`**

```python
"""Biên giới ra các nền tảng video."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class Candidate:
    platform: str
    video_id: str
    url: str
    title: str
    duration_ms: int
    view_count: int
    published_at: str


@dataclass(frozen=True)
class FetchResult:
    video_path: Path
    info_path: Path
    duration_ms: int
    width: int
    height: int


class SourceAdapter(Protocol):
    name: str

    def list_trending(self, region: str, limit: int) -> list[Candidate]: ...

    def fetch(self, url: str, dest: Path) -> FetchResult: ...
```

- [x] **Step 4: Viết `src/reup/adapters/manual.py`**

```python
"""Nguồn thủ công: người dùng dán link, yt-dlp tải về.

Đây là adapter luôn sống. Ba crawler trending ở phase 6 có gãy thì
đường này vẫn chạy, nên pipeline không bao giờ tắc hoàn toàn.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from reup.adapters.source import Candidate, FetchResult


class ManualSource:
    name = "manual"

    def list_trending(self, region: str, limit: int) -> list[Candidate]:
        raise NotImplementedError(
            "nguồn manual không quét trending — dán link bằng `reup add <url>`"
        )

    def fetch(self, url: str, dest: Path) -> FetchResult:
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        info_path = dest.parent / "source.info.json"

        cmd = [
            "yt-dlp",
            "--no-playlist",
            "--write-info-json",
            "--merge-output-format", "mp4",
            "-f", "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/b",
            "-o", str(dest),
            url,
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(
                f"yt-dlp thoát với mã {proc.returncode} khi tải {url}\n"
                f"stderr:\n{proc.stderr}"
            )

        produced = dest.with_suffix(".info.json")
        if produced.exists() and produced != info_path:
            produced.replace(info_path)
        if not info_path.exists():
            info_path.write_text("{}", encoding="utf-8")

        raw = json.loads(info_path.read_text(encoding="utf-8"))
        return FetchResult(
            video_path=dest,
            info_path=info_path,
            duration_ms=round(float(raw.get("duration", 0)) * 1000),
            width=int(raw.get("width", 0)),
            height=int(raw.get("height", 0)),
        )
```

- [x] **Step 5: Viết `src/reup/stages/fetch.py`**

```python
"""Stage 2 — tải video nguồn về thư mục job."""
from __future__ import annotations

from reup.adapters.manual import ManualSource
from reup.config import Config
from reup.core.job import Job
from reup.core.stage import StageSpec
from reup.media.ffmpeg import probe


def run(job: Job, cfg: Config) -> None:
    ManualSource().fetch(job.source_url, job.source_video)

    info = probe(job.source_video)
    if not info.has_audio:
        raise ValueError(
            f"video {job.source_url} không có audio — pipeline này cần tiếng nói để dịch"
        )
    if not info.has_video:
        raise ValueError(f"file tải về từ {job.source_url} không có stream video")


SPEC = StageSpec(name="fetch", produces=("source.mp4", "source.info.json"), run=run)
```

- [x] **Step 6: Chạy test, xác nhận pass**

Run: `uv run pytest tests/test_stage_fetch.py -v`
Expected: PASS — 6 test

- [x] **Step 7: Commit**

```bash
git add src/reup/adapters/source.py src/reup/adapters/manual.py \
        src/reup/stages/fetch.py tests/test_stage_fetch.py tests/conftest.py
git commit -m "feat: adapter nguồn manual và stage fetch"
```

---

## Task 10: Stage `demux`

**Files:**
- Create: `src/reup/stages/demux.py`
- Test: `tests/test_stage_demux.py`

**Interfaces:**
- Consumes: `extract_audio` (Task 8), `Job` (Task 4)
- Produces: `run(job: Job, cfg: Config) -> None`, `SPEC: StageSpec` với `produces=("audio/full_16k.wav", "audio/full_48k.wav")`

- [x] **Step 1: Viết test thất bại**

```python
# tests/test_stage_demux.py
from pathlib import Path
from reup.core.job import create_job
from reup.media.ffmpeg import probe
from reup.stages import demux as demux_stage


def test_demux_writes_both_audio_files(tmp_path: Path, sample_video: Path, cfg_fixture):
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    job.source_video.write_bytes(sample_video.read_bytes())

    demux_stage.run(job, cfg_fixture)

    assert job.full_16k.exists()
    assert job.full_48k.exists()
    assert abs(probe(job.full_16k).duration_ms - 6000) <= 100
    assert abs(probe(job.full_48k).duration_ms - 6000) <= 100


def test_spec_declares_its_artifacts():
    assert demux_stage.SPEC.name == "demux"
    assert set(demux_stage.SPEC.produces) == {
        "audio/full_16k.wav", "audio/full_48k.wav",
    }
```

- [x] **Step 2: Chạy test, xác nhận thất bại**

Run: `uv run pytest tests/test_stage_demux.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'reup.stages.demux'`

- [x] **Step 3: Viết `src/reup/stages/demux.py`**

```python
"""Stage 3 — tách audio ra hai bản: 16kHz mono cho ASR, 48kHz stereo để mix."""
from __future__ import annotations

from reup.config import Config
from reup.core.job import Job
from reup.core.stage import StageSpec
from reup.media.audio import extract_audio


def run(job: Job, cfg: Config) -> None:
    extract_audio(job.source_video, job.full_16k, sample_rate=16000, channels=1)
    extract_audio(job.source_video, job.full_48k, sample_rate=48000, channels=2)


SPEC = StageSpec(
    name="demux",
    produces=("audio/full_16k.wav", "audio/full_48k.wav"),
    run=run,
)
```

- [x] **Step 4: Chạy test, xác nhận pass**

Run: `uv run pytest tests/test_stage_demux.py -v`
Expected: PASS — 2 test

- [x] **Step 5: Commit**

```bash
git add src/reup/stages/demux.py tests/test_stage_demux.py
git commit -m "feat: stage demux tách audio 16k và 48k"
```

---

## Task 11: Bọc Whisper và stage `asr`

**Files:**
- Create: `src/reup/media/whisper.py`
- Create: `src/reup/stages/asr.py`
- Test: `tests/test_stage_asr.py`

**Interfaces:**
- Consumes: `Segment`, `Transcript` (Task 3), `Job` (Task 4), `Config` (Task 1)
- Produces:
  - `transcribe(audio: Path, model: str, language: str | None) -> tuple[str, list[Segment]]` trong `media/whisper.py` — trả `(detected_lang, segments)`
  - `run(job: Job, cfg: Config) -> None`, `SPEC: StageSpec` với `produces=("asr.json",)` trong `stages/asr.py`

Phase 1 chưa có stage `reconcile`, nên `asr` ghi thẳng ra `asr.json` **và** copy sang `transcript.json` để các stage sau có nguồn sự thật. Phase 3 sẽ bỏ dòng copy này khi `reconcile` xuất hiện.

Test không nạp model thật — quá chậm và phụ thuộc mạng. Thay `media.whisper.transcribe` bằng hàm giả.

- [x] **Step 1: Viết test thất bại**

```python
# tests/test_stage_asr.py
from pathlib import Path
import pytest
from reup.models import Segment, Transcript
from reup.core.job import create_job
from reup.stages import asr as asr_stage


@pytest.fixture
def fake_transcribe(monkeypatch):
    calls = {}

    def _fake(audio: Path, model: str, language: str | None):
        calls["audio"] = audio
        calls["model"] = model
        calls["language"] = language
        return "zh", [
            Segment(id=1, start_ms=0, end_ms=3200, text="今天教大家做红烧肉"),
            Segment(id=2, start_ms=3400, end_ms=6000, text="先把肉切块"),
        ]

    monkeypatch.setattr(asr_stage, "transcribe", _fake)
    return calls


def test_asr_writes_asr_json(tmp_path: Path, cfg_fixture, fake_transcribe):
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    job.full_16k.write_bytes(b"")

    asr_stage.run(job, cfg_fixture)

    t = Transcript.load(job.asr_json)
    assert t.source_lang == "zh"
    assert len(t.segments) == 2
    assert t.segments[0].text == "今天教大家做红烧肉"
    assert t.segments[0].text_source == "asr"


def test_asr_also_seeds_transcript_json(tmp_path: Path, cfg_fixture, fake_transcribe):
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    job.full_16k.write_bytes(b"")

    asr_stage.run(job, cfg_fixture)

    assert Transcript.load(job.transcript_json) == Transcript.load(job.asr_json)


def test_asr_passes_model_from_profile(tmp_path: Path, cfg_fixture, fake_transcribe):
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    job.full_16k.write_bytes(b"")

    asr_stage.run(job, cfg_fixture)

    assert fake_transcribe["model"] == "tiny"
    assert fake_transcribe["audio"] == job.full_16k
    assert fake_transcribe["language"] == "zh"


def test_asr_passes_none_language_when_auto(tmp_path: Path, cfg_fixture, fake_transcribe):
    job = create_job(tmp_path / "jobs", "https://a/1", "auto", job_id="j1")
    job.full_16k.write_bytes(b"")

    asr_stage.run(job, cfg_fixture)

    assert fake_transcribe["language"] is None


def test_asr_rejects_empty_transcript(tmp_path: Path, cfg_fixture, monkeypatch):
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    job.full_16k.write_bytes(b"")
    monkeypatch.setattr(asr_stage, "transcribe", lambda a, m, l: ("zh", []))

    with pytest.raises(ValueError, match="không nhận được câu nào"):
        asr_stage.run(job, cfg_fixture)


def test_spec_declares_its_artifacts():
    assert asr_stage.SPEC.name == "asr"
    assert set(asr_stage.SPEC.produces) == {"asr.json", "transcript.json"}
```

- [x] **Step 2: Chạy test, xác nhận thất bại**

Run: `uv run pytest tests/test_stage_asr.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'reup.stages.asr'`

- [x] **Step 3: Viết `src/reup/media/whisper.py`**

```python
"""Bọc mlx-whisper. Hàm thuần — nhận đường dẫn audio, trả câu kèm mốc thời gian."""
from __future__ import annotations

from pathlib import Path

from reup.models import Segment


def transcribe(
    audio: Path, model: str, language: str | None
) -> tuple[str, list[Segment]]:
    import mlx_whisper

    result = mlx_whisper.transcribe(
        str(audio),
        path_or_hf_repo=model,
        language=language,
        word_timestamps=True,
    )
    segments = [
        Segment(
            id=idx,
            start_ms=round(float(raw["start"]) * 1000),
            end_ms=round(float(raw["end"]) * 1000),
            text=raw["text"].strip(),
            text_source="asr",
            confidence=1.0,
        )
        for idx, raw in enumerate(result.get("segments", []), start=1)
        if raw["text"].strip()
    ]
    return result.get("language", language or "unknown"), segments
```

- [x] **Step 4: Viết `src/reup/stages/asr.py`**

```python
"""Stage 5 — nghe audio, viết ra câu kèm mốc thời gian."""
from __future__ import annotations

from reup.config import Config
from reup.core.job import Job
from reup.core.stage import StageSpec
from reup.media.whisper import transcribe
from reup.models import Transcript


def run(job: Job, cfg: Config) -> None:
    language = None if job.source_lang == "auto" else job.source_lang
    detected_lang, segments = transcribe(
        job.full_16k, cfg.profile.whisper_model, language
    )
    if not segments:
        raise ValueError(
            f"ASR không nhận được câu nào từ {job.full_16k} — "
            "video có thể không có tiếng nói"
        )

    transcript = Transcript(source_lang=detected_lang, segments=segments)
    transcript.save(job.asr_json)
    # Phase 1 chưa có stage reconcile, nên asr là nguồn sự thật luôn.
    # Phase 3 bỏ dòng dưới khi reconcile xuất hiện.
    transcript.save(job.transcript_json)


SPEC = StageSpec(name="asr", produces=("asr.json", "transcript.json"), run=run)
```

- [x] **Step 5: Chạy test, xác nhận pass**

Run: `uv run pytest tests/test_stage_asr.py -v`
Expected: PASS — 6 test

- [x] **Step 6: Commit**

```bash
git add src/reup/media/whisper.py src/reup/stages/asr.py tests/test_stage_asr.py
git commit -m "feat: bọc mlx-whisper và stage asr"
```

---

## Task 12: Adapter TTS và StubTTS

**Files:**
- Create: `src/reup/adapters/tts.py`
- Create: `src/reup/adapters/stub_tts.py`
- Test: `tests/test_stub_tts.py`

**Interfaces:**
- Consumes: `count_syllables` (Task 2), `silence` (Task 8)
- Produces:
  - `Voice(id: str, name: str, lang: str)`
  - `TTSResult(path: Path, actual_ms: int)`
  - `TTSAdapter` Protocol: `voices(lang: str) -> list[Voice]`, `synthesize(text: str, lang: str, voice: str, out: Path) -> TTSResult`
  - `StubTTS(ms_per_syllable: int = 220)` — implement `TTSAdapter`

`TTSResult.actual_ms` là bắt buộc: stage `fit` cần con số này. Đây chính là chỗ source CapCut TTS sẽ cắm vào ở phase 2 — chỉ cần một class khác implement đúng `TTSAdapter`.

- [x] **Step 1: Viết test thất bại**

```python
# tests/test_stub_tts.py
from pathlib import Path
import pytest
from reup.adapters.stub_tts import StubTTS
from reup.media.audio import duration_ms


def test_lists_at_least_one_vietnamese_voice():
    voices = StubTTS().voices("vi")
    assert len(voices) >= 1
    assert all(v.lang == "vi" for v in voices)


def test_duration_scales_with_syllable_count(tmp_path: Path):
    tts = StubTTS(ms_per_syllable=200)
    result = tts.synthesize("một hai ba bốn năm", "vi", "stub-vi-1", tmp_path / "a.wav")
    assert result.actual_ms == 1000
    assert abs(duration_ms(result.path) - 1000) <= 100


def test_chinese_text_counts_han_characters(tmp_path: Path):
    tts = StubTTS(ms_per_syllable=100)
    result = tts.synthesize("今天教大家", "zh", "stub-zh-1", tmp_path / "a.wav")
    assert result.actual_ms == 500


def test_empty_text_still_produces_a_short_file(tmp_path: Path):
    result = StubTTS().synthesize("", "vi", "stub-vi-1", tmp_path / "a.wav")
    assert result.actual_ms > 0
    assert result.path.exists()


def test_returns_the_path_it_was_given(tmp_path: Path):
    out = tmp_path / "nested" / "seg_0001.wav"
    result = StubTTS().synthesize("xin chào", "vi", "stub-vi-1", out)
    assert result.path == out
    assert out.exists()


def test_rejects_unknown_voice(tmp_path: Path):
    with pytest.raises(ValueError, match="khong-co"):
        StubTTS().synthesize("xin chào", "vi", "khong-co", tmp_path / "a.wav")
```

- [x] **Step 2: Chạy test, xác nhận thất bại**

Run: `uv run pytest tests/test_stub_tts.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'reup.adapters.stub_tts'`

- [x] **Step 3: Viết `src/reup/adapters/tts.py`**

```python
"""Biên giới ra dịch vụ tổng hợp giọng nói.

Source CapCut TTS cắm vào đây ở phase 2: viết một class implement
TTSAdapter, không sửa gì trong pipeline.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class Voice:
    id: str
    name: str
    lang: str


@dataclass(frozen=True)
class TTSResult:
    path: Path
    actual_ms: int


class TTSAdapter(Protocol):
    def voices(self, lang: str) -> list[Voice]: ...

    def synthesize(
        self, text: str, lang: str, voice: str, out: Path
    ) -> TTSResult: ...
```

- [x] **Step 4: Viết `src/reup/adapters/stub_tts.py`**

```python
"""TTS giả: sinh wav im lặng dài theo số âm tiết.

Nhờ nó mà cả pipeline và toàn bộ test chạy được trước khi nối
source CapCut thật. Nếu chỗ nối TTS có vấn đề, mọi phần khác đã xong.
"""
from __future__ import annotations

from pathlib import Path

from reup.adapters.tts import TTSResult, Voice
from reup.media.audio import silence
from reup.text import count_syllables

_VOICES = [
    Voice(id="stub-vi-1", name="Giọng giả nữ", lang="vi"),
    Voice(id="stub-vi-2", name="Giọng giả nam", lang="vi"),
    Voice(id="stub-zh-1", name="Giọng giả tiếng Trung", lang="zh"),
    Voice(id="stub-en-1", name="Giọng giả tiếng Anh", lang="en"),
]

MIN_MS = 200


class StubTTS:
    def __init__(self, ms_per_syllable: int = 220) -> None:
        self.ms_per_syllable = ms_per_syllable

    def voices(self, lang: str) -> list[Voice]:
        return [v for v in _VOICES if v.lang == lang]

    def synthesize(self, text: str, lang: str, voice: str, out: Path) -> TTSResult:
        if voice not in {v.id for v in _VOICES}:
            raise ValueError(
                f"giọng {voice!r} không có trong StubTTS. "
                f"Có sẵn: {sorted(v.id for v in _VOICES)}"
            )
        ms = max(MIN_MS, count_syllables(text, lang) * self.ms_per_syllable)
        silence(out, ms)
        return TTSResult(path=Path(out), actual_ms=ms)
```

- [x] **Step 5: Chạy test, xác nhận pass**

Run: `uv run pytest tests/test_stub_tts.py -v`
Expected: PASS — 6 test

- [x] **Step 6: Commit**

```bash
git add src/reup/adapters/tts.py src/reup/adapters/stub_tts.py tests/test_stub_tts.py
git commit -m "feat: interface TTSAdapter và StubTTS"
```

---

## Task 13: Stage `tts`

**Files:**
- Create: `src/reup/stages/tts.py`
- Test: `tests/test_stage_tts.py`

**Interfaces:**
- Consumes: `StubTTS` (Task 12), `Transcript` (Task 3), `Job` (Task 4)
- Produces: `DEFAULT_VOICES: dict[str, str]`, `voice_for(lang: str) -> str`, `run(job: Job, cfg: Config) -> None`, `SPEC: StageSpec` với `produces=("tts/manifest.json",)`

`tts/manifest.json` liệt kê từng đoạn: id, đường dẫn wav, `actual_ms`. Runner cần **một** artifact cố định để biết stage đã xong — số file wav thì thay đổi theo video nên không dùng làm dấu hiệu được.

Phase 1 chưa có `translation.json`, nên stage đọc `transcript.json` và đọc luôn văn bản gốc. Phase 2 đổi nguồn sang `translation.json`.

- [x] **Step 1: Viết test thất bại**

```python
# tests/test_stage_tts.py
import json
from pathlib import Path
import pytest
from reup.core.job import create_job
from reup.models import Segment, Transcript
from reup.stages import tts as tts_stage


def _seed(job, texts: list[str], lang: str = "zh") -> None:
    Transcript(
        source_lang=lang,
        segments=[
            Segment(id=i, start_ms=(i - 1) * 3000, end_ms=i * 3000, text=t)
            for i, t in enumerate(texts, start=1)
        ],
    ).save(job.transcript_json)


def test_writes_one_wav_per_segment(tmp_path: Path, cfg_fixture):
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    _seed(job, ["今天教大家", "先把肉切块"])

    tts_stage.run(job, cfg_fixture)

    assert job.tts_segment(1).exists()
    assert job.tts_segment(2).exists()


def test_manifest_records_actual_durations(tmp_path: Path, cfg_fixture):
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    _seed(job, ["今天教大家"])

    tts_stage.run(job, cfg_fixture)

    manifest = json.loads((job.tts_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["voice"] == "stub-zh-1"
    assert manifest["lang"] == "zh"
    assert len(manifest["segments"]) == 1
    entry = manifest["segments"][0]
    assert entry["id"] == 1
    assert entry["actual_ms"] > 0
    assert entry["path"] == "seg_0001.wav"


def test_rerun_regenerates_all_segments(tmp_path: Path, cfg_fixture):
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    _seed(job, ["今天教大家"])
    tts_stage.run(job, cfg_fixture)
    first = job.tts_segment(1).stat().st_size

    _seed(job, ["今天教大家做红烧肉先把肉切块"])
    tts_stage.run(job, cfg_fixture)

    assert job.tts_segment(1).stat().st_size > first


def test_fails_on_empty_transcript(tmp_path: Path, cfg_fixture):
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    Transcript(source_lang="zh", segments=[]).save(job.transcript_json)

    with pytest.raises(ValueError, match="không có câu nào"):
        tts_stage.run(job, cfg_fixture)


def test_spec_declares_its_artifacts():
    assert tts_stage.SPEC.name == "tts"
    assert set(tts_stage.SPEC.produces) == {"tts/manifest.json"}
```

- [x] **Step 2: Chạy test, xác nhận thất bại**

Run: `uv run pytest tests/test_stage_tts.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'reup.stages.tts'`

- [x] **Step 3: Viết `src/reup/stages/tts.py`**

```python
"""Stage 10 — sinh giọng đọc cho từng câu."""
from __future__ import annotations

import json

from reup.adapters.stub_tts import StubTTS
from reup.config import Config
from reup.core.job import Job
from reup.core.runner import atomic_write
from reup.core.stage import StageSpec
from reup.models import Transcript

# Phase 2 thay bằng adapter CapCut thật và cho chọn giọng ở chốt A.
DEFAULT_VOICES = {"vi": "stub-vi-1", "zh": "stub-zh-1", "en": "stub-en-1"}


def voice_for(lang: str) -> str:
    return DEFAULT_VOICES.get(lang, "stub-vi-1")


def run(job: Job, cfg: Config) -> None:
    # Phase 1 đọc transcript gốc vì chưa có stage translate.
    # Phase 2 đổi sang job.translation_json.
    transcript = Transcript.load(job.transcript_json)
    if not transcript.segments:
        raise ValueError(f"{job.transcript_json} không có câu nào để đọc")

    adapter = StubTTS()
    # Phase 1 đọc chính văn bản gốc, nên ngôn ngữ đếm âm tiết là ngôn ngữ nguồn.
    # Phase 2 đọc translation.json và chuyển sang "vi".
    lang = transcript.source_lang
    voice = voice_for(lang)
    entries = []
    for seg in transcript.segments:
        out = job.tts_segment(seg.id)
        result = adapter.synthesize(seg.text, lang, voice, out)
        entries.append(
            {"id": seg.id, "path": out.name, "actual_ms": result.actual_ms}
        )

    atomic_write(
        job.tts_dir / "manifest.json",
        json.dumps(
            {"voice": voice, "lang": lang, "segments": entries},
            ensure_ascii=False,
            indent=2,
        ),
    )


SPEC = StageSpec(name="tts", produces=("tts/manifest.json",), run=run)
```

- [x] **Step 4: Chạy test, xác nhận pass**

Run: `uv run pytest tests/test_stage_tts.py -v`
Expected: PASS — 5 test

- [x] **Step 5: Commit**

```bash
git add src/reup/stages/tts.py tests/test_stage_tts.py
git commit -m "feat: stage tts sinh giọng đọc từng câu kèm manifest"
```

---

## Task 14: Luật khớp thời lượng

**Files:**
- Create: `src/reup/fit.py`
- Test: `tests/test_fit_decision.py`

**Interfaces:**
- Consumes: —
- Produces: `FitDecision(action: str, ratio: float, tempo: float)`; `decide_fit(actual_ms: int, slot_ms: int, revision: int, max_revisions: int = 2) -> FitDecision`

`action` là một trong `"pad"`, `"tempo"`, `"rewrite"`, `"tempo_capped"`, `"overflow"`. Đây là hàm thuần, tách khỏi stage, vì nó là chỗ dễ sai nhất và cần test dày.

Luật lấy nguyên từ spec §7.7:

| Tỉ lệ | revision < max | revision >= max |
|---|---|---|
| < 0.85 | `pad` | `pad` |
| 0.85 – 1.15 | `tempo` | `tempo` |
| 1.15 – 1.5 | `rewrite` | `tempo_capped` (tempo 1.25) |
| > 1.5 | `rewrite` | `overflow` |

- [x] **Step 1: Viết test thất bại**

```python
# tests/test_fit_decision.py
import pytest
from reup.fit import decide_fit


def test_much_shorter_than_slot_pads():
    d = decide_fit(actual_ms=1000, slot_ms=3000, revision=0)
    assert d.action == "pad"
    assert d.tempo == 1.0


def test_slightly_long_compresses_with_tempo():
    d = decide_fit(actual_ms=3300, slot_ms=3000, revision=0)
    assert d.action == "tempo"
    assert d.tempo == pytest.approx(1.1)


def test_exact_fit_uses_tempo_one():
    d = decide_fit(actual_ms=3000, slot_ms=3000, revision=0)
    assert d.action == "tempo"
    assert d.tempo == 1.0


def test_slightly_short_but_within_band_does_not_slow_down():
    d = decide_fit(actual_ms=2700, slot_ms=3000, revision=0)
    assert d.action == "tempo"
    assert d.tempo == 1.0  # không kéo chậm giọng — nghe lè nhè


def test_too_long_asks_for_rewrite_while_budget_remains():
    d = decide_fit(actual_ms=4200, slot_ms=3000, revision=0)
    assert d.action == "rewrite"


def test_rewrite_budget_is_exhausted_at_max():
    d = decide_fit(actual_ms=4200, slot_ms=3000, revision=2)
    assert d.action == "tempo_capped"
    assert d.tempo == 1.25


def test_way_too_long_after_budget_is_overflow():
    d = decide_fit(actual_ms=9000, slot_ms=3000, revision=2)
    assert d.action == "overflow"
    assert d.tempo == 1.25


def test_way_too_long_still_tries_rewrite_first():
    assert decide_fit(actual_ms=9000, slot_ms=3000, revision=1).action == "rewrite"


@pytest.mark.parametrize("revision", [0, 1, 2, 5])
def test_ratio_is_always_reported(revision):
    d = decide_fit(actual_ms=6000, slot_ms=3000, revision=revision)
    assert d.ratio == pytest.approx(2.0)


def test_zero_slot_raises():
    with pytest.raises(ValueError, match="slot_ms"):
        decide_fit(actual_ms=1000, slot_ms=0, revision=0)


def test_boundary_115_is_tempo_not_rewrite():
    assert decide_fit(actual_ms=3450, slot_ms=3000, revision=0).action == "tempo"


def test_boundary_150_is_not_overflow():
    assert decide_fit(actual_ms=4500, slot_ms=3000, revision=2).action == "tempo_capped"
```

- [x] **Step 2: Chạy test, xác nhận thất bại**

Run: `uv run pytest tests/test_fit_decision.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'reup.fit'`

- [x] **Step 3: Viết `src/reup/fit.py`**

```python
"""Luật khớp giọng đọc vào khe thời gian — spec §7.7.

Hàm thuần, tách khỏi stage vì đây là chỗ dễ sai nhất trong pipeline.
"""
from __future__ import annotations

from dataclasses import dataclass

PAD_BELOW = 0.85
TEMPO_CEILING = 1.15
REWRITE_CEILING = 1.5
CAPPED_TEMPO = 1.25


@dataclass(frozen=True)
class FitDecision:
    action: str
    ratio: float
    tempo: float


def decide_fit(
    actual_ms: int, slot_ms: int, revision: int, max_revisions: int = 2
) -> FitDecision:
    if slot_ms <= 0:
        raise ValueError(f"slot_ms phải dương, nhận {slot_ms}")

    ratio = actual_ms / slot_ms

    if ratio < PAD_BELOW:
        return FitDecision("pad", ratio, 1.0)
    if ratio <= TEMPO_CEILING:
        # Chỉ nén, không bao giờ kéo chậm: giọng chậm nghe lè nhè.
        return FitDecision("tempo", ratio, max(ratio, 1.0))
    if revision < max_revisions:
        return FitDecision("rewrite", ratio, 1.0)
    if ratio <= REWRITE_CEILING:
        return FitDecision("tempo_capped", ratio, CAPPED_TEMPO)
    return FitDecision("overflow", ratio, CAPPED_TEMPO)
```

- [x] **Step 4: Chạy test, xác nhận pass**

Run: `uv run pytest tests/test_fit_decision.py -v`
Expected: PASS — 15 test

- [x] **Step 5: Commit**

```bash
git add src/reup/fit.py tests/test_fit_decision.py
git commit -m "feat: luật khớp thời lượng giọng đọc vào khe"
```

---

## Task 15: Stage `fit`

**Files:**
- Create: `src/reup/stages/fit.py`
- Test: `tests/test_stage_fit.py`

**Interfaces:**
- Consumes: `decide_fit` (Task 14), `apply_tempo` / `build_timeline` / `duration_ms` (Task 8), `Transcript` (Task 3)
- Produces: `run(job: Job, cfg: Config) -> None`, `SPEC: StageSpec` với `produces=("dub.wav",)`

Phase 1 gọi `decide_fit` với `revision=max_revisions`, nên nhánh `rewrite` không bao giờ chạy — chưa có stage translate để viết lại. Phase 2 nối vòng lặp thật. Đoạn nào rơi vào `overflow` thì gắn cờ vào `transcript.json` và vẫn chạy tiếp với nén 1.25.

- [x] **Step 1: Viết test thất bại**

```python
# tests/test_stage_fit.py
import json
from pathlib import Path
from reup.core.job import create_job
from reup.media.audio import duration_ms
from reup.models import Segment, Transcript
from reup.stages import fit as fit_stage
from reup.stages import tts as tts_stage


def _seed(job, specs: list[tuple[int, int, str]]) -> None:
    """specs: danh sách (start_ms, end_ms, text)."""
    Transcript(
        source_lang="zh",
        segments=[
            Segment(id=i, start_ms=s, end_ms=e, text=t)
            for i, (s, e, t) in enumerate(specs, start=1)
        ],
    ).save(job.transcript_json)


def test_builds_dub_covering_whole_video(tmp_path: Path, cfg_fixture):
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    _seed(job, [(0, 3000, "今天教大家"), (3000, 6000, "先把肉切块")])
    tts_stage.run(job, cfg_fixture)

    fit_stage.run(job, cfg_fixture)

    assert job.dub_wav.exists()
    assert abs(duration_ms(job.dub_wav) - 6000) <= 150


def test_overlong_segment_is_flagged(tmp_path: Path, cfg_fixture):
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    # 20 ký tự Hán x 220ms = 4400ms nhét vào khe 1000ms → ratio 4.4 → overflow
    _seed(job, [(0, 1000, "今天教大家做红烧肉先把肉切块再下锅炒香")])
    tts_stage.run(job, cfg_fixture)

    fit_stage.run(job, cfg_fixture)

    t = Transcript.load(job.transcript_json)
    assert "overflow" in t.segments[0].flags


def test_well_fitting_segment_is_not_flagged(tmp_path: Path, cfg_fixture):
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    # 5 ký tự x 220ms = 1100ms trong khe 3000ms → pad, không cờ
    _seed(job, [(0, 3000, "今天教大家")])
    tts_stage.run(job, cfg_fixture)

    fit_stage.run(job, cfg_fixture)

    assert Transcript.load(job.transcript_json).segments[0].flags == []


def test_writes_fit_report(tmp_path: Path, cfg_fixture):
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    _seed(job, [(0, 3000, "今天教大家")])
    tts_stage.run(job, cfg_fixture)

    fit_stage.run(job, cfg_fixture)

    report = json.loads((job.tts_dir / "fit.json").read_text(encoding="utf-8"))
    assert report["segments"][0]["action"] in {"pad", "tempo", "tempo_capped", "overflow"}
    assert report["segments"][0]["id"] == 1


def test_spec_declares_its_artifacts():
    assert fit_stage.SPEC.name == "fit"
    assert set(fit_stage.SPEC.produces) == {"dub.wav"}
```

- [x] **Step 2: Chạy test, xác nhận thất bại**

Run: `uv run pytest tests/test_stage_fit.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'reup.stages.fit'`

- [x] **Step 3: Viết `src/reup/stages/fit.py`**

```python
"""Stage 11 — nén hoặc đệm từng câu cho khớp khe, rồi dựng thành một track lồng tiếng."""
from __future__ import annotations

import json

from pathlib import Path

from reup.config import Config
from reup.core.job import Job
from reup.core.runner import atomic_write
from reup.core.stage import StageSpec
from reup.fit import decide_fit
from reup.media.audio import apply_tempo, build_timeline, duration_ms
from reup.models import Transcript

MAX_REVISIONS = 2


def run(job: Job, cfg: Config) -> None:
    transcript = Transcript.load(job.transcript_json)
    placements: list[tuple[int, Path]] = []
    report: list[dict] = []

    for seg in transcript.segments:
        src = job.tts_segment(seg.id)
        actual = duration_ms(src)

        # Phase 1 không có stage translate nên ngân sách viết lại đã cạn sẵn:
        # nhánh "rewrite" không bao giờ chạy. Phase 2 nối vòng lặp thật.
        decision = decide_fit(actual, seg.slot_ms, MAX_REVISIONS, MAX_REVISIONS)

        if decision.tempo > 1.0:
            fitted = src.with_name(f"{src.stem}_fitted.wav")
            apply_tempo(src, fitted, decision.tempo)
        else:
            fitted = src

        if decision.action == "overflow" and "overflow" not in seg.flags:
            seg.flags.append("overflow")

        placements.append((seg.start_ms, fitted))
        report.append({
            "id": seg.id,
            "action": decision.action,
            "ratio": round(decision.ratio, 4),
            "tempo": round(decision.tempo, 4),
            "actual_ms": actual,
            "slot_ms": seg.slot_ms,
        })

    transcript.save(job.transcript_json)
    atomic_write(
        job.tts_dir / "fit.json",
        json.dumps({"segments": report}, ensure_ascii=False, indent=2),
    )
    build_timeline(placements, total_ms=transcript.total_ms, out=job.dub_wav)


SPEC = StageSpec(name="fit", produces=("dub.wav",), run=run)
```

- [x] **Step 4: Chạy test, xác nhận pass**

Run: `uv run pytest tests/test_stage_fit.py -v`
Expected: PASS — 5 test

- [x] **Step 5: Commit**

```bash
git add src/reup/stages/fit.py tests/test_stage_fit.py
git commit -m "feat: stage fit khớp giọng vào khe và dựng track lồng tiếng"
```

---

## Task 16: Stage `compose`

**Files:**
- Create: `src/reup/stages/compose.py`
- Test: `tests/test_stage_compose.py`

**Interfaces:**
- Consumes: `run_ffmpeg` / `probe` (Task 7), `TransformConfig` / `AudioConfig` (Task 1)
- Produces: `build_transform(transform: TransformConfig) -> str`; `build_filter_complex(cfg: Config) -> str`; `run(job: Job, cfg: Config) -> None`; `SPEC: StageSpec` với `produces=("render/final.mp4",)`

Phase 1 chưa có blur và chưa có phụ đề, nên chuỗi filter chỉ gồm `TRANSFORM`. Thứ tự trong hàm `build_filter_complex` đã viết sẵn đúng theo spec §7.3 — phase 3 chèn blur vào **trước** transform và `ass` vào **sau**, không phải sắp xếp lại.

- [x] **Step 1: Viết test thất bại**

```python
# tests/test_stage_compose.py
from dataclasses import replace
from pathlib import Path
from reup.core.job import create_job
from reup.media.ffmpeg import probe
from reup.stages import compose as compose_stage


def test_transform_is_null_when_everything_off(cfg_fixture):
    assert compose_stage.build_transform(cfg_fixture.transform) == "null"


def test_transform_includes_hflip(cfg_fixture):
    t = replace(cfg_fixture.transform, hflip=True)
    assert compose_stage.build_transform(t) == "hflip"


def test_transform_zoom_scales_then_crops(cfg_fixture):
    t = replace(cfg_fixture.transform, zoom=1.05)
    result = compose_stage.build_transform(t)
    assert "scale=" in result
    assert "crop=" in result


def test_transform_speed_uses_setpts(cfg_fixture):
    t = replace(cfg_fixture.transform, speed=1.02)
    assert "setpts=" in compose_stage.build_transform(t)


def test_transform_combines_in_fixed_order(cfg_fixture):
    t = replace(cfg_fixture.transform, hflip=True, zoom=1.05, speed=1.02)
    result = compose_stage.build_transform(t)
    assert result.index("scale=") < result.index("hflip") < result.index("setpts=")


def test_filter_complex_mixes_background_in_separate_mode(cfg_fixture):
    chain = compose_stage.build_filter_complex(cfg_fixture)
    assert "volume=0.35" in chain
    assert "amix=inputs=2" in chain


def test_filter_complex_drops_original_audio_in_drop_mode(cfg_fixture):
    cfg = replace(cfg_fixture, audio=replace(cfg_fixture.audio, mode="drop_original"))
    chain = compose_stage.build_filter_complex(cfg)
    assert "amix" not in chain
    assert "volume=" not in chain


def test_renders_playable_video(tmp_path: Path, sample_video: Path, sample_wav: Path, cfg_fixture):
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    job.source_video.write_bytes(sample_video.read_bytes())
    job.dub_wav.write_bytes(sample_wav.read_bytes())

    compose_stage.run(job, cfg_fixture)

    info = probe(job.final_mp4)
    assert info.has_video is True
    assert info.has_audio is True
    assert info.width == 540
    assert info.height == 960
    assert abs(info.duration_ms - 6000) <= 200


def test_renders_with_transforms_on(tmp_path: Path, sample_video: Path, sample_wav: Path, cfg_fixture):
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    job.source_video.write_bytes(sample_video.read_bytes())
    job.dub_wav.write_bytes(sample_wav.read_bytes())
    cfg = replace(cfg_fixture, transform=replace(cfg_fixture.transform, hflip=True, zoom=1.05))

    compose_stage.run(job, cfg)

    info = probe(job.final_mp4)
    assert info.width == 540
    assert info.height == 960


def test_spec_declares_its_artifacts():
    assert compose_stage.SPEC.name == "compose"
    assert set(compose_stage.SPEC.produces) == {"render/final.mp4"}
```

- [x] **Step 2: Chạy test, xác nhận thất bại**

Run: `uv run pytest tests/test_stage_compose.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'reup.stages.compose'`

- [x] **Step 3: Viết `src/reup/stages/compose.py`**

```python
"""Stage 12 — dựng video cuối bằng một lượt ffmpeg duy nhất.

Thứ tự filter theo spec §7.3 và là bắt buộc:
    blur vùng sub gốc → transform → dán sub Việt → encode
Phase 1 chưa có blur và chưa có sub, nên chỉ còn transform.
Phase 3 chèn hai khâu kia vào đúng chỗ đã chừa sẵn dưới đây.
"""
from __future__ import annotations

from reup.config import Config, TransformConfig
from reup.core.job import Job
from reup.core.stage import StageSpec
from reup.media.ffmpeg import run_ffmpeg


def build_transform(transform: TransformConfig) -> str:
    parts: list[str] = []
    if transform.zoom != 1.0:
        z = transform.zoom
        parts.append(f"scale=iw*{z:.4f}:ih*{z:.4f}")
        parts.append("crop=iw/%.4f:ih/%.4f" % (z, z))
    if transform.hflip:
        parts.append("hflip")
    if transform.speed != 1.0:
        parts.append(f"setpts={1 / transform.speed:.6f}*PTS")
    return ",".join(parts) if parts else "null"


def build_filter_complex(cfg: Config) -> str:
    # Input 0 = video nguồn, input 1 = track lồng tiếng (dub.wav)
    video = f"[0:v]{build_transform(cfg.transform)}[v]"
    # Phase 3: chèn crop+boxblur+overlay TRƯỚC build_transform,
    #          và ass=sub.ass SAU nó — nếu không chữ Việt sẽ bị hflip lật ngược.

    if cfg.audio.mode == "drop_original":
        audio = "[1:a]aresample=48000[a]"
    else:
        audio = (
            f"[0:a]volume={cfg.audio.bgm_gain}[bg];"
            "[bg][1:a]amix=inputs=2:duration=first:normalize=0[a]"
        )
    return f"{video};{audio}"


def run(job: Job, cfg: Config) -> None:
    job.final_mp4.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg([
        "-i", str(job.source_video),
        "-i", str(job.dub_wav),
        "-filter_complex", build_filter_complex(cfg),
        "-map", "[v]", "-map", "[a]",
        "-c:v", cfg.profile.encoder, "-b:v", "8M",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        str(job.final_mp4),
    ])


SPEC = StageSpec(name="compose", produces=("render/final.mp4",), run=run)
```

- [x] **Step 4: Chạy test, xác nhận pass**

Run: `uv run pytest tests/test_stage_compose.py -v`
Expected: PASS — 10 test

- [x] **Step 5: Commit**

```bash
git add src/reup/stages/compose.py tests/test_stage_compose.py
git commit -m "feat: stage compose dựng video một lượt ffmpeg"
```

---

## Task 17: Đăng ký thứ tự stage và CLI

**Files:**
- Create: `src/reup/stages/__init__.py` (ghi đè file rỗng đã tạo ở Task 1)
- Create: `src/reup/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: mọi `SPEC` từ Task 9–16, `run_job` (Task 6), `create_job` / `load_job` (Task 4), `Store` (Task 5), `load_config` (Task 1)
- Produces: `PHASE1_STAGES: list[StageSpec]` trong `stages/__init__.py`; `main(argv: list[str] | None = None) -> int` trong `cli.py`

Lệnh phase 1: `add`, `run`, `redo`, `status`. Lệnh `discover`, `approve`, `web`, `benchmark` thuộc các phase sau.

- [ ] **Step 1: Viết test thất bại**

```python
# tests/test_cli.py
from pathlib import Path
import pytest
from reup.cli import main
from reup.stages import PHASE1_STAGES


def test_phase1_stage_order():
    assert [s.name for s in PHASE1_STAGES] == [
        "fetch", "demux", "asr", "tts", "fit", "compose",
    ]


def test_stage_names_are_unique():
    names = [s.name for s in PHASE1_STAGES]
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
```

Thêm fixture `config_file` vào `tests/conftest.py`:

```python
# thêm vào cuối tests/conftest.py
@pytest.fixture
def config_file(tmp_path: Path) -> Path:
    """Bản sao config.toml của dự án, dùng cho test CLI."""
    src = Path(__file__).resolve().parents[1] / "config.toml"
    dst = tmp_path / "config.toml"
    dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    return dst
```

- [ ] **Step 2: Chạy test, xác nhận thất bại**

Run: `uv run pytest tests/test_cli.py -v`
Expected: FAIL — `ImportError: cannot import name 'PHASE1_STAGES'`

- [ ] **Step 3: Viết `src/reup/stages/__init__.py`**

```python
"""Thứ tự stage. Runner chạy theo đúng danh sách này."""
from __future__ import annotations

from reup.core.stage import StageSpec
from reup.stages import asr, compose, demux, fetch, fit, tts

# Phase 1: sáu trong mười ba stage của spec.
# Còn thiếu: discover(1), separate(4), subdetect(6), ocr(7),
#            reconcile(8), translate(9), export(13).
PHASE1_STAGES: list[StageSpec] = [
    fetch.SPEC,
    demux.SPEC,
    asr.SPEC,
    tts.SPEC,
    fit.SPEC,
    compose.SPEC,
]
```

- [ ] **Step 4: Viết `src/reup/cli.py`**

```python
"""Giao diện dòng lệnh. Phase 1: add, run, redo, status."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from reup.config import load_config
from reup.core.job import create_job, load_job
from reup.core.runner import run_job
from reup.core.store import Store
from reup.stages import PHASE1_STAGES


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="reup", description="Pipeline dịch và lồng tiếng video ngắn")
    p.add_argument("--config", type=Path, default=Path("config.toml"))
    p.add_argument("--jobs-dir", type=Path, default=Path("jobs"))
    p.add_argument("--db", type=Path, default=Path("reup.db"))
    sub = p.add_subparsers(dest="command", required=True)

    add = sub.add_parser("add", help="tạo job từ một link")
    add.add_argument("url")
    add.add_argument("--lang", default="auto", help="ngôn ngữ nguồn: zh, en, hoặc auto")

    run_cmd = sub.add_parser("run", help="chạy job tới stage cuối")
    run_cmd.add_argument("job_id")

    redo = sub.add_parser("redo", help="xóa artifact từ một stage trở đi để chạy lại")
    redo.add_argument("job_id")
    redo.add_argument("--from", dest="from_stage", required=True)

    sub.add_parser("status", help="liệt kê job")
    return p


def _cmd_add(args, store: Store) -> int:
    job = create_job(args.jobs_dir, args.url, args.lang)
    store.upsert_job(job.id, job.source_url, "pending")
    print(job.id)
    return 0


def _cmd_run(args, store: Store) -> int:
    try:
        job = load_job(args.jobs_dir, args.job_id)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    cfg = load_config(args.config)
    status = run_job(job, cfg, store, PHASE1_STAGES)
    if status == "failed":
        row = store.get_job(job.id)
        print(f"job {job.id} hỏng ở stage {row['stage']}: {row['error']}", file=sys.stderr)
        return 1
    print(job.final_mp4)
    return 0


def _cmd_redo(args, store: Store) -> int:
    names = [s.name for s in PHASE1_STAGES]
    if args.from_stage not in names:
        print(
            f"không có stage {args.from_stage!r}. Có: {', '.join(names)}",
            file=sys.stderr,
        )
        return 2
    try:
        job = load_job(args.jobs_dir, args.job_id)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    start = names.index(args.from_stage)
    for spec in PHASE1_STAGES[start:]:
        for rel in spec.produces:
            target = job.root / rel
            if target.exists():
                target.unlink()
    store.upsert_job(job.id, job.source_url, "pending", stage=None)
    print(f"đã xóa artifact từ stage {args.from_stage} trở đi")
    return 0


def _cmd_status(args, store: Store) -> int:
    rows = store.list_jobs()
    if not rows:
        print("chưa có job nào")
        return 0
    for row in rows:
        stage = row["stage"] or "-"
        print(f"{row['id']}  {row['status']:<13} {stage:<10} {row['url']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    store = Store(args.db)
    store.init_schema()
    try:
        return {
            "add": _cmd_add,
            "run": _cmd_run,
            "redo": _cmd_redo,
            "status": _cmd_status,
        }[args.command](args, store)
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: Chạy test, xác nhận pass**

Run: `uv run pytest tests/test_cli.py -v`
Expected: PASS — 7 test

- [ ] **Step 6: Commit**

```bash
git add src/reup/stages/__init__.py src/reup/cli.py tests/test_cli.py tests/conftest.py
git commit -m "feat: đăng ký thứ tự stage và CLI add/run/redo/status"
```

---

## Task 18: Test tích hợp toàn pipeline

**Files:**
- Create: `tests/test_pipeline_integration.py`
- Modify: `README.md` (tạo mới)

**Interfaces:**
- Consumes: mọi thứ từ Task 1–17
- Produces: — (task cuối, không có interface mới)

Chạy trọn pipeline trên fixture, thay `fetch` bằng bản giả copy file local. Không gọi mạng, không nạp model.

- [ ] **Step 1: Viết test tích hợp**

```python
# tests/test_pipeline_integration.py
from pathlib import Path
import pytest
from reup.core.job import create_job
from reup.core.runner import run_job
from reup.core.stage import StageSpec
from reup.core.store import Store
from reup.media.ffmpeg import probe
from reup.models import Segment
from reup.stages import PHASE1_STAGES
from reup.stages import asr as asr_stage


@pytest.fixture
def offline_stages(monkeypatch, sample_video: Path):
    """Thay fetch bằng copy file local, thay whisper bằng transcript cố định."""

    def fake_fetch_run(job, cfg):
        job.source_video.write_bytes(sample_video.read_bytes())
        job.source_info.write_text('{"title": "clip thử"}', encoding="utf-8")

    def fake_transcribe(audio, model, language):
        return "zh", [
            Segment(id=1, start_ms=0, end_ms=3000, text="今天教大家做红烧肉"),
            Segment(id=2, start_ms=3000, end_ms=6000, text="先把五花肉切成小块"),
        ]

    monkeypatch.setattr(asr_stage, "transcribe", fake_transcribe)
    stages = [
        StageSpec("fetch", ("source.mp4", "source.info.json"), fake_fetch_run)
        if s.name == "fetch" else s
        for s in PHASE1_STAGES
    ]
    return stages


def test_full_pipeline_produces_playable_video(
    tmp_path: Path, cfg_fixture, offline_stages
):
    store = Store(tmp_path / "reup.db")
    store.init_schema()
    job = create_job(tmp_path / "jobs", "https://douyin.com/v/1", "zh", job_id="j1")
    store.upsert_job(job.id, job.source_url, "pending")

    assert run_job(job, cfg_fixture, store, offline_stages) == "done"

    info = probe(job.final_mp4)
    assert info.has_video is True
    assert info.has_audio is True
    assert info.width == 540
    assert info.height == 960
    assert abs(info.duration_ms - 6000) <= 200
    store.close()


def test_every_intermediate_artifact_exists(tmp_path: Path, cfg_fixture, offline_stages):
    store = Store(tmp_path / "reup.db")
    store.init_schema()
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    store.upsert_job(job.id, job.source_url, "pending")

    run_job(job, cfg_fixture, store, offline_stages)

    for path in (
        job.source_video, job.source_info, job.full_16k, job.full_48k,
        job.asr_json, job.transcript_json, job.tts_dir / "manifest.json",
        job.tts_dir / "fit.json", job.dub_wav, job.final_mp4,
    ):
        assert path.exists(), f"thiếu {path}"
    store.close()


def test_resume_skips_completed_stages(tmp_path: Path, cfg_fixture, offline_stages):
    store = Store(tmp_path / "reup.db")
    store.init_schema()
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    store.upsert_job(job.id, job.source_url, "pending")
    run_job(job, cfg_fixture, store, offline_stages)
    before = len(store.stage_durations())

    run_job(job, cfg_fixture, store, offline_stages)

    assert len(store.stage_durations()) == before  # không stage nào chạy lại
    store.close()


def test_deleting_one_artifact_reruns_only_from_there(
    tmp_path: Path, cfg_fixture, offline_stages
):
    store = Store(tmp_path / "reup.db")
    store.init_schema()
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    store.upsert_job(job.id, job.source_url, "pending")
    run_job(job, cfg_fixture, store, offline_stages)

    job.final_mp4.unlink()
    run_job(job, cfg_fixture, store, offline_stages)

    ran = [r["stage"] for r in store.stage_durations()]
    assert ran[-1] == "compose"
    assert ran.count("asr") == 1  # asr không chạy lại
    store.close()


def test_log_jsonl_has_one_line_per_stage(tmp_path: Path, cfg_fixture, offline_stages):
    store = Store(tmp_path / "reup.db")
    store.init_schema()
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    store.upsert_job(job.id, job.source_url, "pending")

    run_job(job, cfg_fixture, store, offline_stages)

    lines = job.log_jsonl.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == len(offline_stages)
    store.close()
```

- [ ] **Step 2: Chạy toàn bộ test**

Run: `uv run pytest -v`
Expected: PASS — toàn bộ, khoảng 110 test

- [ ] **Step 3: Chạy thử bằng tay trên một video thật**

```bash
uv run reup add "https://www.youtube.com/shorts/<id-nao-do>" --lang en
uv run reup status
uv run reup run <job_id>
```

Xác nhận: lệnh in ra đường dẫn `jobs/<job_id>/render/final.mp4`, mở file lên thấy hình gốc và nghe được nhạc nền ở mức 35%. Giọng lồng tiếng là im lặng — đúng như thiết kế, vì StubTTS chưa phải TTS thật.

- [ ] **Step 4: Viết `README.md`**

````markdown
# reup-video

Pipeline dịch và lồng tiếng video ngắn, chạy local trên macOS.

Thiết kế: [`docs/superpowers/specs/2026-09-11-reup-video-design.md`](docs/superpowers/specs/2026-09-11-reup-video-design.md)

## Trạng thái

**Phase 1** — xương sống pipeline. 6 trong 13 stage đã chạy: `fetch`, `demux`,
`asr`, `tts`, `fit`, `compose`. Giọng đọc còn là `StubTTS` (wav im lặng), chưa
có dịch, chưa có blur phụ đề. Xem
[`docs/superpowers/plans/`](docs/superpowers/plans/) cho các phase sau.

## Cài đặt

Cần `ffmpeg` 8.x và `uv` trong PATH.

```bash
uv venv --python 3.12
uv pip install -e .
uv pip install pytest
```

## Dùng

```bash
uv run reup add "https://www.douyin.com/video/..." --lang zh
uv run reup status
uv run reup run <job_id>
uv run reup redo <job_id> --from asr
```

File thành phẩm nằm ở `jobs/<job_id>/render/final.mp4`.

## Cấu hình

Sửa `config.toml`. Đổi `profile.active` sang `studio-24` khi chạy trên máy
24GB. Đặt `audio.mode = "drop_original"` nếu máy quá chậm — bỏ nhạc nền nhưng
nhanh hơn nhiều.

## Test

```bash
uv run pytest
```

Test không gọi mạng và không nạp model: fixture video sinh bằng ffmpeg lúc
chạy, Whisper và yt-dlp được thay bằng hàm giả.
````

- [ ] **Step 5: Commit**

```bash
git add tests/test_pipeline_integration.py README.md
git commit -m "test: pipeline chạy thông đầu cuối, kèm README"
```

---

## Hoàn thành phase 1

Sau task 18, `reup add <url>` rồi `reup run <job_id>` cho ra một file mp4 đã
thay audio, đi qua đúng kiến trúc stage rời nhau, có resume và có nhật ký.

**Phase 2 bắt đầu từ đây** — năm thay đổi, tất cả vào chỗ đã chừa sẵn:

1. Viết `adapters/capcut_tts.py` implement `TTSAdapter`, bọc
   `capcut-tts-api/capcut_common_task_client.py` (`tts-new` → poll `tts-query` →
   tải mp3 về). Giọng đọc từ `capcut-tts-api/Voice.json`: mỗi mục là cặp
   `voice_type` + `resource_id`, map thẳng sang `Voice`. Đổi một dòng trong
   `stages/tts.py`.
2. Thêm `stages/translate.py`, đổi `stages/tts.py` đọc `translation.json` thay
   vì `transcript.json` — và bỏ `voice_for(source_lang)`, chuyển sang giọng `vi`.
3. Nối vòng viết lại trong `stages/fit.py`: thay `MAX_REVISIONS` cứng bằng biến
   đếm thật, gọi lại translate cho đoạn nào `decide_fit` trả `"rewrite"`.
4. Dùng `--rate` của CapCut trước khi dùng `atempo`. Thứ tự ưu tiên khi câu dài
   quá khe: `rate` lúc tổng hợp → `atempo` sau tổng hợp → Gemini viết lại.
   `rate` đổi nhịp đọc chứ không nén dạng sóng nên không có tiếng méo tua nhanh.
   `decide_fit` giữ nguyên; chỉ `stages/fit.py` đổi cách thi hành quyết định.
5. Dựng `adapters/asr.py` (Protocol `ASRAdapter`), chuyển `media/whisper.py`
   thành `adapters/whisper_asr.py`, thêm `adapters/capcut_stt.py` gọi `stt-file`
   rồi đọc `payload.utterances[]`. Chọn bằng `asr.engine` trong config.
   Đây là lúc interface có ý nghĩa — trước đó chỉ có một implement.
