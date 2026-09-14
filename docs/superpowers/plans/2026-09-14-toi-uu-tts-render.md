# reup-video — Tối ưu tốc độ TTS, giảm lỗi khi render ffmpeg

**Goal:** `tts` chạy nhanh hơn và ít gãy giữa chừng; `compose` (ffmpeg dựng
video cuối) không còn treo vô thời hạn khi video nhiều câu / nhiều vùng che.

**Đợt đang làm (15/09):** chỉ Task 1.0, 1.5, và toàn bộ mục 1' — xem mục 4
"Phạm vi đợt sửa 15/09" ở cuối file. Phần còn lại (1.2–1.4, mục 2) là backlog,
chưa bắt đầu.

**Ngày viết:** 14/09/2026, cập nhật cùng ngày sau khi đọc thêm
[capcut_driver.py](../../../src/reup/adapters/capcut_driver.py) và
[core/runner.py](../../../src/reup/core/runner.py) — bản đầu chỉ đọc
`tts.py`/`capcut_tts.py`/`compose.py`/`ffmpeg.py`, thiếu phần quan trọng nhất:
**protocol thật giữa `reup` và CapCut** và **một đường fallback ẩn đang mâu
thuẫn với thiết kế đã ghi**. Xem mục 0.0. Task 0.1/0.2 vẫn là đo số thật trên
máy trước khi sửa — phần dưới mới chỉ là đọc code, chưa đo.

---

## 0.0 Phát hiện khi đọc sâu hơn `capcut_driver.py`

`capcut_tts.py` gọi `capcut_driver.py` như hộp đen qua subprocess, nhưng bản
thân driver này ([capcut_driver.py](../../../src/reup/adapters/capcut_driver.py))
**nằm trong repo `reup`**, không phải trong `capcut-tts-api` — nên đọc kỹ nó
mới thấy hết.

**a. Protocol thật là 2 bước, không phải 1:** `tts-new` (POST, tạo task) rồi
poll `tts-query` tối đa `max_polls` lần cách nhau `poll_interval` giây
([capcut_driver.py:113-114](../../../src/reup/adapters/capcut_driver.py),
`--max-polls` mặc định `10`, `--poll-interval` mặc định `0.5`). `CapCutTTS`
chỉ truyền `--poll-interval 1.0` ([capcut_tts.py:106](../../../src/reup/adapters/capcut_tts.py)),
**không bao giờ truyền `--max-polls`** → driver luôn poll tối đa 10 lần × 1.0s
= tới 10 giây, cộng thời gian request `tts-new` (timeout socket 30s) trước đó.

**b. Hai lớp timeout không khớp nhau:** `subprocess.run(..., timeout=15.0)` ở
[capcut_tts.py:112](../../../src/reup/adapters/capcut_tts.py) là trần **tổng**
cho cả request `tts-new` + toàn bộ vòng poll. Nhưng vòng poll bên trong driver
tự nó có thể ngốn gần 10 giây (mục a) — cộng thêm thời gian request đầu và độ
trễ mạng thật, **15 giây ngoài dễ tới trước khi driver kịp tự bỏ cuộc và trả
lỗi có ý nghĩa**. Kết quả: `TimeoutExpired` ở lớp ngoài giết cả tiến trình
đang poll dở, mất sạch tiến độ, rồi retry từ đầu (`sleep(2**attempt)` rồi gọi
lại `tts-new` mới) — tốn thêm một request `tts-new` mỗi lần timeout kiểu này,
**làm chạm ngưỡng gãy ~15 request liên tiếp nhanh hơn** thay vì chậm hơn.

**c. Driver tự âm thầm rơi xuống edge-tts — đúng thứ mà docstring nói là đã
tránh được:** `capcut_tts.py` mở đầu bằng cảnh báo *"khi CapCut lỗi nó âm thầm
rơi xuống edge-tts, đổi cả giọng lẫn bitrate mà không báo... Ở đây lỗi phải
nổi lên"* — nhưng đó là nói về `generate_audio.py` của repo `capcut-tts-api`
gốc. Còn `capcut_driver.py`, **chính là file đứng giữa để tránh việc đó**, lại
tự gọi `fallback_edge_tts()` ở **5 chỗ**
([capcut_driver.py:133,138,145,195,199](../../../src/reup/adapters/capcut_driver.py)):
lỗi request, HTTP khác 200, không có task, status `failed`, và hết `max_polls`
mà chưa xong. Mọi nhánh này đều `print(json.dumps({...}))` và `return` — tức
**exit code 0, coi như thành công** — nên lớp ngoài (`capcut_tts.py`) không hề
biết audio vừa nhận được là giọng CapCut thật hay giọng edge-tts thay thế.
JSON trả về (`path`, `bytes`, `duration_ms`, `hit_cache`) **không có trường nào
đánh dấu đã fallback**.

