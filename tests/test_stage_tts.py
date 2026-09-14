# tests/test_stage_tts.py
import json
import time
from pathlib import Path
from threading import Lock

import pytest
from reup.adapters.tts import TTSResult
from reup.core.job import create_job
from reup.models import Segment, Transcript
from reup.stages import tts as tts_stage


def _seed(job, texts: list[str], lang: str = "vi") -> None:
    """Phase 2: stage tts đọc bản dịch, không còn đọc transcript gốc."""
    Transcript(
        source_lang=lang,
        segments=[
            Segment(id=i, start_ms=(i - 1) * 3000, end_ms=i * 3000, text=t)
            for i, t in enumerate(texts, start=1)
        ],
    ).save(job.translation_json)


def _run(job, cfg):
    from reup.adapters.stub_tts import StubTTS

    tts_stage.run_with(job, cfg, StubTTS())


def test_writes_one_wav_per_segment(tmp_path: Path, cfg_fixture):
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    _seed(job, ["Hôm nay dạy làm", "Trước hết thái thịt"])

    _run(job, cfg_fixture)

    assert job.tts_segment(1).exists()
    assert job.tts_segment(2).exists()


def test_manifest_records_actual_durations(tmp_path: Path, cfg_fixture):
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    _seed(job, ["Hôm nay dạy làm"])

    _run(job, cfg_fixture)

    manifest = json.loads((job.tts_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["voice"] == cfg_fixture.tts.voice
    assert manifest["lang"] == "vi"
    assert len(manifest["segments"]) == 1
    entry = manifest["segments"][0]
    assert entry["id"] == 1
    assert entry["actual_ms"] > 0
    assert entry["path"] == "seg_0001.wav"


def test_rerun_regenerates_all_segments(tmp_path: Path, cfg_fixture):
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    _seed(job, ["Hôm nay dạy làm"])
    _run(job, cfg_fixture)
    first = job.tts_segment(1).stat().st_size

    _seed(job, ["Hôm nay dạy mọi người làm món thịt kho tàu thật ngon"])
    _run(job, cfg_fixture)

    assert job.tts_segment(1).stat().st_size > first


def test_fails_on_empty_translation(tmp_path: Path, cfg_fixture):
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    Transcript(source_lang="vi", segments=[]).save(job.translation_json)

    with pytest.raises(ValueError, match="không có câu nào"):
        _run(job, cfg_fixture)


def test_reads_translation_not_transcript(tmp_path: Path, cfg_fixture):
    """Nhầm nguồn ở đây nghĩa là đọc nguyên văn tiếng Trung bằng giọng Việt."""
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    Transcript(
        source_lang="zh",
        segments=[Segment(id=1, start_ms=0, end_ms=3000, text="今天教大家")],
    ).save(job.transcript_json)

    with pytest.raises(FileNotFoundError):
        _run(job, cfg_fixture)


def test_spec_declares_its_artifacts():
    assert tts_stage.SPEC.name == "tts"
    assert set(tts_stage.SPEC.produces) == {"tts/manifest.json"}


def test_job_voice_beats_config(tmp_path: Path, cfg_fixture):
    """Giọng chọn ở chốt A thuộc riêng job; config.toml là của cả máy."""
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    _seed(job, ["Hôm nay dạy làm"])
    job.set_override("voice", "stub-vi-2")

    _run(job, cfg_fixture)

    manifest = json.loads((job.tts_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["voice"] == "stub-vi-2" != cfg_fixture.tts.voice


class _ScrambledAdapter:
    """Adapter giả: câu có id càng nhỏ ngủ càng lâu, nên nếu chạy song song mà
    không giữ thứ tự, câu id lớn (ngủ ít) sẽ hoàn thành trước — lộ ra ngay nếu
    `synthesize_all` map theo thứ tự hoàn thành thay vì theo `segments` gốc."""

    def __init__(self) -> None:
        self.call_order: list[int] = []
        self._lock = Lock()

    def synthesize(self, text: str, lang: str, voice: str, out: Path) -> TTSResult:
        seg_id = int(out.stem.split("_")[1])
        with self._lock:
            self.call_order.append(seg_id)
        # Đảo ngược: id nhỏ ngủ lâu, id lớn xong sớm.
        time.sleep(0.03 * (6 - seg_id))
        out.write_bytes(b"\x00")
        return TTSResult(path=out, actual_ms=seg_id * 100)


def test_synthesize_all_preserves_segment_order_under_concurrency(
    tmp_path: Path,
):
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    segments = [
        Segment(id=i, start_ms=(i - 1) * 3000, end_ms=i * 3000, text=f"câu {i}")
        for i in range(1, 6)
    ]
    transcript = Transcript(source_lang="vi", segments=segments)
    adapter = _ScrambledAdapter()

    entries = tts_stage.synthesize_all(job, transcript, adapter, "voice-x", concurrency=5)

    assert [e["id"] for e in entries] == [1, 2, 3, 4, 5]
    # Cùng lúc thả cả 5 câu: câu ngủ ít nhất (id=5) phải hoàn thành trước câu
    # ngủ lâu nhất (id=1), chứng tỏ chúng thật sự chạy song song.
    assert set(adapter.call_order) == {1, 2, 3, 4, 5}


class _FailFastAdapter:
    """Câu 3 lỗi; câu 1 ngủ LÂU để giữ một worker bận suốt test, các câu khác
    ngủ NGẮN mô phỏng thời gian gọi CapCut thật.

    Đo thực nghiệm (10 lần liền, xem race_test3.py lúc viết task): không có
    sleep thật ở các câu, một worker vừa rảnh có thể chạy hết veo cả hàng đợi
    còn lại trước khi main thread kịp thấy lỗi và huỷ — vì raise xong, worker
    lập tức lấy tiếp việc kế trong CÙNG một luồng, không nhường CPU. Có sleep
    thật (giống subprocess CapCut thật) thì main thread luôn kịp huỷ các câu
    CÒN NẰM TRONG HÀNG ĐỢI (ở đây là câu 5, 6) — chỉ có đúng MỘT câu (4) là
    worker vừa rảnh kịp chụp trước khi lệnh huỷ tới nơi, không xoá bỏ được
    hoàn toàn hiện tượng "worker đang chạy dở thì không huỷ được", nhưng vẫn
    dừng SỚM hơn hẳn so với bug cũ (chạy hết mọi câu, mỗi câu 3 lần retry)."""

    def __init__(self) -> None:
        self.calls: list[int] = []
        self._lock = Lock()

    def synthesize(self, text: str, lang: str, voice: str, out: Path) -> TTSResult:
        seg_id = int(out.stem.split("_")[1])
        with self._lock:
            self.calls.append(seg_id)
        if seg_id == 1:
            time.sleep(1.0)
        elif seg_id == 3:
            time.sleep(0.05)
            raise RuntimeError(f"lỗi giả ở câu {seg_id}")
        else:
            time.sleep(0.05)
        out.write_bytes(b"\x00")
        return TTSResult(path=out, actual_ms=100)


def test_synthesize_all_fails_fast_on_first_error(tmp_path: Path):
    """Task 1.2/1.5: một câu lỗi (vd. CapCutError khi allow_edge_fallback=false)
    phải dừng SỚM, không để mọi câu khác chạy hết vòng retry riêng của chúng —
    bug cũ dùng `pool.map` nộp hết việc lên ngay từ đầu nên `with` block đợi
    (`shutdown(wait=True)`) toàn bộ chạy xong mới thấy lỗi."""
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    segments = [
        Segment(id=i, start_ms=(i - 1) * 3000, end_ms=i * 3000, text=f"câu {i}")
        for i in range(1, 7)
    ]
    transcript = Transcript(source_lang="vi", segments=segments)
    adapter = _FailFastAdapter()

    with pytest.raises(RuntimeError, match="lỗi giả ở câu 3"):
        tts_stage.synthesize_all(job, transcript, adapter, "voice-x", concurrency=2)

    # Câu 5, 6 còn nằm trong hàng đợi lúc lỗi xảy ra — chưa từng được gọi tới.
    assert 5 not in adapter.calls
    assert 6 not in adapter.calls


def test_synthesize_all_concurrency_one_matches_sequential(tmp_path: Path):
    """concurrency=1 phải cho kết quả giống hệt vòng lặp tuần tự cũ."""
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    segments = [
        Segment(id=i, start_ms=(i - 1) * 3000, end_ms=i * 3000, text=f"câu {i}")
        for i in range(1, 4)
    ]
    transcript = Transcript(source_lang="vi", segments=segments)

    from reup.adapters.stub_tts import StubTTS

    adapter = StubTTS()

    sequential = []
    for seg in transcript.segments:
        out = job.tts_segment(seg.id)
        result = adapter.synthesize(seg.text, tts_stage.LANG, "voice-x", out)
        sequential.append({
            "id": seg.id, "path": out.name, "actual_ms": result.actual_ms,
            "engine": result.engine,
        })

    # Chạy lại từ đầu với job khác để synthesize_all không ghi đè file vừa đo.
    job2 = create_job(tmp_path / "jobs", "https://a/2", "zh", job_id="j2")
    concurrent_result = tts_stage.synthesize_all(
        job2, transcript, adapter, "voice-x", concurrency=1
    )

    assert concurrent_result == sequential


def test_synthesize_all_default_concurrency_is_sequential_when_unset(tmp_path: Path):
    """Không truyền concurrency thì phải giữ hành vi cũ (mặc định tham số = 1)."""
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    segments = [Segment(id=1, start_ms=0, end_ms=3000, text="câu 1")]
    transcript = Transcript(source_lang="vi", segments=segments)

    from reup.adapters.stub_tts import StubTTS

    entries = tts_stage.synthesize_all(job, transcript, StubTTS(), "voice-x")

    assert entries[0]["id"] == 1


class _CountingAdapter:
    """Adapter giả đếm số lần `synthesize` thật sự được gọi, để test cache."""

    def __init__(self) -> None:
        self.calls: list[int] = []

    def synthesize(self, text: str, lang: str, voice: str, out: Path) -> TTSResult:
        seg_id = int(out.stem.split("_")[1])
        self.calls.append(seg_id)
        out.write_bytes(b"\x00\x00")
        return TTSResult(path=out, actual_ms=500)


def test_redo_skips_unchanged_segment_via_text_cache(tmp_path: Path, cfg_fixture):
    """`redo --from tts` không nên gọi lại adapter cho câu chưa đổi chữ."""
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    _seed(job, ["Hôm nay dạy làm", "Trước hết thái thịt"])
    _run(job, cfg_fixture)

    # Mô phỏng `redo --from tts`: chỉ manifest.json bị xoá (đúng như
    # `_cmd_redo` chỉ xoá các artifact khai báo ở `SPEC.produces`), các file
    # wav và text_cache.json vẫn còn nguyên.
    (job.tts_dir / "manifest.json").unlink()

    transcript = Transcript.load(job.translation_json)
    adapter = _CountingAdapter()
    entries = tts_stage.synthesize_all(job, transcript, adapter, "voice-x", concurrency=2)

    assert adapter.calls == []
    assert [e["id"] for e in entries] == [1, 2]
    assert all(e["actual_ms"] > 0 for e in entries)


def test_redo_resynthesizes_changed_segment(tmp_path: Path, cfg_fixture):
    """Câu bị viết lại (đổi text) vẫn phải gọi lại adapter bình thường."""
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    _seed(job, ["Hôm nay dạy làm", "Trước hết thái thịt"])
    _run(job, cfg_fixture)

    _seed(job, ["Hôm nay dạy làm món mới", "Trước hết thái thịt"])
    transcript = Transcript.load(job.translation_json)
    adapter = _CountingAdapter()
    entries = tts_stage.synthesize_all(job, transcript, adapter, "voice-x", concurrency=2)

    assert adapter.calls == [1]
    assert [e["id"] for e in entries] == [1, 2]


def test_redo_resynthesizes_when_wav_missing_despite_cache_match(
    tmp_path: Path, cfg_fixture
):
    """Cache khớp nhưng file .wav bị xoá tay thì vẫn phải tổng hợp lại."""
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    _seed(job, ["Hôm nay dạy làm", "Trước hết thái thịt"])
    _run(job, cfg_fixture)

    job.tts_segment(1).unlink()

    transcript = Transcript.load(job.translation_json)
    adapter = _CountingAdapter()
    entries = tts_stage.synthesize_all(job, transcript, adapter, "voice-x", concurrency=2)

    assert adapter.calls == [1]
    assert [e["id"] for e in entries] == [1, 2]


class _MixedEngineAdapter:
    """Adapter giả: câu 1 CapCut thật, câu 2 bị fallback edge-tts — mô phỏng
    một job thật khi CapCut lỗi giữa chừng.

    Ghi wav im lặng THẬT (không phải byte rác): đường cache-hit của
    `synthesize_all` gọi `duration_ms(out)` — tức ffprobe thật — nên `out`
    phải là audio hợp lệ, khác với vài adapter giả khác trong file này chỉ
    cần qua được đường tổng hợp mới (không đọc lại file)."""

    def synthesize(self, text: str, lang: str, voice: str, out: Path) -> TTSResult:
        from reup.media.audio import silence

        seg_id = int(out.stem.split("_")[1])
        silence(out, 300)
        engine = "capcut" if seg_id == 1 else "edge_tts_fallback"
        return TTSResult(path=out, actual_ms=300, engine=engine)


def test_manifest_records_engine_per_segment(tmp_path: Path, cfg_fixture):
    """Task 1.5: manifest.json phải cho biết câu nào bị đổi sang edge-tts,
    không phải chỉ CapCut thật — để `redo`/xem thủ công biết mà kiểm tra."""
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    _seed(job, ["Hôm nay dạy làm", "Trước hết thái thịt"])

    tts_stage.run_with(job, cfg_fixture, _MixedEngineAdapter())

    manifest = json.loads((job.tts_dir / "manifest.json").read_text(encoding="utf-8"))
    entries = {e["id"]: e["engine"] for e in manifest["segments"]}
    assert entries == {1: "capcut", 2: "edge_tts_fallback"}


def test_redo_from_tts_loses_engine_label_on_cache_hit(tmp_path: Path, cfg_fixture):
    """Khoảng trống CÒN LẠI sau khi sửa bug `fit` xoá `engine` (task final-review
    finding 1): `redo --from tts` xoá hẳn `tts/manifest.json` (nó nằm trong
    `SPEC.produces`), nên `_load_prev_engines` — nguồn dữ liệu DUY NHẤT để câu
    dùng lại từ cache giữ đúng nhãn — không còn gì để đọc. Test này CHỦ Ý xác
    nhận hành vi (sai) hiện tại thay vì giả vờ đã sửa: câu 2 vốn là
    edge_tts_fallback bị dùng lại từ cache (text không đổi) sau `redo` sẽ bị
    dán nhãn mặc định "capcut" — sai. Đây là lỗ hổng CẤU TRÚC riêng, việc sửa
    `fit.py` không đụng tới nó; muốn đóng hẳn phải có một nguồn `engine` sống
    sót qua `redo` (vd. ghi kèm vào `text_cache.json`, vốn không nằm trong
    `SPEC.produces` nên không bị xoá) — nằm ngoài phạm vi đợt sửa này."""
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    _seed(job, ["Hôm nay dạy làm", "Trước hết thái thịt"])
    tts_stage.run_with(job, cfg_fixture, _MixedEngineAdapter())

    before = json.loads((job.tts_dir / "manifest.json").read_text(encoding="utf-8"))
    assert {e["id"]: e["engine"] for e in before["segments"]} == {
        1: "capcut", 2: "edge_tts_fallback",
    }

    # Mô phỏng `redo --from tts`: chỉ manifest.json bị xoá, wav + text_cache
    # còn nguyên nên cả hai câu đều cache-hit (text không đổi).
    (job.tts_dir / "manifest.json").unlink()
    tts_stage.run_with(job, cfg_fixture, _MixedEngineAdapter())

    after = json.loads((job.tts_dir / "manifest.json").read_text(encoding="utf-8"))
    entries = {e["id"]: e["engine"] for e in after["segments"]}
    # Đúng ra câu 2 vẫn phải là "edge_tts_fallback" — nhưng vì manifest cũ đã
    # bị xoá, `_load_prev_engines` trả rỗng, và `reuse_cached` mặc định
    # "capcut". Assertion dưới đây ghi nhận đúng hiện trạng (lỗ hổng còn mở).
    assert entries == {1: "capcut", 2: "capcut"}


def test_write_manifest_writes_text_cache(tmp_path: Path, cfg_fixture):
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    _seed(job, ["Hôm nay dạy làm"])

    _run(job, cfg_fixture)

    cache = json.loads((job.tts_dir / "text_cache.json").read_text(encoding="utf-8"))
    assert cache == {"1": tts_stage._hash_text("Hôm nay dạy làm")}


def test_reads_pronunciation_json_when_present(tmp_path: Path, cfg_fixture):
    """Khi có tts/pronunciation.json, tts.py phải lấy tts_text thay vì text gốc."""
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    _seed(job, ["Hello các bạn", "Số 0912345678"])

    # Ghi pronunciation.json giả lập do stage pronounce sinh ra
    pron = {
        "1": "Hê lô các bạn",
        "2": "Số không chín một hai ba bốn năm sáu bảy tám",
    }
    (job.tts_dir / "pronunciation.json").write_text(
        json.dumps(pron, ensure_ascii=False), encoding="utf-8"
    )

    class TextCapturingAdapter:
        def __init__(self):
            self.captured_texts = []

        def synthesize_batch(self, items, lang, voice):
            from reup.media.audio import silence
            results = []
            for text, out in items:
                self.captured_texts.append(text)
                silence(out, 300)
                results.append(TTSResult(path=out, actual_ms=300, engine="capcut"))
            return results

    adapter = TextCapturingAdapter()
    tts_stage.run_with(job, cfg_fixture, adapter)

    assert adapter.captured_texts == [
        "Hê lô các bạn",
        "Số không chín một hai ba bốn năm sáu bảy tám",
    ]
    # Cache ghi hash của từ phiên âm
    cache = json.loads((job.tts_dir / "text_cache.json").read_text(encoding="utf-8"))
    assert cache["1"] == tts_stage._hash_text("Hê lô các bạn")


def test_fallback_warning_sent_to_notifier(tmp_path: Path, cfg_fixture):
    """Khi có câu fallback sang edge-tts, notifier nhận cảnh báo tts_fallback_warning."""
    job = create_job(tmp_path / "jobs", "https://a/1", "zh", job_id="j1")
    _seed(job, ["Hôm nay dạy làm", "Trước hết thái thịt"])

    class FakeNotifier:
        def __init__(self):
            self.warnings = []

        def tts_fallback_warning(self, j, ids):
            self.warnings.append((j.id, ids))

    notifier = FakeNotifier()
    tts_stage.run_with(job, cfg_fixture, _MixedEngineAdapter(), notifier=notifier)

    assert len(notifier.warnings) == 1
    assert notifier.warnings[0] == ("j1", [2])
