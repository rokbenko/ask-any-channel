"""The import direction between core/ and apps/ui/ is one-way, and each side must never reach
around the seams: the UI never talks to vendor SDKs or the DB driver directly, and core never
grows a dependency on the UI framework. AST-based (not grep) so a comment or string that merely
mentions a package name can't trip it."""

import ast
from pathlib import Path

_REPO = Path(__file__).parent.parent
_UI_FORBIDDEN = {"openai", "anthropic", "psycopg"}
_CORE_FORBIDDEN = {"streamlit"}


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


def test_apps_ui_never_imports_vendor_sdks_or_sql_driver():
    offenders = _offenders(_REPO / "apps" / "ui", _UI_FORBIDDEN)
    assert not offenders, f"apps/ui must not import vendor SDKs/psycopg directly: {offenders}"


def test_core_never_imports_streamlit():
    offenders = _offenders(_REPO / "core", _CORE_FORBIDDEN)
    assert not offenders, f"core/ must stay UI-framework-agnostic: {offenders}"
