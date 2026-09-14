# tests/test_pipeline_integration.py
import json
from pathlib import Path

import pytest
from reup.core.job import create_job
from reup.core.runner import atomic_write, run_job
from reup.core.stage import StageSpec
from reup.core.store import Store
from reup.media.ffmpeg import probe
from reup.models import Segment
from reup.stages import stages_for
from reup.stages import translate as translate_stage


class FakeLLM:
    """Gemini giả: dịch, viết lại, và chọn nguồn khi ASR/OCR lệch nhau."""

    def complete_json(self, prompt, schema):
        props = schema.get("properties", {})
        if "choices" in props:
            return {"choices": []}  # thiếu lựa chọn -> reconcile giữ ASR
        if "segments" in props:
            return {
                "segments": [
                    {"id": 1, "text": "Hôm nay dạy làm thịt kho", "tts_text": "Hôm nay dạy làm thịt kho"},
                    {"id": 2, "text": "Trước hết thái thịt ba chỉ", "tts_text": "Trước hết thái thịt ba chỉ"},
                ]
            }
        return {"text": "Câu ngắn"}


LLM_ROLES_SEEN: list[str] = []


@pytest.fixture
def offline_stages(monkeypatch, sample_video: Path, cfg_fixture):
    """Thay fetch bằng copy file local, whisper và Gemini bằng hàm giả.

    Không stage nào trong test được chạm vào mạng.
    """

    def fake_fetch_run(job, cfg):
        job.source_video.write_bytes(sample_video.read_bytes())
        job.source_info.write_text('{"title": "clip thử"}', encoding="utf-8")

    def fake_transcribe(audio, model, language):
        return "zh", [
            Segment(id=1, start_ms=0, end_ms=3000, text="今天教大家做红烧肉"),
            Segment(id=2, start_ms=3000, end_ms=6000, text="先把五花肉切成小块"),
        ]

    # Giả ở tầng adapter, không ở stage: nhờ vậy đường đi qua registry
    # (asr.engine -> WhisperASR) cũng được test này chạy thật.
    monkeypatch.setattr("reup.adapters.whisper_asr.transcribe", fake_transcribe)
    # Ghi lại vai từng stage khai: đó là thứ quyết định stage nào đi model nào.
    LLM_ROLES_SEEN.clear()

    def fake_make_llm(cfg, role=None):
        LLM_ROLES_SEEN.append(role)
        return FakeLLM()

    monkeypatch.setattr("reup.adapters.registry.make_llm", fake_make_llm)
    # Demucs nạp model và chạy vài giây — quá nặng cho test. Thay bằng bản sao
    # thẳng: đường trộn bgm vẫn được kiểm tra, chỉ khâu tách là giả.
    def fake_separate_run(job, cfg):
        from reup.media.audio import to_wav

        to_wav(job.full_48k, job.vocals, sample_rate=16000, channels=1)
        to_wav(job.full_48k, job.bgm, sample_rate=48000, channels=2)

    # Apple Vision trên fixture testsrc không có chữ nào, và OCR từng khung rất
    # chậm. Thay bằng bản rỗng: đường đi qua reconcile vẫn được kiểm tra, luật
    # "OCR rỗng -> dùng thẳng ASR" chính là nhánh hay gặp nhất ngoài đời.
    def fake_subdetect_run(job, cfg):
        atomic_write(
            job.subrect_json,
            json.dumps({"video_w": 540, "video_h": 960, "regions": [],
                        "present_ranges": []}),
        )

    def fake_ocr_run(job, cfg):
        atomic_write(job.ocr_json, json.dumps({"lines": []}))

    swap = {
        "fetch": StageSpec("fetch", ("source.mp4", "source.info.json"), fake_fetch_run),
        "separate": StageSpec(
            "separate", ("audio/vocals.wav", "audio/bgm.wav"), fake_separate_run
        ),
        "subdetect": StageSpec("subdetect", ("subrect.json",), fake_subdetect_run),
        "ocr": StageSpec("ocr", ("ocr.json",), fake_ocr_run),
    }
    return [swap.get(s.name, s) for s in stages_for(cfg_fixture)]


