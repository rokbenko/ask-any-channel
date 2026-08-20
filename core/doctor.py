"""Fail-fast environment/config checks shared by `aac doctor` (cli/doctor_cmd.py) and
boot-time validation in cli/worker_cmd.py and apps/ui/. Every check function is independently
callable and never raises — a failing check is a CheckResult with ok=False and an actionable
one-line `detail`, never a stack trace. ROLE_CHECKS is the single table of which process runs
which subset; the boot hooks and the compose healthchecks (`aac doctor --role ...`) both read
it, so a process can't boot "healthy" and then be flagged unhealthy over a check it never
needed."""

import os
import platform
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import psycopg

from core.config import ConfigError, get_settings
from core.constants import APP_NAME, DATASETS_DIR, EMBEDDING_DIM, TOOL_VERSION
from core.credentials import CredentialError, CredentialsProvider
from core.db import DatabaseUnavailableError, redact_database_url
from core.db.migrate import MigrationError, apply_all
from core.store.pgvector_store import PgVectorStore

_DB_CONNECT_TIMEOUT_S = 5
_SKIPPED_ENV = "skipped: fix env vars first (see env_vars check)"


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    detail: str  # one actionable line; on success, a short confirmation is fine too


def versions_line() -> str:
    """Header for `aac doctor` output / bug reports: app, Python, and (if importable) yt-dlp
    versions — the three numbers every yt-dlp-related issue needs and nobody includes."""
    try:
        from yt_dlp.version import __version__ as yt_dlp_version
    except ImportError:  # reported properly by the yt_dlp check; the header just shouldn't crash
        yt_dlp_version = "unavailable"
    return (
        f"{APP_NAME} {TOOL_VERSION} · Python {platform.python_version()} · "
        f"yt-dlp {yt_dlp_version} · {platform.system()} {platform.machine()}"
    )


def check_env_vars() -> CheckResult:
    try:
        get_settings()
    except ConfigError as exc:
        return CheckResult("env_vars", False, str(exc))
    return CheckResult("env_vars", True, "required environment variables are present and valid")


def check_database() -> CheckResult:
    try:
        settings = get_settings()
    except ConfigError:
        return CheckResult("database", True, _SKIPPED_ENV)

    try:
        newly_applied = apply_all(settings.database_url, connect_timeout=_DB_CONNECT_TIMEOUT_S)
    except psycopg.OperationalError:
        return CheckResult(
            "database",
            False,
            f"Can't reach Postgres at {redact_database_url(settings.database_url)} — is it "
            "running? Try: docker compose up -d postgres",
        )
    except MigrationError as exc:
        return CheckResult("database", False, str(exc))

    if newly_applied:
        detail = f"reachable, applied {len(newly_applied)} migration(s): {', '.join(newly_applied)}"
    else:
        detail = "reachable, migrations up to date"
    return CheckResult("database", True, detail)


def check_embedding_dim_consistency() -> CheckResult:
    try:
        get_settings()
    except ConfigError:
        return CheckResult("embedding_dim", True, _SKIPPED_ENV)

    try:
        stored_dim = PgVectorStore().sample_embedding_dim()
    except (psycopg.Error, DatabaseUnavailableError):
        return CheckResult(
            "embedding_dim", True, "skipped: database unreachable (see database check)"
        )

    if stored_dim is None:
        return CheckResult("embedding_dim", True, "no channels ingested yet — nothing to check")
    if stored_dim != EMBEDDING_DIM:
        return CheckResult(
            "embedding_dim",
            False,
            f"stored chunk embeddings are {stored_dim}-dimensional but EMBEDDING_DIM is "
            f"configured as {EMBEDDING_DIM} — re-ingest affected channels or fix the mismatch "
            "before searching/chatting",
        )
    return CheckResult(
        "embedding_dim", True, f"stored embeddings match EMBEDDING_DIM ({EMBEDDING_DIM})"
    )


def _writable_dir_problem(path: Path) -> str | None:
    """None if `path` (or, if it doesn't exist yet, its nearest existing ancestor) is writable
    by this process; otherwise a short reason. Side-effect free — never creates the directory."""
    probe = path
    while not probe.exists():
        if probe.parent == probe:
            break
        probe = probe.parent
    if not probe.is_dir():
        return f"{probe} is not a directory"
    try:
        with tempfile.NamedTemporaryFile(dir=probe):
            pass
    except OSError as exc:
        return exc.strerror or str(exc)
    return None


