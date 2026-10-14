# Thiết kế bổ sung Tag Douyin: AI

## Mục tiêu
Bổ sung các tag gợi ý về chủ đề Trí tuệ nhân tạo (AI) cho nền tảng Douyin nhằm hỗ trợ tìm kiếm và cào video nguồn chất lượng cao, đồng thời đảm bảo không làm gián đoạn hay ảnh hưởng đến luồng pipeline và web server đang chạy.

## Ràng buộc & An toàn hệ thống
- Tuyệt đối không thay đổi mã nguồn Python (`.py`) hoặc khởi động lại tiến trình server/pipeline.
- Tiến trình hiện tại (`reup run --all` và `reup web`) không bị gián đoạn.
- Cập nhật an toàn vào file cấu hình JSON runtime (`tags.json`) và file template khởi tạo (`src/reup/web/default_tags.json`).
- Giao diện web tải danh sách tag động từ file JSON theo từng request nên người dùng sẽ thấy ngay lập tức.

## Danh sách tag bổ sung (Douyin - nhóm Topic)

| Tên tiếng Việt (vi) | Từ khoá tìm kiếm (q) | Nhóm (group) | Ngôn ngữ (lang) | Ghi chú |
|---|---|---|---|---|
| Trí tuệ nhân tạo | 人工智能 | topic | zh | Chủ đề tổng quan AI |
| Video AI | AI视频 | topic | zh | Video được sinh ra bởi AI (Sora, Kling, Runway,...) |
| Công nghệ AI | AI科技 | topic | zh | Tin tức, công cụ, mô hình AI mới |
| Hiệu ứng AI | AI特效 | topic | zh | Filter, hiệu ứng hoạt hình biến hình AI |

## Kiểm thử & Xác minh
- Kiểm tra tính hợp lệ cú pháp JSON sau khi cập nhật.
- Đảm bảo các tag mới có ID duy nhất (`id`), trường `platform`: `"douyin"`, `group`: `"topic"`, `lang`: `"zh"`.
- Chạy unit test liên quan đến tags (`pytest tests/test_tags.py`) mà không làm gián đoạn tiến trình chính.
