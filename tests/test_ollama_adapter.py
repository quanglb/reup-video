"""Adapter model chạy local qua Ollama."""
import json
import urllib.error
from io import BytesIO

import pytest

from reup.adapters.llm import LLMError
from reup.adapters.ollama import OllamaLLM, OllamaUnavailable


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
                "body": json.loads(req.data.decode("utf-8")),
            }
        )
        return FakeResponse(json.dumps(queue.pop(0)).encode("utf-8"))

    opener.seen = seen
    return opener


SCHEMA = {"type": "object", "properties": {"text": {"type": "string"}}}


def test_schema_is_sent_as_the_format_constraint():
    """Ollama nhận thẳng JSON Schema — bỏ qua là mất ràng buộc cấu trúc."""
    op = opener_returning({"response": '{"text": "chào"}'})
    out = OllamaLLM("qwen3:8b", opener=op).complete_json("dịch đi", SCHEMA)

    assert out == {"text": "chào"}
    assert op.seen[0]["body"]["format"] == SCHEMA
    assert op.seen[0]["body"]["stream"] is False
    assert op.seen[0]["url"].endswith("/api/generate")


def test_a_fenced_answer_is_still_read():
    """Model nhỏ hay bọc câu trả lời trong ```json."""
    op = opener_returning({"response": '```json\n{"text": "ừ"}\n```'})
    assert OllamaLLM("m", opener=op).complete_json("p", SCHEMA) == {"text": "ừ"}


def test_bad_json_is_retried_with_the_error_in_the_prompt():
    """Không kèm lỗi vào prompt thì model trả lại y hệt."""
    op = opener_returning(
        {"response": "đây là câu trả lời của tôi:"},
        {"response": '{"text": "xong"}'},
    )
    assert OllamaLLM("m", opener=op).complete_json("gốc", SCHEMA) == {"text": "xong"}
    assert "Lần trước bạn trả về" in op.seen[1]["body"]["prompt"]


def test_giving_up_says_the_model_name():
    op = opener_returning(*[{"response": "rác"}] * 3)
    with pytest.raises(LLMError, match=r"Ollama \(m\).*3 lần"):
        OllamaLLM("m", json_retries=2, opener=op).complete_json("p", SCHEMA)


def test_a_missing_model_tells_you_how_to_pull_it():
    def opener(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 404, "not found", {}, BytesIO(b"no model"))

    with pytest.raises(OllamaUnavailable, match="ollama pull qwen3:8b"):
        OllamaLLM("qwen3:8b", opener=opener).complete_json("p", SCHEMA)


def test_a_server_that_is_not_running_says_how_to_start_it():
    """Lỗi mặc định của urllib là "Connection refused" — không ai sửa được gì."""
    def opener(req, timeout=None):
        raise urllib.error.URLError("Connection refused")

    with pytest.raises(OllamaUnavailable, match="ollama serve"):
        OllamaLLM("m", opener=opener).complete_json("p", SCHEMA)


def test_an_empty_answer_is_an_error_not_an_empty_dict():
    """Trả {} lặng lẽ thì stage sau mới gãy, ở chỗ không liên quan."""
    op = opener_returning({"response": ""})
    with pytest.raises(LLMError, match="câu rỗng"):
        OllamaLLM("m", opener=op).complete_json("p", SCHEMA)


def test_timeout_is_generous_and_configurable():
    """Model 8B trên M4 dịch 20+ câu mất hàng chục giây; timeout ngắn giết một
    job đã chạy qua Demucs và Whisper."""
    op = opener_returning({"response": "{}"})
    OllamaLLM("m", timeout_s=42.0, opener=op).complete_json("p", SCHEMA)
    assert op.seen[0]["timeout"] == 42.0


def test_registry_builds_the_ollama_adapter(cfg_fixture):
    from dataclasses import replace

    from reup.adapters.registry import make_llm
    from reup.config import LLMConfig

    cfg = replace(
        cfg_fixture,
        llm=LLMConfig(provider="ollama", model="qwen3:8b", base_url="http://x:1/"),
    )
    llm = make_llm(cfg)
    assert llm.model == "qwen3:8b"
    assert llm.base_url == "http://x:1"  # bỏ dấu / thừa, không thành //api/generate
