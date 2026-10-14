import json
from pathlib import Path

from reup.core.job import create_job, load_job
from reup.core.store import Store
from reup.media.ffmpeg import probe
from reup.stages import SPLIT_STAGES, split, stages_for_job


class StubLLM:
    def __init__(self, answer):
        self.answer = answer
        self.prompts = []

    def complete_json(self, prompt, schema):
        self.prompts.append(prompt)
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def _segments(n: int, step_ms: int = 1000) -> list[dict]:
    return [
        {"id": i + 1, "start_ms": i * step_ms + 100, "end_ms": (i + 1) * step_ms - 100, "text": f"cách {i + 1}"}
        for i in range(n)
    ]


def _job(tmp_path: Path, sample_video: Path, max_s: int = 2):
    job = create_job(tmp_path / "jobs", "https://www.douyin.com/video/1", "zh")
    job.update_settings(split={"max_s": max_s}, name="9 cách")
    job.source_video.write_bytes(sample_video.read_bytes())
    job.source_info.write_text(json.dumps({"title": "9 cách", "duration": 6}), encoding="utf-8")
    job.asr_json.write_text(json.dumps({"segments": _segments(6)}), encoding="utf-8")
    return job


def test_split_by_ai_creates_child_jobs(tmp_path, sample_video, cfg_fixture):
    job = _job(tmp_path, sample_video)
    llm = StubLLM({"parts": [
        {"start_id": 1, "end_id": 2, "topic": "Cách 1-2"},
        {"start_id": 3, "end_id": 4, "topic": "Cách 3-4"},
        {"start_id": 5, "end_id": 6, "topic": "Cách 5-6"},
    ]})
    result = split.run_with(job, cfg_fixture, llm)

    assert result["method"] == "ai"
    assert [p["job_id"] for p in result["parts"]] == [f"{job.id}-p{i}" for i in (1, 2, 3)]
    assert result["parts"][0]["start_ms"] == 0
    assert result["parts"][1]["start_ms"] == 2000  # giữa khoảng lặng 1900–2100
    assert result["parts"][-1]["end_ms"] == probe(job.source_video).duration_ms

    child = load_job(job.root.parent, f"{job.id}-p2")
    s = child.settings
    assert (s["parent"], s["part"], s["parts"]) == (job.id, 2, 3)
    assert s["name"] == "9 cách — Phần 2/3"
    assert json.loads(child.source_info.read_text())["title"] == "9 cách (Phần 2/3)"
    assert abs(probe(child.source_video).duration_ms - 2000) < 150
    assert job.settings["name"].startswith("✂ Đã tách 3 phần")


def test_bad_ai_answer_falls_back_to_silence_gaps(tmp_path, sample_video, cfg_fixture):
    job = _job(tmp_path, sample_video)
    # Không nối liền (bỏ sót câu 3) → không hợp lệ.
    llm = StubLLM({"parts": [{"start_id": 1, "end_id": 2, "topic": ""}, {"start_id": 4, "end_id": 6, "topic": ""}]})
    result = split.run_with(job, cfg_fixture, llm)
    assert result["method"] == "khoảng lặng"
    assert len(result["parts"]) >= 2


def test_ai_error_does_not_fail_split(tmp_path, sample_video, cfg_fixture):
    job = _job(tmp_path, sample_video)
    result = split.run_with(job, cfg_fixture, StubLLM(RuntimeError("hết quota")))
    assert result["method"] == "khoảng lặng"


def test_short_video_is_not_split_and_runs_full_pipeline(tmp_path, sample_video, cfg_fixture):
    job = _job(tmp_path, sample_video, max_s=120)
    assert [s.name for s in stages_for_job(cfg_fixture, job)] == [s.name for s in SPLIT_STAGES]
    result = split.run_with(job, cfg_fixture, StubLLM({"parts": []}))
    assert result["parts"] == []
    assert not job.asr_json.exists()  # nhận diện lại trên giọng đã tách nhạc
    assert "split" not in [s.name for s in stages_for_job(cfg_fixture, job)]


def test_register_children_is_idempotent(tmp_path, sample_video, cfg_fixture):
    job = _job(tmp_path, sample_video)
    split.run_with(job, cfg_fixture, StubLLM(RuntimeError("x")))
    store = Store(tmp_path / "reup.db")
    store.init_schema()
    first = split.register_children(job, store)
    assert first and all(store.get_job(c)["status"] == "pending" for c in first)
    store.upsert_job(first[0], job.source_url, "done")
    assert split.register_children(job, store) == first[1:]


def test_fallback_keeps_parts_under_limit():
    segs = _segments(30, step_ms=10_000)  # 5 phút
    groups = split.fallback(segs, 120_000)
    for a, b, _ in groups:
        assert segs[b]["end_ms"] - segs[a]["start_ms"] <= 120_000
    assert groups[0][0] == 0 and groups[-1][1] == len(segs) - 1
