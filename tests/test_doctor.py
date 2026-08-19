"""core.doctor is the first thing a confused self-hoster runs, so its contract — every check
returns a CheckResult, never raises, and failures carry an actionable line — gets pinned here.
DB-touching checks are exercised by stubbing the one seam they use (apply_all /
PgVectorStore.sample_embedding_dim); everything else runs against a controlled environment."""

import psycopg
import pytest
from typer.testing import CliRunner

from cli.main import app
from core import doctor
from core.config import get_settings
from core.constants import EMBEDDING_DIM
from core.db.migrate import MigrationError, describe_migration_error

_BASE_ENV = {
    "DATABASE_URL": "postgresql://aac:secret@127.0.0.1:5432/askanychannel",
    "INSTANCE_MODE": "selfhost",
    "CHAT_PROVIDER": "openai",
}


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch, tmp_path):
    # get_settings() is lru_cached and load_dotenv() would read the developer's real .env —
    # neutralise both so each test sees exactly the environment it sets up.
    monkeypatch.setattr("core.config.load_dotenv", lambda *a, **k: False)
    for var in (
        "DATABASE_URL",
        "INSTANCE_MODE",
        "CHAT_PROVIDER",
        "CHAT_MODEL",
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_BASE_URL",
        "RAW_CAPTIONS_DIR",
    ):
        monkeypatch.delenv(var, raising=False)
    for key, value in _BASE_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("RAW_CAPTIONS_DIR", str(tmp_path / "raw"))
    monkeypatch.chdir(tmp_path)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _names(results: list[doctor.CheckResult]) -> list[str]:
    return [r.name for r in results]


# --- env vars ---------------------------------------------------------------


def test_env_vars_check_reports_config_error_message_not_traceback(monkeypatch):
    monkeypatch.setenv("CHAT_PROVIDER", "cohere")
    get_settings.cache_clear()

    result = doctor.check_env_vars()

    assert result.ok is False
    assert "CHAT_PROVIDER" in result.detail


def test_dependent_checks_skip_when_env_is_broken(monkeypatch):
    monkeypatch.delenv("DATABASE_URL")
    get_settings.cache_clear()

    for check in (
        doctor.check_database,
        doctor.check_embedding_dim_consistency,
        doctor.check_data_dirs_writable,
        doctor.check_openai_key,
        doctor.check_chat_provider_key,
    ):
        result = check()
        assert result.ok is True, check.__name__
        assert result.detail.startswith("skipped"), check.__name__


# --- keys ---------------------------------------------------------------------


def test_openai_key_missing_is_a_failure_with_the_reason():
    result = doctor.check_openai_key()

    assert result.ok is False
    assert "OPENAI_API_KEY" in result.detail
    assert "embedding" in result.detail


def test_chat_provider_key_only_required_for_anthropic(monkeypatch):
    assert doctor.check_chat_provider_key().ok is True

    monkeypatch.setenv("CHAT_PROVIDER", "anthropic")
    get_settings.cache_clear()
    missing = doctor.check_chat_provider_key()
    assert missing.ok is False
    assert "ANTHROPIC_API_KEY" in missing.detail

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    get_settings.cache_clear()
    assert doctor.check_chat_provider_key().ok is True


# --- database -----------------------------------------------------------------


def test_database_unreachable_redacts_the_password(monkeypatch):
    def _unreachable(*args, **kwargs):
        raise psycopg.OperationalError("connection refused")

    monkeypatch.setattr(doctor, "apply_all", _unreachable)

    result = doctor.check_database()

    assert result.ok is False
    assert "secret" not in result.detail
    assert "docker compose up -d postgres" in result.detail


def test_no_pgvector_extension_gets_the_pgvector_hint():
    # psycopg maps this server error to UndefinedFile — an OperationalError SUBCLASS — so a
    # class-based "unreachable?" catch would call a present-but-unusable server "not running".
    exc = psycopg.errors.UndefinedFile(
        'could not open extension control file "/usr/share/postgresql/16/extension/'
        'vector.control": No such file or directory'
    )
    assert isinstance(exc, psycopg.OperationalError)

    detail = describe_migration_error(exc)

    assert "pgvector" in detail
    assert "CREATE EXTENSION vector" in detail


def test_database_migration_failure_is_a_sentence_not_a_traceback(monkeypatch):
    def _reachable_but_broken(*args, **kwargs):
        raise MigrationError("Postgres is reachable but applying migrations failed: boom")

    monkeypatch.setattr(doctor, "apply_all", _reachable_but_broken)

    result = doctor.check_database()

    assert result.ok is False
    assert result.detail.startswith("Postgres is reachable but")
    assert "is it running" not in result.detail


