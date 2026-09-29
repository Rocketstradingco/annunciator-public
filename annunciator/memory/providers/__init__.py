"""Model providers for the memory router, selected by ``provider.type``.

Each provider takes the router's questions and a fact, calls its API with the
standard library only, and returns ``{"answers": {...}, "usage": {"cost": n}}``
in the shape the Jev decision endpoint uses.
"""

from __future__ import annotations

from annunciator.memory.providers.anthropic import AnthropicProvider
from annunciator.memory.providers.base import Provider, ProviderError, resolve_api_key
from annunciator.memory.providers.jev import JevProvider
from annunciator.memory.providers.openai_compat import OpenAICompatibleProvider

PROVIDERS: dict[str, type[Provider]] = {
    "jev": JevProvider,
    "anthropic": AnthropicProvider,
    "openai": OpenAICompatibleProvider,
}


def make_provider(settings: dict, api_key: str | None = None) -> Provider:
    """Build the provider named by ``settings["type"]``; the key is read if not given."""
    cls = PROVIDERS[settings["type"]]
    return cls(settings, resolve_api_key(settings) if api_key is None else api_key)


__all__ = ["PROVIDERS", "Provider", "ProviderError", "make_provider", "resolve_api_key"]
