# Thiết kế: reup-video — pipeline dịch & lồng tiếng video ngắn

Ngày: 2026-09-11
Trạng thái: đã duyệt qua brainstorming, chờ viết implementation plan

---

## 1. Mục tiêu

Biến một video ngắn tiếng Trung hoặc tiếng Anh thành video tiếng Việt hoàn chỉnh:
phụ đề gốc bị che, phụ đề tiếng Việt đè lên, giọng đọc tiếng Việt thay cho giọng gốc,
nhạc nền và tiếng động gốc được giữ lại.

Đầu vào: link video (thủ công) hoặc kết quả quét trending.
Đầu ra: file `.mp4` 9:16 cùng metadata gợi ý (tiêu đề, mô tả, hashtag), nằm trong thư mục output.

Người dùng duy nhất là chủ dự án, chạy trên máy Mac cá nhân.

## 2. Ngoài phạm vi

- **Không tự động đăng bài.** Pipeline dừng ở file; đăng lên YouTube Shorts / TikTok làm thủ công.
  Lý do: hạn mức YouTube Data API chỉ cho ~6 upload/ngày, và TikTok Content Posting API
  bắt duyệt app mới cho đăng công khai. Cả hai là rào cản ngoài tầm kiểm soát của code.
- **Không cắt clip từ video dài.** Phase 1 chỉ nhận nguồn vốn đã ngắn và 9:16
  (Douyin, TikTok, YouTube Shorts). Việc chọn đoạn hay trong video dài và crop về 9:16
  là một hệ thống riêng, sẽ có spec riêng.
- **Không inpaint bằng AI.** Che phụ đề gốc bằng blur, không dùng model xóa vật thể (xem 7.2).
- **Không deploy server.** Chạy local.

## 3. Ràng buộc và giả định

| | |
|---|---|
| Cặp ngôn ngữ | Trung → Việt, Anh → Việt |
| Định dạng đích | 9:16, dưới ~3 phút |
| Khối lượng | ~5-20 video/ngày |
| Máy | MacBook Air M4 16GB **và** máy M4 24GB — cùng codebase, khác profile |
| Hệ điều hành | macOS (phụ thuộc Apple Vision cho OCR — xem rủi ro R4) |
| Mức tự động | Có chốt duyệt của người trước khi render |

## 4. Ngăn xếp kỹ thuật

| Thành phần | Lựa chọn | Lý do |
|---|---|---|
| Ngôn ngữ | Python 3.12 (venv riêng qua `uv`) | Demucs / Whisper / OCR đều là hệ sinh thái Python. Python hệ thống đang là 3.14, quá mới, nhiều package ML chưa có wheel |
| Tải video | `yt-dlp` | Hỗ trợ cả ba nền tảng |
| Tách nhạc/giọng | `demucs` (htdemucs) | Chất lượng tốt nhất trong nhóm chạy được local |
| ASR | `mlx-whisper`, model `large-v3-turbo` | Nhanh nhất trên Apple Silicon, dùng MPS |
| OCR | `ocrmac` (Apple Vision) | Hỗ trợ tiếng Trung, chạy trên Neural Engine, không phải cài PaddleOCR trên ARM |
| LLM | Gemini qua `google-genai` | Đã có sẵn API key ở dự án anh em |
| Dựng video | `ffmpeg` 8.x, encoder `h264_videotoolbox` | Một lượt filter duy nhất; VideoToolbox gần như không sinh nhiệt — thiết yếu cho Air không quạt |
| Phụ đề | ASS qua `libass` | Kiểm soát font/viền/vị trí, khác hẳn SRT |
| Web UI | FastAPI + Jinja2 + JS thuần | Không có bước build, không có node_modules |
| Lưu trạng thái | SQLite (`sqlite3` stdlib) | Đủ dùng cho một người |
| Test | pytest | |

## 5. Kiến trúc

Pipeline là chuỗi **stage rời nhau**. Mỗi stage đọc file trong thư mục job, ghi file mới vào đó,
rồi cập nhật trạng thái trong SQLite. Không stage nào gọi trực tiếp stage khác.

