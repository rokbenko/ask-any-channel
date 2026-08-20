"""The import direction between core/ and apps/{ui,api}/ is one-way, and each side must never
reach around the seams: neither app talks to vendor SDKs or the DB driver directly, and core
never grows a dependency on either app framework. AST-based (not grep) so a comment or string
that merely mentions a package name can't trip it."""

import ast
from pathlib import Path

_REPO = Path(__file__).parent.parent
_APP_FORBIDDEN = {"openai", "anthropic", "psycopg"}
_CORE_FORBIDDEN = {"streamlit", "fastapi", "pydantic", "uvicorn"}


def _imported_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


def _offenders(root: Path, forbidden: set[str]) -> dict[str, set[str]]:
    return {
        str(path): hit for path in root.rglob("*.py") if (hit := _imported_names(path) & forbidden)
    }


def test_apps_ui_directory_exists():
    # rglob on a missing directory returns nothing and the checks below would pass vacuously —
    # this pins the assumption the other tests in this file quietly depend on.
    assert (_REPO / "apps" / "ui" / "Home.py").is_file()


def test_apps_api_directory_exists():
    assert (_REPO / "apps" / "api" / "main.py").is_file()


def test_apps_ui_never_imports_vendor_sdks_or_sql_driver():
    offenders = _offenders(_REPO / "apps" / "ui", _APP_FORBIDDEN)
    assert not offenders, f"apps/ui must not import vendor SDKs/psycopg directly: {offenders}"


def test_apps_api_never_imports_vendor_sdks_or_sql_driver():
    offenders = _offenders(_REPO / "apps" / "api", _APP_FORBIDDEN)
    assert not offenders, f"apps/api must not import vendor SDKs/psycopg directly: {offenders}"


def test_core_never_imports_an_app_framework():
    offenders = _offenders(_REPO / "core", _CORE_FORBIDDEN)
    assert not offenders, f"core/ must stay UI/API-framework-agnostic: {offenders}"
