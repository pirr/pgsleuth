"""Default colored terminal reporter."""

from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from rich.console import Console
from rich.text import Text

from pgsleuth.checkers.base import Issue, Severity

_COLOR = {
    Severity.ERROR: "bold red",
    Severity.WARNING: "yellow",
    Severity.INFO: "cyan",
}


def render(issues: Iterable[Issue], console: Console | None = None) -> None:
    console = console or Console()
    issues = list(issues)

    if not issues:
        console.print("[green]No issues found.[/green]")
        return

    grouped: dict[str, list[Issue]] = defaultdict(list)
    for issue in issues:
        grouped[issue.checker].append(issue)

    for checker_name in sorted(grouped):
        console.rule(f"[bold]{checker_name}[/bold] ({len(grouped[checker_name])})")
        for issue in grouped[checker_name]:
            tag = Text(f"[{issue.severity.value.upper()}]", style=_COLOR[issue.severity])
            console.print(tag, Text(f" {issue.object_name}", style="bold"))
            console.print(f"  {issue.message}")
            if issue.suggestion:
                console.print(f"  [dim]suggestion:[/dim] {issue.suggestion}")
            if issue.docs_url:
                console.print(f"  [dim]docs:[/dim] {issue.docs_url}")
            console.print()

    counts = defaultdict(int)
    for issue in issues:
        counts[issue.severity] += 1
    summary_parts = [f"[{_COLOR[s]}]{counts[s]} {s.value}[/]" for s in Severity if counts[s]]
    console.print("Summary: " + ", ".join(summary_parts))
