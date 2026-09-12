# reup-video

Pipeline dịch và lồng tiếng video ngắn, chạy local trên macOS.
Dán một link → ra file `.mp4` tiếng Việt có phụ đề, giữ nhạc nền.

Thiết kế: [`docs/superpowers/specs/2026-09-11-reup-video-design.md`](docs/superpowers/specs/2026-09-11-reup-video-design.md)

## Trạng thái

**Mười hai trong mười ba stage đã chạy.** Còn thiếu duy nhất crawler cho TikTok
và Douyin — hai nền tảng đó cần cookie và IP Trung Quốc (spec R1).

```
discover  fetch  demux  separate  asr  subdetect  ocr
reconcile  translate  [CHỐT A]  tts  fit  compose  [CHỐT B]  export
```

## Cài đặt

Cần `ffmpeg`, `uv`, và source [`capcut-tts-api`](https://github.com/) đặt ở
đường dẫn khai trong `config.toml`.

```bash
uv venv --python 3.12
uv pip install -e .
uv pip install pytest httpx
cp .env.example .env      # rồi điền GEMINI_API_KEY
```

## Dùng

```bash
uv run reup discover --limit 10 --add     # quét YouTube Shorts
uv run reup add "https://..." --lang zh   # hoặc dán link
uv run reup run <job_id>
uv run reup run --all                     # cả hàng đợi, song song theo concurrency
uv run reup web                           # giao diện duyệt, cổng 8765
uv run reup approve <job_id>
uv run reup redo <job_id> --from asr
uv run reup benchmark                     # đo từng stage trên máy này
```

Video dừng ở **chốt A** sau khi dịch xong. Mở `reup web`, sửa bản dịch, bấm
duyệt, rồi `reup run` lần nữa. Ở chốt A còn chọn được giọng cho cả job (lựa chọn
ghi vào `overrides.json` của job, không đụng `config.toml` chung) và bấm 🔊 để
nghe thử **một câu** — chưa chạy `tts` thì server tổng hợp ngay câu đó. Thành phẩm nằm ở `output/<job_id>.mp4` kèm
`<job_id>.json` chứa tiêu đề, mô tả và hashtag để chép dán lúc đăng.

## Hiệu năng

Đo trên M4, clip 18 giây 1080×1920:

| Stage | Thời gian | | Stage | Thời gian |
|---|---|---|---|---|
| `fetch` | 1.8s | | `translate` | 21s |
| `demux` | 0.1s | | `tts` | 21s |
| `separate` | 3.4s | | `fit` | 48s |
| `asr` | 4.4s | | `compose` | 5.0s |
| `subdetect` | 8.8s | | `export` | 19s |
| `ocr` | 8.3s | | **tổng** | **~2 phút** |

## Cấu hình

Sửa `config.toml`. Vài knob đáng biết:

- `profile.active` — `air-16` hoặc `studio-24`.
- `profile.concurrency` — số job chạy song song trong `reup run --all`. Một job
  vẫn chạy tuần tự từng stage; knob này chỉ nói chạy mấy job cùng lúc.
- `audio.mode = "drop_original"` — bỏ hẳn Demucs. Mất nhạc nền nhưng cắt được
  stage nặng; nút thoát hiểm khi máy quá ì.
- `profile.video_bitrate` là **trần**, không phải mức cố định. `compose` chọn
  `min(trần, max(2.5M, 1.6 × bitrate nguồn))`. Bỏ luật này thì file ra nặng gấp
  5 lần nguồn mà không thêm chi tiết nào.
- `profile.prefer_h264` mặc định `false`. YouTube trả AV1 cho Shorts; trên M4
  hardware AV1 decode gần như miễn phí trong khi bản AV1 nhỏ hơn một nửa. Chỉ
  bật trên máy không có AV1 decode (Intel, M1, M2).
- `llm.model` — gói Gemini miễn phí chỉ cho **20 request/ngày mỗi model**.
- `asr.engine` — `whisper` (mặc định, chạy local) hoặc `capcut` (đẩy lên mạng,
  không tốn Neural Engine; hữu ích khi máy quá ì). CapCut STT **không tự nhận
  ngôn ngữ**, nên job phải khai rõ: `reup add <url> --lang zh`.
- `review.auto_approve_b = true` — bỏ qua chốt duyệt thành phẩm.

## Ba chỗ môi trường bắt đi chệch thiết kế

**ffmpeg của Homebrew không có libass.** Công thức `homebrew/core` đã bỏ hẳn
libass khỏi phụ thuộc, nên không có filter `ass`, `subtitles` hay `drawtext`.
Phụ đề vì thế được vẽ bằng Pillow ra PNG rồi `overlay` — chạy với mọi bản
ffmpeg. Máy nào có libass thì `compose` tự chuyển sang đường đó.

**`--rate` của CapCut không có tác dụng.** Đo sáu câu cùng 10 âm tiết: rate 1.5
chỉ ngắn hơn 5.9%, ngang mức dao động giữa các câu. Server còn cache theo text
và bỏ qua rate. Nên khi câu dài quá khe, cách duy nhất là **đổi chữ** — vòng
khớp thời lượng chỉ còn hai bậc: viết lại rồi `atempo`.

**CapCut STT trả mảnh phụ đề, không trả câu.** Đo một câu 2.4 giây: nó cắt thành
năm mảnh 2-3 chữ, vì bên trong là bộ sinh phụ đề (`words_per_line = 15`). Whisper
trả câu. Để nguyên thì `translate` tính ngân sách trên khe 200 ms — trần một âm
tiết — và LLM dịch từng cụm rời. Adapter vì thế ghép mảnh lại thành câu trước khi
trả về: cắt ở khoảng lặng dài hơn 400 ms, ở dấu chấm, hoặc khi câu quá 7 giây.

## Test

```bash
uv run pytest
```

494 test, không gọi mạng và không nạp model: fixture video sinh bằng ffmpeg lúc
chạy; Whisper, Demucs, yt-dlp, CapCut và Gemini đều được thay bằng hàm giả.

## Bản quyền

Pipeline này xử lý video do người khác sản xuất. Rủi ro nhận strike hoặc mất
kiếm tiền là có thật và thuộc về người vận hành. Hướng an toàn hơn: xin phép
tác giả gốc, hoặc chọn nguồn có giấy phép cho phép sử dụng lại.
