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

Cần `ffmpeg`, `uv`, một **JS runtime** (`deno`, `node`, hoặc `bun`), và source
[`capcut-tts-api`](https://github.com/) đặt ở đường dẫn khai trong `config.toml`.

JS runtime là bắt buộc cho YouTube: YouTube bắt giải một "JS challenge" mới trả
link tải, và yt-dlp cần runtime cùng script EJS (tải từ GitHub lúc chạy) để giải.
Thiếu chúng thì **mọi** video YouTube đều báo `This video is not available` —
thông báo nghe như video bị gỡ, nên rất dễ đi tìm nhầm chỗ. Không muốn chạy mã
tải từ GitHub thì đặt `fetch.remote_components = ""`, đổi lại YouTube hỏng.

```bash
uv venv --python 3.12
uv pip install -e .
uv pip install pytest httpx
cp .env.example .env      # rồi điền GEMINI_API_KEY
```

## Dùng

```bash
uv run reup discover --limit 10 --add             # hashtag #shorts mặc định
uv run reup discover --query "mèo hài" --limit 20  # tìm kiếm
uv run reup discover --query "@MrBeast"           # tab Shorts của một kênh
uv run reup discover --platform tiktok --query "@tên"
uv run reup add "https://..." --lang zh   # hoặc dán link
uv run reup run <job_id>
uv run reup run --all                     # cả hàng đợi, song song theo concurrency
uv run reup web                           # giao diện duyệt, cổng 8765
uv run reup approve <job_id>
uv run reup redo <job_id> --from asr
uv run reup doctor                        # kiểm môi trường trước khi chạy
uv run reup benchmark                     # đo từng stage trên máy này
```

Để truy cập giao diện web từ máy khác trên mạng, xem hướng dẫn bảo vệ với mật khẩu và truy cập từ xa tại
[`docs/superpowers/plans/2026-09-13-truy-cap-tu-xa.md`](docs/superpowers/plans/2026-09-13-truy-cap-tu-xa.md).

Web UI có bốn tab: **Hàng đợi**, **YouTube**, **TikTok**, **Douyin**. Ba tab sau
hiện từng video thành thẻ 9:16 kèm ảnh đại diện, độ dài, kênh và lượt xem; bấm
**▶ Xem** để nạp player nhúng ngay trong trang (iframe chỉ nạp khi bấm, không
nạp sẵn cả chục cái), rồi **Chọn video này** để tạo job. Video đã thành job hiện
luôn link job thay vì nút chọn, nên không làm trùng; tick **ẩn đã xử lý** để
giấu hẳn chúng. Sắp xếp được theo lượt xem hoặc độ dài.

Ô quét của tab YouTube hiểu bốn kiểu, phân biệt bằng ký tự đầu:

| Gõ | Nguồn |
|---|---|
| `mèo hài` | tìm kiếm, tự kèm bộ lọc dưới 4 phút của YouTube |
| `#shorts` | trang hashtag |
| `@MrBeast` | tab Shorts của kênh |
| `https://…` | quét đúng URL đó (playlist, kênh, trang kết quả…) |

Chữ trần là **tìm kiếm** chứ không phải hashtag — gõ "mèo hài" mà ra trang
hashtag rỗng thì không ai đoán được vì sao. Tìm kiếm bắt buộc phải kèm bộ lọc
thời lượng: đo thật với "mèo hài", bốn kết quả đầu không lọc dài 638s, 940s,
515s — bộ lọc 3 phút của pipeline quét sạch và trang ra rỗng. Trang luôn in ra
URL nó thật sự quét, để thấy ngay ô mình gõ được hiểu thành gì.

Quét là thao tác chủ động: mở tab không gọi `yt-dlp`, phải bấm **Quét**. TikTok
gần như luôn đòi cookie và Douyin còn cần IP ra được Trung Quốc — đặt
`cookies_from_browser` hoặc `cookie_file` ở mục `[discover.<nền tảng>]`. Quét
hỏng thì trang hiện nguyên lời `yt-dlp` kèm chỗ cần sửa, không phải lỗi trơn.

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

Số đo `tts` ở trên là chạy tuần tự (`tts.concurrency = 1`). Với
`tts.concurrency > 1`, các câu sinh giọng song song nên `tts` có thể nhanh hơn
đáng kể — mức nhanh hơn bao nhiêu phụ thuộc độ trễ mạng thật tới CapCut ở từng
môi trường, chưa đo lại trên M4 nên chưa ghi số cụ thể vào bảng.

## Giá một job tính theo request LLM

Hạn mức miễn phí của Gemini đếm theo **số request**, không theo lượng chữ, nên
chỗ đáng tối ưu là số lượt gọi chứ không phải độ dài prompt.

| Stage | Request |
|---|---|
| `reconcile` | 1, chỉ khi có xung đột cần xử |
| `translate` | 1 — cả video một lượt |
| `fit` | tối đa `MAX_REVISIONS` (2) — **một lượt cho cả loạt câu cần viết lại** |
| `export` | 1 — tiêu đề, mô tả, hashtag |

Tổng: **tối đa 5 request một job**, không phụ thuộc số câu. Trước đây `fit` gọi
lẻ từng câu nên giá là `3 + 2 × số câu vượt trần` — một clip 4 câu có thể tốn 7
request, và với 20 request/ngày thì chỉ làm được 2–3 video. Giờ `fit` viết lại
theo **vòng**: mỗi vòng gom mọi câu còn dài vào một lượt gọi, đo lại, rồi mới
vào vòng sau. Vẫn phải chia vòng vì có tổng hợp và đo lại mới biết câu đã vừa
khe chưa.

Hết hạn mức thì job dừng ở đúng stage đó và giữ nguyên mọi artifact đã làm —
bấm **Chạy lại** trong Web UI là đi tiếp, không phải chạy lại Demucs và Whisper.

### Tách model theo stage

`[llm.<stage>]` ghi đè `[llm]` cho riêng một stage; khoá không khai thì thừa kế.
Bốn stage dùng LLM: `reconcile`, `translate`, `fit`, `export`. Bố cục đang dùng
để Gemini chỉ còn gánh 1 request mỗi video:

```toml
[llm]
provider = "gemini"
model    = "gemini-3.6-flash"

[llm.reconcile]   # và [llm.fit], [llm.export]
provider = "ollama"
model    = "qwen3:8b"
```

### Chạy model local, bỏ hẳn hạn mức

`llm.provider = "ollama"` đẩy cả bốn việc sang model chạy trên máy: không hạn
mức, không API key, không cần mạng.

```bash
brew install ollama
ollama serve &
ollama pull qwen3:8b        # ~5GB, vừa RAM 16GB
```

rồi trong `config.toml`:

```toml
[llm]
provider = "ollama"
model    = "qwen3:8b"
```

Đổi lại chậm hơn, và **chất lượng tiếng Việt phụ thuộc model** — `translate` là
chỗ đáng đo trước khi tin, vì nó quyết định sản phẩm. Ba việc còn lại (`fit`
viết lại, `reconcile` chọn A hay B, `export` sinh metadata) nhẹ hơn nhiều.

## Cấu hình

Sửa `config.toml`. Vài knob đáng biết:

- `profile.active` — `air-16` hoặc `studio-24`.
- `profile.concurrency` — số job chạy song song trong `reup run --all`. Một job
  vẫn chạy tuần tự từng stage; knob này chỉ nói chạy mấy job cùng lúc.
  Lưu ý: knob này nhân với `tts.concurrency` (số câu song song trong một job)
  và số worker vẽ PNG phụ đề ở `compose.py` — `profile.concurrency=2` ×
  `tts.concurrency=3` đã là tối đa 6 request CapCut cùng lúc trên toàn máy,
  đáng nhớ khi chỉnh hai knob này cùng lúc.
- `discover.<youtube|tiktok|douyin>.query` — nguồn mặc định của tab quét (xem
  bảng ở trên). `cookies_from_browser = "chrome"` khi nền tảng chặn khách vãng
  lai — TikTok gần như luôn cần, Douyin cần thêm IP ra được Trung Quốc.
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
- `tts.concurrency` (mặc định `3`) — số câu sinh giọng song song trong một
  job; server CapCut gãy sau ~15 request liên tiếp nên đừng đẩy quá cao.
- `tts.batch_size` (mặc định `8`) — số câu gộp vào 1 request CapCut TTS, giảm hẳn
  số round-trip và nguy cơ quá tải server.
- `tts.max_polls` / `tts.poll_interval` (mặc định `10` / `1.0`) — số lượt poll tối đa
  và thời gian chờ giữa các lượt khi truy vấn audio từ CapCut.
- `tts.pause_every` / `tts.pause_seconds` (mặc định `12` / `2.0`) — nghỉ nhịp
  chủ động sau mỗi 12 request thành công liên tiếp, mỗi lần nghỉ 2 giây —
  tránh chạm ngưỡng gãy của CapCut ở trên.
- `tts.allow_edge_fallback` (mặc định `true`) — khi CapCut lỗi, `true` cho
  phép âm thầm đổi sang edge-tts để job chạy tiếp; `false` bắt lỗi CapCut phải
  raise thật. Dù chọn gì, `tts/manifest.json` cũng ghi field `engine` cho từng
  câu (`capcut` / `edge_tts_fallback` / `silence`) để biết câu nào không phải
  giọng CapCut thật.
- `tts.pronunciation_rules_path` (mặc định `docs/tts_pronunciation_rules.md`) —
  file quy tắc phiên âm tiếng Anh, chữ số, từ viết tắt sang tiếng Việt cho stage `pronounce`.
- `[llm.pronounce]` — cấu hình mô hình AI thực hiện chuẩn hoá phiên âm TTS sau khi dịch.

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
