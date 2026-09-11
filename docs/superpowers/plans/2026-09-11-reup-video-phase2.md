# reup-video Phase 2 — Giọng Việt thật

**Goal:** Video ra có **giọng tiếng Việt thật** đọc **bản dịch thật**, thay cho
StubTTS im lặng và transcript gốc của phase 1.

**Spec:** `docs/superpowers/specs/2026-09-11-reup-video-design.md`
**Phase trước:** `2026-09-11-reup-video-phase1.md` (18 task, 172 test, đã xong)

---

## Khảo sát source CapCut — đo thật, trước khi viết dòng nào

Mọi quyết định dưới đây đến từ việc chạy thử `capcut-tts-api` chứ không phải suy đoán.
Ba phát hiện làm đổi thiết kế:

| # | Phát hiện | Hệ quả |
|---|---|---|
| 1 | TTS chạy tốt. 10 âm tiết → 2.28s = **~225 ms/âm tiết**, khớp con số 4.5 âm tiết/giây trong spec | StubTTS đoán 220ms là gần đúng; `fit` không cần đổi hằng số |
| 2 | **`--rate` không có tác dụng.** rate 1.5 chỉ ngắn hơn 5.9% (225.6 → 212.4 ms/âm tiết), ngang dao động giữa các câu | Spec §7.7 mất bậc ưu tiên đầu. Còn hai bậc: **viết lại → `atempo`** |
| 3 | Server **cache theo text, bỏ qua rate** (`hit_cache=true`); gọi lại cùng câu trả file cũ giống từng byte | Đổi độ dài chỉ có thể bằng **đổi chữ**. Mặt tốt: chạy lại job không tốn lượt gọi |
| 4 | Ra **mp3 24kHz mono**, không phải wav | Cần khâu chuyển sang wav 48k stereo trước khi vào `build_timeline` |
| 5 | API gãy `KeyError: 'tasks'` sau ~15 request liên tiếp | Adapter bắt buộc có retry + backoff |
| 6 | `generate_audio.py` **tự rơi xuống edge-tts** khi CapCut lỗi — đổi cả giọng lẫn bitrate (160kbps → 48kbps) mà không báo | **Không dùng** `generate_audio.py`. Tự lái `capcut_common_task_client` để lỗi nổi lên |
| 7 | `Voice.json` có 22 giọng, **tất cả `lan: "vi"`** | `voices("en")` trả rỗng — đúng, vì đầu ra luôn tiếng Việt |

### Ràng buộc: venv của CapCut là Python 3.9

`capcut-tts-api/.venv` chạy 3.9. Driver script của ta chạy **bằng interpreter đó**
nên phải viết cú pháp 3.9: không `X | Y`, không `match`, không generic builtin.
Phần còn lại của repo vẫn là 3.12.

---

## Global Constraints

- Dependency mới **chỉ gồm** `google-genai`. CapCut đi qua subprocess, không import.
- `capcut-tts-api/` là repo riêng, **không** commit vào đây. Đường dẫn nằm trong config.
- **Không gọi mạng trong test.** LLM dùng cassette, TTS dùng StubTTS hoặc cassette.
- Giữ nguyên quy tắc phụ thuộc: `web` → `core` → `adapters`/`media`; `media` không import `core`.
- Mọi artifact vẫn ghi nguyên tử.
- Commit sau mỗi task.

---

## Kiến trúc: chỗ nào đổi

```
src/reup/
  translate.py              MỚI  ngân sách âm tiết + dựng prompt (hàm thuần)
  adapters/
    llm.py                  MỚI  LLMAdapter Protocol
    gemini.py               MỚI  GeminiLLM
    cassette_llm.py         MỚI  ghi/phát lại, cho test
    asr.py                  MỚI  ASRAdapter Protocol
    whisper_asr.py          MỚI  bọc media/whisper.py
    capcut_stt.py           MỚI  CapCut STT
    capcut_tts.py           MỚI  CapCutTTS
    capcut_driver.py        MỚI  script chạy bằng venv 3.9 của CapCut
    registry.py             MỚI  chọn implement theo config
  stages/
    translate.py            MỚI  stage 9
    tts.py                  SỬA  đọc translation.json, giọng vi, adapter theo config
    fit.py                  SỬA  vòng viết lại thật
    asr.py                  SỬA  dùng ASRAdapter
```

Tám task. Mỗi task một commit, test trước code.

