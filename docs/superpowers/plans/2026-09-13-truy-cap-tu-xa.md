# reup-video — Chạy trên máy server, dùng từ máy khác mạng

**Goal:** Một máy Mac làm server chạy `reup web` 24/7. Bạn mở web UI từ máy khác
(laptop, điện thoại) ở **mạng khác**, làm được mọi việc như ngồi trước máy server.
Người ngoài không vào được.

**Ngày viết:** 13/09/2026. Viết để tự làm theo, không cần đọc lại hội thoại.

---

## 0. Tóm tắt quyết định

| Câu hỏi | Chọn | Vì sao |
|---|---|---|
| Máy server | **Mac Apple Silicon** | Code dùng `h264_videotoolbox`, Apple Vision (`ocrmac`), `mlx-whisper`, Demucs `mps`, AppleScript điều khiển Chrome. Linux/Windows không chạy được |
| Nối 2 mạng | **Tailscale** + `tailscale serve` | Không mở port router, có HTTPS, chỉ máy trong tài khoản của bạn thấy server |
| Web app nghe ở đâu | **Giữ `127.0.0.1`** | `tailscale serve` chuyển tiếp vào loopback. Không phơi cổng 8765 ra LAN của server |
| Đăng nhập | **Thêm mật khẩu vào web app** (phần 4) | Hiện app không có xác thực. Tailscale là lớp 1, mật khẩu là lớp 2 |
| Công khai internet | **Không** ở giai đoạn này | Chỉ cân nhắc Cloudflare Tunnel + Access sau khi có mật khẩu (phụ lục B) |

Sơ đồ:

```
[Máy dùng — mạng A]                         [Máy server Mac — mạng B]
 trình duyệt                                  tailscale serve (HTTPS :443)
   │  https://<ten-may>.<tailnet>.ts.net          │
   └──────── mạng riêng Tailscale (WireGuard) ────┘
                                                   ▼
                                           reup web 127.0.0.1:8765
                                             ├─ 9router  localhost:20128
                                             ├─ capcut-tts-api (subprocess)
                                             ├─ Chrome đã đăng nhập Douyin
                                             └─ jobs/ output/ reup.db
```

Thứ tự làm: **1 → 2 → 3 → 4 → 5 → 6**. Phần 1–3 và 5–6 là thao tác máy, phần 4
là sửa code (có test). Làm phần 3 xong là **đã dùng được từ xa**; phần 4 nên
làm ngay sau đó, trước khi để server chạy qua đêm.

---

## 1. Chuẩn bị máy server

> Nếu máy server **chính là máy đang code hiện tại** thì bỏ qua 1.2 và 1.4,
> chỉ làm 1.1, 1.3, 1.5.

### 1.1 Cài công cụ

```bash
xcode-select --install
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
brew install ffmpeg uv deno git
brew install --cask google-chrome tailscale
```

Kiểm:

```bash
ffmpeg -hide_banner -encoders | grep videotoolbox   # phải thấy h264_videotoolbox
uv --version && deno --version
```

### 1.2 Lấy code + phụ thuộc ngoài repo

```bash
mkdir -p /Applications/_QuangLB/Workspace/VideoProject
cd /Applications/_QuangLB/Workspace/VideoProject
git clone <remote-của-repo> reup-video
cd reup-video
uv venv --python 3.12
uv pip install -e .
```

Những thứ **không nằm trong git** phải mang sang tay:

| Thứ | Ở máy cũ | Ghi chú |
|---|---|---|
| `capcut-tts-api` | `/Applications/_QuangLB/Workspace/Agent/capcut-tts-api` | Đặt đúng đường dẫn trong `[tts].capcut_dir`, hoặc sửa config. Có `.venv` Python 3.9 riêng — tạo lại trên server |
| 9router | chạy ở `localhost:20128` | Cài và bật trên server; `[llm].base_url` giữ nguyên |
| `.env` | gốc repo | `OPENAI_API_KEY`, `GEMINI_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` |
| Font `Be Vietnam Pro` | Font Book | Thiếu thì phụ đề ra font khác |
| `config.toml` | gốc repo | Có trong git, nhưng xem lại `[profile].active` (1.3) |

