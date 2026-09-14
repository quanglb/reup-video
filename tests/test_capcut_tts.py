import json
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from reup.adapters.capcut_tts import CapCutTTS, CapCutError
from reup.media.audio import duration_ms


@pytest.fixture
def capcut_dir(tmp_path: Path) -> Path:
    """Thư mục CapCut giả: chỉ cần Voice.json và một .venv/bin/python."""
    d = tmp_path / "capcut"
    (d / ".venv" / "bin").mkdir(parents=True)
    (d / ".venv" / "bin" / "python").write_text("#!/bin/sh\n", encoding="utf-8")
    (d / "Voice.json").write_text(
        json.dumps(
            [
                {
                    "lan": "vi", "lang": "vi-VN", "voice_type": "BV074_streaming",
                    "display_name": "Cô Gái Hoạt Ngôn", "resource_id": "7102355709945188865",
                },
                {
                    "lan": "vi", "lang": "vi-VN", "voice_type": "vi_female_huong",
                    "display_name": "Giọng Nữ Phổ Thông", "resource_id": "7264854897953083905",
                },
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return d


def _fake_driver(monkeypatch, mp3_source: Path, *, fail_times: int = 0):
    """Giả subprocess: chép sẵn một mp3 thật rồi in JSON như driver."""
    state = {"calls": 0, "cmds": []}
    real_run = subprocess.run

    def fake_run(cmd, **kwargs):
        # chỉ chặn driver; ffmpeg/ffprobe phải chạy thật để đo được wav
        if "capcut_driver.py" not in " ".join(str(c) for c in cmd):
            return real_run(cmd, **kwargs)
        state["calls"] += 1
        state["cmds"].append(cmd)
        if state["calls"] <= fail_times:
            return subprocess.CompletedProcess(cmd, 1, "", "CapCut báo lỗi: quá tải")

        outs = []
        for i, arg in enumerate(cmd):
            if arg == "--out" and i + 1 < len(cmd):
                outs.append(Path(cmd[i + 1]))

        results = []
        for out in outs:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(mp3_source.read_bytes())
            results.append({
                "path": str(out),
                "bytes": out.stat().st_size,
                "duration_ms": 2000,
                "hit_cache": False,
                "engine": "capcut",
            })

        payload = json.dumps(results)
        return subprocess.CompletedProcess(cmd, 0, payload, "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    return state


@pytest.fixture
def sample_mp3(tmp_path: Path) -> Path:
    """mp3 24kHz mono 2 giây — đúng thứ CapCut trả về."""
    out = tmp_path / "voice.mp3"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
         "-i", "sine=frequency=300:duration=2:sample_rate=24000",
         "-ac", "1", "-c:a", "libmp3lame", str(out)],
        check=True, capture_output=True,
    )
    return out


def test_lists_vietnamese_voices_from_voice_json(capcut_dir: Path):
    voices = CapCutTTS(capcut_dir).voices("vi")
    assert len(voices) == 2
    assert {v.id for v in voices} == {"BV074_streaming", "vi_female_huong"}
    assert all(v.lang == "vi" for v in voices)


def test_no_english_voices_exist(capcut_dir: Path):
    """Voice.json thật chỉ có 22 giọng vi — đầu ra của pipeline luôn tiếng Việt."""
    assert CapCutTTS(capcut_dir).voices("en") == []


def test_synthesize_converts_mp3_to_48k_stereo_wav(
    capcut_dir: Path, tmp_path: Path, monkeypatch, sample_mp3: Path
):
    """Pipeline dựng timeline bằng wav 48k stereo; CapCut trả mp3 24k mono."""
    _fake_driver(monkeypatch, sample_mp3)
    out = tmp_path / "seg_0001.wav"

    result = CapCutTTS(capcut_dir).synthesize("Hôm nay", "vi", "BV074_streaming", out)

    assert out.exists()
    assert result.path == out
    from reup.media.ffmpeg import probe
    info = probe(out)
    assert info.has_audio is True
    assert abs(duration_ms(out) - 2000) <= 100


def test_actual_ms_is_measured_from_the_wav_not_trusted_from_api(
    capcut_dir: Path, tmp_path: Path, monkeypatch, sample_mp3: Path
):
    """fit quyết định dựa trên actual_ms, nên phải là số đo thật của file."""
    _fake_driver(monkeypatch, sample_mp3)
    result = CapCutTTS(capcut_dir).synthesize(
        "x", "vi", "BV074_streaming", tmp_path / "a.wav"
    )
    assert abs(result.actual_ms - duration_ms(result.path)) <= 1


def test_rejects_unknown_voice(capcut_dir: Path, tmp_path: Path):
    with pytest.raises(ValueError, match="khong-co"):
        CapCutTTS(capcut_dir).synthesize("x", "vi", "khong-co", tmp_path / "a.wav")


def test_passes_resource_id_matching_the_voice(
    capcut_dir: Path, tmp_path: Path, monkeypatch, sample_mp3: Path
):
    state = _fake_driver(monkeypatch, sample_mp3)
    CapCutTTS(capcut_dir).synthesize("x", "vi", "vi_female_huong", tmp_path / "a.wav")
    cmd = state["cmds"][0]
    assert cmd[cmd.index("--resource-id") + 1] == "7264854897953083905"
    assert cmd[cmd.index("--voice") + 1] == "vi_female_huong"


def test_never_sends_a_rate_other_than_one(
    capcut_dir: Path, tmp_path: Path, monkeypatch, sample_mp3: Path
):
    """rate của CapCut không có tác dụng (spec §7.7) — đừng giả vờ là có."""
    state = _fake_driver(monkeypatch, sample_mp3)
    CapCutTTS(capcut_dir).synthesize("x", "vi", "BV074_streaming", tmp_path / "a.wav")
    cmd = state["cmds"][0]
    assert cmd[cmd.index("--rate") + 1] == "1.0"


def test_retries_on_transient_failure(
    capcut_dir: Path, tmp_path: Path, monkeypatch, sample_mp3: Path
):
    """API gãy sau ~15 request liên tiếp — phải thử lại, không bỏ cuộc ngay."""
    state = _fake_driver(monkeypatch, sample_mp3, fail_times=2)
    tts = CapCutTTS(capcut_dir, sleep=lambda s: None)
    result = tts.synthesize("x", "vi", "BV074_streaming", tmp_path / "a.wav")
    assert state["calls"] == 3
    assert result.actual_ms > 0


def test_gives_up_after_retry_budget(
    capcut_dir: Path, tmp_path: Path, monkeypatch, sample_mp3: Path
):
    _fake_driver(monkeypatch, sample_mp3, fail_times=99)
    tts = CapCutTTS(capcut_dir, sleep=lambda s: None, retries=3)
    with pytest.raises(CapCutError, match="quá tải|3 lần"):
        tts.synthesize("x", "vi", "BV074_streaming", tmp_path / "a.wav")


def test_backoff_grows(capcut_dir: Path, tmp_path: Path, monkeypatch, sample_mp3: Path):
    waits: list[float] = []
    _fake_driver(monkeypatch, sample_mp3, fail_times=2)
    CapCutTTS(capcut_dir, sleep=waits.append).synthesize(
        "x", "vi", "BV074_streaming", tmp_path / "a.wav"
    )
    assert len(waits) == 2
    assert waits[1] > waits[0]


def test_missing_capcut_dir_fails_with_a_useful_message(tmp_path: Path):
    with pytest.raises(CapCutError, match="không thấy"):
        CapCutTTS(tmp_path / "khong-co").voices("vi")


def test_timeout_computed_from_max_polls_and_poll_interval(
    capcut_dir: Path, tmp_path: Path, monkeypatch, sample_mp3: Path
):
    """Timeout ngoài tính từ max_polls * poll_interval + (10 + N*2)."""
    timeouts_captured = []
    real_run = subprocess.run

    def capture_timeout(cmd, **kwargs):
        if "capcut_driver.py" in " ".join(str(c) for c in cmd):
            timeouts_captured.append(kwargs.get("timeout"))
            out = Path(cmd[cmd.index("--out") + 1])
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(sample_mp3.read_bytes())
            payload = json.dumps([{
                "path": str(out), "bytes": out.stat().st_size,
                "duration_ms": 2000, "hit_cache": False, "engine": "capcut",
            }])
            return subprocess.CompletedProcess(cmd, 0, payload, "")
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(subprocess, "run", capture_timeout)

    # 1 item: max_polls=10, poll_interval=1.0 -> 10*1.0 + 10 + 1*2 = 22.0
    tts = CapCutTTS(capcut_dir, max_polls=10, poll_interval=1.0)
    tts.synthesize("test", "vi", "BV074_streaming", tmp_path / "a.wav")
    assert len(timeouts_captured) == 1
    assert timeouts_captured[0] == 22.0


def test_timeout_scales_with_poll_interval(
    capcut_dir: Path, tmp_path: Path, monkeypatch, sample_mp3: Path
):
    """Khi poll_interval thay đổi, timeout phải thay đổi theo."""
    timeouts_captured = []
    real_run = subprocess.run

    def capture_timeout(cmd, **kwargs):
        if "capcut_driver.py" in " ".join(str(c) for c in cmd):
            timeouts_captured.append(kwargs.get("timeout"))
            out = Path(cmd[cmd.index("--out") + 1])
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(sample_mp3.read_bytes())
            payload = json.dumps([{
                "path": str(out), "bytes": out.stat().st_size,
                "duration_ms": 2000, "hit_cache": False, "engine": "capcut",
            }])
            return subprocess.CompletedProcess(cmd, 0, payload, "")
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(subprocess, "run", capture_timeout)

    # 1 item: max_polls=10, poll_interval=2.0 -> 10*2.0 + 10 + 1*2 = 32.0
    tts = CapCutTTS(capcut_dir, max_polls=10, poll_interval=2.0)
    tts.synthesize("test", "vi", "BV074_streaming", tmp_path / "b.wav")
    assert len(timeouts_captured) == 1
    assert timeouts_captured[0] == 32.0


def test_synthesize_batch_multiple_items(
    capcut_dir: Path, tmp_path: Path, monkeypatch, sample_mp3: Path
):
    state = _fake_driver(monkeypatch, sample_mp3)
    tts = CapCutTTS(capcut_dir)
    items = [
        ("Câu thứ nhất", tmp_path / "s1.wav"),
        ("Câu thứ hai", tmp_path / "s2.wav"),
        ("Câu thứ ba", tmp_path / "s3.wav"),
    ]
    results = tts.synthesize_batch(items, "vi", "BV074_streaming")
    assert len(results) == 3
    assert all(r.path.exists() for r in results)
    assert [r.path for r in results] == [tmp_path / "s1.wav", tmp_path / "s2.wav", tmp_path / "s3.wav"]
    assert state["calls"] == 1
    cmd = state["cmds"][0]
    # Kiểm tra --text và --out xuất hiện 3 lần
    texts_in_cmd = [cmd[i + 1] for i, arg in enumerate(cmd) if arg == "--text"]
    assert len(texts_in_cmd) == 3
    assert texts_in_cmd == ["Câu thứ nhất", "Câu thứ hai", "Câu thứ ba"]


def test_synthesize_batch_timeout_scales_with_batch_size(
    capcut_dir: Path, tmp_path: Path, monkeypatch, sample_mp3: Path
):
    timeouts_captured = []
    real_run = subprocess.run

    def capture_timeout(cmd, **kwargs):
        if "capcut_driver.py" in " ".join(str(c) for c in cmd):
            timeouts_captured.append(kwargs.get("timeout"))
            outs = [Path(cmd[i + 1]) for i, arg in enumerate(cmd) if arg == "--out"]
            results = []
            for out in outs:
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(sample_mp3.read_bytes())
                results.append({"path": str(out), "bytes": 100, "duration_ms": 2000, "hit_cache": False, "engine": "capcut"})
            return subprocess.CompletedProcess(cmd, 0, json.dumps(results), "")
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(subprocess, "run", capture_timeout)
    tts = CapCutTTS(capcut_dir, max_polls=10, poll_interval=1.0)
    items = [(f"câu {i}", tmp_path / f"seg_{i}.wav") for i in range(8)]
    tts.synthesize_batch(items, "vi", "BV074_streaming")
    # timeout: 10*1.0 + (10 + 8*2) = 36.0
    assert len(timeouts_captured) == 1
    assert timeouts_captured[0] == 36.0


def test_synthesize_batch_empty_text_handled_locally(
    capcut_dir: Path, tmp_path: Path, monkeypatch, sample_mp3: Path
):
    state = _fake_driver(monkeypatch, sample_mp3)
    tts = CapCutTTS(capcut_dir)
    items = [
        ("Câu một hợp lệ", tmp_path / "v1.wav"),
        ("   ...   ", tmp_path / "v2.wav"),
        ("Câu ba hợp lệ", tmp_path / "v3.wav"),
    ]
    results = tts.synthesize_batch(items, "vi", "BV074_streaming")
    assert len(results) == 3
    assert results[0].engine == "capcut"
    assert results[1].engine == "silence"
    assert results[2].engine == "capcut"
    assert state["calls"] == 1
    cmd = state["cmds"][0]
    texts_in_cmd = [cmd[i + 1] for i, arg in enumerate(cmd) if arg == "--text"]
    assert texts_in_cmd == ["Câu một hợp lệ", "Câu ba hợp lệ"]


def test_synthesize_batch_all_empty_does_not_call_driver(
    capcut_dir: Path, tmp_path: Path, monkeypatch
):
    state = {"calls": 0}
    real_run = subprocess.run
    def fake_run(cmd, **kwargs):
        if "capcut_driver.py" in " ".join(str(c) for c in cmd):
            state["calls"] += 1
            return subprocess.CompletedProcess(cmd, 0, "[]", "")
        return real_run(cmd, **kwargs)
    monkeypatch.setattr(subprocess, "run", fake_run)

    tts = CapCutTTS(capcut_dir)
    items = [("   ", tmp_path / "e1.wav"), ("...", tmp_path / "e2.wav")]
    results = tts.synthesize_batch(items, "vi", "BV074_streaming")
    assert len(results) == 2
    assert all(r.engine == "silence" for r in results)
    assert state["calls"] == 0


def test_synthesize_batch_fails_entire_batch_falls_back_to_single(
    capcut_dir: Path, tmp_path: Path, monkeypatch, sample_mp3: Path
):
    """Khi batch nhiều câu lỗi sau các lần retry, tự động fallback gọi từng câu một."""
    calls = []
    real_run = subprocess.run

    def fake_run(cmd, **kwargs):
        if "capcut_driver.py" not in " ".join(str(c) for c in cmd):
            return real_run(cmd, **kwargs)
        outs = [Path(cmd[i + 1]) for i, arg in enumerate(cmd) if arg == "--out"]
        calls.append(len(outs))
        if len(outs) > 1:
            # Giả lập batch fail
            return subprocess.CompletedProcess(cmd, 1, "", "Batch fail lỗi toàn bộ")
        # Single item succeed
        out = outs[0]
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(sample_mp3.read_bytes())
        return subprocess.CompletedProcess(
            cmd, 0, json.dumps([{"path": str(out), "bytes": 100, "duration_ms": 2000, "hit_cache": False, "engine": "capcut"}]), ""
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    tts = CapCutTTS(capcut_dir, sleep=lambda s: None, retries=2)
    items = [
        ("Câu A", tmp_path / "a.wav"),
        ("Câu B", tmp_path / "b.wav"),
    ]
    results = tts.synthesize_batch(items, "vi", "BV074_streaming")
    assert len(results) == 2
    assert all(r.path.exists() for r in results)
    # Lần đầu batch 2 câu (thử 2 lần retry), sau đó fallback 2 lần gọi single (1 câu mỗi lần)
    assert calls.count(2) == 2
    assert calls.count(1) == 2


def test_pauses_every_pause_every_successful_calls(
    capcut_dir: Path, tmp_path: Path, monkeypatch, sample_mp3: Path
):
    """20 lần gọi liên tiếp, pause_every=12 → chỉ nghỉ đúng 1 lần, ở lần thứ 12."""
    _fake_driver(monkeypatch, sample_mp3)
    sleeps: list[float] = []
    tts = CapCutTTS(capcut_dir, sleep=sleeps.append, pause_every=12, pause_seconds=2.0)

    for i in range(20):
        tts.synthesize("x", "vi", "BV074_streaming", tmp_path / f"seg_{i}.wav")
        if i + 1 == 12:
            assert sleeps == [2.0]
        else:
            assert len(sleeps) == (1 if i + 1 >= 12 else 0)

    assert sleeps == [2.0]


def test_no_pause_before_reaching_pause_every(
    capcut_dir: Path, tmp_path: Path, monkeypatch, sample_mp3: Path
):
    _fake_driver(monkeypatch, sample_mp3)
    sleeps: list[float] = []
    tts = CapCutTTS(capcut_dir, sleep=sleeps.append, pause_every=12, pause_seconds=2.0)

    for i in range(11):
        tts.synthesize("x", "vi", "BV074_streaming", tmp_path / f"seg_{i}.wav")

    assert sleeps == []


def test_empty_text_early_return_does_not_count_toward_pause(
    capcut_dir: Path, tmp_path: Path, monkeypatch
):
    """Đường im lặng cho text rỗng không gọi driver, không nên tính vào bộ đếm."""
    sleeps: list[float] = []
    tts = CapCutTTS(capcut_dir, sleep=sleeps.append, pause_every=1, pause_seconds=2.0)

    tts.synthesize("   ", "vi", "BV074_streaming", tmp_path / "a.wav")

    assert sleeps == []


def test_empty_text_silence_is_labelled_neither_capcut_nor_fallback(
    capcut_dir: Path, tmp_path: Path
):
    """Đường im lặng cho text rỗng không gọi driver — không phải CapCut thật,
    không phải edge-tts fallback. Không được để rơi vào default "capcut" của
    TTSResult, kẻo manifest ghi sai là audio CapCut thật (cùng tinh thần bug
    mà task này sửa)."""
    result = CapCutTTS(capcut_dir).synthesize(
        "   ", "vi", "BV074_streaming", tmp_path / "a.wav"
    )
    assert result.engine == "silence"


def test_pause_counter_is_thread_safe_under_concurrent_calls(
    capcut_dir: Path, tmp_path: Path, monkeypatch, sample_mp3: Path
):
    """Bộ đếm nghỉ nhịp thread-safe."""
    _fake_driver(monkeypatch, sample_mp3)
    lock = threading.Lock()
    sleeps: list[float] = []

    def recording_sleep(seconds: float) -> None:
        with lock:
            sleeps.append(seconds)

    pause_every = 5
    n_calls = 47
    tts = CapCutTTS(
        capcut_dir, sleep=recording_sleep, pause_every=pause_every, pause_seconds=1.0
    )

    def call(i: int) -> None:
        tts.synthesize("x", "vi", "BV074_streaming", tmp_path / f"c_{i}.wav")

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(call, range(n_calls)))

    assert tts._request_count == n_calls
    assert len(sleeps) == n_calls // pause_every
    assert all(s == 1.0 for s in sleeps)


def test_engine_field_defaults_to_capcut_when_driver_omits_it(
    capcut_dir: Path, tmp_path: Path, monkeypatch, sample_mp3: Path
):
    """Driver cũ không có trường `engine` trong JSON -> default 'capcut'."""
    state = {"calls": 0}
    real_run = subprocess.run
    def fake_run(cmd, **kwargs):
        if "capcut_driver.py" not in " ".join(str(c) for c in cmd):
            return real_run(cmd, **kwargs)
        state["calls"] += 1
        out = Path(cmd[cmd.index("--out") + 1])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(sample_mp3.read_bytes())
        payload = json.dumps({"path": str(out), "bytes": 100, "duration_ms": 2000, "hit_cache": False})
        return subprocess.CompletedProcess(cmd, 0, payload, "")
    monkeypatch.setattr(subprocess, "run", fake_run)

    result = CapCutTTS(capcut_dir).synthesize(
        "x", "vi", "BV074_streaming", tmp_path / "a.wav"
    )
    assert result.engine == "capcut"


def test_edge_tts_fallback_engine_is_surfaced_not_hidden(
    capcut_dir: Path, tmp_path: Path, monkeypatch, sample_mp3: Path, caplog
):
    """Bug thật: driver âm thầm rơi xuống edge-tts mà caller không hề biết.
    `engine == "edge_tts_fallback"` từ JSON của driver phải nổi lên tới
    TTSResult, và phải có cảnh báo qua logging — không được nuốt thầm lặng."""
    state = {"calls": 0}
    real_run = subprocess.run

    def fake_run(cmd, **kwargs):
        if "capcut_driver.py" not in " ".join(str(c) for c in cmd):
            return real_run(cmd, **kwargs)
        state["calls"] += 1
        out = Path(cmd[cmd.index("--out") + 1])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(sample_mp3.read_bytes())
        payload = json.dumps([{
            "path": str(out), "bytes": out.stat().st_size,
            "duration_ms": 0, "hit_cache": False,
            "engine": "edge_tts_fallback",
        }])
        return subprocess.CompletedProcess(cmd, 0, payload, "")

    monkeypatch.setattr(subprocess, "run", fake_run)

    import logging
    caplog.set_level(logging.WARNING, logger="reup.adapters.capcut_tts")

    result = CapCutTTS(capcut_dir).synthesize(
        "câu bị đổi giọng", "vi", "BV074_streaming", tmp_path / "a.wav"
    )

    assert result.engine == "edge_tts_fallback"
    assert any("edge" in rec.message.lower() for rec in caplog.records)
    assert any("câu bị đổi giọng" in rec.message for rec in caplog.records)


def test_allow_edge_fallback_false_is_passed_to_driver(
    capcut_dir: Path, tmp_path: Path, monkeypatch, sample_mp3: Path
):
    state = _fake_driver(monkeypatch, sample_mp3)
    CapCutTTS(capcut_dir, allow_edge_fallback=False).synthesize(
        "x", "vi", "BV074_streaming", tmp_path / "a.wav"
    )
    cmd = state["cmds"][0]
    assert cmd[cmd.index("--allow-fallback") + 1] == "0"


def test_allow_edge_fallback_true_by_default(
    capcut_dir: Path, tmp_path: Path, monkeypatch, sample_mp3: Path
):
    state = _fake_driver(monkeypatch, sample_mp3)
    CapCutTTS(capcut_dir).synthesize("x", "vi", "BV074_streaming", tmp_path / "a.wav")
    cmd = state["cmds"][0]
    assert cmd[cmd.index("--allow-fallback") + 1] == "1"


def test_allow_edge_fallback_false_and_driver_error_raises_capcuterror(
    capcut_dir: Path, tmp_path: Path, monkeypatch
):
    """tts.allow_edge_fallback = false + driver báo lỗi → CapCutError thật,
    không rơi vào audio giả — spec mục 0.0.c / Task 1.5."""
    real_run = subprocess.run

    def fake_run(cmd, **kwargs):
        if "capcut_driver.py" not in " ".join(str(c) for c in cmd):
            return real_run(cmd, **kwargs)
        assert cmd[cmd.index("--allow-fallback") + 1] == "0"
        return subprocess.CompletedProcess(
            cmd, 1, "", "CapCut báo lỗi: quá tải, và fallback bị tắt"
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    tts = CapCutTTS(
        capcut_dir, sleep=lambda s: None, retries=2, allow_edge_fallback=False
    )
    with pytest.raises(CapCutError, match="fallback bị tắt"):
        tts.synthesize("x", "vi", "BV074_streaming", tmp_path / "a.wav")


def test_max_polls_passed_to_driver(
    capcut_dir: Path, tmp_path: Path, monkeypatch, sample_mp3: Path
):
    """Argument --max-polls phải được truyền cho driver."""
    state = _fake_driver(monkeypatch, sample_mp3)
    tts = CapCutTTS(capcut_dir, max_polls=5)
    tts.synthesize("test", "vi", "BV074_streaming", tmp_path / "c.wav")
    cmd = state["cmds"][0]
    assert "--max-polls" in cmd
    assert cmd[cmd.index("--max-polls") + 1] == "5"
