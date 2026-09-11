import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from reup.core.job import create_job, load_job
from reup.core.store import Store
from reup.models import Segment, Transcript
from reup.web.app import create_app


@pytest.fixture
def client(tmp_path: Path, config_file: Path):
    jobs = tmp_path / "jobs"
    jobs.mkdir()
    db = tmp_path / "reup.db"
    Store(db).init_schema()
    app = create_app(config_file, jobs, db)
    return TestClient(app), jobs, db


def seed_translation(job, specs):
    segments = [
        {
            "id": i, "start_ms": s, "end_ms": e, "slot_ms": e - s,
            "syllable_budget": max(1, int((e - s) / 1000 * 4.5)),
            "text": text, "syllables": len(text.split()),
            "text_source": "llm", "confidence": 1.0, "revision": 0, "flags": list(fl),
        }
        for i, s, e, text, fl in specs
    ]
    job.translation_json.write_text(
        json.dumps({"source_lang": "vi", "target_lang": "vi", "segments": segments},
                   ensure_ascii=False),
        encoding="utf-8",
    )


def test_queue_page_loads_when_empty(client):
    c, _, _ = client
    r = c.get("/")
    assert r.status_code == 200
    assert "Chưa có job nào" in r.text


def test_adding_a_job_creates_it_and_redirects(client):
    c, jobs, db = client
    r = c.post("/jobs", data={"url": "https://a/1", "lang": "zh"},
               follow_redirects=False)
    assert r.status_code == 303
    job_id = r.headers["location"].rsplit("/", 1)[-1]
    assert (jobs / job_id / "job.json").exists()

    s = Store(db)
    assert s.get_job(job_id)["status"] == "pending"
    s.close()


def test_queue_lists_the_job(client):
    c, _, _ = client
    c.post("/jobs", data={"url": "https://douyin.com/v/42", "lang": "zh"})
    body = c.get("/").text
    assert "douyin.com/v/42" in body
    assert "pending" in body


def test_queue_filters_by_status(client):
    c, _, db = client
    s = Store(db)
    s.upsert_job("j1", "https://a/1", "done")
    s.upsert_job("j2", "https://a/2", "failed")
    s.close()

    assert "j2" in c.get("/?status=failed").text
    assert "j1" not in c.get("/?status=failed").text


def test_review_page_shows_segments(client):
    c, jobs, db = client
    job = create_job(jobs, "https://a/1", "zh", job_id="j1")
    Transcript(
        source_lang="zh",
        segments=[Segment(id=1, start_ms=0, end_ms=3000, text="今天教大家")],
    ).save(job.transcript_json)
    seed_translation(job, [(1, 0, 3000, "Hôm nay dạy mọi người", [])])
    s = Store(db); s.upsert_job("j1", "https://a/1", "needs_review", stage="gate_a"); s.close()

    body = c.get("/jobs/j1").text
    assert "今天教大家" in body
    assert "Hôm nay dạy mọi người" in body


def test_review_page_marks_over_budget_rows(client):
    c, jobs, db = client
    job = create_job(jobs, "https://a/1", "zh", job_id="j1")
    seed_translation(job, [(1, 0, 1000, "một hai ba bốn năm sáu bảy tám", [])])
    s = Store(db); s.upsert_job("j1", "https://a/1", "needs_review"); s.close()

    body = c.get("/jobs/j1").text
    assert "warn" in body


def test_review_page_shows_mismatch_flags(client):
    c, jobs, db = client
    job = create_job(jobs, "https://a/1", "zh", job_id="j1")
    seed_translation(job, [(1, 0, 3000, "Hôm nay", ["asr_ocr_mismatch"])])
    s = Store(db); s.upsert_job("j1", "https://a/1", "needs_review"); s.close()

    assert "asr_ocr_mismatch" in c.get("/jobs/j1").text


def test_unknown_job_is_404(client):
    c, _, _ = client
    assert c.get("/jobs/khong-co").status_code == 404


def test_saving_edits_updates_the_file(client):
    c, jobs, db = client
    job = create_job(jobs, "https://a/1", "zh", job_id="j1")
    seed_translation(job, [(1, 0, 3000, "Câu cũ", [])])

    r = c.post("/jobs/j1/segments", json={"segments": {"1": "Câu mới"}})
    assert r.status_code == 200
    assert r.json()["changed"] == 1

    raw = json.loads(job.translation_json.read_text(encoding="utf-8"))
    assert raw["segments"][0]["text"] == "Câu mới"


def test_approve_writes_the_gate_marker(client):
    c, jobs, db = client
    create_job(jobs, "https://a/1", "zh", job_id="j1")
    s = Store(db); s.upsert_job("j1", "https://a/1", "needs_review", stage="gate_a"); s.close()

    r = c.post("/jobs/j1/approve", data={"gate": "a"}, follow_redirects=False)
    assert r.status_code == 303
    assert load_job(jobs, "j1").gate_approved("a")

    s = Store(db)
    assert s.get_job("j1")["status"] == "pending"
    s.close()


def test_approve_follows_the_blocking_gate(client):
    """Job chờ chốt B mà form gửi gate=a thì vẫn phải duyệt B."""
    c, jobs, db = client
    create_job(jobs, "https://a/1", "zh", job_id="j1")
    s = Store(db); s.upsert_job("j1", "https://a/1", "needs_review", stage="gate_b"); s.close()

    c.post("/jobs/j1/approve", data={"gate": "a"}, follow_redirects=False)

    job = load_job(jobs, "j1")
    assert job.gate_approved("b")
    assert not job.gate_approved("a")


def test_source_video_is_served(client, sample_video: Path):
    c, jobs, _ = client
    job = create_job(jobs, "https://a/1", "zh", job_id="j1")
    job.source_video.write_bytes(sample_video.read_bytes())

    r = c.get("/jobs/j1/source")
    assert r.status_code == 200
    assert r.headers["content-type"] == "video/mp4"


def test_missing_video_is_404(client):
    c, jobs, _ = client
    create_job(jobs, "https://a/1", "zh", job_id="j1")
    assert c.get("/jobs/j1/final").status_code == 404


def test_segment_audio_is_served(client):
    c, jobs, _ = client
    job = create_job(jobs, "https://a/1", "zh", job_id="j1")
    job.tts_segment(3).write_bytes(b"RIFFxxxx")

    r = c.get("/jobs/j1/audio/3")
    assert r.status_code == 200
    assert r.headers["content-type"] == "audio/wav"


def test_missing_segment_audio_is_404(client):
    c, jobs, _ = client
    create_job(jobs, "https://a/1", "zh", job_id="j1")
    assert c.get("/jobs/j1/audio/9").status_code == 404


def test_meta_endpoint_returns_json(client):
    c, jobs, _ = client
    job = create_job(jobs, "https://a/1", "zh", job_id="j1")
    job.meta_json.write_text(
        json.dumps({"title": "Tiêu đề", "hashtags": ["a"]}, ensure_ascii=False),
        encoding="utf-8",
    )
    assert c.get("/jobs/j1/meta").json()["title"] == "Tiêu đề"