Hệ quả cụ thể:
- Không có test nào trong `tests/test_capcut_tts.py` chạm tới nhánh
  `fallback_edge_tts` — đường này chưa từng được kiểm.
- `fallback_edge_tts` chỉ hoạt động nếu `import edge_tts` thành công **trong
  venv Python 3.9 của `capcut-tts-api`** — một repo ngoài, không do `reup`
  quản lý. Máy này có cài, máy khác có thể không → hành vi lúc lỗi **khác nhau
  giữa các máy** mà `reup` không kiểm soát được.
- Nếu fallback từng xảy ra âm thầm, nó giải thích được kiểu lỗi khó bắt mà
  "hay bị lỗi" thường ám chỉ: không phải job báo `CapCutError` rõ ràng, mà
  audio ra **nghe khác giọng, độ dài không khớp dự đoán của `fit`** (`fit`
  tính toán dựa trên giả định giọng CapCut nhất quán) — mà không có dòng lỗi
  nào để lần theo.

→ Đây là phát hiện quan trọng nhất của lượt đọc này. Task 1.0 và 1.5 dưới đây
xử lý riêng phần a/b (timeout) và c (fallback ẩn); không gộp chung vì rủi ro
và cách sửa khác nhau.

---

## 0. Tóm tắt vấn đề

| Chỗ | Vấn đề | Vì sao |
|---|---|---|
| `tts` | Sinh giọng **tuần tự từng câu** | [tts.py:20-27](../../../src/reup/stages/tts.py) `synthesize_all` là vòng `for` thường, không có gì chạy song song |
| `tts` | Mỗi câu **spawn một tiến trình Python mới** | [capcut_tts.py:98-107](../../../src/reup/adapters/capcut_tts.py) gọi `subprocess.run` với venv Python 3.9 riêng — chi phí khởi động interpreter cộng dồn theo số câu |
| `tts` | Server CapCut **gãy sau ~15 request liên tiếp** | Đã biết (comment ở đầu file), nhưng code chỉ *phản ứng* bằng retry+backoff sau khi gãy, không chủ động nghỉ nhịp để tránh gãy |
| `tts` | `redo --from tts` gọi lại API cho **mọi câu**, kể cả câu không đổi | Không có cache cục bộ theo `(voice, text)` — chỉ server CapCut cache theo text, vẫn tốn 1 round-trip subprocess + HTTP mỗi câu |
| `compose` (ffmpeg) | `run_ffmpeg` **không có timeout** | [ffmpeg.py:24-34](../../../src/reup/media/ffmpeg.py) — `subprocess.run` không truyền `timeout`. Treo là treo vô hạn, phải tự Ctrl+C |
| `compose` (ffmpeg) | Overlay phụ đề **chain tuần tự theo số câu** | [compose.py:125-146](../../../src/reup/stages/compose.py) `build_subtitle_overlays` — mỗi câu thêm một filter `overlay` nối vào chuỗi trước, cộng thêm một input file. Video nhiều câu → filtergraph dài, ffmpeg chậm dần và dễ hụt hơi |
| `compose` (Pillow) | Vẽ PNG phụ đề **tuần tự từng câu** | [compose.py:300-324](../../../src/reup/stages/compose.py) `build_overlays` — CPU-bound, độc lập từng câu, nhưng chạy vòng `for` thường |

Cả hai phần đã có một lần sửa treo trước đó (commit `667cef7`: gộp `split`
một lần cho vùng che, tránh filtergraph phình cấp số nhân). Việc lần này khác:
tối ưu **tốc độ** TTS, và giảm rủi ro treo ở **overlay phụ đề** — chỗ chưa
được sửa ở commit đó vì lúc ấy vấn đề nằm ở vùng che (`build_blur_chain`), còn
`build_subtitle_overlays` (đường overlay ảnh, dùng khi máy không có libass)
vẫn chain tuần tự y nguyên.

Thứ tự làm: đo trước (0.1, 0.2) → TTS (1) → ffmpeg (2). Hai phần độc lập nhau,
làm phần nào trước cũng được.

### 0.1 Đo TTS trước khi sửa

```bash
uv run reup benchmark   # đã có, xem README mục Hiệu năng
```

Ghi lại: số câu trung bình một job, thời gian `tts` hiện tại, có job nào từng
gặp lỗi `CapCutError` trong log/Telegram chưa (tìm `hỏng sau` trong log).

### 0.2 Đo ffmpeg trước khi sửa

Tìm job có nhiều câu nhất đang có trong `jobs/` (hoặc dựng fixture nhiều câu),
chạy `compose` riêng, đo thời gian và theo dõi `ffmpeg -progress` (xem Task 2.1)
để biết filtergraph hiện tại tốn bao lâu ở mức "bình thường", làm mốc so sánh.

---

## 1. Tối ưu `tts`

