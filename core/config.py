"""Process-wide settings, loaded from the environment once and cached.

This is the only module (besides core/credentials.py, which reads from this) allowed to
call os.getenv directly. Everything else goes through get_settings() or CredentialsProvider.
"""

import os
from dataclasses import dataclass
from functools import lru_cache

from dotenv import load_dotenv

from core.constants import DEFAULT_RETRIEVAL_MODE, RAW_CAPTIONS_DIR, VALID_RETRIEVAL_MODES

VALID_INSTANCE_MODES = ("selfhost", "cloud")
VALID_CHAT_PROVIDERS = ("openai", "anthropic")

# Vendor SDKs (openai, anthropic) read these env vars directly, independent of whatever we
# pass as constructor kwargs. A blank-but-present value in .env (e.g. "OPENAI_BASE_URL=")
# is picked up as a literal empty string by those SDKs instead of falling back to their own
# default — so a placeholder left blank in .env silently breaks every API call. Scrubbing
# blank values here means our own get_settings() output and every downstream SDK's own env
# lookup agree: "unset" for both, never "present but empty" for one and "unset" for the other.
_ENV_VARS_TO_SCRUB_IF_BLANK = (
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_BASE_URL",
    "API_TOKEN",
)


@dataclass(frozen=True)
class Settings:
    instance_mode: str
    database_url: str
    openai_api_key: str | None
    openai_base_url: str | None
    anthropic_api_key: str | None
    anthropic_base_url: str | None
    chat_provider: str
    chat_model: str | None  # None means "use the provider's default" from core/constants.py
    raw_captions_dir: str
    retrieval_mode: str
    api_token: str | None  # unset = the HTTP API is open, no auth (selfhost default)
    cors_origins: tuple[str, ...]  # empty = no cross-origin requests allowed


class ConfigError(RuntimeError):
    pass


@lru_cache
def get_settings() -> Settings:
    load_dotenv()

    for var in _ENV_VARS_TO_SCRUB_IF_BLANK:
        if os.environ.get(var) == "":
            del os.environ[var]

    instance_mode = os.getenv("INSTANCE_MODE", "selfhost")
    if instance_mode not in VALID_INSTANCE_MODES:
        raise ConfigError(
            f"INSTANCE_MODE must be one of {VALID_INSTANCE_MODES}, got {instance_mode!r}"
        )

    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise ConfigError("DATABASE_URL is required")

    # `or`, not a getenv default: a present-but-blank `CHAT_PROVIDER=` line (the .env.example
    # shape) must mean "default", not "invalid value ''" — same lesson as the SDK-var scrubbing.
    chat_provider = os.getenv("CHAT_PROVIDER") or "openai"
    if chat_provider not in VALID_CHAT_PROVIDERS:
        raise ConfigError(
            f"CHAT_PROVIDER must be one of {VALID_CHAT_PROVIDERS}, got {chat_provider!r}"
        )

    retrieval_mode = os.getenv("RETRIEVAL_MODE") or DEFAULT_RETRIEVAL_MODE
    if retrieval_mode not in VALID_RETRIEVAL_MODES:
        raise ConfigError(
            f"RETRIEVAL_MODE must be one of {VALID_RETRIEVAL_MODES}, got {retrieval_mode!r}"
        )

    return Settings(
        instance_mode=instance_mode,
        database_url=database_url,
        openai_api_key=os.getenv("OPENAI_API_KEY") or None,
        openai_base_url=os.getenv("OPENAI_BASE_URL") or None,
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY") or None,
        anthropic_base_url=os.getenv("ANTHROPIC_BASE_URL") or None,
        chat_provider=chat_provider,
        chat_model=os.getenv("CHAT_MODEL") or None,
        raw_captions_dir=os.getenv("RAW_CAPTIONS_DIR") or RAW_CAPTIONS_DIR,
        retrieval_mode=retrieval_mode,
        api_token=os.getenv("API_TOKEN") or None,
        cors_origins=tuple(
            origin.strip()
            for origin in (os.getenv("CORS_ORIGINS") or "").split(",")
            if origin.strip()
        ),
    )