Hệ quả có chủ đích:
- Chạy lại được từ giữa. ASR sai thì chỉ chạy lại từ stage 5 trở đi, không phải tải lại video.
- Test được từng stage độc lập, chỉ cần dựng sẵn file đầu vào.
- Xem được toàn bộ trạng thái trung gian bằng cách mở file, không cần debugger.

### 5.1 Bốn module

```
core/       stage runner, job state, resume, sổ cái. Không biết gì về video.
adapters/   mọi thứ giao tiếp ra ngoài, mỗi cái một interface cố định.
media/      bọc ffmpeg / demucs / whisper / ocr. Hàm thuần, không biết job là gì.
web/        FastAPI + UI duyệt. Chỉ đọc ghi job state, không chứa logic xử lý.
```

Quy tắc phụ thuộc: `web` → `core` → `adapters` / `media`. Không có chiều ngược lại.
`media` không được import `core`.

### 5.2 Thư mục job

```
jobs/<job_id>/
  job.json              siêu dữ liệu job: nguồn, ngôn ngữ, cấu hình đã dùng
  source.mp4
  source.info.json      metadata do yt-dlp trả về
  audio/
    full_16k.wav        16kHz mono — đầu vào cho Demucs/ASR
    full_48k.wav        48kHz stereo — dùng khi mix lại
    vocals.wav
    bgm.wav             nhạc nền + tiếng động, đã bỏ giọng người
  asr.json
  ocr.json
  subrect.json
  transcript.json       ← hợp nhất ASR + OCR, nguồn sự thật
  translation.json
  tts/seg_0001.wav ...
  sub.ass
  render/final.mp4
  meta.json             tiêu đề / mô tả / hashtag
  log.jsonl             một dòng cho mỗi lần stage chạy
```

### 5.3 Mười ba stage

