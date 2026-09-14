"""Middleware + route /login, /logout khi bật REUP_WEB_PASSWORD (Task 4.2)."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from reup.core.store import Store
from reup.web import auth
from reup.web.app import create_app


class FakeRunner:
    def __init__(self) -> None:
        self.started: list[str] = []

    def start(self, job_id: str) -> bool:
        self.started.append(job_id)
        return True

    def is_running(self, job_id: str) -> bool:
        return False

    def live(self) -> set:
        return set()


@pytest.fixture
def protected_client(tmp_path: Path, config_file: Path, monkeypatch):
    monkeypatch.setenv("REUP_WEB_PASSWORD", "pw")
    jobs = tmp_path / "jobs"
    jobs.mkdir()
    db = tmp_path / "reup.db"
    Store(db).init_schema()
    app = create_app(config_file, jobs, db)
    app.state.runner = FakeRunner()
    return TestClient(app), jobs, db, app


def test_no_password_env_leaves_app_unprotected(tmp_path: Path, config_file: Path, monkeypatch):
    monkeypatch.delenv("REUP_WEB_PASSWORD", raising=False)
    jobs = tmp_path / "jobs"
    jobs.mkdir()
    db = tmp_path / "reup.db"
    Store(db).init_schema()
    app = create_app(config_file, jobs, db)
    app.state.runner = FakeRunner()
    c = TestClient(app)
    assert app.state.password == ""
    assert c.get("/").status_code == 200
    assert c.get("/login").status_code == 404
    assert c.post("/logout").status_code == 404


def test_get_root_without_cookie_redirects_to_login(protected_client):
    c, _, _, _ = protected_client
    r = c.get("/", headers={"accept": "text/html"}, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login?next=%2F"


def test_get_api_progress_without_cookie_is_401(protected_client):
    c, _, _, _ = protected_client
    r = c.get("/api/progress")
    assert r.status_code == 401


def test_post_jobs_without_cookie_is_401_and_no_job_created(protected_client):
    c, jobs, db, _ = protected_client
    r = c.post("/jobs", data={"url": "https://a/1", "lang": "zh"})
    assert r.status_code == 401
    assert not list(jobs.iterdir())
    s = Store(db)
    assert s.list_jobs() == []
    s.close()


def test_get_job_final_without_cookie_is_401(protected_client):
    c, _, _, _ = protected_client
    r = c.get("/jobs/whatever/final")
    assert r.status_code == 401


def test_static_files_are_exempt(protected_client):
    c, _, _, _ = protected_client
    r = c.get("/static/app.css")
    assert r.status_code == 200


def test_wrong_password_rerenders_login_with_error_and_no_cookie(protected_client, monkeypatch):
    c, _, _, _ = protected_client
    # login_submit là async và chờ bằng anyio.sleep(1) (không phải time.sleep)
    # để không chiếm một worker trong threadpool dùng chung khi bị dò mật
    # khẩu hàng loạt — patch đúng anyio.sleep (đã import trong reup.web.app)
    # thành no-op để test không phải chờ 1 giây thật.
    async def no_delay(*_a, **_k):
        return None

    monkeypatch.setattr("reup.web.app.anyio.sleep", no_delay)
    r = c.post("/login", data={"password": "nope"})
    assert r.status_code == 200
    assert "Sai mật khẩu" in r.text
    assert auth.COOKIE not in r.cookies


def test_correct_password_sets_cookie_and_redirects_to_next(protected_client):
    c, _, _, _ = protected_client
    r = c.post(
        "/login", data={"password": "pw", "next": "/saved"}, follow_redirects=False
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/saved"
    set_cookie = r.headers["set-cookie"]
    assert f"{auth.COOKIE}=" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "samesite=lax" in set_cookie.lower()


def test_correct_password_behind_https_proxy_sets_secure_cookie(protected_client):
    c, _, _, _ = protected_client
    r = c.post(
        "/login",
        data={"password": "pw"},
        headers={"x-forwarded-proto": "https"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "Secure" in r.headers["set-cookie"]


def test_correct_password_with_unsafe_next_redirects_home(protected_client):
    c, _, _, _ = protected_client
    r = c.post(
        "/login", data={"password": "pw", "next": "//evil.com"}, follow_redirects=False
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/"


def test_after_login_root_is_reachable(protected_client):
    c, _, _, _ = protected_client
    c.post("/login", data={"password": "pw"})
    r = c.get("/")
    assert r.status_code == 200


def test_logout_clears_cookie_and_locks_again(protected_client):
    c, _, _, _ = protected_client
    c.post("/login", data={"password": "pw"})
    assert c.get("/").status_code == 200
    r = c.post("/logout", follow_redirects=False)
    assert r.status_code == 303
    r2 = c.get("/", headers={"accept": "text/html"}, follow_redirects=False)
    assert r2.status_code == 303
    assert r2.headers["location"].startswith("/login")
