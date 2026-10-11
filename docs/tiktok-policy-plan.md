# Kế hoạch chống dính chính sách TikTok

Ngày 11/10/2026. Viết sau hai lần bị phạt:

1. **Video bị xoá** — "Tết này chán đánh bài? Học ngay các chiêu múa bài Thần Bài cực ảo"
   (job `20261010-150507-88d13d99`), lý do: *Nguyên tắc Cộng đồng*.
2. **Video bị hạn chế đề xuất** — lý do: *Nội dung không phải nguyên tác, chất lượng thấp,
   chứa mã QR* ("video có thể không phải nguyên tác nếu có hình mờ hoặc logo trên đó").

Không có cách nào bảo đảm 100% — TikTok không công bố thuật toán. Mục tiêu là gỡ
hết những dấu hiệu đã biết và chặn video rủi ro **trước khi** nó ra thư mục `output/`.

---

## 1. Nguyên nhân đã xác minh (có bằng chứng trong job)

| # | Vấn đề | Bằng chứng | Chính sách dính |
|---|---|---|---|
| A | **Mảng mờ trống suốt video.** Tiêu đề chữ Trung cố định của video gốc (vd. `教你一招一摸就变的变牌手法`) bị subdetect xếp loại "watermark" và bị blur 3 lượt + phủ trắng 10% từ đầu đến cuối, **không có chữ Việt nào đè lên**. | `subrect.json` của 5/6 job ảo thuật có 1–2 vùng `watermark` coverage 1.0 ở nửa trên; ảnh thành phẩm thấy rõ hai ô kính mờ. Chính AI vision khi hỏi cũng trả lời "vùng mờ dùng để che logo". | Không nguyên tác (che logo) |
| B | **Khung phụ đề "kính mờ".** Phụ đề Việt đặt trên ô blur + trắng mờ — hình chữ nhật mờ là đặc trưng của video reup. | `compose.py`: `BLUR_RADIUS=20, BLUR_PASSES=3, COVER_OPACITY=0.1`. | Không nguyên tác |
| C | **Chủ đề cờ bạc.** Tiêu đề "đánh bài… Thần Bài", phụ đề lặp "Thần Bài" 5 lần, chữ Trung cháy sẵn `赌神` (thần cờ bạc), chiêu "đổi bài lén để khán giả không nhận ra" = dạy gian lận. | `meta.json`, `translation.json`, khung hình. Nguồn Douyin gắn `#赌神 #我要验牌`. | Cờ bạc / gian lận → **xoá video** |
| D | **Dòng LOL không có chỉnh sửa.** Chỉ đặt vào khung, giữ nguyên tiếng gốc, hai bên lấp bằng video phóng to làm mờ, nguồn 586×576 / 1024×576, còn outro "THANKS FOR WATCHING" của kênh gốc. | `frame.py` (`boxblur` lấp nền), `subrect`/probe các job 18–33. | Không nguyên tác + chất lượng thấp |
| E | **Tiêu đề hàng loạt gần giống nhau.** 15 video "Đẳng cấp Thách Đấu Hàn… pha xử lý đỉnh cao…". | `output/18…33`. | Spam / nội dung lặp |
| F | **Hashtag khác nền tảng.** `#Shorts #Reels` khi đăng TikTok. | Ảnh chụp mô tả video. | Spam nhẹ |

"Chứa mã QR" chỉ là một phần của nhãn chính sách: đã quét toàn bộ `output/`, không có mã QR thật.

---

## 2. Giải pháp — theo tầng

### Tầng 1. Không còn mảng mờ (sửa gốc vấn đề A, B, D)

- **Che kiểu "nền phẳng"**: vùng chữ gốc nằm trên nền đồng màu (dải đen letterbox — gần như
  mọi video Douyin dọc) → tô đúng màu nền, chữ biến mất không dấu vết. Đo bằng độ lệch màu
  của dải viền quanh vùng trên vài khung.
- **Che kiểu "biển chữ"**: nền không phẳng → blur để xoá nét chữ rồi phủ một tấm nền tối gần
  như đặc (thiết kế như caption), không còn là kính mờ.
- **Tiêu đề cố định của video gốc được DỊCH và đặt lại** đúng chỗ, đúng màu (không còn ô trống).
  Bản dịch lấy trong cùng lượt gọi LLM sinh tiêu đề → không tốn thêm request.
- **Nhãn chữ Hán trong cảnh** (vd. "黑桃10" cạnh lá bài, cả chữ **viết dọc** "这里有11张牌" —
  Vision thường không đọc được, dùng LiveText của macOS) cũng được dịch và đặt lại đúng lúc,
  đúng chỗ, cùng màu. Phát hiện nhờ stage policy chặn video 17 khi làm lại.
- Che trên nền có hình: dải phụ đề tối kéo hết bề ngang (đồng nhất cả video nếu ≥30% câu cần),
  chữ Việt được rộng tới 80% khung để không bé và xuống dòng.
- **LOL**: bỏ nền blur hai bên (thay bằng màu tối của khung), in tiêu đề tiếng Việt lên phần
  trên của khung → có chỉnh sửa sáng tạo.

### Tầng 2. Luật chữ miễn phí (sửa C, E, F) — `reup/policy/rules.py`

- Từ điển từ khoá theo nhóm (cờ bạc, gian lận, ma tuý, vũ khí, tình dục, tự hại, nền tảng
  khác…) bằng tiếng Việt + Trung + Anh, mỗi từ có mức `block` (chặn) hoặc `risky` (cần sửa).
