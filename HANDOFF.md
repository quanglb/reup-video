# Bàn giao — nhánh `feat/discover-va-chay-tu-web`

Ngày 12/09/2026. Viết cho người/agent tiếp nhận, không cần đọc lại hội thoại cũ.

Trạng thái: **6 commit, cây làm việc sạch, 583 test pass.** Chưa merge vào `main`.

```bash
uv run reup doctor      # kiểm môi trường — phải xanh trước khi làm gì
uv run pytest -q        # 583 passed
```

---

## 1. Việc tiếp theo, theo thứ tự ưu tiên

### 1.1 Chạy trọn một job với cấu hình lai — VIỆC QUAN TRỌNG NHẤT

`config.toml` đang để `reconcile`, `fit`, `export` chạy `qwen3:8b` local, chỉ
`translate` còn dùng Gemini. **Cấu hình này chưa bao giờ chạy trọn một job.**

Rủi ro cụ thể: model local phải trả JSON đúng schema dưới prompt thật. Đã đo
được `translate` làm được (46s, 23/23 câu), nhưng ba stage kia thì chưa:

- `export` cần `{title, description, hashtags[]}` — mảng hashtag là chỗ dễ hỏng
- `reconcile` cần `{choices: [{id, pick: "asr"|"ocr"}]}` và prompt **cấm** viết
  câu mới; model nhỏ hay "giúp đỡ" bằng cách sửa chữ
- `fit` cần `{segments: [{id, text}]}` và phải giữ nguyên id

Cách làm:

```bash
uv run reup web                       # chọn một video ở tab YouTube
# hoặc: uv run reup add "<url>" && uv run reup run <job_id>
```

Chạy tới `done`, rồi mở `output/<job_id>.json` xem metadata có hợp lý không.
Hỏng ở stage nào thì **đọc kỹ lời báo lỗi trước khi sửa** — adapter Ollama có
vòng sửa JSON tự động 2 lần, nên nếu vẫn hỏng là model thật sự không làm được,
không phải lỗi ngẫu nhiên.

Nếu một stage không ổn: đổi riêng stage đó về `gemini` trong `config.toml` (mỗi
stage một bảng con), hoặc thử `qwen3:14b` / `gemma3:12b` (RAM 16GB vẫn chịu
được vì các stage chạy tuần tự).

### 1.2 Cookie cho TikTok và Douyin

Tab TikTok/Douyin đã dựng xong nhưng **chưa quét được lần nào**. TikTok chặn
khách vãng lai (`ERROR: No working app info is available`), Douyin cần thêm IP
ra được Trung Quốc.

```toml
[discover.tiktok]
cookies_from_browser = "chrome"   # hoặc cookie_file = "/đường/dẫn/cookies.txt"
```

Sau khi đặt, quét thử ở `http://127.0.0.1:8765/discover?platform=tiktok`. Trang
hiện nguyên lời `yt-dlp` khi hỏng, dùng đó để chẩn đoán.

### 1.3 Merge vào `main`

Nhánh này là một chuỗi tuyến tính trên `main`, fast-forward được.

### 1.4 Vụn

- `uv.lock` đang untracked, có từ trước nhánh này. Nên commit để dựng lại được
  môi trường, nhưng đó là quyết định của chủ dự án.
- `review.auto_approve_b` đang `false`. Bật `true` khi đã tin dây chuyền thì
  bớt được một lần bấm mỗi video.

---

## 2. Quyết định đã chốt — đừng lật lại nếu không có dữ liệu mới

**Giữ Gemini cho `translate`.** Đo trên job `20260912-094648-bdb467ec` (23 câu,
video dạy nấu ăn tiếng Trung): qwen3:8b vượt trần âm tiết **6/23 câu** trong khi
Gemini 0/23, và sai nghĩa ở chỗ cần suy luận — ASR nghe nhầm `拍蒜` (tỏi đập)
thành `拍饭`, Gemini đoán đúng theo ngữ cảnh nấu ăn, model local dịch thành
"gạo"; `小炒肉` (thịt xào) thành "thịt kho tàu". Ba stage còn lại không cần suy
luận kiểu đó.

**Không tự động hoá giao diện web Gemini bằng Chromium.** Vi phạm điều khoản
Google, rủi ro mất cả tài khoản. Về kỹ thuật cũng tệ hơn: chat web trả văn xuôi
tự do nên mất ràng buộc `response_schema`, mà chính ràng buộc đó làm pipeline
chạy được.

