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
    def subrect_json(self) -> Path:
        return self.root / "subrect.json"

    @property
    def ocr_json(self) -> Path:
        return self.root / "ocr.json"

    @property
    def sub_ass(self) -> Path:
        return self.root / "sub.ass"

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
    def preview_dir(self) -> Path:
        return self.root / "preview"

    def preview_segment(self, seg_id: int) -> Path:
        return self.preview_dir / f"seg_{seg_id:04d}.wav"

    @property
    def overrides_json(self) -> Path:
        return self.root / "overrides.json"

    @property
    def overrides(self) -> dict:
        """Knob của config bị job này ghi đè (spec §9).

        Chỉ có `voice` dùng tới: giọng chọn ở chốt A phải sống qua lần chạy sau,
        mà config.toml là của cả máy — sửa nó thì mọi job khác đổi theo.
        """
        if not self.overrides_json.exists():
            return {}
        return json.loads(self.overrides_json.read_text(encoding="utf-8"))

    def set_override(self, key: str, value) -> None:
        from reup.core.runner import atomic_write

        data = self.overrides
        data[key] = value
        atomic_write(
            self.overrides_json, json.dumps(data, ensure_ascii=False, indent=2)
        )

    @property
    def log_jsonl(self) -> Path:
        return self.root / "log.jsonl"

    def gate_marker(self, gate: str) -> Path:
        return self.root / f"gate_{gate}.ok"

    def gate_approved(self, gate: str) -> bool:
        return self.gate_marker(gate).exists()

    def approve_gate(self, gate: str) -> None:
        """Ghi dấu duyệt vào thư mục job, không vào SQLite.

        Mất reup.db thì dựng lại được từ thư mục job; mất dấu duyệt thì người
        dùng phải ngồi duyệt lại từ đầu.
        """
        self.gate_marker(gate).write_text(
            datetime.now().isoformat(timespec="seconds"), encoding="utf-8"
        )


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