### 1.3 Chọn profile theo máy server

Trong `config.toml`, `[profile].active` đang là `air-16`. Server mạnh (Mac mini
M4 Pro / Studio, RAM ≥ 24GB) thì đổi sang `studio-24` (`concurrency = 2`).
Server là M1/M2 thì bật `prefer_h264 = true` (không có hardware AV1 decode).

### 1.4 Chuyển dữ liệu cũ (nếu muốn giữ lịch sử)

**Tắt `reup web` ở máy cũ trước** — `reup.db` đang ở chế độ WAL, copy lúc đang
chạy dễ ra file hỏng.

```bash
# chạy ở máy cũ, thay <server> bằng tên Tailscale hoặc IP LAN tạm thời
rsync -avh --progress \
  reup.db jobs output douyin-exports tags.json title-vi-cache.json \
  <server>:/Applications/_QuangLB/Workspace/VideoProject/reup-video/
```

Không copy `reup.db-shm` / `reup.db-wal` khi app đã tắt hẳn (SQLite tự gộp lúc tắt).

**Không** đặt `jobs/` hay `reup.db` lên iCloud / NAS / thư mục đồng bộ: SQLite
trên ổ mạng hay khoá và hỏng.

### 1.5 Quyền macOS cho Douyin + Chrome (phải ngồi trước máy server, một lần)

1. Mở Chrome, đăng nhập Douyin bằng profile `quangxa14@gmail.com`.
2. Chrome → View → Developer → **Allow JavaScript from Apple Events**.
3. Chạy thử nút Quét lại một kênh (sau phần 2). macOS hỏi quyền điều khiển
   Chrome → Allow. Nếu lỡ từ chối: System Settings → Privacy & Security →
   Automation → bật Google Chrome cho Terminal (hoặc cho `uv`/`python` khi
   chạy bằng launchd, xem 5.2).
4. Kiểm môi trường:

```bash
uv run reup doctor     # phải xanh hết
uv run reup telegram test
```

### ✅ Xong phần 1 khi

- [ ] `uv run reup doctor` xanh
- [ ] `uv run reup web` rồi mở `http://127.0.0.1:8765` **trên chính server** thấy Hàng đợi
- [ ] Chạy trọn 1 job ngắn trên server ra được file trong `output/`

---

## 2. Tailscale

### 2.1 Cài và đăng nhập

1. Server: mở app Tailscale → Log in (Google `quangxa14@gmail.com`).
2. Máy dùng (laptop / điện thoại): cài Tailscale, đăng nhập **cùng tài khoản**.
3. Vào https://login.tailscale.com/admin/machines kiểm thấy cả 2 máy.

### 2.2 Cấu hình trong trang admin

- **DNS → MagicDNS: bật.** Server có tên cố định dạng `<ten-may>.<tailnet>.ts.net`.
- **DNS → HTTPS Certificates: bật** (cần cho `tailscale serve` ra HTTPS).
- **Machines → server → ⋯ → Disable key expiry.** Không tắt thì 180 ngày sau
  server tự rớt khỏi mạng, đang ở xa là không vào được.
- Đổi tên máy server cho dễ nhớ, ví dụ `reup-server`.

### 2.3 Kiểm kết nối (từ máy dùng, đang ở mạng khác)

```bash
tailscale status              # thấy reup-server, trạng thái active/idle
tailscale ping reup-server    # "via DERP" vẫn chạy được, "direct" thì nhanh hơn
```

Muốn thử thật là "khác mạng": tắt Wi-Fi laptop, phát 4G từ điện thoại.

### ✅ Xong phần 2 khi

- [ ] `tailscale ping reup-server` từ máy dùng có phản hồi khi ở mạng khác