### Task 1.0 — Batch nhiều câu vào 1 lần gọi CapCut, khớp lại timeout (cập nhật 15/09)

**Đọc lại `capcut-tts-api/capcut_common_task_client.py:250-296`
(`tts_new_body`) phát hiện: API CapCut đã hỗ trợ sẵn nhiều câu trong CÙNG MỘT
request `tts-new`** — SSML được build với một block `<voice>` cho mỗi câu
trong `texts`, và `"need_merge_voice": False` nghĩa là server trả về nhiều
audio **riêng biệt** (`audio_subtitles[i]`, một phần tử cho mỗi câu, cùng thứ
tự với các block `<voice>` gửi lên), không gộp thành một audio dài.
`capcut_driver.py` hiện tại **không dùng khả năng này** — luôn gửi đúng 1 câu
(`text=[args.text]`) và chỉ đọc `audio_subtitles[0]`.

Đây là sửa quan trọng nhất của phần 1: **không phải chạy song song nhiều
subprocess** (Task 1.1 cũ, xem ghi chú SUPERSEDED bên dưới) mà là **giảm hẳn
số lần gọi HTTP/spawn subprocess** — gộp N câu vào 1 request thay vì N
request. Ít request hơn → ít khả năng chạm ngưỡng "gãy sau ~15 request liên
tiếp" hơn, đúng gốc rễ của lỗi `"CapCut TTS hỏng sau 3 lần thử ... timeout"`,
thay vì chỉ retry giỏi hơn khi đã gãy.

**Sửa `capcut_driver.py`:**
- `--text` đổi thành `action="append"` (nhận nhiều `--text`, một cho mỗi câu
  trong batch), `--out` cũng đổi thành `action="append"`, hai list này khớp
  theo thứ tự (câu thứ `i` ghi ra file `out[i]`).
- `new_args.text = args.text` (list nhiều câu, thay vì bọc 1 câu vào list như
  hiện tại) — `build_request`/`tts_new_body` đã nhận `texts` là list sẵn, chỉ
  cần truyền đúng list nhiều phần tử.
- Khi `status == "succeed"`: đọc **toàn bộ** `task["payload"]["audio_subtitles"]`
  (list), không chỉ `[0]` — tải từng `speech_url` xuống đúng `out[i]` tương
  ứng theo index.
- In ra **1 mảng JSON** (một object mỗi câu: `path`, `bytes`, `duration_ms`,
  `hit_cache`, thêm field `engine` — xem Task 1.5) thay vì 1 object đơn.
- Mọi nhánh `fallback_edge_tts` hiện có (5 chỗ) phải xử lý **từng câu trong
  batch riêng lẻ** khi task thất bại toàn batch — gọi `fallback_edge_tts` cho
  từng `(text[i], out[i])`, gộp kết quả thành cùng mảng JSON, đánh dấu
  `"engine": "edge_tts_fallback"` cho các câu fallback.

**Sửa `capcut_tts.py`:**
- Thêm `synthesize_batch(items: list[tuple[str, Path]], lang, voice) ->
  list[TTSResult]` — build `cmd` với nhiều cặp `--text/--out` lặp lại, gọi
  subprocess **1 lần cho cả batch**, parse mảng JSON, trả về list `TTSResult`
  đúng thứ tự `items`.
- Trước khi gửi batch: lọc riêng các câu "rỗng" (không có ký tự alnum sau
  `sanitize_for_tts`, xem `capcut_tts.py:93-96` hiện tại) — các câu này vẫn
  ra silence ngay, **không** đưa vào batch gửi CapCut.
- Timeout ngoài tính theo công thức, không phải hằng số: `outer_timeout =
  max_polls * poll_interval + buffer`, `buffer` đủ bù cho request đầu (POST
  `tts-new`, tối đa 6s) **cộng** thời gian tải tuần tự N file mp3 trong batch
  (ước lượng theo kích thước batch, ví dụ `buffer = 10 + batch_size * 2`) —
  batch càng lớn, phần tải file càng chiếm nhiều thời gian của lớp ngoài.
  `--max-polls` truyền tường minh xuống driver (không còn phụ thuộc mặc định
  ẩn `10` của driver).
- **Lưới an toàn cho batch lỗi toàn bộ:** một task SSML gộp nhiều câu có thể
  fail nguyên task (ví dụ 1 câu có ký tự khiến CapCut từ chối cả task — chưa
  xác nhận được hành vi thật của server ở mức này, cần đo ở Task 0.1 mở
  rộng). Nếu `synthesize_batch` fail sau đủ `retries` cho cả batch, **fallback
  về gọi từng câu một** (dùng lại `synthesize` cũ, giữ nguyên làm đường an
  toàn) thay vì làm cả batch treo vĩnh viễn vì một câu hỏng.

