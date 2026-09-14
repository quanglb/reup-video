"""Task 4.3 — login.html hoàn thiện + nút Đăng xuất trên thanh trên (base.html)."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from reup.core.store import Store
from reup.web.app import create_app


class FakeRunner:
    def start(self, job_id: str) -> bool:
        return True

    def is_running(self, job_id: str) -> bool:
        return False

    def live(self) -> set:
        return set()


def _make_app(tmp_path: Path, config_file: Path):
    jobs = tmp_path / "jobs"
    jobs.mkdir()
    db = tmp_path / "reup.db"
    Store(db).init_schema()
    app = create_app(config_file, jobs, db)
    app.state.runner = FakeRunner()
    return app


def test_logout_button_absent_when_no_password(tmp_path: Path, config_file: Path, monkeypatch):
    monkeypatch.delenv("REUP_WEB_PASSWORD", raising=False)
    app = _make_app(tmp_path, config_file)
    c = TestClient(app)
    r = c.get("/")
    assert r.status_code == 200
    assert "logout-form" not in r.text
    assert "Đăng xuất" not in r.text


def test_logout_button_present_when_password_set(tmp_path: Path, config_file: Path, monkeypatch):
    monkeypatch.setenv("REUP_WEB_PASSWORD", "pw")
    app = _make_app(tmp_path, config_file)
    c = TestClient(app)
    c.post("/login", data={"password": "pw"})
    r = c.get("/")
    assert r.status_code == 200
    assert 'class="logout-form"' in r.text
    assert 'action="/logout"' in r.text
    assert "Đăng xuất" in r.text


def test_logout_button_present_on_other_pages_sharing_base(
    tmp_path: Path, config_file: Path, monkeypatch
):
    # Nút Đăng xuất nằm trong base.html (dùng chung) nên phải xuất hiện ở mọi
    # trang kế thừa nó, không riêng gì trang Dự án.
    monkeypatch.setenv("REUP_WEB_PASSWORD", "pw")
    app = _make_app(tmp_path, config_file)
    c = TestClient(app)
    c.post("/login", data={"password": "pw"})
    r = c.get("/saved")
    assert r.status_code == 200
    assert 'action="/logout"' in r.text


def test_login_page_has_password_field_hidden_next_and_no_error_by_default(
    tmp_path: Path, config_file: Path, monkeypatch
):
    monkeypatch.setenv("REUP_WEB_PASSWORD", "pw")
    app = _make_app(tmp_path, config_file)
    c = TestClient(app)
    r = c.get("/login?next=/saved")
    assert r.status_code == 200
    assert 'type="password"' in r.text
    assert 'autocomplete="current-password"' in r.text
    assert 'name="next" value="/saved"' in r.text
    assert 'class="error"' not in r.text


def test_login_page_shows_error_message_with_error_class(
    tmp_path: Path, config_file: Path, monkeypatch
):
    monkeypatch.setenv("REUP_WEB_PASSWORD", "pw")
    app = _make_app(tmp_path, config_file)
    c = TestClient(app)

    async def no_delay(*_a, **_k):
        return None

    monkeypatch.setattr("reup.web.app.anyio.sleep", no_delay)
    r = c.post("/login", data={"password": "nope"})
    assert r.status_code == 200
    assert 'class="error"' in r.text
    assert "Sai mật khẩu" in r.text


def test_error_css_class_has_a_rule_in_app_css():
    css = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "reup"
        / "web"
        / "static"
        / "app.css"
    ).read_text(encoding="utf-8")
    assert ".error {" in css or ".error{" in css
