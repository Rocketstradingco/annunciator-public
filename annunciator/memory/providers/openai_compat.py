"""Any OpenAI-compatible Chat Completions API: OpenAI, OpenRouter, Ollama, LM Studio, vLLM...

``base_url`` selects the server (it must end before ``/chat/completions``). A
key is optional when the server runs on this computer.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from annunciator.memory.prompt import build_messages, parse_answers
from annunciator.memory.providers.base import Provider, ProviderError, is_loopback


class OpenAICompatibleProvider(Provider):
    name = "OpenAI-compatible endpoint"

    def __init__(self, settings, api_key: str = "") -> None:
        super().__init__(settings, api_key)
        self.key_optional = is_loopback(self.base_url)
        self.openrouter = (urlsplit(self.base_url).hostname or "").endswith("openrouter.ai")

    def decide(self, questions: dict, fact: str) -> dict:
        self.require_key()
        system, user = build_messages(questions, fact)
        payload = {
            "model": self.model,
            "max_tokens": int(self.settings.get("max_tokens", 4096)),
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        if self.settings.get("json_mode"):
            payload["response_format"] = {"type": "json_object"}
        if self.openrouter:
            payload["usage"] = {"include": True}  # OpenRouter reports the call's cost
        headers = {"Authorization": "Bearer " + self.api_key} if self.api_key else {}
        reply = self.post("/chat/completions", payload, headers)
        try:
            choice = reply["choices"][0]
            text = choice["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            raise ProviderError("The endpoint returned no chat completion.") from None
        try:
            answers = parse_answers(text)
        except ValueError:
            reason = " (the reply hit max_tokens)" if choice.get("finish_reason") == "length" else ""
            raise ProviderError(f"The model's reply was not the expected JSON{reason}.") from None
        usage = reply.get("usage") or {}
        result = {"answers": answers, "usage": {}}
        if isinstance(usage, dict):
            if usage.get("cost") is not None:
                result["usage"]["cost"] = usage["cost"]
            result["usage"]["input_tokens"] = usage.get("prompt_tokens")
            result["usage"]["output_tokens"] = usage.get("completion_tokens")
        return result
