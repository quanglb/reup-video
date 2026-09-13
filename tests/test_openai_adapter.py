"""Adapter model gọi qua OpenAI-compatible API (9router, LM Studio, vLLM, OpenAI...)."""
import json
import urllib.error
from io import BytesIO

import pytest

from reup.adapters.llm import LLMError
from reup.adapters.openai import OpenAILLM, OpenAIUnavailable


class FakeResponse(BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def opener_returning(*payloads):
    """Mỗi lần gọi trả payload kế tiếp. Ghi lại request để soi."""
    seen = []
    queue = list(payloads)

    def opener(req, timeout=None):
        seen.append(
            {
                "url": req.full_url,
                "timeout": timeout,
                "headers": dict(req.headers),
                "body": json.loads(req.data.decode("utf-8")),
            }
        )
        return FakeResponse(json.dumps(queue.pop(0)).encode("utf-8"))

    opener.seen = seen
    return opener


SCHEMA = {"type": "object", "properties": {"text": {"type": "string"}}}


def test_openai_sends_chat_completion_format():
    op = opener_returning(
        {
            "choices": [
                {"message": {"role": "assistant", "content": '{"text": "chào"}'}}
            ]
        }
    )
    llm = OpenAILLM(
        "ag/gemini-3.7-flash-medium",
        base_url="http://localhost:20128/v1",
        api_key="sk-test",
        opener=op,
    )
    out = llm.complete_json("dịch đi", SCHEMA)

    assert out == {"text": "chào"}
    assert op.seen[0]["url"] == "http://localhost:20128/v1/chat/completions"
    assert op.seen[0]["headers"]["Authorization"] == "Bearer sk-test"
    assert op.seen[0]["body"]["model"] == "ag/gemini-3.7-flash-medium"
    assert op.seen[0]["body"]["messages"] == [{"role": "user", "content": "dịch đi"}]
    assert op.seen[0]["body"]["response_format"] == {"type": "json_object"}
    assert op.seen[0]["body"]["stream"] is False


def test_missing_api_key_raises_helpful_error(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ROUTER_API_KEY", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    llm = OpenAILLM("m", api_key="", opener=opener_returning({}))
    with pytest.raises(OpenAIUnavailable, match="Chưa cấu hình API key"):
        llm.complete_json("p", SCHEMA)


def test_api_key_from_env(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env")
    op = opener_returning(
        {"choices": [{"message": {"content": '{"text": "ok"}'}}]}
    )
    llm = OpenAILLM("m", api_key="", opener=op)
    assert llm.complete_json("p", SCHEMA) == {"text": "ok"}
    assert op.seen[0]["headers"]["Authorization"] == "Bearer sk-from-env"


def test_fenced_json_is_stripped():
    op = opener_returning(
        {"choices": [{"message": {"content": '```json\n{"text": "ừ"}\n```'}}]}
    )
    llm = OpenAILLM("m", api_key="sk-1", opener=op)
    assert llm.complete_json("p", SCHEMA) == {"text": "ừ"}


def test_bad_json_is_retried_with_error():
    op = opener_returning(
        {"choices": [{"message": {"content": "không phải json"}}]},
        {"choices": [{"message": {"content": '{"text": "xong"}'}}]},
    )
    llm = OpenAILLM("m", api_key="sk-1", opener=op)
    assert llm.complete_json("gốc", SCHEMA) == {"text": "xong"}
    assert "Lần trước bạn trả về" in op.seen[1]["body"]["messages"][0]["content"]


def test_http_401_raises_openai_unavailable():
    def opener(req, timeout=None):
        raise urllib.error.HTTPError(
            req.full_url, 401, "Unauthorized", {}, BytesIO(b"Invalid key")
        )

    llm = OpenAILLM("m", api_key="sk-bad", opener=opener)
    with pytest.raises(OpenAIUnavailable, match="API key không hợp lệ"):
        llm.complete_json("p", SCHEMA)


def test_connection_error_raises_openai_unavailable():
    def opener(req, timeout=None):
        raise urllib.error.URLError("Connection refused")

    llm = OpenAILLM("m", api_key="sk-1", opener=opener)
    with pytest.raises(OpenAIUnavailable, match="Không kết nối được"):
        llm.complete_json("p", SCHEMA)


def test_registry_builds_the_openai_adapter(cfg_fixture):
    from dataclasses import replace
    from reup.adapters.registry import make_llm
    from reup.config import LLMConfig

    cfg = replace(
        cfg_fixture,
        llm=LLMConfig(
            provider="openai",
            model="ag/gemini-3.7-flash-medium",
            base_url="http://localhost:20128/v1/",
            api_key="sk-test",
        ),
    )
    llm = make_llm(cfg)
    assert llm.model == "ag/gemini-3.7-flash-medium"
    assert llm.base_url == "http://localhost:20128/v1"
    assert llm.api_key == "sk-test"
