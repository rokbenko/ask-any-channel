"""`aac doctor` — runs the environment/config checks (core.doctor) and reports pass/fail per
line. Exits non-zero if any check fails, so it doubles as a compose HEALTHCHECK command
(`aac doctor --quiet --role worker|ui`) and a CI/first-run diagnostic. Never raises on missing
config/DB/keys — that's exactly what it's meant to report, per cli/main.py's "no global
connect-to-DB callback" rule."""

import typer
from rich.console import Console

from core.doctor import ROLE_CHECKS, run_checks, versions_line

console = Console()


def _validate_role(value: str) -> str:
    if value not in ROLE_CHECKS:
        raise typer.BadParameter(f"must be one of {', '.join(sorted(ROLE_CHECKS))}")
    return value


def doctor(
    quiet: bool = typer.Option(False, "--quiet", help="Only print failing checks"),
    role: str = typer.Option(
        "all",
        "--role",
        callback=_validate_role,
        help="Which process's checks to run: all (default), worker, or ui — the same subsets "
        "the worker and UI run at boot.",
    ),
) -> None:
    if not quiet:
        console.print(versions_line())
        console.print()

    results = run_checks(role)
    failures = [r for r in results if not r.ok]

    for r in results:
        if r.ok and quiet:
            continue
        style, label = ("green", "PASS") if r.ok else ("red", "FAIL")
        console.print(f"[{style}]{label}[/{style}] {r.name}: {r.detail}")

    if failures:
        if not quiet:
            console.print(f"\n[red]{len(failures)} check(s) failed.[/red]")
        raise typer.Exit(code=1)

    if not quiet:
        console.print("\n[green]All checks passed.[/green]")
