import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from reup.core.job import create_job, load_job
from reup.core.store import Store
from reup.models import Segment, Transcript
from reup.web.app import create_app


class FakeRunner:
    """Ghi lại lệnh chạy thay vì chạy thật.

    Không có nó thì mỗi lần test tạo job là một luồng nền tải video thật về.
    """

    def __init__(self) -> None:
        self.started: list[str] = []

    def start(self, job_id: str) -> bool:
        self.started.append(job_id)
        return True

    def is_running(self, job_id: str) -> bool:
        return False

    def live(self) -> set:
        return set()


@pytest.fixture
def client(tmp_path: Path, config_file: Path):
    jobs = tmp_path / "jobs"
    jobs.mkdir()
    db = tmp_path / "reup.db"
    Store(db).init_schema()
    app = create_app(config_file, jobs, db)
    app.state.runner = FakeRunner()
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


def test_progress_reports_which_segments_have_tts(client):
    c, jobs, db = client
    job = create_job(jobs, "https://a/1", "zh", job_id="j1")
    seed_translation(job, [(1, 0, 3000, "Câu một", []), (2, 3000, 6000, "Câu hai", [])])
    job.tts_segment(2).write_bytes(b"RIFFxxxx")
    s = Store(db); s.upsert_job("j1", "https://a/1", "running", stage="tts"); s.close()

    tts = c.get("/jobs/j1/progress").json()["tts"]
    assert tts["total"] == 2 and tts["ready"] == 1
    assert list(tts["segments"]) == ["2"] and tts["segments"]["2"]["state"] == "tts"
    assert "1</b>/2 câu đã tổng hợp" in c.get("/jobs/j1").text


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


# --- chọn giọng và nghe thử (spec §8.2) ----------------------------------


@pytest.fixture
def stub_client(tmp_path: Path):
    """App với `tts.engine = "stub"`: nghe thử sinh wav thật, không gọi mạng."""
    src = Path(__file__).resolve().parents[1] / "config.toml"
    text = src.read_text(encoding="utf-8")
    text = text.replace('engine     = "capcut"', 'engine     = "stub"')
    text = text.replace('voice      = "BV074_streaming"', 'voice      = "stub-vi-1"')
    cfg = tmp_path / "config.toml"
    cfg.write_text(text, encoding="utf-8")

    jobs = tmp_path / "jobs"
    jobs.mkdir()
    db = tmp_path / "reup.db"
    Store(db).init_schema()
    return TestClient(create_app(cfg, jobs, db)), jobs


def test_review_page_offers_the_voice_list(stub_client):
    c, jobs = stub_client
    job = create_job(jobs, "https://a/1", "zh", job_id="j1")
    seed_translation(job, [(1, 0, 3000, "hôm nay trời đẹp", [])])

    r = c.get("/jobs/j1")

    assert 'id="voice"' in r.text
    assert "Giọng giả nữ" in r.text


def test_posting_a_voice_sticks_to_the_job(stub_client):
    c, jobs = stub_client
    create_job(jobs, "https://a/1", "zh", job_id="j1")

    r = c.post("/jobs/j1/voice", data={"voice": "stub-vi-2"})

    assert r.json() == {"voice": "stub-vi-2", "changed": True}
    assert load_job(jobs, "j1").overrides["voice"] == "stub-vi-2"


def test_posting_an_unknown_voice_is_rejected(stub_client):
    c, jobs = stub_client
    create_job(jobs, "https://a/1", "zh", job_id="j1")
    r = c.post("/jobs/j1/voice", data={"voice": "khong-co"})
    assert r.status_code == 400


def test_play_button_synthesizes_when_there_is_no_recording_yet(stub_client):
    """Ở chốt A stage tts chưa chạy; nút 🔊 vẫn phải phát được."""
    c, jobs = stub_client
    job = create_job(jobs, "https://a/1", "zh", job_id="j1")
    seed_translation(job, [(1, 0, 3000, "hôm nay trời đẹp", [])])

    r = c.get("/jobs/j1/audio/1")

    assert r.status_code == 200
    assert r.headers["content-type"] == "audio/wav"
    assert job.preview_segment(1).exists()


def test_tts_engine_failure_is_reported_not_hidden_as_404(stub_client, monkeypatch):
    c, jobs = stub_client
    job = create_job(jobs, "https://a/1", "zh", job_id="j1")
    seed_translation(job, [(1, 0, 3000, "hôm nay trời đẹp", [])])

    def boom(cfg):
        raise RuntimeError("CapCut hỏng")

    monkeypatch.setattr("reup.adapters.registry.make_tts", boom)

    r = c.get("/jobs/j1/audio/1")

    assert r.status_code == 502
    assert "CapCut hỏng" in r.json()["detail"]