**Sửa `tts.py` (`synthesize_all`):**
- Gom `transcript.segments` thành các nhóm `batch_size` câu liên tiếp (config
  `[tts].batch_size`, mặc định `8`), gọi `adapter.synthesize_batch` cho từng
  nhóm, giữ nguyên thứ tự `entries` theo `segments` gốc.

**SUPERSEDED — Task 1.1 (chạy song song bằng `ThreadPoolExecutor`, bản kế
hoạch gốc):** không cần nữa. Batch 1 request/nhiều câu giảm số round-trip
hiệu quả hơn chạy song song nhiều round-trip (và không có rủi ro dồn dập tới
ngưỡng gãy như chạy song song). Giữ lại ý tưởng này trong lịch sử file, không
xoá, phòng khi đo thực tế cho thấy batch không đủ (ví dụ batch hay bị lỗi
nguyên task, phải giảm `batch_size` xuống 1 và cần lại concurrency).

Test (`tests/test_capcut_tts.py`):
- driver giả trả mảng JSON N phần tử cho batch N câu → `synthesize_batch`
  trả đúng N `TTSResult`, đúng thứ tự.
- Với `max_polls=10, poll_interval=1.0, batch_size=8`, timeout ngoài tính ra
  phải theo đúng công thức (đổi tham số → timeout đổi theo, không còn hằng
  số cố định).
- Batch fail toàn bộ (mock subprocess trả lỗi) → rơi xuống gọi từng câu một,
  không mất dữ liệu các câu còn lại trong batch.
- Câu rỗng/không alnum trong batch → ra silence ngay, không nằm trong lệnh
  gửi CapCut (đếm số `--text` trong `cmd` được build).

### Task 1.2 — (backlog, không làm đợt này) Nghỉ nhịp chủ động thay vì đợi gãy rồi retry

**Vấn đề:** Ngưỡng gãy ~15 request liên tiếp là biết trước, nhưng code hiện
tại ([capcut_tts.py:110-126](../../../src/reup/adapters/capcut_tts.py)) chỉ
retry *sau khi* đã gãy (backoff `2**attempt` giây). Sau khi Task 1.0 (batch
nhiều câu/1 request) làm ở đợt này, số request giảm hẳn theo hệ số
`batch_size` nên bớt cấp bách — chỉ cần làm lại task này nếu batch vẫn chạm
ngưỡng gãy trên thực tế đo được.

**Sửa (nếu làm):**
- Thêm bộ đếm request liên tiếp trong `CapCutTTS` (đơn giản: đếm số lần gọi
  `synthesize`/`synthesize_batch` thành công liên tục); sau mỗi `pause_every`
  request (config, mặc định `12` — dưới ngưỡng biết là 15) thì
  `sleep(pause_seconds)` (mặc định `2s`) trước khi tiếp tục.
- Nếu Task 1.1 gốc (chạy song song, hiện SUPERSEDED — xem Task 1.0) được hồi
  sinh sau này, bộ đếm này phải **dùng chung** giữa các thread (một
  `threading.Lock` quanh bộ đếm là đủ — không cần gì phức tạp hơn).

Test: giả lập 20 lần gọi liên tiếp với `sleep` được mock (đếm số lần gọi và
tổng thời gian ngủ) → xác nhận có nghỉ nhịp ở request thứ 12, không nghỉ ở
những request khác.

### Task 1.3 — Cache cục bộ theo `(voice, text)` cho `redo`

**Vấn đề:** `reup redo <job_id> --from tts` (đã có, xem README) chạy lại toàn
bộ `tts` từ đầu — kể cả câu chưa đổi (ví dụ chỉ 1 câu bị viết lại ở `fit` sau
khi job quay lại từ `translate`). Server CapCut có cache theo text nên round-trip
HTTP rẻ, nhưng **subprocess Python 3.9 vẫn spawn mới mỗi câu** — chi phí chính
nằm ở đó, không phải ở phần audio thật.

**Sửa:**
- Trước khi gọi `adapter.synthesize`, kiểm `job.tts_segment(seg.id)` đã tồn
  tại và một file cache nhỏ `tts/text_cache.json` (map `seg.id` → hash của
  `text` lúc sinh) khớp với `text` hiện tại → bỏ qua, dùng lại file cũ.
- Ghi `text_cache.json` cùng lúc với `manifest.json` trong `write_manifest`.
- Chỉ áp dụng khi **không** ép chạy lại toàn bộ (không có cờ `--force` hoặc
  tương đương ở `redo`) — cần xem `cli.py` phần `_cmd_redo` có sẵn cờ gì để
  quyết định có cần thêm cờ `--force-tts` hay tái dùng cờ hiện có.

Test:
- Câu không đổi → không gọi `adapter.synthesize` (đếm số lần gọi mock).
- Câu đổi text → gọi lại như bình thường.
- Thiếu file `.wav` dù cache khớp → vẫn gọi lại (file có thể bị xoá tay).