- **Thay từ an toàn tất định** trước TTS: "Thần Bài" → "cao thủ ảo thuật", "đánh bài" →
  "chơi bài", "tráo bài" → "đổi bài"… → giọng đọc và phụ đề không còn từ nhạy cảm.
- Prompt dịch và prompt tiêu đề có một khối "từ cấm → từ thay" ngắn (~100 token).
- Hashtag: bỏ `shorts/reels/youtube/capcut/douyin…`, bỏ thẻ chung chung `fyp/viral/xuhuong…`,
  tối đa 5 thẻ.
- So tiêu đề với 30 tiêu đề gần nhất trong `output/` — giống ≥ 75% thì bắt viết lại.

### Tầng 3. Stage `precheck` ngay sau `fetch` (không tốn token)

Chỉ dùng luật trên tiêu đề/mô tả nguồn + độ dài + độ phân giải. Nguồn dính từ `block`
(`赌博 博彩 棋牌 彩票 色情 毒品`…), dài < 5 s hoặc cạnh ngắn < 360 px → **dừng ngay**, khỏi tốn
Demucs/Whisper/TTS. Từ `risky` (`赌神 千术`…) → ghi cờ để bước dịch dùng bộ từ chặt hơn.

### Tầng 4. Stage `policy` — AI duyệt thành phẩm (sau compose/frame, trước export)

Thứ tự kiểm, rẻ trước đắt sau:

1. **Kiểm cục bộ, miễn phí** (Apple Vision trên máy): lấy 12 khung từ `final.mp4`
   - còn chữ Hán trên hình không (chữ gốc sót) → cảnh báo/chặn
   - có mã QR không (Vision barcode) → chặn
   - độ phân giải nguồn, thời lượng, bitrate
   - luật chữ trên tiêu đề / mô tả / hashtag / phụ đề / chữ đè lên hình
2. Vi phạm cứng → **chặn luôn, không gọi AI** (tiết kiệm token).
3. **Một lượt gọi AI vision duy nhất** gồm: 1 ảnh ghép 6 khung (~768 px, JPEG), tiêu đề +
   mô tả + hashtag, tóm tắt nội dung có sẵn từ bước dịch + tối đa ~600 ký tự phụ đề, và kết
   quả kiểm cục bộ. AI trả JSON ngắn: `verdict` (pass/fix/reject), danh sách lỗi theo mã,
   và tiêu đề/mô tả/hashtag đã sửa nếu cần.
4. `fix` → áp tiêu đề mới (kiểm lại bằng luật, không gọi AI lần 2); nếu tiêu đề đã in lên
   hình (LOL, tiêu đề dịch) thì render lại một lần.
5. `reject` → job dừng ở bước `policy`, báo Telegram kèm lý do. Muốn đăng vẫn được:
   `reup policy-allow <job_id>` rồi chạy lại.

**Tiết kiệm token** (nhiều video mỗi ngày):

| Cách | Hiệu quả |
|---|---|
| Luật chạy trước, vi phạm cứng không gọi AI | 0 token cho video chắc chắn hỏng |
| 1 request/video, gộp hình + chữ | thay vì 3–4 request riêng |
| Ảnh ghép nhỏ 768 px, JPEG q70 | ~1,1k token ảnh (512 px cũng tốn ngần ấy — Gemini tính theo ô) |
| Không gửi cả transcript: dùng tóm tắt đã có + 600 ký tự + câu dính luật | ~300–500 token chữ |
| Model `-low` (không suy luận) mặc định, cấu hình ở `[llm.policy]` | nhanh ~4 s, rẻ |
| Cache theo hash nội dung trong `policy.json` | chạy lại không tốn |
| `policy.ai = "risky"` (tuỳ chọn) | chỉ gọi AI khi luật thấy rủi ro |

Đo thật (job 13, router 9router + `gemini-3.8-flash-low`): **~4,2k token / video**, trong
đó ~2k là phần router tự chèn vào MỌI request (không tránh được — lý do phải gộp một request),
~0,9k chữ của prompt, ~1,1k ảnh ghép, ~0,2k câu trả lời. ~4 giây/video.
Video vi phạm cứng (vd. phụ đề lặp “Thần Bài”) bị chặn bằng luật: **0 token**.

---

## 3. Thứ tự stage mới

```
fetch → precheck → demux → separate → asr → subdetect → ocr → reconcile → translate
      → pronounce → tts → fit → meta → compose → policy → export
LOL:   fetch → precheck → meta → frame → policy → export
```

`meta` (tiêu đề + dịch chữ cố định) chạy TRƯỚC khi render để chữ Việt in được lên hình;
`export` giờ chỉ chép file ra `output/`.

---

## 4. Cấu hình

```toml
[policy]
enabled          = true
ai               = "always"   # "always" | "risky" | "off"
sample_frames    = 12         # khung kiểm cục bộ
sheet_frames     = 6          # khung gửi AI
min_short_side   = 480        # dưới mức này: cảnh báo chất lượng thấp
block_short_side = 360        # dưới mức này: chặn
min_duration_s   = 8
block_on_reject  = true

[llm.policy]
model = "ag/gemini-3.8-flash-low"
```

---

## 5. Việc người dùng nên làm thêm (ngoài code)

- Khiếu nại video đã bị xoá (nói rõ là ảo thuật/cardistry, không có tiền cược).
- Đừng đăng dồn 10–15 video cùng chủ đề trong một ngày từ cùng một nguồn; giãn ra.
- Tránh nguồn có chủ đề cờ bạc dù là ảo thuật — `precheck` sẽ cảnh báo.
- Khi đăng, chỉ dùng hashtag trong file `.json` (đã lọc), đừng thêm `#Shorts #Reels`.