def run_all_gates(job, cfg, store, stages, max_gates: int = 4) -> str:
    """Chạy tới xong, tự duyệt mọi chốt gặp trên đường.

    Chốt là việc của người thật; ở đây chỉ cần chứng minh đường đi thông.
    """
    for _ in range(max_gates + 1):
        status = run_job(job, cfg, store, stages)
        if status != "needs_review":
            return status
        gate = store.get_job(job.id)["stage"].removeprefix("gate_")
        job.approve_gate(gate)
    raise AssertionError("quá nhiều chốt — có chốt nào không duyệt được")


def test_full_pipeline_produces_playable_video(
    tmp_path: Path, cfg_fixture, offline_stages
):
    store = Store(tmp_path / "reup.db")
    store.init_schema()
    job = create_job(tmp_path / "jobs", "https://douyin.com/v/1", "zh", job_id="j1")
    store.upsert_job(job.id, job.source_url, "pending")

    assert run_all_gates(job, cfg_fixture, store, offline_stages) == "done"

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

    run_all_gates(job, cfg_fixture, store, offline_stages)

    for path in (
        job.source_video, job.source_info, job.full_16k, job.full_48k,
        job.vocals, job.bgm, job.subrect_json, job.ocr_json,
        job.asr_json, job.transcript_json, job.translation_json,
        job.sub_ass, job.tts_dir / "manifest.json",
        job.tts_dir / "fit.json", job.dub_wav, job.final_mp4,
    ):
        assert path.exists(), f"thiếu {path}"
    store.close()


def test_resume_skips_completed_stages(tmp_path: Path, cfg_fixture, offline_stages):
    store = Store(tmp_path / "reup.db")
    store.init_schema()
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    store.upsert_job(job.id, job.source_url, "pending")
    run_all_gates(job, cfg_fixture, store, offline_stages)
    before = len(store.stage_durations())

    run_all_gates(job, cfg_fixture, store, offline_stages)

    assert len(store.stage_durations()) == before  # không stage nào chạy lại
    store.close()


def test_deleting_one_artifact_reruns_only_from_there(
    tmp_path: Path, cfg_fixture, offline_stages
):
    store = Store(tmp_path / "reup.db")
    store.init_schema()
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    store.upsert_job(job.id, job.source_url, "pending")
    run_all_gates(job, cfg_fixture, store, offline_stages)

    job.final_mp4.unlink()
    run_all_gates(job, cfg_fixture, store, offline_stages)

    ran = [r["stage"] for r in store.stage_durations()]
    assert ran[-1] == "compose"
    assert ran.count("asr") == 1  # asr không chạy lại
    store.close()


def test_log_jsonl_has_one_line_per_stage(tmp_path: Path, cfg_fixture, offline_stages):
    store = Store(tmp_path / "reup.db")
    store.init_schema()
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    store.upsert_job(job.id, job.source_url, "pending")

    run_all_gates(job, cfg_fixture, store, offline_stages)

    lines = job.log_jsonl.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == len(offline_stages)
    store.close()


def test_every_llm_stage_declares_its_role(tmp_path: Path, cfg_fixture, offline_stages):
    """Stage nào quên khai vai sẽ lặng lẽ dùng [llm] chung, và người dùng tưởng
    mình đã đẩy nó sang model local rồi."""
    store = Store(tmp_path / "reup.db")
    store.init_schema()
    job = create_job(tmp_path / "jobs", "https://douyin.com/v/1", "zh", job_id="j1")
    store.upsert_job(job.id, job.source_url, "pending")

    run_all_gates(job, cfg_fixture, store, offline_stages)

    assert set(LLM_ROLES_SEEN) == {"reconcile", "translate", "fit", "export", "pronounce"}
    assert None not in LLM_ROLES_SEEN
