"""The single seam for reading API keys and provider endpoints.

No module outside this file and core/config.py may read os.environ directly. In selfhost
mode, credentials come from .env (BYOK). Cloud mode would resolve platform-owned keys here
instead — callers never need to know the difference.
"""

from core.config import Settings


class CredentialError(RuntimeError):
    pass


class CredentialsProvider:
    def __init__(self, settings: Settings):
        self._settings = settings

    def openai_api_key(self) -> str:
        if not self._settings.openai_api_key:
            raise CredentialError("OPENAI_API_KEY is not set")
        return self._settings.openai_api_key

    def openai_base_url(self) -> str | None:
        return self._settings.openai_base_url

    def anthropic_api_key(self) -> str:
        if not self._settings.anthropic_api_key:
            raise CredentialError("ANTHROPIC_API_KEY is not set")
        return self._settings.anthropic_api_key

    def anthropic_base_url(self) -> str | None:
        return self._settings.anthropic_base_url
