import json

import pytest

from reup.adapters.gemini import GeminiLLM, MissingAPIKey
from reup.adapters.llm import LLMError

SCHEMA = {"type": "object", "properties": {"text": {"type": "string"}}}


class FakeResponse:
    def __init__(self, text):
        self.text = text


class FakeModels:
    """Giả `client.models` của google-genai: trả lần lượt các câu đã dựng sẵn."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return FakeResponse(reply)


class FakeClient:
    def __init__(self, replies):
        self.models = FakeModels(replies)


def test_missing_api_key_says_which_variable(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(MissingAPIKey, match="GEMINI_API_KEY"):
        GeminiLLM(model="gemini-2.0-flash")


def test_parses_json_answer():
    client = FakeClient(['{"text": "xin chào"}'])
    llm = GeminiLLM(model="m", client=client)
    assert llm.complete_json("dịch", SCHEMA) == {"text": "xin chào"}


def test_strips_markdown_fence_around_json():
    """Gemini hay bọc JSON trong ```json ... ``` dù đã bảo đừng."""
    client = FakeClient(['```json\n{"text": "a"}\n```'])
    assert GeminiLLM(model="m", client=client).complete_json("x", SCHEMA) == {"text": "a"}


def test_retries_on_bad_json_then_succeeds():
    client = FakeClient(["khong phai json", '{"text": "ok"}'])
    llm = GeminiLLM(model="m", client=client, sleep=lambda s: None)
    assert llm.complete_json("x", SCHEMA) == {"text": "ok"}
    assert len(client.models.calls) == 2


def test_gives_up_after_json_retry_budget():
    client = FakeClient(["rác", "vẫn rác", "rác nữa"])
    llm = GeminiLLM(model="m", client=client, sleep=lambda s: None, json_retries=2)
    with pytest.raises(LLMError, match="JSON"):
        llm.complete_json("x", SCHEMA)


def test_retries_on_network_error_then_succeeds():
    client = FakeClient([ConnectionError("mạng rớt"), '{"text": "ok"}'])
    llm = GeminiLLM(model="m", client=client, sleep=lambda s: None)
    assert llm.complete_json("x", SCHEMA) == {"text": "ok"}


def test_gives_up_after_network_retry_budget():
    client = FakeClient([ConnectionError("a"), ConnectionError("b"), ConnectionError("c")])
    llm = GeminiLLM(model="m", client=client, sleep=lambda s: None, net_retries=3)
    with pytest.raises(LLMError, match="mạng|3 lần"):
        llm.complete_json("x", SCHEMA)


def test_backoff_grows_between_attempts():
    waits = []
    client = FakeClient([ConnectionError("a"), ConnectionError("b"), '{"text": "ok"}'])
    llm = GeminiLLM(model="m", client=client, sleep=waits.append)
    llm.complete_json("x", SCHEMA)
    assert waits == sorted(waits)
    assert len(waits) >= 2
    assert waits[1] > waits[0]


def test_retry_prompt_carries_the_parse_error():
    """Lần thử lại phải nói cho model biết nó sai ở đâu, nếu không nó lặp lại y hệt."""
    client = FakeClient(["rác", '{"text": "ok"}'])
    GeminiLLM(model="m", client=client, sleep=lambda s: None).complete_json("dịch", SCHEMA)
    second_prompt = client.models.calls[1]["contents"]
    assert "rác" in second_prompt or "JSON" in second_prompt


def test_schema_is_passed_to_the_model():
    client = FakeClient(['{"text": "a"}'])
    GeminiLLM(model="m", client=client).complete_json("x", SCHEMA)
    cfg = client.models.calls[0]["config"]
    assert cfg["response_mime_type"] == "application/json"
    assert cfg["response_schema"] == SCHEMA


def test_non_dict_json_is_rejected():
    """Schema nói object; trả list là sai hợp đồng, không được nuốt."""
    client = FakeClient(["[1, 2, 3]", "[4]", "[5]"])
    llm = GeminiLLM(model="m", client=client, sleep=lambda s: None, json_retries=2)
    with pytest.raises(LLMError):
        llm.complete_json("x", SCHEMA)


# --- giới hạn hạn mức -------------------------------------------------------

DAILY_429 = (
    "429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': 'You exceeded "
    "your current quota', 'details': [{'violations': [{'quotaId': "
    "'GenerateRequestsPerDayPerProjectPerModel-FreeTier', 'quotaValue': '20'}]}]}}"
)
MINUTE_429 = (
    "429 RESOURCE_EXHAUSTED. {'error': {'message': 'rate limited', 'details': "
    "[{'@type': 'type.googleapis.com/google.rpc.RetryInfo', 'retryDelay': '14s'}]}}"
)


def test_daily_quota_fails_fast_with_a_usable_message():
    """Hạn mức theo NGÀY: thử lại trong ngày cũng vô ích, đừng treo job."""
    from reup.adapters.gemini import QuotaExhausted

    client = FakeClient([RuntimeError(DAILY_429)])
    llm = GeminiLLM(model="gemini-3.6-flash", client=client, sleep=lambda s: None)
    with pytest.raises(QuotaExhausted, match="20 request/ngày"):
        llm.complete_json("x", SCHEMA)
    assert len(client.models.calls) == 1  # không thử lại


def test_daily_quota_message_names_the_model_and_the_way_out():
    from reup.adapters.gemini import QuotaExhausted

    llm = GeminiLLM(model="gemini-3.6-flash", client=FakeClient([RuntimeError(DAILY_429)]))
    with pytest.raises(QuotaExhausted) as err:
        llm.complete_json("x", SCHEMA)
    msg = str(err.value)
    assert "gemini-3.6-flash" in msg
    assert "llm.model" in msg


def test_rate_limit_honours_the_suggested_delay():
    """Backoff 1s của ta ngắn hơn 14s Gemini yêu cầu, nên chắc chắn lại 429."""
    waits = []
    client = FakeClient([RuntimeError(MINUTE_429), '{"text": "ok"}'])
    GeminiLLM(model="m", client=client, sleep=waits.append).complete_json("x", SCHEMA)
    assert waits == [15.0]


def test_suggested_delay_is_capped():
    from reup.adapters.gemini import MAX_BACKOFF_S, _suggested_delay

    assert _suggested_delay("'retryDelay': '3600s'") == MAX_BACKOFF_S


def test_plain_network_error_uses_exponential_backoff():
    waits = []
    client = FakeClient([ConnectionError("mạng rớt"), '{"text": "ok"}'])
    GeminiLLM(model="m", client=client, sleep=waits.append).complete_json("x", SCHEMA)
    assert waits == [1]
