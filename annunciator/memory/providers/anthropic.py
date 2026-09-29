"""Claude models through the Anthropic Messages API (``POST /v1/messages``)."""

from __future__ import annotations

from annunciator.memory.prompt import build_messages, parse_answers
from annunciator.memory.providers.base import Provider, ProviderError

API_VERSION = "2023-06-01"


class AnthropicProvider(Provider):
    name = "Anthropic"

    def decide(self, questions: dict, fact: str) -> dict:
        self.require_key()
        system, user = build_messages(questions, fact)
        payload = {
            "model": self.model,
            "max_tokens": int(self.settings.get("max_tokens", 4096)),
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        if self.settings.get("effort"):
            payload["output_config"] = {"effort": self.settings["effort"]}
        reply = self.post(
            "/messages",
            payload,
            {"x-api-key": self.api_key, "anthropic-version": API_VERSION},
        )
        if not isinstance(reply, dict):
            raise ProviderError("Anthropic returned an unexpected reply.")
        if reply.get("stop_reason") == "refusal":
            raise ProviderError("Claude declined to classify this fact; nothing should be stored.")
        # Thinking blocks come first on models that always think; only text blocks hold the answer.
        text = "".join(
            block.get("text", "")
            for block in reply.get("content") or []
            if isinstance(block, dict) and block.get("type") == "text"
        )
        try:
            answers = parse_answers(text)
        except ValueError:
            reason = " (the reply hit max_tokens)" if reply.get("stop_reason") == "max_tokens" else ""
            raise ProviderError(f"Claude's reply was not the expected JSON{reason}.") from None
        usage = reply.get("usage") or {}
        return {
            "answers": answers,
            "usage": {"input_tokens": usage.get("input_tokens"), "output_tokens": usage.get("output_tokens")},
        }
