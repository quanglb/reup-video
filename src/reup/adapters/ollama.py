"""Model chạy local qua Ollama.

Không hạn mức, không API key, không cần mạng. Đổi lại chậm hơn và chất lượng
tiếng Việt phụ thuộc model bạn kéo về — nên `translate` là chỗ đáng đo trước
khi tin.

Dùng `urllib` của thư viện chuẩn chứ không thêm dependency: cả giao thức chỉ là
một POST JSON tới localhost.

Ollama nhận thẳng JSON Schema ở trường `format`, nên ràng buộc cấu trúc giống
hệt `response_schema` của Gemini. Vẫn giữ vòng sửa JSON dùng chung vì model nhỏ
hay trả kèm lời dẫn hoặc bọc trong ```.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

from reup.adapters.llm import LLMError, complete_json_with_repair

DEFAULT_BASE_URL = "http://localhost:11434"
# Model 8B trên M4 dịch một video 20+ câu mất hàng chục giây. Trần rộng tay còn
# hơn giết một job đã chạy qua Demucs và Whisper.
DEFAULT_TIMEOUT_S = 600.0


class OllamaUnavailable(LLMError):
    """Không gọi được Ollama — thường là chưa bật, hoặc chưa kéo model."""


class OllamaLLM:
    def __init__(
        self,
        model: str,
        base_url: str = DEFAULT_BASE_URL,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        json_retries: int = 2,
        opener=None,
    ) -> None:
        self.model = model
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.timeout_s = timeout_s
        self.json_retries = json_retries
        # Cho test thay chỗ gọi mạng mà không cần dựng server.
        self._opener = opener or urllib.request.urlopen

    def _call_once(self, prompt: str, schema: dict) -> str:
        body = json.dumps(
            {
                "model": self.model,
                "prompt": prompt,
                "format": schema,
                "stream": False,
                # Dịch và chọn phương án cần ổn định, không cần sáng tạo.
                "options": {"temperature": 0.2},
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/api/generate",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with self._opener(req, timeout=self.timeout_s) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            if exc.code == 404:
                raise OllamaUnavailable(
                    f"Ollama không có model {self.model!r}.\n"
                    f"Kéo về bằng: ollama pull {self.model}"
                ) from exc
            raise LLMError(f"Ollama trả lỗi {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise OllamaUnavailable(
                f"không gọi được Ollama ở {self.base_url} ({exc.reason}).\n"
                "Bật bằng `ollama serve`, hoặc sửa llm.base_url trong config.toml."
            ) from exc

        text = payload.get("response")
        if not text:
            raise LLMError(f"Ollama trả về câu rỗng cho model {self.model!r}")
        return text

    def complete_json(self, prompt: str, schema: dict) -> dict:
        return complete_json_with_repair(
            lambda p: self._call_once(p, schema),
            prompt,
            retries=self.json_retries,
            who=f"Ollama ({self.model})",
        )