| # | Stage | Đọc | Ghi | Công cụ |
|---|---|---|---|---|
| 1 | `discover` | — | hàng đợi ứng viên | SourceAdapter |
| 2 | `fetch` | url | `source.mp4`, `source.info.json` | yt-dlp |
| 3 | `demux` | source.mp4 | `audio/full.wav` | ffmpeg |
| 4 | `separate` | full.wav | `vocals.wav`, `bgm.wav` | demucs |
| 5 | `asr` | vocals.wav | `asr.json` | mlx-whisper |
| 6 | `subdetect` | source.mp4 | `subrect.json` | Apple Vision + gom cụm |
| 7 | `ocr` | source.mp4 + subrect.json | `ocr.json` | Apple Vision |
| 8 | `reconcile` | asr.json + ocr.json | `transcript.json` | Gemini |
| 9 | `translate` | transcript.json | `translation.json` | Gemini |
| — | **CHỐT A** | | | Web UI |
| 10 | `tts` | translation.json | `tts/*.wav` | TTSAdapter |
| 11 | `fit` | tts/*.wav | `tts/*.wav` đã khớp, có thể quay lại 9 | ffmpeg |
| 12 | `compose` | tất cả | `render/final.mp4` | ffmpeg một lượt |
| 13 | `export` | final.mp4 | thư mục output + `meta.json` | Gemini sinh metadata |
| — | **CHỐT B** | | | Web UI (tùy chọn bỏ qua) |

Stage 6 và 7 không phụ thuộc stage 3-5, có thể chạy song song với nhánh audio.

## 6. Interface adapter

Mọi thứ ra ngoài đều qua một interface hẹp. Đổi nhà cung cấp = viết adapter mới, pipeline không đụng tới.

```python
class SourceAdapter(Protocol):
    name: str                       # "youtube" | "tiktok" | "douyin" | "manual"
    def list_trending(self, region: str, limit: int) -> list[Candidate]: ...
    def fetch(self, url: str, dest: Path) -> FetchResult: ...

class TTSAdapter(Protocol):
    def voices(self, lang: str) -> list[Voice]: ...
    def synthesize(self, text: str, lang: str, voice: str, out: Path) -> TTSResult: ...
    # TTSResult.actual_ms là bắt buộc — stage fit cần con số này

class LLMAdapter(Protocol):
    def complete_json(self, prompt: str, schema: dict) -> dict: ...
```

**TTS của CapCut nối vào đây.** Source code CapCut TTS đã có sẵn, sẽ được bọc thành
một implement của `TTSAdapter`. Trong lúc chưa nối, dùng `StubTTS`: sinh file wav
im lặng có độ dài suy ra từ số âm tiết × 220ms. Nhờ vậy toàn bộ pipeline và test
chạy được mà không cần CapCut.

## 7. Các quyết định kỹ thuật

### 7.1 Dò vùng phụ đề (stage 6)

Lấy mẫu 2 khung/giây, Apple Vision trả hộp bao mọi vùng có chữ, gom cụm theo vị trí.
Phân loại bằng tần suất và vị trí:

| Loại | Dấu hiệu | Xử lý |
|---|---|---|
| Phụ đề | 1/3 dưới, giữa ngang, hiện 40-90% thời lượng, **chữ đổi liên tục** | blur |
| Watermark | góc, nhỏ, hiện ~100%, **chữ không đổi** | blur |
| Chữ trong cảnh | vị trí không ổn định | bỏ qua |

`subrect.json` chứa vùng ổn định (hợp của các cụm, cộng đệm 8px) và các khoảng thời gian có sub.
Sub nhảy vị trí giữa video thì lấy hợp — thà blur rộng hơn cần thiết còn hơn sót chữ.

```json
{
  "video_w": 1080, "video_h": 1920,
  "regions": [
    {"x": 90, "y": 1480, "w": 900, "h": 180, "kind": "subtitle", "coverage": 0.72},
    {"x": 830, "y": 60, "w": 200, "h": 60, "kind": "watermark", "coverage": 0.99}
  ],
  "present_ranges": [[0, 3200], [3400, 7100]]
}
```

### 7.2 Che phụ đề cũ: blur, không inpaint

Phụ đề tiếng Việt mới sẽ đặt **đè đúng lên vùng phụ đề cũ**. Nên chỗ blur không cần đẹp,
vì nó bị che gần hết.

```
vùng sub gốc → boxblur mạnh + giảm sáng 25% → dán sub tiếng Việt lên trên
```

Vì vậy **không dùng inpaint**. Inpaint (ProPainter/STTN) cho kết quả đẹp hơn nhưng tốn
vài phút GPU mỗi clip — không đáng trên máy không quạt, trong khi phần lớn kết quả đó
bị phụ đề mới che mất.

Phụ đề mới render bằng ASS qua `libass`: viền dày sẽ ăn nốt phần rìa blur còn thò ra.

### 7.3 Thứ tự filter

Bẫy: `hflip` sau khi dán phụ đề sẽ lật ngược chữ tiếng Việt. Thứ tự bắt buộc:

```
blur vùng sub gốc  →  transform (zoom / crop / hflip)  →  dán sub Việt  →  encode
  ↑ tọa độ gốc        ↑ sub gốc lật theo, không sao       ↑ không bị lật
```

Gộp một lượt ffmpeg duy nhất, không xuất file trung gian:

```
-filter_complex "
  [0:v]split=2[base][region];
  [region]crop=W:H:X:Y,boxblur=20:2,eq=brightness=-0.25[blurred];
  [base][blurred]overlay=X:Y[cleaned];
  [cleaned]TRANSFORM[xf];
  [xf]ass=sub.ass[v];
  [1:a][2:a]amix=inputs=2:duration=first:weights='0.35 1'[a]
"
-map "[v]" -map "[a]" -c:v h264_videotoolbox -b:v 8M -c:a aac -b:a 192k
```

`TRANSFORM` được sinh ra từ mục `[transform]` trong config: chuỗi rỗng khi tắt hết,
hoặc ghép các filter đang bật (`scale`+`crop` cho zoom, `hflip`, `setpts` cho tốc độ).

Nhiều vùng cần blur thì lặp cặp `crop → boxblur → overlay` cho từng vùng.

### 7.4 Transform giảm rủi ro bản quyền

Bốn nút vặn, cấu hình được từng job: `hflip`, zoom 3-5%, đổi tốc độ 1.01-1.03x
(kèm `asetrate`/`atempo` bù cao độ), lệch màu nhẹ.

Xếp theo hiệu quả thật:

1. **Thay toàn bộ audio** — đòn chính. Fingerprint audio mạnh hơn fingerprint video nhiều.
2. **Blur vùng sub + xóa watermark** — thay đổi pixel thật ở vùng đặc trưng.
3. `hflip` / zoom / đổi tốc độ — **giá trị thấp**. Content ID chịu được các phép này.
   Vẫn để vì chúng gần như miễn phí, nhưng không nên tin vào chúng.

`hflip` **mặc định tắt**: video có chữ trong cảnh, người thuận tay, hay logo sản phẩm
thì lật xong nhìn sai rõ. Bật theo từng job.

### 7.5 Hợp nhất ASR và OCR (stage 8)

Không để Gemini tự do trộn hai văn bản — đó là cách nhanh nhất để nó bịa. Ràng buộc:

1. Ghép theo chồng lấn thời gian: dòng OCR nào phủ lên đoạn ASR nào.
2. Hai bên khớp trên 80% ký tự → **lấy chữ của OCR, lấy giờ của ASR**. Không gọi LLM.
3. Lệch nhiều → gửi cả hai cho Gemini, bắt **chọn một trong hai**, cấm viết mới.
   Gắn cờ `asr_ocr_mismatch` để UI tô đỏ.
4. OCR rỗng (video không có hardsub) → dùng thẳng ASR, bỏ qua Gemini.

```json
{
  "source_lang": "zh",
  "segments": [
    {"id": 1, "start_ms": 0, "end_ms": 3200,
     "text": "今天教大家做红烧肉",
     "text_source": "ocr", "confidence": 0.93, "flags": []}
  ]
}
```

### 7.6 Dịch có ngân sách âm tiết (stage 9)

Tiếng Việt đọc khoảng **4,5 âm tiết/giây**. Mỗi đoạn suy ra trần âm tiết từ độ dài khe
thời gian, đưa vào prompt Gemini theo từng câu. Prompt yêu cầu dịch tự nhiên **trong trần đó**,
không phải dịch xong rồi cắt.

```json
{
  "target_lang": "vi",
  "segments": [
    {"id": 1, "start_ms": 0, "end_ms": 3200,
     "slot_ms": 3200, "syllable_budget": 14,
     "text": "Hôm nay mình dạy mọi người làm thịt kho tàu",
     "syllables": 11, "revision": 0, "flags": []}
  ]
}
```

Gemini nhận cả câu trước và câu sau làm ngữ cảnh, để đại từ và văn phong nhất quán.

### 7.7 Vòng khớp thời lượng (stage 11)

TTS xong đo lại, so `dài thật / khe`:

| Tỉ lệ | Xử lý |
|---|---|
| ≤ 1.15 | `atempo` nén nhẹ — tai không nghe ra |
| 1.15 – 1.5 | bắt Gemini viết lại ngắn hơn, tối đa 2 lần; vẫn dài thì chấp nhận nén 1.25 |
| > 1.5 sau 2 lần | gắn cờ `overflow`, đưa job **trở lại trạng thái `needs_review`** ở chốt A |
| < 0.85 | chèn khoảng lặng ở cuối đoạn. **Không** làm chậm giọng — nghe lè nhè |

Ngân sách 2 lần viết lại là cứng, để một câu hỏng không kéo cả job vào vòng lặp vô tận.

Job bị đẩy ngược về chốt A giữ nguyên mọi file TTS đã sinh; duyệt lại chỉ chạy lại TTS
cho những đoạn có nội dung thay đổi. Trạng thái `needs_review` mang thêm trường
`reopened_from: "fit"` để UI hiển thị đúng lý do.

## 8. Web UI

### 8.1 Trang hàng đợi

Bảng job: ảnh thu nhỏ, nguồn, stage hiện tại, trạng thái, lỗi nếu có.
Lọc theo: đang chờ duyệt / đang chạy / lỗi / xong.

### 8.2 Chốt A — duyệt bản dịch

Sau stage 9, **trước khi** tốn TTS và render. Màn chia đôi: trái là video gốc phát được,
phải là bảng từng câu, sửa trực tiếp.

| Giờ | Chữ gốc | Bản Việt (sửa được) | Âm tiết | |
|---|---|---|---|---|
| 0:00–0:03 | 今天教大家做红烧肉 | Hôm nay dạy mọi người làm thịt kho tàu | 11/14 | 🔊 |
| 0:03–0:07 | ⚠️ lệch ASR/OCR | … | 16/**11** ⚠️ | 🔊 |

Tô đỏ tự động hai loại: `asr_ocr_mismatch` và vượt ngân sách âm tiết.
Nút 🔊 sinh TTS thử **một câu** để nghe trước.
Có chọn giọng cho cả job.

### 8.3 Chốt B — duyệt thành phẩm

Sau stage 12. Xem video đã render, bấm Xuất hoặc Bỏ.
Cấu hình `auto_approve_b` bật thì bỏ qua chốt này.

## 9. Cấu hình

`config.toml` ở gốc dự án, ghi đè được theo từng job.

```toml
[profile]
active = "air-16"            # hoặc "studio-24"

[profile.air-16]
concurrency = 1
whisper_model = "large-v3-turbo-4bit"
demucs_segment = 7
encoder = "h264_videotoolbox"

[profile.studio-24]
concurrency = 2
whisper_model = "large-v3-turbo"
demucs_segment = 12
encoder = "h264_videotoolbox"

[audio]
mode = "separate"            # "separate" | "drop_original"
bgm_gain = 0.35

[transform]
hflip = false
zoom = 1.0
speed = 1.0

[subtitle]
font = "Be Vietnam Pro"
size = 64
outline = 4
position = "bottom"

[review]
auto_approve_b = false
```

`audio.mode = "drop_original"` bỏ hẳn stage 4 (Demucs) — mất nhạc nền nhưng cắt được
stage nặng nhất, xuống còn ~1 phút/video. Nút thoát hiểm khi Air quá ì.

## 10. CLI

```
reup add <url>                    tạo job từ link
reup discover --platform douyin --limit 20
reup run <job_id>                 chạy tới chốt tiếp theo
reup run --all                    chạy mọi job đang chờ
reup approve <job_id> [--gate a|b]
reup redo <job_id> --from asr     chạy lại từ một stage
reup web                          bật UI
reup benchmark                    đo thời gian từng stage trên máy hiện tại
```

## 11. Sổ cái và chống trùng

SQLite `reup.db`:

- `jobs` — id, nguồn, url, trạng thái, stage, thời điểm, lỗi
- `seen` — id video nguồn + perceptual hash. Bắt cả trường hợp cùng nội dung
  đăng lại ở nền tảng khác
- `stage_runs` — job, stage, bắt đầu, kết thúc, kết quả. Dùng cho `benchmark`

## 12. Lỗi và phục hồi

Mỗi stage nguyên tử: ghi ra file tạm rồi mới đổi tên. Stage chết giữa chừng
không để lại artifact nửa vời.

| Loại lỗi | Xử lý |
|---|---|
| Mạng (yt-dlp, Gemini) | thử lại 3 lần, backoff lũy thừa |
| Gemini trả JSON sai | thử lại 2 lần kèm thông báo lỗi; vẫn sai thì gắn cờ, chuyển chốt A |
| yt-dlp gãy do nền tảng đổi | job sang trạng thái `failed`, ghi rõ; không chặn job khác |
| Demucs hết bộ nhớ | giảm `demucs_segment` rồi thử lại một lần |
| ffmpeg lỗi | lưu nguyên stderr vào `log.jsonl`, job `failed` |

`reup run` luôn bắt đầu từ stage đầu tiên chưa có artifact, nên chạy lại là an toàn.

## 13. Chiến lược test

**Unit — hàm thuần, không cần ffmpeg:**
đếm âm tiết tiếng Việt, luật hợp nhất ASR/OCR, gom cụm vùng sub, quyết định của vòng khớp,
tính ngân sách âm tiết, sinh ASS.

**Fixture — 6 clip ngắn 10-15 giây, commit vào repo**, mỗi clip một tình huống:
có hardsub Trung, không hardsub, sub nhảy vị trí, nhạc nền to, nhiều người nói chồng,
video tiếng Anh có caption sẵn.

**Integration:** chạy trọn pipeline trên fixture với `StubTTS` và LLM ở chế độ phát lại
(ghi response Gemini một lần vào cassette, các lần sau đọc lại). Test không tốn tiền,
không phụ thuộc mạng, và không đổi kết quả giữa các lần chạy.

**Về ffmpeg:** kiểm tra file ra tồn tại, đúng độ dài ±100ms, đúng độ phân giải, có đủ
stream video và audio. **Không** so khớp pixel — quá giòn.

## 14. Thứ tự xây

Xếp theo "sớm nhất ra được video thật".

| Phase | Nội dung | Xong thì có gì |
|---|---|---|
| 1 | core + stage runner + adapter `manual` + stage 2,3,5,10,11,12 với StubTTS | dán link → ra mp4, chưa có sub, giọng giả |
| 2 | nối TTSAdapter CapCut thật + stage 9 (dịch) | ra video có giọng Việt thật |
| 3 | stage 6,7,8 + sinh ASS | blur được sub cũ, đè được sub mới |
| 4 | stage 4 (Demucs) + mix audio | giữ được nhạc nền |
| 5 | Web UI: hàng đợi + chốt A | sửa được bản dịch trước khi render |
| 6 | stage 1 crawler: YouTube → TikTok → Douyin | tự động đầu vào |
| 7 | stage 13 metadata + chốt B + `benchmark` | khép vòng, đo được hiệu năng |

Phase 1 dùng StubTTS có chủ đích: cả pipeline chạy thông trước khi phụ thuộc vào
source CapCut, nên nếu chỗ nối TTS có vấn đề thì mọi phần khác đã xong và đã test.

Crawler xếp gần cuối vì nó là phần dễ gãy nhất và ít giá trị nhất khi luồng chính chưa chạy.

## 15. Rủi ro đã biết

| | Rủi ro | Giảm thiểu |
|---|---|---|
| R1 | Crawler Douyin gãy định kỳ (không API, cần cookie + IP Trung Quốc) | Cô lập trong adapter; adapter `manual` luôn sống nên không bao giờ tắc hoàn toàn |
| R2 | Gemini bịa nội dung khi hợp nhất ASR/OCR | Cấm viết mới, chỉ cho chọn một trong hai; gắn cờ ra UI |
| R3 | Demucs chậm và làm nóng Air | `demucs_segment` nhỏ, concurrency 1, VideoToolbox cho encode, và `drop_original` làm nút thoát |
| R4 | `ocrmac` khóa dự án vào macOS | OCR nằm sau interface; muốn chạy Linux thì viết implement RapidOCR thay vào |
| R5 | Whisper sai với tiếng Trung phương ngữ | OCR đối chiếu bắt phần lớn; còn lại chốt A bắt |
| R6 | VideoToolbox chất lượng kém hơn libx264 ở cùng bitrate | Dùng bitrate cao hơn (8M cho 1080×1920); profile studio-24 có thể đổi sang libx264 |
| R7 | Chưa biết hình thù source CapCut TTS | StubTTS cho phép xây xong mọi thứ khác trước |

## 16. Lưu ý về bản quyền

Pipeline này xử lý video do người khác sản xuất. Phần transform (hflip/zoom/đổi tốc độ)
làm giảm chứ không loại bỏ khả năng Content ID nhận diện; fingerprint video của YouTube
chịu được các phép biến đổi này. Việc thay toàn bộ audio có tác dụng lớn hơn nhiều,
nhưng nhạc nền giữ lại vẫn có thể bị nhận.

Rủi ro nhận strike hoặc mất kiếm tiền là có thật và thuộc về người vận hành.
Hướng an toàn hơn: xin phép tác giả gốc, hoặc chọn nguồn có giấy phép cho phép sử dụng lại.
