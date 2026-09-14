# reup-video — Tối ưu tốc độ TTS, giảm lỗi khi render ffmpeg

**Goal:** `tts` chạy nhanh hơn và ít gãy giữa chừng; `compose` (ffmpeg dựng
video cuối) không còn treo vô thời hạn khi video nhiều câu / nhiều vùng che.

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

### Task 1.0 — Khớp lại hai lớp timeout (mục 0.0.b)

**Sửa trước tiên, vì Task 1.1 (song song) sẽ làm vấn đề này lộ rõ hơn, không
phải làm nó biến mất:**

- `CapCutTTS.synthesize` truyền thêm `--max-polls` cho driver, tính sao cho
  `max_polls * poll_interval` **nhỏ hơn rõ rệt** timeout ngoài của
  `subprocess.run` (ví dụ trần ngoài = `(max_polls * poll_interval) + 5`,
  thay vì hằng số `15.0` cố định không liên quan gì tới hai tham số kia).
- Mục tiêu: khi driver tự bỏ cuộc (hết `max_polls`), nó luôn có đủ thời gian
  `return`/`raise` bình thường trước khi lớp ngoài kịp `TimeoutExpired` — lớp
  timeout ngoài chỉ nên bắt trường hợp thật sự treo (driver không phản hồi gì,
  ví dụ mạng rớt giữa chừng), không bắt nhầm trường hợp driver đang poll đúng
  quy trình.

Test: với `max_polls=10, poll_interval=1.0`, timeout ngoài tính ra phải >
10s; đổi `poll_interval` → timeout ngoài đổi theo, không còn là hằng số `15.0`.

### Task 1.1 — Chạy song song nhiều câu, giới hạn concurrency

**Vấn đề:** `synthesize_all` ([tts.py:20](../../../src/reup/stages/tts.py)) gọi
`adapter.synthesize` tuần tự. Mỗi lần gọi là một subprocess độc lập, không
phụ thuộc câu trước — chạy song song được. Nhưng server CapCut gãy sau ~15
request liên tiếp, nên không thể thả hết N câu chạy cùng lúc không giới hạn.

**Sửa:**
- Thêm tham số `concurrency` (đọc từ `config.toml`, ví dụ `[tts].concurrency`,
  mặc định `3` — đủ ẩn độ trễ round-trip mà không dồn dập tới ngưỡng gãy).
- Dùng `concurrent.futures.ThreadPoolExecutor(max_workers=concurrency)` +
  `pool.map` bọc quanh vòng lặp trong `synthesize_all` — **cùng pattern** đã
  dùng cho chạy nhiều job song song ở
  [core/runner.py:179-183](../../../src/reup/core/runner.py) (`run_jobs`), để
  nhất quán trong repo thay vì phát minh cách khác. Đây là song song **trong
  một stage của một job** (khác trục với `profile.concurrency` — số job chạy
  cùng lúc), nên cần tên config riêng (`tts.concurrency`), không tái dùng
  `profile.concurrency`.
- **Không** áp dụng cách né `ThreadPoolExecutor` mà `web/runner.py` dùng cho
  job chạy nền (daemon thread + semaphore, xem comment ở đầu file đó) — lý do
  họ né là vì cần một tiến trình nền **sống ngoài** một request HTTP, không
  áp dụng ở đây: `synthesize_all` là một lời gọi hàm đồng bộ, xong là trả kết
  quả ngay, không cần semaphore sống ngoài scope hàm.
- Giữ **thứ tự kết quả** đúng theo `segments` gốc (map theo index, không phải
  theo thứ tự hoàn thành) — `entries` phải khớp thứ tự câu như code cũ, chỗ
  gọi sau (`fit`, `compose`) đang dựa vào thứ tự này.

Test (`tests/test_tts.py`):
- adapter giả trả về theo thời gian ngẫu nhiên (dùng `time.sleep` giả qua
  fake clock hoặc chỉ đơn giản trả ngay) → `entries` vẫn đúng thứ tự `seg.id`
  dù chạy song song.
- `concurrency=1` cho kết quả giống hệt code tuần tự cũ (test hồi quy).

### Task 1.2 — Nghỉ nhịp chủ động thay vì đợi gãy rồi retry

**Vấn đề:** Ngưỡng gãy ~15 request liên tiếp là biết trước, nhưng code hiện
tại ([capcut_tts.py:110-126](../../../src/reup/adapters/capcut_tts.py)) chỉ
retry *sau khi* đã gãy (backoff `2**attempt` giây). Với concurrency ở Task 1.1,
rủi ro chạm ngưỡng tăng lên nếu không kiểm soát.