---

## 3. Mở web UI ra Tailscale

### 3.1 Bật web (vẫn nghe loopback)

```bash
cd /Applications/_QuangLB/Workspace/VideoProject/reup-video
uv run reup web            # mặc định 127.0.0.1:8765 — KHÔNG đổi sang 0.0.0.0
```

### 3.2 Chuyển tiếp qua Tailscale

Chạy trên server (CLI nằm trong app: `/Applications/Tailscale.app/Contents/MacOS/Tailscale`,
nên tạo alias `tailscale`):

```bash
tailscale serve --bg 8765
tailscale serve status     # in ra https://reup-server.<tailnet>.ts.net
```

`--bg` giữ cấu hình qua lần khởi động lại. Gỡ bằng `tailscale serve reset`.

> **Tuyệt đối không** dùng `tailscale funnel` — funnel mở ra cả internet.

### 3.3 Sửa link Telegram

`config.toml`:

```toml
[notify]
web_url = "https://reup-server.<tailnet>.ts.net"
```

Khởi động lại `reup web`. Link "Mở job" trong tin Telegram giờ mở được từ
điện thoại (điện thoại phải bật Tailscale).

### 3.4 Chỉ một nơi chạy bot

Bot Telegram nghe lệnh bằng long-polling. **Hai máy cùng chạy `reup web` với
cùng `TELEGRAM_BOT_TOKEN`** thì Telegram trả lỗi 409 Conflict và lệnh lúc được
lúc không. Từ giờ chỉ server chạy `reup web`. Máy dev nếu cần chạy web để test
thì đặt `[notify] commands = false` ở bản config local.

### ✅ Xong phần 3 khi (thử từ máy dùng, mạng khác)

- [ ] Mở `https://reup-server.<tailnet>.ts.net` thấy Hàng đợi
- [ ] Thanh tiến độ tự cập nhật (gọi `/api/progress`)
- [ ] Xem được video nguồn và thành phẩm trong trang job (tua được)
- [ ] Nghe được audio từng câu ở trang duyệt
- [ ] Tắt Tailscale trên máy dùng → **không** vào được nữa
- [ ] Máy khác cùng Wi-Fi với server nhưng không trong tailnet mở `http://<IP-LAN-server>:8765` → **không** vào được

---

## 4. Thêm mật khẩu cho web app (sửa code)

Hiện `create_app` ([app.py:27](../../../src/reup/web/app.py)) không có lớp
xác thực nào: ai tới được là tạo job, xoá dự án, tiêu hạn mức AI được.

### 4.1 Thiết kế

- Mật khẩu đọc từ biến môi trường `REUP_WEB_PASSWORD` (để trong `.env`).
  **Không có biến → không bật xác thực**, để các test hiện có không phải sửa.
- Đăng nhập một lần → cookie `reup_session` sống 30 ngày.
- Cookie = `"{hết_hạn}.{hmac_sha256(secret, hết_hạn)}"`. Không cần thêm
  dependency (không dùng `itsdangerous`/`SessionMiddleware`), chỉ `hmac` + `hashlib`.
- `secret` = `REUP_WEB_SECRET` nếu có, không thì dẫn xuất từ mật khẩu
  (`sha256("reup-session:" + password)`). Đổi mật khẩu = mọi phiên cũ mất hiệu lực.
- Cookie: `HttpOnly`, `SameSite=Lax`, `Secure` khi request là HTTPS
  (`X-Forwarded-Proto: https` từ `tailscale serve`, hoặc `request.url.scheme`).
- Đường miễn xác thực: `/login`, `/static/*`.
- Chưa đăng nhập:
  - request trang (`Accept` có `text/html`, method GET) → `303` sang `/login?next=<đường cũ>`
  - còn lại (`fetch` JSON, POST form, video/audio) → `401`
- `next` chỉ nhận đường tương đối bắt đầu bằng `/` và không bắt đầu bằng `//`
  (chặn open redirect).