---

## Task 1: Ngân sách âm tiết (hàm thuần)

- Create: `src/reup/translate.py` · Test: `tests/test_translate_budget.py`
- Produces: `syllable_budget(slot_ms: int, rate: float = 4.5) -> int`,
  `fits_budget(text: str, budget: int) -> bool`, `budget_report(...)`

Tiếng Việt ~4.5 âm tiết/giây (đo thật: 225 ms/âm tiết → 4.44/giây). Trần cho một
khe = `slot_ms / 1000 * 4.5`, làm tròn xuống, tối thiểu 1.

## Task 2: LLMAdapter + cassette

- Create: `src/reup/adapters/llm.py`, `src/reup/adapters/cassette_llm.py`
- Test: `tests/test_cassette_llm.py`
- Produces: Protocol `LLMAdapter.complete_json(prompt, schema) -> dict`;
  `CassetteLLM(path, fallback=None)` — khoá theo sha256 của prompt, đọc lại từ
  file JSON; thiếu bản ghi thì ném lỗi rõ ràng (không âm thầm gọi mạng).

## Task 3: GeminiLLM

- Create: `src/reup/adapters/gemini.py` · Test: `tests/test_gemini_adapter.py`
- Đọc key từ `GEMINI_API_KEY`. Thiếu key → lỗi nói rõ phải đặt biến nào.
- Retry 3 lần, backoff lũy thừa (spec §12). JSON sai → thử lại 2 lần kèm thông báo lỗi.
- Test **không gọi mạng**: giả `client`.

## Task 4: Stage translate

- Create: `src/reup/stages/translate.py` · Test: `tests/test_stage_translate.py`
- Đọc `transcript.json` → ghi `translation.json`.
- Mỗi đoạn kèm câu trước/sau làm ngữ cảnh (spec §7.6).
- Đoạn vượt ngân sách → cờ `over_budget`, **không** tự cắt.
- Ghi `slot_ms`, `syllable_budget`, `syllables`, `revision`, `flags`.

## Task 5: Driver CapCut + CapCutTTS

- Create: `src/reup/adapters/capcut_driver.py` (Python 3.9), `src/reup/adapters/capcut_tts.py`
- Test: `tests/test_capcut_tts.py` (giả subprocess, **không** gọi mạng)
- Driver: `tts-new` → poll `tts-query` → tải mp3 → in JSON `{path, duration_ms, hit_cache}`.
  Không fallback edge-tts.
- `CapCutTTS.synthesize` gọi driver, chuyển mp3 → wav 48k stereo, đo lại bằng ffprobe.
- `voices(lang)` đọc `Voice.json`.
- Retry + backoff cho phát hiện #5.

## Task 6: Registry chọn adapter theo config

- Create: `src/reup/adapters/registry.py` · Test: `tests/test_registry.py`
- Config mới: `[tts] engine/voice/capcut_dir`, `[llm] provider/model`, `[asr] engine`.
- `stages/tts.py` đổi sang `translation.json`, ngôn ngữ `vi`, giọng từ config.

## Task 7: Vòng viết lại thật trong fit

- Sửa: `src/reup/stages/fit.py` · Test: `tests/test_stage_fit_rewrite.py`
- `decide_fit` trả `"rewrite"` → gọi translate viết lại ngắn hơn, tổng hợp lại,
  đo lại. Tối đa 2 lần (`MAX_REVISIONS`), rồi `atempo` trần 1.25, rồi `overflow`.
- **Không** dùng `rate` (phát hiện #2).
- Đoạn nào không đổi chữ thì không gọi TTS lại (cache của ta, và của server).

## Task 8: ASRAdapter + CapCut STT

- Create: `src/reup/adapters/asr.py`, `whisper_asr.py`, `capcut_stt.py`
- Test: `tests/test_asr_adapters.py`
- `media/whisper.py` giữ nguyên; `whisper_asr.py` chỉ bọc lại.
- `capcut_stt.py`: `stt-file` → `payload.utterances[]` có mốc từng từ.
- Chọn bằng `asr.engine`.

---

## Xong phase 2 thì có gì

`reup run <job>` cho ra mp4 có giọng Việt thật đọc bản dịch thật, khớp vào đúng
khe thời gian của câu gốc. Còn thiếu (phase 3+): blur sub cũ, sub Việt đè lên,
Demucs tách nhạc, Web UI duyệt.
