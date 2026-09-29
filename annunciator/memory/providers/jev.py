"""OpenRouter's typed-decision endpoint with the Jev model (the original provider)."""

from __future__ import annotations

from annunciator.memory.providers.base import Provider


class JevProvider(Provider):
    name = "OpenRouter (Jev)"

    def decide(self, questions: dict, fact: str) -> dict:
        self.require_key()
        return self.post(
            "/decisions",
            {"model": self.model, "state": {"fact": fact}, "questions": questions},
            {"Authorization": "Bearer " + self.api_key},
        )