def test_database_reports_newly_applied_migrations(monkeypatch):
    monkeypatch.setattr(doctor, "apply_all", lambda *a, **k: ["0001_init.sql", "0002_x.sql"])

    result = doctor.check_database()

    assert result.ok is True
    assert "0001_init.sql, 0002_x.sql" in result.detail


def test_describe_migration_error_names_privilege_fix():
    exc = psycopg.errors.InsufficientPrivilege("permission denied for schema public")

    detail = describe_migration_error(exc)

    assert "permission denied" in detail
    assert "superuser" in detail


def test_privilege_hint_wins_when_the_message_also_mentions_vector():
    # Seen live: pgvector IS installed, the role just can't create extensions.
    exc = psycopg.errors.InsufficientPrivilege('permission denied to create extension "vector"')

    detail = describe_migration_error(exc)

    assert "privilege" in detail
    assert "isn't installed" not in detail


# --- embedding dim ------------------------------------------------------------


class _DimStore:
    def __init__(self, dim):
        self._dim = dim

    def sample_embedding_dim(self):
        if isinstance(self._dim, Exception):
            raise self._dim
        return self._dim


def test_embedding_dim_mismatch_is_flagged(monkeypatch):
    monkeypatch.setattr(doctor, "PgVectorStore", lambda: _DimStore(EMBEDDING_DIM + 1))

    result = doctor.check_embedding_dim_consistency()

    assert result.ok is False
    assert str(EMBEDDING_DIM + 1) in result.detail
    assert str(EMBEDDING_DIM) in result.detail


def test_embedding_dim_empty_store_and_unreachable_db_are_not_failures(monkeypatch):
    monkeypatch.setattr(doctor, "PgVectorStore", lambda: _DimStore(None))
    assert doctor.check_embedding_dim_consistency().ok is True

    monkeypatch.setattr(
        doctor, "PgVectorStore", lambda: _DimStore(psycopg.OperationalError("down"))
    )
    skipped = doctor.check_embedding_dim_consistency()
    assert skipped.ok is True
    assert skipped.detail.startswith("skipped")


# --- data dirs ----------------------------------------------------------------


def test_data_dirs_pass_when_parent_is_writable_even_if_dirs_do_not_exist_yet():
    # Fresh clone: neither data/raw nor datasets/ exists; the cwd (tmp_path) is writable.
    result = doctor.check_data_dirs_writable()

    assert result.ok is True


def test_data_dirs_fail_names_the_directory_and_the_fix(monkeypatch, tmp_path):
    blocked = tmp_path / "blocked-file"
    blocked.write_text("not a directory")
    monkeypatch.setenv("RAW_CAPTIONS_DIR", str(blocked / "raw"))
    get_settings.cache_clear()

    result = doctor.check_data_dirs_writable()

    assert result.ok is False
    assert "RAW_CAPTIONS_DIR" in result.detail
    assert "chown" in result.detail


# --- roles + CLI --------------------------------------------------------------


def test_role_tables_are_subsets_of_all_and_worker_skips_chat_only_checks():
    assert set(doctor.ROLE_CHECKS) == {"all", "worker", "ui"}
    for role, checks in doctor.ROLE_CHECKS.items():
        assert set(checks) <= set(doctor.ALL_CHECKS), role
    assert doctor.check_chat_provider_key not in doctor.ROLE_CHECKS["worker"]
    assert doctor.check_yt_dlp_and_js_runtime not in doctor.ROLE_CHECKS["ui"]
    assert doctor.check_data_dirs_writable in doctor.ROLE_CHECKS["worker"]


def test_unknown_role_is_a_programming_error():
    with pytest.raises(ValueError):
        doctor.run_checks("cron")


def test_versions_line_mentions_app_and_python():
    line = doctor.versions_line()

    assert "Python" in line
    assert "yt-dlp" in line


def test_doctor_cli_exits_nonzero_and_prints_only_failures_when_quiet(monkeypatch):
    # No OPENAI_API_KEY in the isolated env → openai_key fails; stub the DB-touching checks so
    # the CLI test needs no Postgres.
    monkeypatch.setattr(doctor, "apply_all", lambda *a, **k: [])
    monkeypatch.setattr(doctor, "PgVectorStore", lambda: _DimStore(None))

    result = CliRunner().invoke(app, ["doctor", "--quiet", "--role", "ui"])

    assert result.exit_code == 1
    assert "FAIL openai_key" in result.output
    assert "PASS" not in result.output


def test_doctor_cli_rejects_unknown_role():
    result = CliRunner().invoke(app, ["doctor", "--role", "cron"])

    assert result.exit_code != 0
    assert "worker" in result.output and "ui" in result.output


def test_version_flag_prints_version_without_touching_anything():
    result = CliRunner().invoke(app, ["--version"])

    assert result.exit_code == 0
    assert "0.1.0" in result.output
