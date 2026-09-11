import json
from pathlib import Path

import pytest

from reup.core.job import create_job
from reup.core.store import Store
from reup.models import Segment, Transcript
from reup.web.service import list_jobs, review_rows, save_edits


@pytest.fixture
def store(tmp_path: Path):
    s = Store(tmp_path / "reup.db")
    s.init_schema()
    yield s
    s.close()


def seed(job, specs):
    """specs: (id, start, end, text, flags)"""
    segments = []
    for i, s, e, text, flags in specs:
        segments.append(
            {
                "id": i, "start_ms": s, "end_ms": e, "slot_ms": e - s,
                "syllable_budget": max(1, int((e - s) / 1000 * 4.5)),
                "text": text, "syllables": len(text.split()),
                "text_source": "llm", "confidence": 1.0, "revision": 0,
                "flags": list(flags),
            }
        )
    job.translation_json.write_text(
        json.dumps({"source_lang": "vi", "target_lang": "vi", "segments": segments},
                   ensure_ascii=False),
        encoding="utf-8",
    )


def test_list_jobs_maps_rows(store):
    store.upsert_job("j1", "https://a/1", "needs_review", stage="gate_a")
    rows = list_jobs(store)
    assert rows[0].id == "j1"
    assert rows[0].status == "needs_review"
    assert rows[0].stage == "gate_a"


def test_list_jobs_filters(store):
    store.upsert_job("j1", "https://a/1", "done")
    store.upsert_job("j2", "https://a/2", "failed")
    assert [r.id for r in list_jobs(store, "failed")] == ["j2"]


def test_review_rows_pair_source_with_translation(tmp_path: Path):
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    Transcript(
        source_lang="zh",
        segments=[Segment(id=1, start_ms=0, end_ms=2000, text="今天教大家")],
    ).save(job.transcript_json)
    seed(job, [(1, 0, 2000, "Hôm nay dạy mọi người", [])])

    row = review_rows(job)[0]
    assert row["source_text"] == "今天教大家"
    assert row["text"] == "Hôm nay dạy mọi người"


def test_review_rows_flag_over_budget(tmp_path: Path):
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    # khe 1s -> trần 4 âm tiết, câu 8 âm tiết
    seed(job, [(1, 0, 1000, "một hai ba bốn năm sáu bảy tám", [])])
    assert review_rows(job)[0]["over"] is True


def test_review_rows_carry_mismatch_flags(tmp_path: Path):
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    seed(job, [(1, 0, 3000, "Hôm nay", ["asr_ocr_mismatch"])])
    assert "asr_ocr_mismatch" in review_rows(job)[0]["flags"]


def test_review_rows_without_translation_is_empty(tmp_path: Path):
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    assert review_rows(job) == []


def test_save_edits_writes_new_text(tmp_path: Path):
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    seed(job, [(1, 0, 3000, "Câu cũ", [])])

    assert save_edits(job, {1: "Câu mới hoàn toàn"}) == 1

    raw = json.loads(job.translation_json.read_text(encoding="utf-8"))
    assert raw["segments"][0]["text"] == "Câu mới hoàn toàn"
    assert raw["segments"][0]["text_source"] == "human"


def test_save_edits_ignores_unchanged_text(tmp_path: Path):
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    seed(job, [(1, 0, 3000, "Giữ nguyên", [])])
    assert save_edits(job, {1: "Giữ nguyên"}) == 0


def test_save_edits_ignores_empty_text(tmp_path: Path):
    """Xoá trắng một ô rồi lưu không được biến câu thành khoảng lặng."""
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    seed(job, [(1, 0, 3000, "Giữ nguyên", [])])
    assert save_edits(job, {1: "   "}) == 0
    raw = json.loads(job.translation_json.read_text(encoding="utf-8"))
    assert raw["segments"][0]["text"] == "Giữ nguyên"


def test_edited_segment_loses_its_old_audio(tmp_path: Path):
    """Không xoá thì giọng đọc là câu cũ còn phụ đề là câu mới."""
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    seed(job, [(1, 0, 3000, "Câu cũ", []), (2, 3000, 6000, "Câu hai", [])])
    job.tts_segment(1).write_bytes(b"cu")
    job.tts_segment(2).write_bytes(b"hai")

    save_edits(job, {1: "Câu mới"})

    assert not job.tts_segment(1).exists()
    assert job.tts_segment(2).exists()  # câu không sửa thì giữ nguyên


def test_editing_invalidates_dub_and_render(tmp_path: Path):
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    seed(job, [(1, 0, 3000, "Câu cũ", [])])
    job.dub_wav.write_bytes(b"x")
    job.final_mp4.write_bytes(b"x")

    save_edits(job, {1: "Câu mới"})

    assert not job.dub_wav.exists()
    assert not job.final_mp4.exists()


def test_no_edits_leaves_artifacts_alone(tmp_path: Path):
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    seed(job, [(1, 0, 3000, "Giữ nguyên", [])])
    job.dub_wav.write_bytes(b"x")

    save_edits(job, {1: "Giữ nguyên"})

    assert job.dub_wav.exists()


def test_edit_recomputes_the_budget_flag(tmp_path: Path):
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    seed(job, [(1, 0, 1000, "một hai ba bốn năm sáu bảy", ["over_budget"])])

    save_edits(job, {1: "ngắn"})

    raw = json.loads(job.translation_json.read_text(encoding="utf-8"))
    assert "over_budget" not in raw["segments"][0]["flags"]


def test_editing_invalidates_the_tts_manifest(tmp_path: Path):
    """Artifact khai báo của stage tts là manifest.json.

    Chỉ xoá file wav của câu đã sửa thì tts bị bỏ qua (manifest còn đó) và fit
    đi tìm một file không còn tồn tại — đúng lỗi gặp khi chạy thật.
    """
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    seed(job, [(1, 0, 3000, "Câu cũ", [])])
    manifest = job.tts_dir / "manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    job.tts_segment(1).write_bytes(b"x")

    save_edits(job, {1: "Câu mới"})

    assert not manifest.exists()
    assert not job.tts_segment(1).exists()


def test_editing_invalidates_the_subtitle_file(tmp_path: Path):
    """sub.ass mang chữ cũ; giữ lại là phụ đề lệch với giọng đọc."""
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    seed(job, [(1, 0, 3000, "Câu cũ", [])])
    job.sub_ass.write_text("cũ", encoding="utf-8")

    save_edits(job, {1: "Câu mới"})

    assert not job.sub_ass.exists()
