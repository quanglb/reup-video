# Thiết kế: Phân nhóm trạng thái và sắp xếp theo ngày xong tại tab Dự án

**Ngày lập**: 12/10/2026  
**Trạng thái**: Đã thống nhất  

## 1. Mục tiêu & Bối cảnh
Người dùng cần quản lý các video/job trong tab **Dự án** dễ dàng hơn trong khi các job vẫn đang tiếp tục chạy ngầm.
Thay vì gom nhóm theo nền tảng nguồn (YouTube, TikTok, Douyin), giao diện sẽ chia thành 3 phân mục trạng thái rõ rệt:
1. **Đang làm**: Các video đang chạy, chờ duyệt chốt A/B, hoặc bị lỗi.
2. **Đang chờ**: Các video đang xếp hàng chờ chạy.
3. **Đã xong**: Các video đã render và xuất thành phẩm hoàn tất, được sắp xếp theo thời gian hoàn thành mới nhất lên đầu, kèm thông tin ngày/giờ xong trên thẻ.

**Yêu cầu an toàn bắt buộc**:
- Không làm ảnh hưởng đến tiến trình `reup run --all` và server web đang chạy.
- Không sửa đổi schema SQLite database `reup.db`.

---

## 2. Thiết kế chi tiết

### 2.1. Phân loại và sắp xếp dữ liệu (`src/reup/web/app.py`)
Tại hàm xử lý route `/` (`queue`):
- Khi ở chế độ xem thông thường (`view != 'archived'`):
  - Phân loại `rows` thành 3 nhóm:
    - **Đang làm** (`key='in_progress'`, `label='⚡ Đang làm'`): các job có `status in ('running', 'needs_review', 'failed')`.
      - Thứ tự sắp xếp nội bộ: ưu tiên job `running` lên đầu, sau đó đến `needs_review`, rồi đến `failed`, sắp xếp theo `updated_at` giảm dần.
    - **Đang chờ** (`key='pending'`, `label='⏳ Đang chờ'`): các job có `status == 'pending'`.
      - Sắp xếp theo thứ tự thêm/thời gian cập nhật.
    - **Đã xong** (`key='done'`, `label='✅ Đã xong'`): các job có `status == 'done'`.
      - **Sắp xếp theo ngày/giờ hoàn tất**: `updated_at` giảm dần (mới nhất lên đầu).
  - Khi người dùng bấm lọc theo một trạng thái cụ thể (ví dụ bấm lọc "Đang chờ" hay "Xong"), chỉ hiển thị nhóm tương ứng.
  - Khi ở chế độ "Lưu trữ" (`view == 'archived'`), giữ nguyên cách hiển thị lưu trữ.

### 2.2. Trình bày giao diện (`src/reup/web/templates/queue.html` & `app.css`)
- Hiển thị từng nhóm trong thẻ `<section class="status-group ...">` với tiêu đề nhóm rõ ràng (`<h2>`) kèm huy hiệu đếm số lượng.
- Các nút công cụ ("▶ Chạy tất cả", "☑ Chọn nhiều") và bộ lọc nền tảng vẫn hoạt động đầy đủ trên các nhóm.
- Thẻ video (`job-card`) trong nhóm "Đã xong":
  - Hiển thị nhãn thời gian hoàn tất: `✓ Xong: {giờ}:{phút} {ngày}/{tháng}` dựa trên `r.updated_at`.
- Thêm style CSS nhẹ nhàng, hài hoà với giao diện tối (dark theme) hiện tại của hệ thống.

---

## 3. Kế hoạch kiểm thử (Testing)
- Chạy test hiện có của web (`pytest tests/test_web.py` hoặc tương đương) để đảm bảo không làm gãy các endpoint và trang web.
- Kiểm tra hiển thị đúng 3 nhóm khi có đủ các trạng thái, thứ tự sắp xếp của nhóm "Đã xong", và hiển thị timestamp hoàn thành.
