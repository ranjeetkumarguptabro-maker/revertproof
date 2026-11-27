"""Evidence report serialization (JSON + self-contained HTML) and history loader."""

from __future__ import annotations

import html
import json
from pathlib import Path

from revertproof.models import EvidenceReport


def save_evidence_report(report: EvidenceReport, report_dir: Path) -> tuple[Path, Path]:
    """Persist machine-readable JSON and human-readable HTML evidence reports."""
    report_dir.mkdir(parents=True, exist_ok=True)
    json_path = report_dir / f"{report.run_id}.json"
    html_path = report_dir / f"{report.run_id}.html"

    json_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    html_path.write_text(render_html_report(report), encoding="utf-8")
    return json_path, html_path


def load_recent_reports(report_dir: Path, limit: int = 50) -> list[EvidenceReport]:
    """Load persisted EvidenceReport JSON files sorted newest first."""
    if not report_dir.exists():
        return []
    files = sorted(report_dir.glob("rp-*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    reports: list[EvidenceReport] = []
    for path in files[:limit]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            reports.append(EvidenceReport.model_validate(data))
        except Exception:
            continue
    return reports


def render_html_report(report: EvidenceReport) -> str:
    """Render a clean, self-contained HTML evidence report for judges and CI artifacts."""
    badge_color = {
        "PASS": "#15803d",
        "FAIL": "#b91c1c",
        "CONDITIONAL": "#b45309",
    }.get(report.decision.value, "#334155")

    findings_rows = "".join(
        f"<tr><td>{html.escape(f.rule_id)}</td><td><b>{html.escape(f.severity.value)}</b></td>"
        f"<td>Line {f.line_number}</td><td><code>{html.escape(f.statement)}</code></td>"
        f"<td>{html.escape(f.explanation)}</td></tr>"
        for f in report.scan_report.findings
    ) or "<tr><td colspan='5'>No destructive SQL patterns flagged by static scanner.</td></tr>"

    checks_rows = "".join(
        f"<tr><td>{html.escape(k)}</td><td><b>{html.escape(v)}</b></td></tr>"
        for k, v in report.checks.items()
    )

    integrity_html = "<p>Not executed.</p>"
    if report.integrity_report:
        ir = report.integrity_report
        integrity_html = (
            f"<p><b>Status:</b> {html.escape(ir.status.value)} — {html.escape(ir.summary)}</p>"
            f"<ul>"
            f"<li>Expected records: {ir.total_expected} | Actual: {ir.total_actual} | Preserved: {ir.records_preserved}</li>"
            f"<li>Missing IDs: {len(ir.missing_ids)} | Duplicate IDs: {len(ir.duplicate_ids)}</li>"
            f"<li>Amount mismatches: {ir.amount_mismatches} | Currency mismatches: {ir.currency_mismatches} | NULL violations: {ir.null_violations}</li>"
            f"</ul>"
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>RevertProof Evidence Report — {html.escape(report.run_id)}</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; margin: 2rem; color: #0f172a; background: #f8fafc; }}
  .card {{ background: #fff; border: 1px solid #e2e8f0; border-radius: 10px; padding: 1.5rem; margin-bottom: 1.25rem; }}
  .badge {{ display: inline-block; padding: 0.35rem 0.85rem; border-radius: 999px; color: #fff; font-weight: 700; background: {badge_color}; }}
  table {{ width: 100%; border-collapse: collapse; margin-top: 0.75rem; }}
  th, td {{ border: 1px solid #e2e8f0; padding: 0.6rem; text-align: left; font-size: 0.9rem; }}
  th {{ background: #f1f5f9; }}
  code {{ background: #f1f5f9; padding: 0.15rem 0.35rem; border-radius: 4px; font-size: 0.85rem; }}
</style>
</head>
<body>
  <div class="card">
    <h1>RevertProof Evidence Report</h1>
    <p><span class="badge">{html.escape(report.decision.value)}</span> &nbsp; <b>Run ID:</b> {html.escape(report.run_id)} &nbsp;|&nbsp; <b>Domain:</b> {html.escape(report.domain)} &nbsp;|&nbsp; <b>Engine:</b> {html.escape(report.engine)}</p>
    <p><b>Migration:</b> <code>{html.escape(report.migration_file)}</code> &nbsp;|&nbsp; <b>Fixtures:</b> {report.fixture_count} &nbsp;|&nbsp; <b>Rollback Classification:</b> <code>{html.escape(report.rollback_classification.value)}</code></p>
    <p><b>Reason:</b> {html.escape(report.reason)}</p>
  </div>
  <div class="card">
    <h2>Gate Checks Summary</h2>
    <table><tr><th>Check</th><th>Result</th></tr>{checks_rows}</table>
  </div>
  <div class="card">
    <h2>Module 1 — Static Migration Scanner Findings</h2>
    <table><tr><th>Rule</th><th>Severity</th><th>Location</th><th>SQL</th><th>Explanation</th></tr>{findings_rows}</table>
  </div>
  <div class="card">
    <h2>Module 3 — Transaction / Data Integrity</h2>
    {integrity_html}
  </div>
</body>
</html>
"""