### Task 1.4 — (tuỳ chọn, làm sau nếu 1.1–1.3 chưa đủ) Driver sống thay vì spawn mỗi câu

**Ý tưởng:** Thay vì `subprocess.run` một script Python 3.9 mới cho mỗi câu
([capcut_tts.py:98](../../../src/reup/adapters/capcut_tts.py) `DRIVER`), giữ
một tiến trình driver sống nhận nhiều câu qua stdin/stdout (hoặc socket cục
bộ), tránh chi phí khởi động interpreter + import lặp lại.

**Ghi chú:** đây là thay đổi lớn hơn (đổi giao thức giữa `reup` và
`capcut-tts-api`), rủi ro cao hơn 1.1–1.3. Chỉ làm nếu đo ở Task 0.1 cho thấy
chi phí khởi động subprocess chiếm phần đáng kể (>20%) thời gian mỗi câu. Nếu
làm, viết task riêng — không gộp vào đây.

### Task 1.5 — Fallback edge-tts ẩn trong `capcut_driver.py` phải nổi lên + báo Telegram (mục 0.0.c)

**Quan trọng ngang Task 1.0** — không phải tối ưu tốc độ, mà sửa một chỗ
code hiện tại đang **âm thầm vi phạm bất biến mà chính nó ghi ra trong
docstring**, và là nơi tự nhiên để trả lời yêu cầu "báo cáo về bot Telegram"
khi TTS gặp sự cố.

**Sửa (không phải xoá `fallback_edge_tts` — nó vẫn có giá trị làm phao cứu
sinh khi CapCut chết hẳn, chỉ là phải BÁO cho biết):**

- `capcut_driver.py`: field `engine` (`"capcut"` hoặc `"edge_tts_fallback"`)
  đã đưa vào thiết kế batch ở Task 1.0 (mỗi phần tử trong mảng JSON trả về có
  field này) — không phải việc riêng nữa, chỉ cần đảm bảo mọi nhánh gọi
  `fallback_edge_tts` set đúng giá trị, đường CapCut thật set `"capcut"`
  tường minh (không suy luận ngược từ việc thiếu trường).
- `capcut_tts.py`: `synthesize_batch`/`synthesize` đọc trường `engine` từ
  từng kết quả; câu nào là `edge_tts_fallback` thì **log cảnh báo rõ ràng**
  (câu nào, job nào) và gọi `TelegramNotifier` có sẵn (`notify.py:114` hàm
  `send`, hoặc thêm method chuyên biệt kiểu `tts_fallback_warning(job, ids)`
  cạnh `job_failed` ở `notify.py:240-247` — cùng pattern HTML-format đã có)
  để đẩy cảnh báo lên kênh Telegram cấu hình ở `[notify]`
  (`config.toml:47-53`) — người vận hành biết ngay, không phải đào log.
- `TTSResult` ([adapters/tts.py](../../../src/reup/adapters/tts.py)) thêm
  field `engine: str`, `write_manifest` ([tts.py:30](../../../src/reup/stages/tts.py))
  ghi field này vào `tts/manifest.json` cho từng câu — job sau (`redo`, xem
  thủ công) biết câu nào đã bị đổi giọng.
- Thêm cờ config `tts.allow_edge_fallback` (mặc định `true`, không đổi hành
  vi hiện tại đột ngột) — đặt `false` thì driver không được gọi
  `fallback_edge_tts`, lỗi phải `raise` thật để `CapCutError` ở lớp ngoài bắt
  được và retry đúng nghĩa, thay vì trả một kết quả "thành công" giả.

Test (`tests/test_capcut_tts.py`, hiện chưa có test nào chạm nhánh này —
xem mục 0.0.c):
- Driver trả JSON có phần tử `"engine": "edge_tts_fallback"` →
  `synthesize_batch` set đúng field tương ứng trên `TTSResult`, không nuốt
  thầm lặng, và gọi notifier đúng 1 lần với đúng danh sách câu bị fallback.
- `tts.allow_edge_fallback = false` + driver báo lỗi → `CapCutError` được
  ném, không rơi vào audio giả.
- `manifest.json` sau `run_with` ([tts.py:41](../../../src/reup/stages/tts.py))
  có trường `engine` cho từng câu.
- Notifier lỗi/không cấu hình (`Notifier()` no-op, xem `notify.py:264-277`)
  không làm vỡ luồng TTS — cảnh báo Telegram là best-effort, không phải điều
  kiện thành công của stage.

### ✅ Xong phần 1 khi

- [ ] `uv run pytest -q` pass hết
- [ ] `uv run reup benchmark` cho thấy `tts` nhanh hơn rõ rệt so với số đo ở 0.1
      trên cùng một job (kỳ vọng: giảm gần theo hệ số `batch_size`, tuỳ độ trễ
      mạng thật của CapCut và việc task batch có hay fail nguyên khối không)