**Sửa:**
- Thêm bộ đếm request liên tiếp trong `CapCutTTS` (đơn giản: đếm số lần gọi
  `synthesize` thành công liên tục); sau mỗi `pause_every` request (config,
  mặc định `12` — dưới ngưỡng biết là 15) thì `sleep(pause_seconds)` (mặc định
  `2s`) trước khi tiếp tục.
- Bộ đếm này phải **dùng chung** giữa các thread khi kết hợp với Task 1.1 (một
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

### Task 1.5 — Fallback edge-tts ẩn trong `capcut_driver.py` phải nổi lên (mục 0.0.c)

**Đây là phần quan trọng nhất của kế hoạch này** — không phải tối ưu tốc độ,
mà sửa một chỗ code hiện tại đang **âm thầm vi phạm bất biến mà chính nó ghi
ra trong docstring**.

**Sửa (không phải xoá `fallback_edge_tts` — nó vẫn có giá trị làm phao cứu
sinh khi CapCut chết hẳn, chỉ là phải BÁO cho biết):**

- `capcut_driver.py`: mọi nhánh gọi `fallback_edge_tts` phải in kèm cờ
  `"engine": "edge_tts_fallback"` trong JSON trả về (hiện chỉ có `path`,
  `bytes`, `duration_ms`, `hit_cache`); đường thành công CapCut thật thì in
  `"engine": "capcut"` để phân biệt tường minh, không suy luận ngược từ việc
  thiếu trường.
- `capcut_tts.py`: đọc trường `engine` từ JSON; nếu là `edge_tts_fallback`
  thì **log cảnh báo rõ ràng** (câu nào, job nào) — tối thiểu là logging,
  tốt hơn là đẩy lên kênh Telegram sẵn có (xem `[notify]` trong
  `config.toml`) vì đây là thứ người vận hành cần biết ngay, không phải đào
  log mới thấy.
- `TTSResult` ([adapters/tts.py](../../../src/reup/adapters/tts.py) — xem
  định nghĩa) nên có thêm field `engine: str` để `write_manifest`
  ([tts.py:30](../../../src/reup/stages/tts.py)) ghi vào
  `tts/manifest.json` — job sau này (`redo`, xem thủ công) biết câu nào đã
  bị đổi giọng.
- Cân nhắc thêm cờ config `tts.allow_edge_fallback` (mặc định `true` để
  không đổi hành vi hiện tại đột ngột) — đặt `false` thì driver không được
  gọi `fallback_edge_tts`, lỗi phải `raise` thật để `CapCutError` ở lớp ngoài
  bắt được và retry đúng nghĩa, thay vì trả một kết quả "thành công" giả.

Test (`tests/test_capcut_tts.py`, hiện chưa có test nào chạm nhánh này —
xem mục 0.0.c):
- Driver trả JSON có `"engine": "edge_tts_fallback"` → `CapCutTTS.synthesize`
  set đúng field tương ứng trên `TTSResult`, không nuốt thầm lặng.
- `tts.allow_edge_fallback = false` + driver báo lỗi → `CapCutError` được ném,
  không rơi vào audio giả.
- `manifest.json` sau `run_with` ([tts.py:41](../../../src/reup/stages/tts.py))
  có trường `engine` cho từng câu.

### ✅ Xong phần 1 khi

- [ ] `uv run pytest -q` pass hết
- [ ] `uv run reup benchmark` cho thấy `tts` nhanh hơn rõ rệt so với số đo ở 0.1
      trên cùng một job (kỳ vọng: gần chia đôi với `concurrency=3`, tuỳ độ trễ
      mạng thật của CapCut)
- [ ] Chạy `--all` với vài job liên tiếp không thấy `CapCutError` mới xuất hiện
      so với trước khi sửa
- [ ] `redo --from tts` trên job chỉ đổi 1 câu không gọi lại API cho câu khác
- [ ] `tts/manifest.json` của job mới chạy có trường `engine` cho mọi câu, và
      nếu ép driver giả lỗi CapCut, thấy đúng cảnh báo fallback thay vì im lặng

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
- Nếu thêm config mới (`tts.concurrency`, `tts.pause_every`, ...), thêm dòng
  giải thích ngắn vào mục "Cấu hình" của `README.md`, và giá trị mặc định vào
  `config.toml`.
- Chạy `uv run reup doctor` sau khi đổi — không có kiểm mới cần thêm ở đây trừ
  khi Task 1.4 (driver sống) được làm, lúc đó `doctor` cần biết kiểm driver còn
  sống hay không.