- So mật khẩu bằng `hmac.compare_digest`. Sai → chờ 1 giây rồi trả trang login
  kèm lỗi (làm chậm dò mật khẩu).
- Nút **Đăng xuất** (`POST /logout`) xoá cookie.
- CSRF: `SameSite=Lax` đã chặn POST cross-site kèm cookie; đủ cho app một người dùng.

### 4.2 File đổi

```
src/reup/web/
  auth.py                 MỚI  make_token / verify_token / safe_next (hàm thuần)
  app.py                  SỬA  middleware + /login GET/POST + /logout
  templates/login.html    MỚI  form một ô mật khẩu, dùng lại app.css
  templates/*.html        SỬA  nút Đăng xuất ở thanh trên (chỉ hiện khi bật auth)
src/reup/cli.py           SỬA  cảnh báo khi chạy web mà thiếu REUP_WEB_PASSWORD
.env.example              SỬA  thêm REUP_WEB_PASSWORD=, REUP_WEB_SECRET=
tests/test_web_auth.py    MỚI
```

### 4.3 Task

**Task 4.1 — `auth.py` (hàm thuần, test trước)**

```python
# src/reup/web/auth.py
COOKIE = "reup_session"
MAX_AGE = 30 * 24 * 3600

def secret_for(password: str, explicit: str = "") -> bytes: ...
def make_token(secret: bytes, now: float) -> str:          # "exp.sig"
def verify_token(secret: bytes, token: str, now: float) -> bool:
def safe_next(value: str | None) -> str:                    # mặc định "/"
```

Test (`tests/test_web_auth.py`):
- token vừa tạo → hợp lệ
- token quá hạn → không hợp lệ
- sửa 1 ký tự chữ ký / sửa `exp` → không hợp lệ
- token rác (`""`, `"abc"`, `"1.2.3"`) → không hợp lệ, không ném lỗi
- secret khác → không hợp lệ
- `safe_next`: `"/jobs/x"` giữ; `"//evil.com"`, `"https://evil.com"`, `None`, `""` → `"/"`

**Task 4.2 — middleware + route trong `create_app`**

Đọc `os.environ.get("REUP_WEB_PASSWORD", "")` một lần lúc tạo app, lưu vào
`app.state.password`. Rỗng thì không gắn middleware, không tạo route login.

```python
@app.middleware("http")
async def require_login(request: Request, call_next):
    path = request.url.path
    if path == "/login" or path.startswith("/static/"):
        return await call_next(request)
    token = request.cookies.get(auth.COOKIE, "")
    if auth.verify_token(app.state.session_secret, token, time.time()):
        return await call_next(request)
    wants_page = request.method == "GET" and "text/html" in request.headers.get("accept", "")
    if wants_page:
        return RedirectResponse(f"/login?{urlencode({'next': path})}", status_code=303)
    return JSONResponse({"detail": "cần đăng nhập"}, status_code=401)
```

Test (dùng `monkeypatch.setenv("REUP_WEB_PASSWORD", "pw")` rồi mới `create_app`):
- không cookie, GET `/` với `Accept: text/html` → 303 tới `/login?next=%2F`
- không cookie, GET `/api/progress` → 401
- không cookie, POST `/jobs` → 401, **và không có job nào được tạo**
- không cookie, GET `/jobs/<id>/final` → 401
- GET `/static/app.css` → 200
- POST `/login` sai mật khẩu → 200 trang login có thông báo lỗi, không set cookie
  (monkeypatch `time.sleep` để test không chậm)
- POST `/login` đúng, `next=/saved` → 303 tới `/saved`, có `Set-Cookie: reup_session=...; HttpOnly; SameSite=lax`
- POST `/login` đúng, `next=//evil.com` → 303 tới `/`
- sau đăng nhập, GET `/` → 200
- POST `/logout` → cookie bị xoá, GET `/` lại 303
- **không** set env → mọi test cũ trong `tests/test_web_app.py` vẫn pass nguyên

