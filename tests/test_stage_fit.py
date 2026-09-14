import json
import re
from pathlib import Path

import pytest

from reup.adapters.stub_tts import StubTTS
from reup.adapters.tts import TTSResult
from reup.core.job import create_job
from reup.media.audio import duration_ms, silence
from reup.models import Segment, Transcript
from reup.stages import fit as fit_stage
from reup.stages import tts as tts_stage


class NoLLM:
    """Không đoạn nào được phép cần viết lại trong các test này."""

    def complete_json(self, prompt, schema):
        raise AssertionError("không mong đợi gọi LLM ở đây")


def _seed(job, specs: list[tuple[int, int, str]]) -> None:
    """specs: danh sách (start_ms, end_ms, text tiếng Việt)."""
    Transcript(
        source_lang="vi",
        segments=[
            Segment(id=i, start_ms=s, end_ms=e, text=t)
            for i, (s, e, t) in enumerate(specs, start=1)
        ],
    ).save(job.translation_json)


def _prepare(job, cfg, specs):
    _seed(job, specs)
    tts_stage.run_with(job, cfg, StubTTS())


def test_builds_dub_covering_whole_video(tmp_path: Path, cfg_fixture):
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    _prepare(job, cfg_fixture, [(0, 3000, "Hôm nay dạy làm"), (3000, 6000, "Trước thái thịt")])

    fit_stage.run_with(job, cfg_fixture, StubTTS(), NoLLM())

    assert job.dub_wav.exists()
    assert abs(duration_ms(job.dub_wav) - 6000) <= 150


def test_well_fitting_segment_is_not_flagged(tmp_path: Path, cfg_fixture):
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    # 3 âm tiết x 220ms = 660ms trong khe 3000ms -> pad, không cờ
    _prepare(job, cfg_fixture, [(0, 3000, "Hôm nay nắng")])

    fit_stage.run_with(job, cfg_fixture, StubTTS(), NoLLM())

    assert Transcript.load(job.translation_json).segments[0].flags == []


def test_writes_fit_report(tmp_path: Path, cfg_fixture):
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    _prepare(job, cfg_fixture, [(0, 3000, "Hôm nay nắng")])

    fit_stage.run_with(job, cfg_fixture, StubTTS(), NoLLM())

    report = json.loads((job.tts_dir / "fit.json").read_text(encoding="utf-8"))
    assert report["segments"][0]["action"] in {"pad", "tempo", "tempo_capped", "overflow"}
    assert report["segments"][0]["id"] == 1


def test_fails_on_empty_translation(tmp_path: Path, cfg_fixture):
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    Transcript(source_lang="vi", segments=[]).save(job.translation_json)
    with pytest.raises(ValueError, match="không có câu nào"):
        fit_stage.run_with(job, cfg_fixture, StubTTS(), NoLLM())


def test_subtitles_are_generated_after_the_text_is_final(tmp_path: Path, cfg_fixture):
    """Sinh sub ở translate thì sub hiện câu cũ còn giọng đọc câu đã viết lại."""
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    _prepare(job, cfg_fixture, [(0, 3000, "Hôm nay nắng")])

    fit_stage.run_with(job, cfg_fixture, StubTTS(), NoLLM())

    ass = job.sub_ass.read_text(encoding="utf-8")
    assert "Hôm nay nắng" in ass
    assert "Dialogue:" in ass


def test_spec_declares_its_artifacts():
    assert fit_stage.SPEC.name == "fit"
    assert set(fit_stage.SPEC.produces) == {"dub.wav", "sub.ass"}


class _EngineAwareTTS:
    """Câu 1: luôn "capcut", đủ ngắn — không bao giờ bị `fit` tổng hợp lại.

    Câu 2: lần tổng hợp ĐẦU (ở stage `tts`) trả "edge_tts_fallback" và quá dài
    (buộc `fit` phải viết lại rồi tổng hợp lại); lần tổng hợp SAU (do `fit`
    gọi lại sau khi LLM viết ngắn) trả "capcut" và đủ ngắn — mô phỏng CapCut
    lành lại giữa hai lần gọi. Manifest cuối cùng phải phản ánh đúng lần tổng
    hợp MỚI NHẤT của câu 2 (capcut), không phải bản ghi cũ từ stage `tts`
    (edge_tts_fallback) — đúng bug mà finding 1 của final review yêu cầu sửa.
    """

    def __init__(self) -> None:
        self.calls: dict[int, int] = {}

    def synthesize(self, text: str, lang: str, voice: str, out: Path) -> TTSResult:
        seg_id = int(Path(out).stem.split("_")[1])
        self.calls[seg_id] = self.calls.get(seg_id, 0) + 1
        if seg_id == 1:
            ms, engine = 600, "capcut"
        elif self.calls[seg_id] == 1:
            ms, engine = 2200, "edge_tts_fallback"
        else:
            ms, engine = 600, "capcut"
        silence(out, ms)
        return TTSResult(path=Path(out), actual_ms=ms, engine=engine)


class _OneShotRewriteLLM:
    """Viết lại thành một câu chắc chắn ngắn, cho mọi id được hỏi trong lượt."""

    def complete_json(self, prompt, schema):
        ids = [int(n) for n in re.findall(r"### Đoạn (\d+)", prompt)]
        return {"segments": [{"id": i, "text": "một hai ba"} for i in ids]}


def test_engine_field_survives_fit(tmp_path: Path, cfg_fixture):
    """Finding 1 (final review): trước khi sửa, `fit.write_manifest` ghi đè
    `tts/manifest.json` với entry KHÔNG có field `engine`, xoá sạch nhãn
    capcut/edge_tts_fallback/silence mà stage `tts` vừa gắn. Test này chạy cả
    hai stage thật (`tts` rồi `fit`) và xác nhận manifest CUỐI CÙNG (sau khi
    `fit` đã ghi đè) vẫn đúng — kể cả với câu bị `fit` tổng hợp lại, engine
    phải là của lần tổng hợp MỚI, không phải bản ghi cũ."""
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    Transcript(
        source_lang="vi",
        segments=[
            Segment(id=1, start_ms=0, end_ms=1000, text="một hai ba"),
            Segment(
                id=2, start_ms=1000, end_ms=2000,
                text="một hai ba bốn năm sáu bảy tám chín mười",
            ),
        ],
    ).save(job.translation_json)

    tts = _EngineAwareTTS()
    tts_stage.run_with(job, cfg_fixture, tts)

    before = json.loads((job.tts_dir / "manifest.json").read_text(encoding="utf-8"))
    assert {e["id"]: e["engine"] for e in before["segments"]} == {
        1: "capcut", 2: "edge_tts_fallback",
    }

    fit_stage.run_with(job, cfg_fixture, tts, _OneShotRewriteLLM())

    # Câu 2 phải thật sự bị viết lại (chứng tỏ nhánh tổng hợp lại có chạy).
    assert tts.calls[2] == 2

    after = json.loads((job.tts_dir / "manifest.json").read_text(encoding="utf-8"))
    entries = {e["id"]: e["engine"] for e in after["segments"]}
    assert entries[1] == "capcut"
    assert entries[2] == "capcut"  # tươi từ lần tổng hợp lại, không phải "edge_tts_fallback" cũ
