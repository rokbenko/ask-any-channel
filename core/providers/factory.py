"""Resolves CHAT_PROVIDER/CHAT_MODEL into a concrete chat LLMProvider. The one place that
maps Settings.chat_provider to a provider class + default model — CLI/UI callers use this
instead of branching on chat_provider themselves."""

from core.config import Settings
from core.constants import DEFAULT_CHAT_MODEL_ANTHROPIC, DEFAULT_CHAT_MODEL_OPENAI
from core.credentials import CredentialError, CredentialsProvider
from core.providers.anthropic_provider import AnthropicProvider
from core.providers.base import LLMProvider
from core.providers.openai_provider import OpenAIProvider


def build_chat_provider(
    settings: Settings, credentials: CredentialsProvider
) -> tuple[LLMProvider, str]:
    if settings.chat_provider == "anthropic":
        return AnthropicProvider(credentials), settings.chat_model or DEFAULT_CHAT_MODEL_ANTHROPIC
    return OpenAIProvider(credentials), settings.chat_model or DEFAULT_CHAT_MODEL_OPENAI


def build_chat_provider_if_configured(
    settings: Settings, credentials: CredentialsProvider
) -> tuple[LLMProvider, str] | None:
    """For optional chat features (suggested questions): None when the configured provider's
    key isn't set, instead of raising — callers treat that as "skip", never as an error."""
    try:
        return build_chat_provider(settings, credentials)
    except CredentialError:
        return None