**`fit` gọi LLM theo vòng, không theo câu.** Mỗi vòng gom mọi câu còn dài vào
một request. Vẫn phải chia vòng chứ không gộp tất vào một lượt: có tổng hợp và
đo lại mới biết câu đã vừa khe chưa. Đừng "tối ưu" thành một lượt duy nhất.

**Nới `TEMPO_CEILING` không còn giúp tiết kiệm quota.** Đã đo: sau khi `fit`
gộp request, nó tốn tối đa 2 request bất kể 1 hay 9 câu cần viết lại. Nới trần
lên 1.3 vẫn còn 1 câu, vẫn 2 request.

**Tab quét không tự quét khi mở.** Cố ý — một lần gọi `yt-dlp` mất hàng chục
giây, để nó tự nổ mỗi lần đổi tab thì tab nào cũng treo. Phải bấm **Quét**.

---

## 3. Bẫy đã mất thời gian để tìm ra

**Luôn chạy bằng `uv run`.** Gọi thẳng `.venv/bin/python -m reup.cli` không đưa
`.venv/bin` vào PATH, nên `yt-dlp` biến mất và mọi lần quét lẫn tải đều hỏng.
`reup doctor` bắt được lỗi này và chỉ thẳng nguyên nhân.

**`This video is not available` không có nghĩa là video bị gỡ.** YouTube bắt
giải JS challenge; thiếu JS runtime thì yt-dlp tụt xuống player visionos và
**mọi** video đều hỏng như nhau. `fetch` tự dò `deno`/`node`/`bun` và thêm
`--remote-components ejs:github` (tải script từ GitHub rồi chạy trên máy — tắt
được bằng `fetch.remote_components = ""`, đổi lại YouTube hỏng).

**Web UI chạy job trong luồng nền của chính tiến trình uvicorn.** Sửa code xong
phải **khởi động lại server** mới có hiệu lực. Từng mất thời gian vì server cũ
chạy code cũ và trả 404 cho route mới.

**API CapCut gãy sau khoảng 15 request liên tiếp.** Adapter tự thử lại có
backoff — chờ, đừng bấm lại.

**Server CapCut cache theo *text*, không theo giọng.** Nên sửa chữ ở chốt A thì
tổng hợp lại cả loạt gần như miễn phí, nhưng đổi giọng thì tốn lượt gọi thật.

---

## 4. Bản đồ những gì đã làm

| Commit | Nội dung |
|---|---|
| `7ff6679` | Tab quét YouTube/TikTok/Douyin, xem iframe, chạy job từ Web UI, fix JS challenge |
| `4b252cb` | `fit` gộp request LLM theo vòng (giá job: `3 + 2×số câu vượt trần` → tối đa 5) |
| `a2e6018` | Adapter Ollama, tách vòng sửa JSON dùng chung cho mọi provider |
| `02bc7d0` | Test thôi ghi vào `output/` thật |
| `8a2d681` | `[llm.<stage>]` — cấu hình LLM riêng từng stage |
| `dd561cf` | `reup doctor` |

File mới đáng biết:

- `src/reup/adapters/crawl.py` — phần chung ba crawler
- `src/reup/adapters/{tiktok,douyin,ollama}.py`
- `src/reup/web/runner.py` — luồng nền chạy job
- `src/reup/doctor.py`
- `src/reup/web/templates/discover.html`

Runbook vận hành (dựng cho chủ dự án, có sơ đồ 12 stage và bảng gỡ lỗi):
https://claude.ai/code/artifact/aa7b42ce-4ad2-4f50-80f5-daa7129f6500

---

## 5. Quy ước của repo này

Đọc `README.md` và docstring trước khi sửa — dự án ghi **lý do** vào docstring,
kể cả lý do đã thử cách khác và bỏ. Ví dụ `fit.py` giải thích vì sao `rate` của
CapCut vô dụng (đo: rate 1.5 chỉ ngắn 5.9%), `youtube.py` giải thích vì sao
`/feed/trending` chết.

Comment và commit message viết bằng tiếng Việt, nói lý do chứ không nói lại
code. Test đặt tên theo hành vi, và docstring test nói rõ *vì sao* hành vi đó
quan trọng.