**Task 4.3 — giao diện**

- `login.html`: một ô `type=password` `autocomplete=current-password`, nút Đăng nhập,
  input ẩn `next`. Dùng `app.css` sẵn có, hiển thị tốt trên điện thoại.
- Thanh trên các template: nút Đăng xuất (form POST `/logout`), chỉ render khi
  `request.app.state.password` có giá trị.
- `app.js`: ở các chỗ `fetch(...)` (dòng ~16, 70, 234, 255, 267, 318, 339),
  nếu `res.status === 401` thì `location.href = '/login?next=' + encodeURIComponent(location.pathname)`.
  Gom vào một hàm nhỏ, không lặp 7 lần. Thiếu bước này thì phiên hết hạn giữa
  chừng, thanh tiến độ im lặng đứng yên mà không ai biết vì sao.

**Task 4.4 — CLI**

Trong `_cmd_web` ([cli.py](../../../src/reup/cli.py), trước `uvicorn.run`):
- thiếu `REUP_WEB_PASSWORD` → in cảnh báo vàng: web **không có mật khẩu**, chỉ an
  toàn khi chỉ mở trên máy này.
- thiếu mật khẩu **và** `--host` không phải `127.0.0.1`/`localhost`/`::1` →
  **từ chối chạy**, return 1, in cách đặt biến.

Test: hai nhánh trên bằng `capsys`, không bật uvicorn thật.

**Task 4.5 — cập nhật**

- `.env.example` thêm hai biến.
- `doctor`: thêm dòng kiểm `REUP_WEB_PASSWORD` (vàng nếu thiếu, không đỏ).
- README mục "Dùng": một đoạn ngắn về truy cập từ xa, trỏ tới file plan này.

Chạy toàn bộ: `uv run pytest -q` phải pass hết. Commit sau mỗi task.

### 4.4 Bật trên server

```bash
# .env trên server
REUP_WEB_PASSWORD=<chuỗi dài, sinh bằng: openssl rand -base64 24>
```

Khởi động lại `reup web`. Lưu mật khẩu vào trình quản lý mật khẩu của máy dùng.

### ✅ Xong phần 4 khi

- [ ] `uv run pytest -q` pass hết
- [ ] Từ máy dùng: mở URL → ra trang đăng nhập → đăng nhập → dùng bình thường
- [ ] Mở tab ẩn danh → bị đòi đăng nhập
- [ ] `curl -X POST https://reup-server.<tailnet>.ts.net/jobs` → 401

---

## 5. Giữ server chạy 24/7

### 5.1 Không cho máy ngủ

System Settings:
- **Energy** (Mac mini/Studio): bật *Prevent automatic sleeping when the display is off*,
  bật *Start up automatically after a power failure*, bật *Wake for network access*.
- MacBook làm server: Battery → Options → *Prevent automatic sleeping on power adapter
  when the display is off*; luôn cắm sạc; bật *Optimized Battery Charging*.

Kiểm bằng lệnh:

```bash
pmset -g | grep -E "sleep|autorestart|womp"   # sleep 0, autorestart 1
```

Đặt nhanh bằng lệnh (cần mật khẩu admin, tự gõ):

```bash
sudo pmset -c sleep 0 disksleep 0 autorestart 1 womp 1
```

### 5.2 Tự bật `reup web` khi đăng nhập (launchd)

Dùng **LaunchAgent** (chạy trong phiên người dùng), **không** dùng LaunchDaemon:
AppleScript điều khiển Chrome và `mps` cần phiên GUI.

Tạo `~/Library/LaunchAgents/com.quanglb.reup-web.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.quanglb.reup-web</string>
  <key>WorkingDirectory</key>
  <string>/Applications/_QuangLB/Workspace/VideoProject/reup-video</string>
  <key>ProgramArguments</key>
  <array>
    <string>/opt/homebrew/bin/uv</string>
    <string>run</string><string>reup</string><string>web</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>StandardOutPath</key><string>/tmp/reup-web.log</string>
  <key>StandardErrorPath</key><string>/tmp/reup-web.err</string>
</dict>
</plist>
```