- [ ] Chạy `--all` với vài job liên tiếp không thấy `CapCutError` mới xuất hiện
      so với trước khi sửa
- [ ] `redo --from tts` trên job chỉ đổi 1 câu không gọi lại API cho câu khác
- [ ] `tts/manifest.json` của job mới chạy có trường `engine` cho mọi câu, và
      nếu ép driver giả lỗi CapCut, thấy đúng cảnh báo fallback thay vì im lặng
      **và** thấy tin nhắn cảnh báo tới Telegram (test với notifier giả/mock,
      không gửi tin thật trong test)

---

## 1'. Phiên âm cho TTS bằng AI, sửa lỗi đọc sai từ (thêm 15/09)

**Vấn đề:** `tts.py:43` đưa thẳng `job.translation_json` (bản dịch tiếng
Việt, có thể còn sót từ nước ngoài, số, viết tắt) cho CapCut TTS đọc — không
qua bước chuẩn hoá cách đọc nào ngoài `sanitize_for_tts`
([text.py:56-73](../../../src/reup/text.py), chỉ xoá/thay chữ Hán còn sót và
ký tự lạ, không xử lý phiên âm). Kết quả: CapCut đọc sai những từ như
"Hello", số điện thoại, viết tắt — vì CapCut chỉ có giọng đọc tiếng Việt
(`Voice.json`), không tự phiên âm từ nước ngoài.

**Không sửa trực tiếp `translation.json`** — file này là bản dịch hiển thị
cho trang Duyệt xem lại; nhét "Hê lô" vào đó sẽ làm hỏng bản duyệt của người
xem.

### Task 1'.1 — Stage mới `pronounce`, chạy AI phiên âm sau `fit`, trước `tts`

**Sửa:**
- File mới `src/reup/stages/pronounce.py`, đăng ký `StageSpec(name="pronounce",
  produces=("tts/pronunciation.json",), ...)` giữa `fit` và `tts` trong thứ tự
  pipeline (xem chỗ đăng ký stage hiện có, cùng chỗ `fit`/`tts` đã đăng ký).
- Đọc `job.translation_json` **sau khi `fit` đã ghi đè** (fit rewrite câu cho
  vừa khung thời gian, xem [fit.py:184](../../../src/reup/stages/fit.py)) —
  đây là bản văn bản cuối cùng trước khi thành giọng đọc.
- Gọi LLM theo batch, cùng pattern `translate.py` (`BATCH_SIZE`, xem
  [translate.py:52,217-223](../../../src/reup/stages/translate.py)): batch
  15 câu/lần, dùng `make_llm(cfg, "pronounce")` (role LLM mới, cấu hình qua
  `[llm.pronounce]` trong `config.toml`, cùng cơ chế `cfg.llm_for(role)` các
  role khác đang dùng).
