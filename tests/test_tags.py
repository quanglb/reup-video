"""Tag gợi ý: đọc từ tags.json, CRUD, AI dịch tên tiếng Việt thành từ khoá."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from reup.core.store import Store
from reup.web import tags as ts
from reup.web.app import create_app

from tests.test_web_app import FakeRunner


class FakeLLM:
    def __init__(self, q="rural cooking", error=None):
        self.q, self.error = q, error
        self.prompts = []

    def complete_json(self, prompt, schema):
        self.prompts.append(prompt)
        if self.error:
            raise self.error
        return {"q": self.q}


def no_ai():
    raise AssertionError("không được gọi AI khi đã có từ khoá")


def test_missing_file_is_seeded_with_the_old_hardcoded_tags(tmp_path):
    p = tmp_path / "tags.json"
    tags = ts.load(p)
    assert p.exists()
    assert "美食 制作" in [t["q"] for t in ts.for_platform(tags, "douyin")]
    assert ("hashtag", "#shorts") in [(t["group"], t["q"]) for t in ts.for_platform(tags, "youtube")]
    assert "#funny" in [t["q"] for t in ts.for_platform(tags, "tiktok")]


def test_empty_keyword_is_translated_from_vietnamese_by_ai(tmp_path):
    p = tmp_path / "tags.json"
    llm = FakeLLM()
    tag = ts.create(p, {"platform": "youtube", "vi": " Nấu ăn  thôn quê "}, lambda: llm)
    assert (tag["vi"], tag["q"], tag["lang"], tag["group"]) == (
        "Nấu ăn thôn quê", "rural cooking", "en", "topic"
    )
    assert "Nấu ăn thôn quê" in llm.prompts[0] and "tiếng Anh" in llm.prompts[0]
    assert tag in ts.load(p)


def test_hashtags_are_normalised_and_douyin_translates_to_chinese(tmp_path):
    p = tmp_path / "tags.json"
    h = ts.create(p, {"platform": "tiktok", "group": "hashtag", "vi": "Món Trung"},
                  lambda: FakeLLM("# chinese Dish"))
    assert h["q"] == "#chineseDish"

    llm = FakeLLM("野外 钓鱼")
    d = ts.create(p, {"platform": "douyin", "vi": "Câu cá ngoài trời"}, lambda: llm)
    assert d["lang"] == "zh" and "tiếng Trung" in llm.prompts[0]

    with pytest.raises(ValueError, match="đã có tag"):
        ts.create(p, {"platform": "douyin", "vi": "Câu cá", "q": "钓鱼"}, no_ai)


def test_given_keyword_skips_ai_then_update_and_delete(tmp_path):
    p = tmp_path / "tags.json"
    tag = ts.create(p, {"platform": "youtube", "vi": "Mèo", "q": "funny cats"}, no_ai)
    assert tag["q"] == "funny cats"

    llm = FakeLLM("cute kittens")
    new = ts.update(p, tag["id"], {"platform": "youtube", "vi": "Mèo con", "q": ""}, lambda: llm)
    assert (new["id"], new["vi"], new["q"]) == (tag["id"], "Mèo con", "cute kittens")

    ts.delete(p, tag["id"])
    assert tag["id"] not in [t["id"] for t in ts.load(p)]
    with pytest.raises(ValueError, match="không có tag"):
        ts.delete(p, tag["id"])


def test_ai_failure_is_explained_and_nothing_is_saved(tmp_path):
    p = tmp_path / "tags.json"
    before = len(ts.load(p))
    with pytest.raises(ValueError, match="AI dịch lỗi"):
        ts.create(p, {"platform": "youtube", "vi": "Mèo"}, lambda: FakeLLM(error=RuntimeError("hết quota")))
    with pytest.raises(ValueError, match="nền tảng"):
        ts.create(p, {"platform": "facebook", "vi": "Mèo", "q": "cat"}, no_ai)
    assert len(ts.load(p)) == before


@pytest.fixture
def client(tmp_path: Path, config_file: Path):
    jobs = tmp_path / "jobs"
    jobs.mkdir()
    db = tmp_path / "reup.db"
    Store(db).init_schema()
    app = create_app(config_file, jobs, db)
    app.state.runner = FakeRunner()
    return TestClient(app), tmp_path / "tags.json"


def test_tags_tab_crud_feeds_the_discover_chips(client, monkeypatch):
    c, path = client
    monkeypatch.setattr("reup.adapters.registry.make_llm", lambda cfg, stage=None: FakeLLM("street food"))

    assert "#shorts" in c.get("/discover?platform=youtube").text  # seed hiện ở tab quét
    assert c.post("/api/tags/translate", json={"vi": "Ăn vặt", "lang": "en", "group": "topic"}).json() == {"q": "street food"}

    r = c.post("/tags", data={"platform": "youtube", "group": "topic", "vi": "Ăn vặt đường phố", "q": "", "lang": "en"},
               follow_redirects=False)
    assert r.status_code == 303
    page = c.get("/discover?platform=youtube").text
    assert 'data-q="street food"' in page and "Ăn vặt đường phố" in page
    assert "Ăn vặt đường phố" in c.get("/tags?platform=youtube").text

    tag = next(t for t in ts.load(path) if t["q"] == "street food")
    c.post(f"/tags/{tag['id']}", data={"platform": "tiktok", "group": "hashtag", "vi": "Ăn vặt", "q": "street eats", "lang": "en"})
    assert 'data-q="#streeteats"' in c.get("/discover?platform=tiktok").text

    c.post(f"/tags/{tag['id']}/delete")
    assert "#streeteats" not in c.get("/discover?platform=tiktok").text

    bad = c.post("/tags", data={"platform": "youtube", "vi": "", "q": ""})
    assert bad.status_code == 400 and "cần tên tiếng Việt" in bad.text
