"""Provider registry. Exactly one provider is ever constructed per run."""

from __future__ import annotations

from ..contexts import Context
from ..errors import unknown_choice
from .azure import AzureProvider
from .base import Identity, Provider
from .jira import JiraProvider

_REGISTRY: dict[str, type[Provider]] = {
    JiraProvider.name: JiraProvider,
    AzureProvider.name: AzureProvider,
}

__all__ = ["Identity", "Provider", "for_context", "names"]


def names() -> list[str]:
    return sorted(_REGISTRY)


def for_context(context: Context) -> Provider:
    try:
        provider_class = _REGISTRY[context.provider]
    except KeyError:
        raise unknown_choice("provider", context.provider, names()) from None
    return provider_class(context)