- Prompt gồm: (a) toàn bộ nội dung file rule phiên âm
  `docs/tts_pronunciation_rules.md` (đường dẫn cấu hình qua
  `tts.pronunciation_rules_path`, xem Task 1'.2), (b) danh sách câu trong
  batch kèm `id`, (c) ràng buộc rõ: **chỉ sửa từ/cụm cần phiên âm** (từ nước
  ngoài, số, viết tắt, ký hiệu), **giữ nguyên số âm tiết/cấu trúc câu** — vì
  `fit` đã tính khớp thời lượng dựa trên bản dịch gốc, viết lại quá tay sẽ
  lệch timing.
- Output JSON `{id: tts_text}`; ghi ra `job.tts_dir / "pronunciation.json"`
  (dùng `atomic_write`, cùng cơ chế các stage khác) — **không đụng**
  `translation.json`.
- Dùng lại `complete_json_with_repair` ([llm.py:31-68](../../../src/reup/adapters/llm.py))
  cho retry/parse JSON, cùng cơ chế `translate.py` đang dùng — không phát
  minh lại.
- Câu nào AI không trả về (mất `id` trong response, giống case
  [translate.py:225-240](../../../src/reup/stages/translate.py)) → giữ
  nguyên `text` gốc làm `tts_text` (an toàn hơn là raise lỗi chặn cả job vì
  một câu AI bỏ sót).

### Task 1'.2 — File rule phiên âm, markdown viết tay

**Sửa:**
- Tạo `docs/tts_pronunciation_rules.md` — viết bằng tiếng Việt tự nhiên, có
  ví dụ mẫu: từ tiếng Anh phổ biến ("Hello" → "Hê lô"/"Hế lô" tuỳ ngữ cảnh vui
  hay nghiêm túc), số điện thoại/số dài đọc rời từng chữ số, viết tắt đọc
  theo cách người Việt hay đọc (ví dụ "AI" → "Ây Ai" hoặc giữ nguyên tuỳ ngữ
  cảnh), đơn vị đo, v.v. Không cần đầy đủ ngay — bạn tự bổ sung dần, file này
  không phải code nên sửa không cần deploy lại gì.
- `config.toml` thêm `tts.pronunciation_rules_path` trỏ tới file này (theo
  đúng convention field khác trong `[tts]`).

### Task 1'.3 — `tts.py` đọc từ `pronunciation.json` nếu có

**Sửa:**
- `run_with` ([tts.py:41-50](../../../src/reup/stages/tts.py)): nếu
  `job.tts_dir / "pronunciation.json"` tồn tại, dùng `tts_text` của từng câu
  (map theo `id`) thay cho `seg.text` khi gọi `adapter.synthesize`/
  `synthesize_batch`; nếu file chưa tồn tại (job cũ, chưa chạy qua
  `pronounce`), fallback dùng `seg.text` như hiện tại — không phá job cũ.

Test (`tests/test_pronounce.py` mới, `tests/test_tts.py` cập nhật):
- LLM giả trả về `{id: tts_text}` cho batch → `pronunciation.json` ghi đúng
  nội dung, đúng thứ tự.
- Câu bị AI bỏ sót `id` → `tts_text` = `text` gốc, không raise.
- `tts.py` có `pronunciation.json` → gọi TTS với `tts_text`, không phải
  `text` gốc (đếm tham số truyền vào adapter giả).
- `tts.py` không có `pronunciation.json` (job cũ) → gọi TTS với `text` gốc
  như hành vi hiện tại (test hồi quy).
- File rule không tồn tại/đường dẫn sai → lỗi rõ ràng lúc load config hoặc
  lúc chạy stage (không silent bỏ qua phiên âm).

### ✅ Xong phần 1' khi

- [ ] `uv run pytest -q` pass hết
- [ ] Chạy thử 1 job có câu tiếng Anh sót lại (ví dụ "Hello", số điện thoại)
      → `tts/pronunciation.json` có bản phiên âm hợp lý, nghe thử audio đọc
      đúng hơn bản cũ
- [ ] Trang Duyệt vẫn hiển thị `translation.json` gốc, không bị lẫn chữ phiên
      âm kiểu "Hê lô"
- [ ] `redo --from fit` rồi chạy lại `pronounce`/`tts` không lỗi, không đụng
      job cũ chưa có `pronunciation.json`

---

## 2. Giảm lỗi khi render (`compose`, ffmpeg)

### Task 2.1 — Timeout cho `run_ffmpeg`, lỗi rõ thay vì treo im lặng

**Vấn đề:** [ffmpeg.py:24-34](../../../src/reup/media/ffmpeg.py) không có
`timeout` — nếu filtergraph phức tạp làm ffmpeg treo (từng xảy ra, commit
`667cef7`), tiến trình `reup run` đứng im vô thời hạn, không có thông báo lỗi
nào cho tới khi có người tay kill.

**Sửa:**
- Thêm tham số `timeout_s` cho `run_ffmpeg` (mặc định tính theo độ dài video
  nguồn, ví dụ `max(60, duration_s * 4)` — clip ngắn (~20s) vẫn có sàn 60s cho
  máy chậm).
- `except subprocess.TimeoutExpired` → raise `FFmpegError` với thông điệp rõ
  ("ffmpeg treo quá {timeout_s}s, khả năng filtergraph quá nhiều overlay/vùng
  che — xem phần 2.2, 2.3") thay vì để tiến trình treo.
- `compose.py` (nơi gọi `run_ffmpeg` ở [compose.py:348](../../../src/reup/stages/compose.py))
  truyền `timeout_s` tính từ `probe(job.source_video).duration_ms`.

Test (`tests/test_ffmpeg.py` hoặc tương đương): mock `subprocess.run` ném
`TimeoutExpired` → `run_ffmpeg` raise `FFmpegError` chứ không để lộ exception
gốc; timeout mặc định tính đúng theo công thức trên với vài giá trị `duration_s`.

### Task 2.2 — Gộp overlay liền kề, giảm số input/filter theo số câu

**Vấn đề:** [build_subtitle_overlays](../../../src/reup/stages/compose.py:125)
tạo một filter `overlay` **và một input file** cho MỖI câu. Video 40-50 câu
(clip dài, hoặc nói nhanh) ra filtergraph có 40-50 tầng `overlay` nối tiếp —
đây đúng là hình dạng đã gây treo trước đó ở nhánh `build_blur_chain` (sửa ở
`667cef7`), nhưng `build_subtitle_overlays` chưa được sửa theo cùng hướng.

**Sửa (chọn một hướng, ưu tiên hướng A vì ít rủi ro hơn):**

- **Hướng A — giảm input, giữ nhiều overlay:** vẽ **tất cả PNG phụ đề của một
  job lên một sheet ảnh duy nhất** (ví dụ lưới hoặc dán cạnh nhau theo trục Y),
  rồi mỗi overlay filter `crop` đúng ô của mình từ MỘT input ảnh thay vì N input
  ảnh riêng. Giảm số file input (đỡ áp lực demux ffmpeg) nhưng số filter overlay
  không đổi.
- **Hướng B — batch theo `split` một lần** giống `build_blur_chain`: `split`
  input gốc thành N nhánh một lần, mỗi nhánh overlay một PNG có `enable`, rồi
  gộp lại — tránh chuỗi nối tiếp N tầng. Cần đo lại xem `overlay` có bị vấn đề
  tăng theo cấp số nhân giống `split` lồng nhau không (`overlay` không tự nhân
  bản khung hình như `split` lồng nhau, nên rủi ro thấp hơn — nhưng vẫn đáng đo
  ở video nhiều câu trước khi kết luận không cần sửa).

Bắt đầu bằng **đo thật** (Task 0.2) trên job nhiều câu nhất hiện có: nếu thời
gian `compose` không tăng bất thường theo số câu (tuyến tính là bình thường,
mũ là vấn đề), có thể việc này chưa cấp bách — ghi lại số đo vào PR để quyết
định có cần sửa ngay hay để dành.

Test: video giả có ví dụ 50 overlay → `compose` chạy xong trong thời gian hợp
lý (đặt ngưỡng cụ thể sau khi đo ở Task 0.2, ví dụ không quá 2x thời gian với
10 overlay) — test này cần đánh dấu `slow` nếu chạy ffmpeg thật.

### Task 2.3 — Vẽ PNG phụ đề song song

**Vấn đề:** [build_overlays](../../../src/reup/stages/compose.py:284)
gọi `render_fitted`/`render_line` (Pillow, CPU-bound) tuần tự cho từng câu.
Độc lập hoàn toàn giữa các câu — nhân được với `ProcessPoolExecutor` (CPU-bound,
khác TTS là I/O-bound nên không dùng thread).

**Sửa:**
- Bọc vòng `for seg in ...` bằng `ProcessPoolExecutor` (số worker = số lõi CPU,
  hoặc config `profile.concurrency` cho nhất quán với chỗ khác).
- Giữ thứ tự `overlays` theo thứ tự câu gốc (map theo index, như Task 1.1).
- Cẩn thận: hàm `render_fitted`/`render_line` và các giá trị truyền vào (`font`,
  `Config`) phải pickle được cho `ProcessPoolExecutor` — kiểm tra `find_font`
  trả về gì (đường dẫn string thường ổn, object font thì không).

Test: so `overlays` ra từ đường song song và đường tuần tự trên cùng input →
danh sách bằng nhau (thứ tự, toạ độ, đường dẫn PNG).

### ✅ Xong phần 2 khi

- [ ] `uv run pytest -q` pass hết
- [ ] Ngắt mạng/giả treo ffmpeg (mock) → job báo lỗi rõ trong log/Telegram thay
      vì đứng im
- [ ] `compose` trên job nhiều câu (dùng job đo ở 0.2) nhanh hơn hoặc bằng số đo
      cũ, không có job nào mới bị treo
- [ ] Video ra vẫn đúng: phụ đề đúng câu, đúng thời điểm, đúng vị trí (so sánh
      thủ công một job trước/sau)

---

## 3. Việc chung sau khi xong cả hai phần

- Cập nhật bảng "Hiệu năng" trong `README.md` với số đo mới cho `tts` và
  `compose`.
- Config mới thêm ở đợt này (`tts.batch_size`, `tts.max_polls`,
  `tts.allow_edge_fallback`, `tts.pronunciation_rules_path`,
  `[llm.pronounce]`), thêm dòng giải thích ngắn vào mục "Cấu hình" của
  `README.md`, và giá trị mặc định vào `config.toml`.
- Chạy `uv run reup doctor` sau khi đổi — kiểm thêm: file
  `tts.pronunciation_rules_path` tồn tại và đọc được.

## 4. Phạm vi đợt sửa 15/09 (2 lỗi báo cáo)

Đợt này **chỉ làm**: Task 1.0, 1.5, 1' (toàn bộ). **Không làm** trong đợt
này (giữ nguyên trong file làm backlog cho sau): Task 1.2 (nghỉ nhịp chủ
động — bớt cấp bách vì Task 1.0 đã giảm hẳn số request), Task 1.3 (cache
`(voice, text)` cho `redo`), Task 1.4 (driver sống), toàn bộ mục 2 (ffmpeg
timeout/gộp overlay/vẽ PNG song song). Lý do: hai lỗi người dùng báo là
timeout/hỏng TTS và đọc sai từ — không phải tốc độ ffmpeg hay `redo` lặp lại
chi phí thấp; làm gọn đúng phạm vi trước, phần còn lại vẫn có giá trị nhưng
để đợt sau.