def check_data_dirs_writable() -> CheckResult:
    """The non-root container (uid 1000) writes captions and bundles into host bind mounts; a
    host whose user isn't uid 1000 gets a PermissionError deep inside the first ingest unless
    this says so first."""
    try:
        settings = get_settings()
    except ConfigError:
        return CheckResult("data_dirs", True, _SKIPPED_ENV)

    problems = []
    for label, path in (
        ("RAW_CAPTIONS_DIR", Path(settings.raw_captions_dir)),
        ("datasets", Path(DATASETS_DIR)),
    ):
        problem = _writable_dir_problem(path)
        if problem:
            problems.append(f"{label} ({path.resolve()}): {problem}")
    if problems:
        uid = getattr(os, "getuid", lambda: "?")()
        return CheckResult(
            "data_dirs",
            False,
            f"not writable by this process (uid {uid}): {'; '.join(problems)} — on the host run "
            "`sudo chown -R 1000:1000 data datasets` (compose runs as uid 1000), or point "
            "RAW_CAPTIONS_DIR at a writable directory",
        )
    return CheckResult("data_dirs", True, "caption cache and datasets directories are writable")


def check_openai_key() -> CheckResult:
    try:
        settings = get_settings()
    except ConfigError:
        return CheckResult("openai_key", True, _SKIPPED_ENV)

    try:
        CredentialsProvider(settings).openai_api_key()
    except CredentialError as exc:
        return CheckResult(
            "openai_key",
            False,
            f"{exc} — needed for query embeddings; see README → Configuration",
        )
    return CheckResult("openai_key", True, "OPENAI_API_KEY is set")


def check_chat_provider_key() -> CheckResult:
    try:
        settings = get_settings()
    except ConfigError:
        return CheckResult("chat_provider_key", True, _SKIPPED_ENV)

    if settings.chat_provider != "anthropic":
        return CheckResult(
            "chat_provider_key", True, "CHAT_PROVIDER=openai — OPENAI_API_KEY covers chat too"
        )

    try:
        CredentialsProvider(settings).anthropic_api_key()
    except CredentialError as exc:
        return CheckResult(
            "chat_provider_key",
            False,
            f"{exc} — needed because CHAT_PROVIDER=anthropic; see README → Configuration",
        )
    return CheckResult("chat_provider_key", True, "ANTHROPIC_API_KEY is set")


def check_yt_dlp_and_js_runtime() -> CheckResult:
    # Imported here, not at module top: yt-dlp is a heavy import the UI process never needs,
    # and this module is imported by every entry point.
    try:
        from yt_dlp.version import __version__ as yt_dlp_version
    except ImportError:
        return CheckResult(
            "yt_dlp", False, "yt-dlp is not importable — reinstall dependencies (uv sync)"
        )

    from core.ingest.captions import JsRuntimeMissingError, ensure_js_runtime

    try:
        ensure_js_runtime()
    except JsRuntimeMissingError as exc:
        return CheckResult("yt_dlp", False, str(exc))
    return CheckResult(
        "yt_dlp",
        True,
        f"yt-dlp {yt_dlp_version} is importable and a JS runtime is available "
        "(if every video comes back no_captions, yt-dlp probably needs updating)",
    )


Check = Callable[[], CheckResult]

ALL_CHECKS: tuple[Check, ...] = (
    check_env_vars,
    check_database,
    check_embedding_dim_consistency,
    check_data_dirs_writable,
    check_openai_key,
    check_chat_provider_key,
    check_yt_dlp_and_js_runtime,
)

# Which process needs which checks. The worker never serves chat or search (so no chat-key /
# embedding-dim gate — a gap there must not crash-loop the ingest daemon); the UI never fetches
# captions (so no yt-dlp / data-dir gate). Boot hooks and compose healthchecks both use this.
ROLE_CHECKS: dict[str, tuple[Check, ...]] = {
    "all": ALL_CHECKS,
    "worker": (
        check_env_vars,
        check_database,
        check_data_dirs_writable,
        check_openai_key,
        check_yt_dlp_and_js_runtime,
    ),
    "ui": (
        check_env_vars,
        check_database,
        check_embedding_dim_consistency,
        check_openai_key,
        check_chat_provider_key,
    ),
    # Same subset as "ui": the API serves the same chat/search surface, so it needs the same
    # gates (a stale embedding dim or missing chat key breaks it the same way).
    "api": (
        check_env_vars,
        check_database,
        check_embedding_dim_consistency,
        check_openai_key,
        check_chat_provider_key,
    ),
}


def run_checks(role: str = "all") -> list[CheckResult]:
    """Runs the checks for `role` ("all", "worker", "ui"). Unknown roles raise ValueError —
    that's a programming error at a call site, not an environment problem."""
    try:
        checks = ROLE_CHECKS[role]
    except KeyError:
        raise ValueError(
            f"unknown doctor role {role!r}; expected one of {sorted(ROLE_CHECKS)}"
        ) from None
    return [check() for check in checks]


def run_all_checks() -> list[CheckResult]:
    return run_checks("all")
