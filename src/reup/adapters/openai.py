"""Model gọi qua endpoint tương thích OpenAI (9router, LM Studio, vLLM, OpenAI...).

Dùng `urllib` của thư viện chuẩn để gửi POST JSON tới endpoint `/chat/completions`.
Hỗ trợ cả model local router (như 9router) và provider OpenAI-compatible trên cloud.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from reup.adapters.llm import LLMError, complete_json_with_repair

DEFAULT_BASE_URL = "http://localhost:20128/v1"
DEFAULT_TIMEOUT_S = 120.0
OPENAI_KEY_ENVS = ("OPENAI_API_KEY", "ROUTER_API_KEY", "LLM_API_KEY")


class OpenAIUnavailable(LLMError):
    """Không gọi được endpoint OpenAI/Router — có thể chưa bật service, sai port hoặc thiếu key."""


class OpenAILLM:
    def __init__(
        self,
        model: str,
        base_url: str = DEFAULT_BASE_URL,
        api_key: str = "",
        timeout_s: float = DEFAULT_TIMEOUT_S,
        json_retries: int = 2,
        opener=None,
    ) -> None:
        self.model = model
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        if not api_key:
            for env_name in OPENAI_KEY_ENVS:
                val = os.environ.get(env_name)
                if val:
                    api_key = val
                    break
        self.api_key = api_key or ""

        self.timeout_s = timeout_s
        self.json_retries = json_retries
        self._opener = opener or urllib.request.urlopen

    def _endpoint(self) -> str:
        if self.base_url.endswith("/chat/completions"):
            return self.base_url
        return f"{self.base_url}/chat/completions"

    def _call_once(self, prompt: str) -> str:
        if not self.api_key:
            raise OpenAIUnavailable(
                f"Chưa cấu hình API key cho OpenAI provider ({self.model}).\n"
                "Điền `api_key` vào config.toml hoặc đặt OPENAI_API_KEY / ROUTER_API_KEY trong .env"
            )
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "response_format": {"type": "json_object"},
            "stream": False,
            "temperature": 0.2,
        }
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self._endpoint(),
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            with self._opener(req, timeout=self.timeout_s) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:400]
            if exc.code in (401, 403):
                raise OpenAIUnavailable(
                    f"API key không hợp lệ hoặc bị từ chối ({exc.code}): {detail}\n"
                    "Kiểm tra lại key trong .env hoặc config.toml"
                )
            if exc.code == 404:
                raise OpenAIUnavailable(
                    f"Không tìm thấy endpoint hoặc model {self.model!r} ở {self.base_url} (404).\n"
                    f"Chi tiết: {detail}"
                )
            raise LLMError(f"OpenAI/Router trả lỗi HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise OpenAIUnavailable(
                f"Không kết nối được tới {self.base_url} ({exc}).\n"
                "Kiểm tra xem service/router đã bật chưa hoặc sửa llm.base_url."
            ) from exc

        choices = data.get("choices") or []
        if not choices:
            raise LLMError(f"OpenAI/Router không trả về choices nào cho model {self.model!r}")
        msg = choices[0].get("message") or {}
        content = msg.get("content")
        if not content:
            raise LLMError(f"OpenAI/Router trả về câu rỗng cho model {self.model!r}")
        return content

    def complete_json(self, prompt: str, schema: dict) -> dict:
        return complete_json_with_repair(
            self._call_once,
            prompt,
            retries=self.json_retries,
            who=f"OpenAI ({self.model})",
        )
