"""End-to-end repeatable demonstration of RevertProof.

Runs all required scenarios with 1,000 synthetic banking transactions (plus the
e-commerce shopping site migration proof):
  - Scenario A: Unsafe Banking Migration (V2 passes, V1 fails -> BLOCKED)
  - Scenario B: Safe Expand-and-Contract Banking Migration (V1 + V2 + Dual-Write + 1,000 tx integrity -> PASS)
  - Scenario C: Controlled V2 Failure -> Rollback to V1 Verification -> PASS
  - Bonus Scenario D: E-Commerce Shopping Site (Unsafe vs Safe Expand)
"""

from __future__ import annotations

from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from revertproof.config import Settings, get_settings
from revertproof.gate import evaluate_migration_file, run_rollback_simulation
from revertproof.models import EvidenceReport

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def run_full_demo(
    fixture_count: int = 1000,
    settings: Settings | None = None,
    include_ecommerce: bool = True,
    quiet: bool = False,
) -> dict[str, EvidenceReport]:
    """Execute Scenarios A, B, C (and optional E-Commerce D) and return all EvidenceReports."""
    cfg = settings or get_settings()
    console = Console(quiet=quiet)

    console.print(
        Panel.fit(
            "[bold cyan]RevertProof — Database-Aware Deployment Rollback Verification[/bold cyan]\n"
            f"Primary Demo: {fixture_count:,} synthetic banking transactions | "
            f"DB Engine: {'postgresql' if cfg.is_postgres else 'sqlite (local)'}",
            border_style="cyan",
        )
    )

    results: dict[str, EvidenceReport] = {}

    # Scenario A: Unsafe Banking Migration
    console.print("\n[bold red]Scenario A — Unsafe Banking Migration (sql/unsafe_v2.sql)[/bold red]")
    results["scenario_a_unsafe"] = evaluate_migration_file(
        PROJECT_ROOT / "sql" / "unsafe_v2.sql",
        domain="banking",
        scenario="scenario_a_unsafe_banking",
        fixture_count=fixture_count,
        settings=cfg,
    )
    _print_summary(console, results["scenario_a_unsafe"])

    # Scenario B: Safe Expand-and-Contract Banking Migration
    console.print(
        "\n[bold green]Scenario B — Safe Expand-and-Contract Migration (sql/safe_expand.sql)[/bold green]"
    )
    results["scenario_b_safe"] = evaluate_migration_file(
        PROJECT_ROOT / "sql" / "safe_expand.sql",
        domain="banking",
        scenario="scenario_b_safe_expand_banking",
        fixture_count=fixture_count,
        settings=cfg,
    )
    _print_summary(console, results["scenario_b_safe"])

    # Scenario C: Controlled Rollback Verification
    console.print(
        "\n[bold blue]Scenario C — Simulated V2 Health Failure -> Rollback to V1 Verification[/bold blue]"
    )
    results["scenario_c_rollback"] = run_rollback_simulation(
        PROJECT_ROOT / "sql" / "safe_expand.sql",
        fixture_count=fixture_count,
        settings=cfg,
    )
    _print_summary(console, results["scenario_c_rollback"])

    # Scenario D: E-Commerce Shopping Site Proof (Any Database Migration)
    if include_ecommerce:
        console.print(
            "\n[bold magenta]Scenario D — General Migration Proof: E-Commerce Shopping Site[/bold magenta]"
        )
        results["scenario_d_ecommerce_unsafe"] = evaluate_migration_file(
            PROJECT_ROOT / "sql" / "ecommerce" / "unsafe_v2.sql",
            domain="ecommerce",
            scenario="scenario_d_ecommerce_unsafe",
            fixture_count=200,
            settings=cfg,
        )
        _print_summary(console, results["scenario_d_ecommerce_unsafe"])

        results["scenario_d_ecommerce_safe"] = evaluate_migration_file(
            PROJECT_ROOT / "sql" / "ecommerce" / "safe_expand.sql",
            domain="ecommerce",
            scenario="scenario_d_ecommerce_safe",
            fixture_count=200,
            settings=cfg,
        )
        _print_summary(console, results["scenario_d_ecommerce_safe"])

    return results


def _print_summary(console: Console, report: EvidenceReport) -> None:
    color = {"PASS": "green", "FAIL": "red", "CONDITIONAL": "yellow"}[report.decision.value]
    table = Table(show_header=True, header_style="bold")
    table.add_column("Check")
    table.add_column("Result")
    for k, v in report.checks.items():
        table.add_row(k, v)
    table.add_row("rollback_classification", report.rollback_classification.value)
    table.add_row("pipeline_decision", f"[{color}]{report.decision.value} (exit {report.exit_code})[/{color}]")
    console.print(table)
    console.print(f"[bold]Reason:[/bold] {report.reason}")
    if report.aws_artifacts.get("local_json"):
        console.print(f"[dim]Evidence saved: {report.aws_artifacts['local_json']}[/dim]")


if __name__ == "__main__":
    run_full_demo()