- `PATH` bắt buộc: launchd không đọc `.zshrc`, thiếu thì không thấy `ffmpeg`, `deno`.
- Kiểm đường dẫn `uv` bằng `which uv` rồi sửa cho khớp.
- 9router cũng cần một LaunchAgent tương tự (hoặc cơ chế tự bật của nó).

```bash
launchctl load ~/Library/LaunchAgents/com.quanglb.reup-web.plist
launchctl list | grep reup          # có PID là đang chạy
tail -f /tmp/reup-web.log /tmp/reup-web.err
# khởi động lại sau khi git pull / sửa config:
launchctl kickstart -k gui/$(id -u)/com.quanglb.reup-web
```

Sau khi chạy bằng launchd, quyền Automation (1.5) có thể bị hỏi lại cho `uv`/`python`
→ phải ngồi trước server bấm Allow một lần, rồi thử lại nút Quét lại Douyin.

### 5.3 Tự đăng nhập sau khi mất điện

LaunchAgent chỉ chạy khi có người đăng nhập. Máy khởi động lại mà dừng ở màn
hình đăng nhập thì web không bật.

- System Settings → Users & Groups → **Automatically log in as** = tài khoản chạy reup.
- Tuỳ chọn này **bị khoá nếu bật FileVault**. Phải chọn: tắt FileVault (máy để ở
  nhà, ít rủi ro mất máy) hoặc chấp nhận mất điện thì phải có người tới gõ mật khẩu.
- Tailscale: trong app bật **Launch at login**.

### ✅ Xong phần 5 khi

- [ ] Khởi động lại server, **không chạm vào**, 2 phút sau mở được URL từ máy dùng
- [ ] Để qua đêm, sáng mở vẫn vào được
- [ ] `kill` tiến trình reup → 10 giây sau tự sống lại

---

## 6. Chỉnh trải nghiệm khi dùng từ xa (sửa code, nhỏ)

### 6.1 Nút "Mở thư mục" chạy nhầm máy

`POST /jobs/{id}/reveal` ([app.py:838](../../../src/reup/web/app.py)) gọi
`open -R` + `osascript` → Finder bật lên **trên màn hình server**, người ở xa
không thấy gì.

Sửa:
- Thêm `GET /jobs/{id}/download` trả `FileResponse(path, filename=f"{job.id}.mp4")`
  (header `Content-Disposition: attachment`), ưu tiên file trong `output/`, không
  có thì `final_mp4`. Không có file → 404.
- Giao diện: hiện nút **Tải về** luôn; nút **Mở thư mục** chỉ hiện khi request
  tới từ chính máy server (`request.client.host` là loopback **và** không có
  header `Tailscale-User-Login` / `X-Forwarded-For`).

  > Qua `tailscale serve`, `request.client.host` luôn là `127.0.0.1`, nên chỉ
  > nhìn client host sẽ tưởng mọi người đều ở máy server. Phải xét thêm header.

Test: download trả đúng byte + đúng `Content-Disposition`; job chưa có file → 404;
có header `X-Forwarded-For` thì trang job không render nút Mở thư mục.

### 6.2 Thông báo Douyin khi cần ngồi trước máy

Lỗi quyền AppleScript ([douyin_chrome.py:28-33](../../../src/reup/adapters/douyin_chrome.py))
hiện đang bảo bấm menu trong Chrome. Người ở xa không bấm được. Thêm một câu:
"việc này phải làm trực tiếp trên máy server". Không cần test mới ngoài sửa
chuỗi mong đợi trong test cũ (nếu có).

### 6.3 Upload / video lớn