# --- chạy job từ Web UI -----------------------------------------------------

def test_run_button_starts_the_job(client):
    c, jobs, db = client
    create_job(jobs, "https://a/1", "zh", job_id="j1")
    s = Store(db); s.upsert_job("j1", "https://a/1", "pending"); s.close()

    r = c.post("/jobs/j1/run", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/jobs/j1"
    assert c.app.state.runner.started == ["j1"]


def test_running_an_unknown_job_is_404(client):
    c, _, _ = client
    assert c.post("/jobs/khong-co/run").status_code == 404
    assert c.app.state.runner.started == []


def test_adding_a_job_starts_it(client):
    c, _, _ = client
    r = c.post("/jobs", data={"url": "https://a/9"}, follow_redirects=False)
    job_id = r.headers["location"].removeprefix("/jobs/")
    assert c.app.state.runner.started == [job_id]


def test_approving_a_gate_runs_the_rest(client):
    """Duyệt xong mà vẫn phải ra terminal thì chốt duyệt chưa xong việc của nó."""
    c, jobs, db = client
    create_job(jobs, "https://a/1", "zh", job_id="j1")
    s = Store(db); s.upsert_job("j1", "https://a/1", "needs_review", stage="gate_a"); s.close()

    c.post("/jobs/j1/approve", data={"gate": "a"}, follow_redirects=False)
    assert c.app.state.runner.started == ["j1"]


def test_run_all_starts_pending_and_failed_but_not_done(client):
    c, _, db = client
    s = Store(db)
    s.upsert_job("a", "https://a", "pending")
    s.upsert_job("b", "https://b", "failed")
    s.upsert_job("c", "https://c", "done")
    s.upsert_job("d", "https://d", "needs_review")
    s.close()

    r = c.post("/run-all", follow_redirects=False)
    assert r.status_code == 303
    assert sorted(c.app.state.runner.started) == ["a", "b"]


def test_queue_shows_a_run_button_for_waiting_jobs(client):
    c, _, db = client
    s = Store(db)
    s.upsert_job("a", "https://a", "pending")
    s.upsert_job("b", "https://b", "failed")
    s.close()

    body = c.get("/").text
    assert 'action="/jobs/a/run"' in body
    assert "Chạy lại" in body  # job hỏng thì nói rõ là chạy LẠI


def test_api_progress_endpoint(client):
    c, _, db = client
    s = Store(db)
    s.upsert_job("j1", "https://a/1", "running", stage="separate")
    s.upsert_job("j2", "https://a/2", "done")
    s.close()

    r = c.get("/api/progress")
    assert r.status_code == 200
    data = r.json()
    assert "j1" in data and "j2" in data
    assert data["j1"]["percent"] > 0
    assert "Tách nhạc nền" in data["j1"]["stage_label"]
    assert data["j2"]["percent"] == 100


def test_job_progress_endpoint_and_logs(client):
    c, jobs, db = client
    job = create_job(jobs, "https://a/1", "zh", job_id="j_log")
    s = Store(db)
    s.upsert_job("j_log", "https://a/1", "running", stage="asr")
    s.close()

    job.log_jsonl.write_text(
        json.dumps({"stage": "fetch", "ok": True, "started": 100.0, "finished": 102.5}) + "\n"
        + json.dumps({"stage": "demux", "ok": True, "started": 102.5, "finished": 103.0}) + "\n"
    )

    r = c.get("/jobs/j_log/progress")
    assert r.status_code == 200
    data = r.json()
    assert data["id"] == "j_log"
    assert data["status"] == "running"
    assert len(data["logs"]) == 2
    assert data["logs"][0]["label"] == "Tải video nguồn"
    assert data["logs"][0]["duration_s"] == 2.5
    assert len(data["stages"]) == 12

    # Check review page contains progress card
    review_html = c.get("/jobs/j_log").text
    assert "jobProgressCard" in review_html
    assert "Nhật ký hoạt động" in review_html


def test_reveal_endpoint_and_rerender(client, monkeypatch):
    c, jobs, db = client
    job = create_job(jobs, "https://a/1", "zh", job_id="j_rev")
    job.final_mp4.parent.mkdir(parents=True, exist_ok=True)
    job.final_mp4.write_bytes(b"dummy")
    s = Store(db)
    s.upsert_job("j_rev", "https://a/1", "done")
    s.close()

    opened = []
    monkeypatch.setattr("subprocess.run", lambda cmd, **kw: opened.append(cmd))

    r = c.post("/jobs/j_rev/reveal")
    assert r.status_code == 200
    assert opened and "open" in opened[0]

    # Test rerender clears final_mp4 and starts runner
    r_re = c.post("/jobs/j_rev/rerender", follow_redirects=False)
    assert r_re.status_code == 303
    assert not job.final_mp4.exists()
    assert c.app.state.runner.started == ["j_rev"]


def test_download_endpoint_returns_file_with_attachment_header(client):
    c, jobs, db = client
    job = create_job(jobs, "https://a/1", "zh", job_id="j_dl")
    job.final_mp4.parent.mkdir(parents=True, exist_ok=True)
    job.final_mp4.write_bytes(b"noi dung video gia")
    s = Store(db)
    s.upsert_job("j_dl", "https://a/1", "done")
    s.close()

    r = c.get("/jobs/j_dl/download")
    assert r.status_code == 200
    assert r.content == b"noi dung video gia"
    disposition = r.headers["content-disposition"]
    assert "attachment" in disposition
    assert "j_dl.mp4" in disposition


def test_download_endpoint_404_when_no_output_file(client):
    c, jobs, db = client
    create_job(jobs, "https://a/1", "zh", job_id="j_dl_missing")
    s = Store(db)
    s.upsert_job("j_dl_missing", "https://a/1", "pending")
    s.close()

    r = c.get("/jobs/j_dl_missing/download")
    assert r.status_code == 404


def test_reveal_button_hidden_behind_proxy_even_from_loopback(tmp_path, config_file):
    """Qua tailscale serve, client.host vẫn là 127.0.0.1 nhưng có X-Forwarded-For.

    Nút Mở thư mục phải ẩn trong trường hợp này vì Finder sẽ bật lên trên máy
    server, người dùng ở xa không thấy gì.
    """
    from fastapi.testclient import TestClient

    jobs = tmp_path / "jobs"
    jobs.mkdir()
    db = tmp_path / "reup.db"
    Store(db).init_schema()
    app = create_app(config_file, jobs, db)
    app.state.runner = FakeRunner()
    c = TestClient(app, client=("127.0.0.1", 50000))

    job = create_job(jobs, "https://a/1", "zh", job_id="j_proxy")
    job.final_mp4.parent.mkdir(parents=True, exist_ok=True)
    job.final_mp4.write_bytes(b"dummy")
    s = Store(db)
    s.upsert_job("j_proxy", "https://a/1", "done")
    s.close()

    # Trực tiếp từ loopback, không proxy: nút Mở thư mục vẫn hiện.
    direct = c.get("/jobs/j_proxy")
    assert direct.status_code == 200
    assert "btnRevealFinder" in direct.text
    assert "Tải về" in direct.text

    # Qua tailscale serve: client.host vẫn 127.0.0.1 nhưng có X-Forwarded-For.
    proxied = c.get("/jobs/j_proxy", headers={"X-Forwarded-For": "100.64.0.5"})
    assert proxied.status_code == 200
    assert "btnRevealFinder" not in proxied.text
    assert "Tải về" in proxied.text


# --- đổi tên, lưu trữ, xoá ---------------------------------------------------

def test_renaming_a_job_shows_the_new_name(client):
    c, jobs, db = client
    create_job(jobs, "https://a/1", "zh", job_id="j1")
    s = Store(db); s.upsert_job("j1", "https://a/1", "pending"); s.close()

    r = c.post("/jobs/j1/rename", data={"name": "  Món   chay ngon "})
    assert r.json() == {"name": "Món chay ngon", "title": "Món chay ngon"}
    assert load_job(jobs, "j1").settings["name"] == "Món chay ngon"
    assert "Món chay ngon" in c.get("/").text
    assert "Món chay ngon" in c.get("/jobs/j1").text


def test_empty_name_falls_back_to_the_generated_title(client):
    c, jobs, db = client
    job = create_job(jobs, "https://a/1", "zh", job_id="j1")
    job.meta_json.write_text(json.dumps({"title": "Tiêu đề Việt"}), encoding="utf-8")
    c.post("/jobs/j1/rename", data={"name": "Tên tạm"})

    r = c.post("/jobs/j1/rename", data={"name": ""})
    assert r.json() == {"name": "", "title": "Tiêu đề Việt"}
    assert "name" not in load_job(jobs, "j1").settings


def test_archived_jobs_move_to_the_archive_view_and_are_not_run(client):
    c, jobs, db = client
    for jid in ("keep", "old"):
        create_job(jobs, f"https://a/{jid}", "zh", job_id=jid)
    s = Store(db)
    s.upsert_job("keep", "https://a/keep", "pending")
    s.upsert_job("old", "https://a/old", "pending")
    s.close()

    assert c.post("/jobs/old/archive", data={"archived": 1}).json() == {"archived": True}
    assert 'data-job-id="old"' not in c.get("/").text
    assert 'data-job-id="old"' in c.get("/?view=archived").text

    c.post("/run-all")
    assert c.app.state.runner.started == ["keep"]

    c.post("/jobs/old/archive", data={"archived": 0})
    assert 'data-job-id="old"' in c.get("/").text


def test_deleting_a_job_removes_folder_output_and_row(client, tmp_path, monkeypatch):
    c, jobs, db = client
    job = create_job(jobs, "https://a/1", "zh", job_id="j1")
    s = Store(db); s.upsert_job("j1", "https://a/1", "done"); s.close()
    monkeypatch.chdir(tmp_path)  # output_dir mặc định là "output" tương đối
    out = tmp_path / "output"
    out.mkdir()
    (out / "j1.mp4").write_bytes(b"x")

    assert c.post("/jobs/j1/delete").json() == {"deleted": "j1"}
    assert not job.root.exists()
    assert not (out / "j1.mp4").exists()
    s = Store(db); assert s.get_job("j1") is None; s.close()


def test_deleting_many_jobs_skips_running_and_missing(client):
    c, jobs, db = client
    s = Store(db)
    for jid in ("a", "b", "busy"):
        create_job(jobs, f"https://a/{jid}", "zh", job_id=jid)
        s.upsert_job(jid, f"https://a/{jid}", "done")
    s.close()
    c.app.state.runner.is_running = lambda job_id: job_id == "busy"

    r = c.post("/jobs/delete-many", data={"job_ids": ["a", "b", "busy", "nope"]}).json()
    assert r["deleted"] == ["a", "b"]
    assert [x["id"] for x in r["skipped"]] == ["busy", "nope"]
    assert not (jobs / "a").exists() and (jobs / "busy").exists()


def test_emptying_the_archive_deletes_only_archived_jobs(client):
    c, jobs, db = client
    s = Store(db)
    for jid in ("keep", "old1", "old2"):
        create_job(jobs, f"https://a/{jid}", "zh", job_id=jid)
        s.upsert_job(jid, f"https://a/{jid}", "done")
    s.close()
    c.post("/jobs/old1/archive", data={"archived": 1})
    c.post("/jobs/old2/archive", data={"archived": 1})
    assert "bulkStart" in c.get("/?view=archived").text

    assert sorted(c.post("/archive/empty").json()["deleted"]) == ["old1", "old2"]
    assert (jobs / "keep").exists()
    assert not (jobs / "old1").exists() and not (jobs / "old2").exists()


def test_deleting_a_running_job_is_refused(client):
    c, jobs, db = client
    job = create_job(jobs, "https://a/1", "zh", job_id="j1")
    c.app.state.runner.is_running = lambda job_id: True
    assert c.post("/jobs/j1/delete").status_code == 409
    assert job.root.exists()


def test_deleting_an_orphan_row_without_folder(client):
    c, _, db = client
    s = Store(db); s.upsert_job("ghost", "https://a/1", "failed"); s.close()
    assert c.post("/jobs/ghost/delete").status_code == 200
    assert c.post("/jobs/ghost/delete").status_code == 404


def test_thumbnail_is_cut_from_the_video(client, sample_video: Path):
    c, jobs, _ = client
    job = create_job(jobs, "https://a/1", "zh", job_id="j1")
    job.source_video.write_bytes(sample_video.read_bytes())
    r = c.get("/jobs/j1/thumb")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/jpeg"
    assert job.thumb_jpg.exists()


def test_thumbnail_without_video_is_404(client):
    c, jobs, _ = client
    create_job(jobs, "https://a/1", "zh", job_id="j1")
    assert c.get("/jobs/j1/thumb").status_code == 404


def test_autouse_fixture_clears_web_auth_env_vars(tmp_path: Path, config_file: Path, monkeypatch):
    """Kiểm chứng: autouse fixture no_web_auth chạy trước create_app.

    Fixture xoá REUP_WEB_PASSWORD/REUP_WEB_SECRET; test có thể setenv lại để override.
    """
    # Mặc định (sau autouse delenv): app không bật auth
    jobs = tmp_path / "jobs"
    jobs.mkdir()
    db = tmp_path / "reup.db"
    Store(db).init_schema()
    app1 = create_app(config_file, jobs, db)
    assert app1.state.password == ""  # autouse fixture đã xoá var

    # Test có thể setenv lại (chạy sau autouse fixture, trong test body)
    monkeypatch.setenv("REUP_WEB_PASSWORD", "test_password")
    app2 = create_app(config_file, jobs, db)
    assert app2.state.password == "test_password"  # test override thành công
