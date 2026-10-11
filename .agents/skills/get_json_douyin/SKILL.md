---
name: get_json_douyin
description: Use when the user requests to extract, harvest, or export video list JSON from a Douyin channel, mentions `/get_json_douyin`, or asks to scrape/cào kênh Douyin.
---

# Douyin Channel JSON Exporter (`/get_json_douyin`)

Trích xuất toàn bộ danh sách video (ID, tiêu đề, hashtag, lượt like, ảnh bìa, video URL, thứ tự hiển thị) từ một kênh Douyin ra file `.json` chuẩn định dạng `reup-douyin/1` và lưu vào thư mục `douyin-exports/`.

## Khi nào sử dụng
- Người dùng gõ lệnh `/get_json_douyin <url_kênh_hoặc_sec_uid>`
- Người dùng yêu cầu lấy json kênh Douyin, xuất danh sách video, cào dữ liệu kênh Douyin.

## Điều kiện tiên quyết (Prerequisites)
1. **Google Chrome trên macOS**: Đã mở ít nhất 1 tab Douyin hoặc đã đăng nhập tài khoản Douyin trên Chrome.
2. **Quyền Apple Events trong Chrome**:
   - Menu Chrome: **View** $\rightarrow$ **Developer** $\rightarrow$ tick chọn **Allow JavaScript from Apple Events**.
3. **Quyền Automation trên macOS**:
   - Mở **System Settings** $\rightarrow$ **Privacy & Security** $\rightarrow$ **Automation** $\rightarrow$ Bật cho phép điều khiển Google Chrome.

## Cách chạy

Chạy script tự động hoá có sẵn trong dự án:

```bash
uv run python scripts/get_json_douyin.py "<link_kênh_hoặc_sec_user_id>"
```

### Ví dụ:
```bash
# Bằng URL kênh:
uv run python scripts/get_json_douyin.py "https://www.douyin.com/user/MS4wLjABAAAAvsyzFrt_m2PJnWzOibAJCwwUnVJF6drCpSFBcKok2Vg?from_tab_name=main"

# Hoặc bằng sec_user_id trực tiếp:
uv run python scripts/get_json_douyin.py "MS4wLjABAAAAvsyzFrt_m2PJnWzOibAJCwwUnVJF6drCpSFBcKok2Vg"
```

## Kết quả đầu ra
- File JSON được lưu tại: `douyin-exports/douyin_<sec_uid>_<timestamp>.json`
- Định dạng dữ liệu chuẩn `reup-douyin/1`:
  ```json
  {
    "format": "reup-douyin/1",
    "sec_user_id": "MS4wLjABAAAA...",
    "author": "Tên kênh",
    "exported_at": "2026-09-23T...",
    "videos": [
      {
        "position": 1,
        "aweme_id": "7584062042567527729",
        "desc": "Tiêu đề video #hashtag",
        "digg_count": 175000,
        "share_count": 0,
        "comment_count": 0,
        "play_count": 0,
        "duration_ms": 60000,
        "create_time": 1790176306,
        "author": "Tên kênh",
        "cover": "https://p3-pc-sign.douyinpic.com/...",
        "play_url": "https://www.douyin.com/video/7584062042567527729"
      }
    ]
  }
  ```
- File này sẽ tự động xuất hiện trên giao diện Web UI tại tab **Douyin** ([http://127.0.0.1:8765/discover?platform=douyin](http://127.0.0.1:8765/discover?platform=douyin)) để người dùng chọn và reup video.