Không phải sửa gì, chỉ lưu ý: `tailscale serve` không giới hạn kích thước, nhưng
đi qua relay DERP (khi `tailscale ping` báo "via DERP") thì tốc độ vài Mbps, xem
video thành phẩm 8–12 Mbps có thể giật. Tải về rồi xem, hoặc cải thiện kết nối
direct (mở UPnP trên router server).

### ✅ Xong phần 6 khi

- [ ] Từ máy dùng bấm Tải về → nhận file `.mp4` chơi được
- [ ] Từ máy dùng không thấy nút Mở thư mục; ngồi trước server thì thấy

---

## 7. Kiểm thu cuối (làm từ máy dùng, mạng khác, bằng 4G cho chắc)

- [ ] Đăng nhập được, tab ẩn danh bị chặn
- [ ] Quét YouTube → Chọn video → job chạy, thanh tiến độ chạy
- [ ] Nhận tin Telegram, bấm link → mở đúng job (điện thoại bật Tailscale)
- [ ] Bot nhận lệnh `/status`
- [ ] Hàng chờ chạy hàng loạt 3 video
- [ ] Tab Douyin: Quét lại một kênh thành công
- [ ] Tab Tag: dịch tag bằng AI chạy (9router trên server sống)
- [ ] Tải thành phẩm về máy dùng
- [ ] Rút điện server, cắm lại, không chạm → dùng lại được

---

## 8. Sự cố thường gặp

| Triệu chứng | Nguyên nhân / cách sửa |
|---|---|
| URL `ts.net` không mở, `tailscale ping` cũng không | Máy dùng chưa bật Tailscale, hoặc server ngủ / key hết hạn (2.2) |
| `ping` được nhưng trang không mở | `reup web` chết: `launchctl list \| grep reup`, xem `/tmp/reup-web.err`. Hoặc quên `tailscale serve --bg 8765` |
| `cổng 8765 đang bận` trong log | Có bản chạy tay song song với launchd: `lsof -ti tcp:8765 \| xargs kill` |
| Job lỗi ở `tts` | `capcut-tts-api` sai đường dẫn hoặc venv 3.9 chưa tạo trên server |
| Job lỗi ở `translate`/`export` | 9router không chạy trên server (`curl localhost:20128/v1/models`) |
| YouTube báo "not available" | Thiếu `deno` trong `PATH` của launchd (5.2) |
| Bot Telegram lúc nhận lúc không, log 409 | Có máy thứ hai cũng chạy `reup web` (3.4) |
| Quét lại Douyin báo lỗi quyền | Phải ngồi trước server bấm Allow (1.5, 5.2) |
| Bị đòi đăng nhập liên tục | Truy cập bằng `http://` thay vì `https://` nên cookie `Secure` không lưu — dùng đúng URL `https://…ts.net` |

---

## Phụ lục A — Rollback

```bash
tailscale serve reset                                             # gỡ đường vào
launchctl unload ~/Library/LaunchAgents/com.quanglb.reup-web.plist
```

Phần code (4, 6) không ảnh hưởng khi chạy local: không đặt `REUP_WEB_PASSWORD`
thì app chạy y như cũ.

## Phụ lục B — Nếu sau này cần URL công khai (không cài Tailscale ở máy dùng)

Chỉ làm **sau khi phần 4 đã xong**.

1. Cần một domain đã trỏ về Cloudflare.
2. `brew install cloudflared` → `cloudflared tunnel login` → `cloudflared tunnel create reup`.
3. Route `reup.<domain>` → `http://127.0.0.1:8765`.
4. Cloudflare Zero Trust → Access → Application cho `reup.<domain>`, policy chỉ
   cho email `quangxa14@gmail.com` (đăng nhập bằng mã gửi email).
5. Chạy `cloudflared` bằng LaunchAgent như 5.2.

Hai lớp: Cloudflare Access chặn ở ngoài, mật khẩu reup chặn ở trong. **Không**
dùng `trycloudflare.com` tạm thời cho việc này: URL công khai, không có Access.
