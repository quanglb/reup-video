# reup-video

Pipeline dịch và lồng tiếng video ngắn, chạy local trên macOS.

Thiết kế: [`docs/superpowers/specs/2026-09-11-reup-video-design.md`](docs/superpowers/specs/2026-09-11-reup-video-design.md)

## Trạng thái

**Phase 1** — xương sống pipeline. 6 trong 13 stage đã chạy: `fetch`, `demux`,
`asr`, `tts`, `fit`, `compose`. Giọng đọc còn là `StubTTS` (wav im lặng), chưa
có dịch, chưa có blur phụ đề. Xem
[`docs/superpowers/plans/`](docs/superpowers/plans/) cho các phase sau.

## Cài đặt

Cần `ffmpeg` 8.x và `uv` trong PATH.

```bash
uv venv --python 3.12
uv pip install -e .
uv pip install pytest
```

## Dùng

```bash
uv run reup add "https://www.douyin.com/video/..." --lang zh
uv run reup status
uv run reup run <job_id>
uv run reup redo <job_id> --from asr
```

File thành phẩm nằm ở `jobs/<job_id>/render/final.mp4`.

## Cấu hình

Sửa `config.toml`. Đổi `profile.active` sang `studio-24` khi chạy trên máy
24GB. Đặt `audio.mode = "drop_original"` nếu máy quá chậm — bỏ nhạc nền nhưng
nhanh hơn nhiều.

Hai knob trong `[profile.*]` đáng để ý:

- `video_bitrate` là **trần**, không phải mức cố định. `compose` chọn
  `min(trần, max(2.5M, 1.6 x bitrate nguồn))`, nên một Short 1.5 Mbps ra file
  2.5 Mbps chứ không phải 8 Mbps. Đo thật: bỏ luật này thì file ra nặng gấp
  5 lần nguồn (18.6MB cho clip 3.7MB) mà không thêm chi tiết nào.
- `prefer_h264` mặc định `false`. YouTube trả AV1 cho Shorts, và trên M4
  hardware AV1 decode gần như miễn phí (`compose` chênh 45ms) trong khi bản
  AV1 nhỏ hơn một nửa. Chỉ bật knob này trên máy không có AV1 decode
  (Intel, M1, M2).

## Test

```bash
uv run pytest
```

Test không gọi mạng và không nạp model: fixture video sinh bằng ffmpeg lúc
chạy, Whisper và yt-dlp được thay bằng hàm giả.
