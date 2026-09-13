"""Dịch tiêu đề ở tab quét: chỉ dịch chữ ngoại, cache ra đĩa, lỗi thì trả phần có."""
import json
from pathlib import Path

from fastapi.testclient import TestClient

from reup.core.store import Store
from reup.web import title_translate as tt
from reup.web.app import create_app

from tests.test_web_app import FakeRunner


class FakeLLM:
    def __init__(self, fail: bool = False) -> None:
        self.calls = 0
        self.fail = fail

    def complete_json(self, prompt: str, schema: dict) -> dict:
        self.calls += 1
        if self.fail:
            raise RuntimeError("model sập")
        lines = [l for l in prompt.splitlines() if l[:1].isdigit()]
        return {"items": [{"i": int(l.split(".", 1)[0]), "vi": "VI:" + l.split(". ", 1)[1]}
                          for l in lines]}


def test_only_foreign_titles_need_translation():
    assert tt.needs_translation("素三鲜#chinesefood")
    assert tt.needs_translation("I Made the Biggest Red Bull")
    assert not tt.needs_translation("Món chay thanh mát")
    assert not tt.needs_translation("123 😳")
    assert not tt.needs_translation("")


def test_translations_are_cached(tmp_path: Path):
    cache = tmp_path / "c.json"
    llm = FakeLLM()
    found, err = tt.translate(["素三鲜", "Món chay", "素三鲜"], cache, llm)
    assert found == {"素三鲜": "VI:素三鲜"} and err == ""
    assert json.loads(cache.read_text(encoding="utf-8")) == {"素三鲜": "VI:素三鲜"}

    found, _ = tt.translate(["素三鲜"], cache, llm)
    assert found == {"素三鲜": "VI:素三鲜"}
    assert llm.calls == 1


def test_llm_failure_is_reported_not_raised(tmp_path: Path):
    found, err = tt.translate(["素三鲜"], tmp_path / "c.json", FakeLLM(fail=True))
    assert found == {} and "model sập" in err


def test_translate_endpoint(tmp_path: Path, config_file: Path, monkeypatch):
    jobs = tmp_path / "jobs"
    jobs.mkdir()
    db = tmp_path / "reup.db"
    Store(db).init_schema()
    app = create_app(config_file, jobs, db)
    app.state.runner = FakeRunner()
    monkeypatch.setattr("reup.adapters.registry.make_llm", lambda cfg, role=None: FakeLLM())

    r = TestClient(app).post("/api/translate-titles", json={"titles": ["搞笑", "Vui quá"]})
    assert r.json() == {"translations": {"搞笑": "VI:搞笑"}, "error": ""}
    assert (tmp_path / "title-vi-cache.json").exists()
