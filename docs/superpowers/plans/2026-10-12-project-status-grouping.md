# Kế hoạch Triển khai: Phân nhóm trạng thái và sắp xếp theo ngày xong tại tab Dự án

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Chia danh sách video ở tab Dự án thành 3 nhóm rõ rệt: Đang làm, Đang chờ, Đã xong, sắp xếp video đã xong theo thời gian hoàn thành giảm dần và hiển thị ngày/giờ hoàn tất trên thẻ video.

**Architecture:** 
1. Tầng backend (`app.py`): Điều chỉnh hàm `queue()` khi `view != 'archived'` để gom `rows` theo 3 nhóm trạng thái: `in_progress` (running, needs_review, failed), `pending`, và `done` (sắp xếp `updated_at` giảm dần).
2. Tầng giao diện (`queue.html`, `app.css`): Render 3 section với header/icon/số lượng tương ứng, hiển thị badge thời gian hoàn tất trên thẻ video `done`.
3. Kiểm thử (`test_web_app.py`): Viết test tự động kiểm tra phân nhóm và thứ tự sắp xếp theo ngày xong.

**Tech Stack:** Python 3.12, FastAPI, Jinja2, Pytest.

## Global Constraints
- Không làm ảnh hưởng đến tiến trình `reup run --all` và `reup web` đang chạy trên máy người dùng.
- Không sửa đổi schema SQLite (`reup.db`).
- Không làm đứt các chức năng hiện có: chọn nhiều (bulk delete), chạy tất cả (run-all), bộ lọc nền tảng.

---

### Task 1: Tổ chức phân nhóm trạng thái và định dạng ngày hoàn thành tại Backend

**Files:**
- Modify: `src/reup/web/app.py:165-195`
- Test: `tests/test_web_app.py`

**Interfaces:**
- Consumes: `rows: list[JobRow]` từ `service.list_jobs(s, None, c, app.state.jobs_dir)`
- Produces: `groups` truyền vào template gồm:
  - `in_progress`: `[r for r in rows if r.status in ("running", "needs_review", "failed")]` (running ưu tiên trước, rồi needs_review, failed, sắp xếp `updated_at` desc)
  - `pending`: `[r for r in rows if r.status == "pending"]`
  - `done`: `[r for r in rows if r.status == "done"]` (sắp xếp theo `updated_at` desc)
  - Mỗi group có `key`, `label`, `icon`, `rows`
  - Bổ sung helper / Jinja filter định dạng thời gian `completed_time(ts)` (vd: "22:00 12/10")

- [ ] **Step 1: Viết test cho logic phân nhóm trạng thái và sắp xếp ngày xong**

```python
def test_queue_groups_by_status_and_sorts_done_by_date(client):
    # Tạo job pending, running, done với các mốc updated_at khác nhau
    # Kiểm tra thứ tự các group và thứ tự job trong group done
```

- [ ] **Step 2: Chạy test để xác nhận test thất bại hoặc phản ánh đúng hành vi mới**

Run: `uv run pytest tests/test_web_app.py -k "test_queue_groups_by_status" -v`

- [ ] **Step 3: Triển khai logic trong `src/reup/web/app.py`**
Cập nhật `queue()` trong `src/reup/web/app.py` để tạo `groups` theo 3 trạng thái khi `view != 'archived'`, đồng thời đăng ký helper `format_datetime` vào Jinja environment.

- [ ] **Step 4: Chạy lại test để xác nhận pass**

Run: `uv run pytest tests/test_web_app.py -k "test_queue" -v`

- [ ] **Step 5: Commit**

```bash
git add src/reup/web/app.py tests/test_web_app.py
git commit -m "feat(web): group queue by status and sort done by date"
```

---

### Task 2: Cập nhật giao diện `queue.html` và Style `app.css`

**Files:**
- Modify: `src/reup/web/templates/queue.html:78-105`
- Modify: `src/reup/web/static/app.css`

**Interfaces:**
- Consumes: `groups` từ `app.py`
- Produces: Các khối `<section class="status-group g-{{ g.key }}">` với tiêu đề, biểu tượng và nhãn số lượng; badge `✓ Xong: HH:MM DD/MM` trên thẻ video của nhóm `done`.

- [ ] **Step 1: Cập nhật template `src/reup/web/templates/queue.html`**
Hiển thị tiêu đề nhóm trạng thái (icon + nhãn + số lượng), gắn badge ngày giờ hoàn tất trên thẻ video của nhóm `done`.

- [ ] **Step 2: Cập nhật CSS `src/reup/web/static/app.css`**
Thêm class styling cho `.status-group`, `.group-done`, `.badge-completed` để giao diện trực quan, rõ ràng và đồng bộ dark mode.

- [ ] **Step 3: Kiểm tra giao diện qua test tích hợp web**

Run: `uv run pytest tests/test_web_app.py -k "test_queue" -v`

- [ ] **Step 4: Commit**

```bash
git add src/reup/web/templates/queue.html src/reup/web/static/app.css
git commit -m "feat(web): render status groups and completion date badge"
```

---

### Task 3: Chạy toàn bộ test suite web để đảm bảo an toàn tuyệt đối

**Files:**
- Test: `tests/test_web_app.py`

- [ ] **Step 1: Chạy test suite `test_web_app.py`**

Run: `uv run pytest tests/test_web_app.py -v`

- [ ] **Step 2: Xác nhận không có lỗi phát sinh và các tiến trình đang chạy không bị ảnh hưởng**
Kiểm tra `ps aux | grep -E "reup"` để đảm bảo background job vẫn đang chạy ổn định.
