"""Module 1: SQL Migration Scanner.

Static heuristic scanner that flags destructive or backward-incompatible SQL changes
across any relational database migration (banking, e-commerce, SaaS, etc.).

Important: Static scanning is an early-warning layer only. It must always be combined
with execution-based compatibility and data integrity tests in an isolated database.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from revertproof.models import (
    RiskSeverity,
    RollbackClassification,
    ScanFinding,
    ScannerExceptionRule,
    ScanReport,
)

SEVERITY_ORDER: dict[RiskSeverity, int] = {
    RiskSeverity.SAFE: 0,
    RiskSeverity.WARNING: 1,
    RiskSeverity.HIGH_RISK: 2,
    RiskSeverity.CRITICAL: 3,
}


@dataclass(frozen=True)
class ScanRule:
    rule_id: str
    severity: RiskSeverity
    pattern: re.Pattern[str]
    explanation: str
    affected_versions: tuple[str, ...]
    recommendation: str
    rollback_impact: RollbackClassification


RULES: tuple[ScanRule, ...] = (
    ScanRule(
        rule_id="DROP_TABLE",
        severity=RiskSeverity.CRITICAL,
        pattern=re.compile(r"\bDROP\s+TABLE\b", re.IGNORECASE),
        explanation=(
            "Drops an entire table and its data. Previous application versions querying this "
            "table will crash immediately on rollback, and data cannot be recovered without backups."
        ),
        affected_versions=("V1 (previous application)", "Downstream analytics/reporting"),
        recommendation=(
            "Keep the table during the rollback window. Stop all reads/writes first, verify "
            "no rollback target references it, and drop only in a later Contract migration."
        ),
        rollback_impact=RollbackClassification.MANUAL_RECOVERY_REQUIRED,
    ),
    ScanRule(
        rule_id="TRUNCATE_TABLE",
        severity=RiskSeverity.CRITICAL,
        pattern=re.compile(r"\bTRUNCATE\s+(?:TABLE\s+)?\w+", re.IGNORECASE),
        explanation=(
            "Irreversibly removes all rows from a table. Both V1 and V2 lose historical records."
        ),
        affected_versions=("V1 (previous application)", "V2 (new application)"),
        recommendation="Avoid TRUNCATE in release migrations; archive or copy data before cleanup.",
        rollback_impact=RollbackClassification.MANUAL_RECOVERY_REQUIRED,
    ),
    ScanRule(
        rule_id="DROP_COLUMN",
        severity=RiskSeverity.CRITICAL,
        pattern=re.compile(r"\bDROP\s+COLUMN\b", re.IGNORECASE),
        explanation=(
            "Removes a column that the previous application version (V1) may still SELECT, "
            "INSERT, or UPDATE. Rolling back application code to V1 will fail."
        ),
        affected_versions=("V1 (previous application)",),
        recommendation=(
            "Use Expand-and-Contract: retain the legacy column during the V2 rollout window, "
            "dual-write both columns, and drop the legacy column only in a later Contract release."
        ),
        rollback_impact=RollbackClassification.ROLL_FORWARD_ONLY,
    ),
    ScanRule(
        rule_id="RENAME_TABLE",
        severity=RiskSeverity.CRITICAL,
        pattern=re.compile(r"\bALTER\s+TABLE\b[^;]*\bRENAME\s+TO\b", re.IGNORECASE | re.DOTALL),
        explanation=(
            "Renames a table referenced by V1 queries. Any rollback to V1 will fail with "
            "'relation does not exist'."
        ),
        affected_versions=("V1 (previous application)",),
        recommendation=(
            "Create a new table or updatable view, synchronize writes, and remove the old name "
            "only after V1 is retired."
        ),
        rollback_impact=RollbackClassification.ROLL_FORWARD_ONLY,
    ),
    ScanRule(
        rule_id="RENAME_COLUMN",
        severity=RiskSeverity.HIGH_RISK,
        pattern=re.compile(r"\bRENAME\s+COLUMN\b", re.IGNORECASE),
        explanation=(
            "Renames a column in place. Previous application code referencing the old column "
            "name will break immediately if deployed or rolled back."
        ),
        affected_versions=("V1 (previous application)",),
        recommendation=(
            "Expand by adding the new column name, backfill + dual-write both columns, migrate "
            "reads to V2, and drop the old column in a later Contract migration."
        ),
        rollback_impact=RollbackClassification.ROLL_FORWARD_ONLY,
    ),
    ScanRule(
        rule_id="ALTER_COLUMN_TYPE",
        severity=RiskSeverity.CRITICAL,
        pattern=re.compile(
            r"\bALTER\s+COLUMN\s+\w+\s+(?:SET\s+DATA\s+)?TYPE\b", re.IGNORECASE
        ),
        explanation=(
            "Changes a column's data type in place (e.g., NUMERIC -> JSONB or VARCHAR -> INT). "
            "Previous application versions expecting the old type will fail to parse or write rows."
        ),
        affected_versions=("V1 (previous application)",),
        recommendation=(
            "Add a new column with the target type (e.g., amount_minor BIGINT), backfill exact "
            "values, keep both columns synchronised via dual-write, and contract later."
        ),
        rollback_impact=RollbackClassification.ROLL_FORWARD_ONLY,
    ),
    ScanRule(
        rule_id="SET_NOT_NULL",
        severity=RiskSeverity.HIGH_RISK,
        pattern=re.compile(r"\bALTER\s+COLUMN\s+\w+\s+SET\s+NOT\s+NULL\b", re.IGNORECASE),
        explanation=(
            "Adds a NOT NULL constraint on an existing column. Older V1 code that does not "
            "populate newly added columns will fail on INSERT."
        ),
        affected_versions=("V1 (previous application)",),
        recommendation=(
            "Ensure a safe DEFAULT or dual-write trigger populates the column for V1 writes, "
            "or enforce NOT NULL only after V1 is retired."
        ),
        rollback_impact=RollbackClassification.CONDITIONAL_ROLLBACK,
    ),
    ScanRule(
        rule_id="ADD_COLUMN_NOT_NULL_NO_DEFAULT",
        severity=RiskSeverity.HIGH_RISK,
        pattern=re.compile(
            r"\bADD\s+COLUMN\s+(?:IF\s+NOT\s+EXISTS\s+)?\w+\s+[^,;]*\bNOT\s+NULL\b(?![^,;]*\bDEFAULT\b)",
            re.IGNORECASE,
        ),
        explanation=(
            "Adds a NOT NULL column without a DEFAULT value. Existing rows and V1 INSERT "
            "statements that omit the new column will fail."
        ),
        affected_versions=("V1 (previous application)",),
        recommendation=(
            "Add the column as nullable first (or provide a safe DEFAULT), backfill existing "
            "rows, and keep V1 writes valid via dual-write synchronisation."
        ),
        rollback_impact=RollbackClassification.CONDITIONAL_ROLLBACK,
    ),
    ScanRule(
        rule_id="DESTRUCTIVE_DELETE_NO_WHERE",
        severity=RiskSeverity.CRITICAL,
        pattern=re.compile(
            r"\bDELETE\s+FROM\s+\w+\s*;", re.IGNORECASE
        ),
        explanation=(
            "Executes DELETE FROM without a WHERE clause, wiping all rows in the table."
        ),
        affected_versions=("V1 (previous application)", "V2 (new application)"),
        recommendation="Add an explicit WHERE filter or remove destructive deletes from migrations.",
        rollback_impact=RollbackClassification.MANUAL_RECOVERY_REQUIRED,
    ),
    ScanRule(
        rule_id="DESTRUCTIVE_UPDATE_OVERWRITE",
        severity=RiskSeverity.WARNING,
        pattern=re.compile(
            r"\bUPDATE\s+\w+\s+SET\b(?![^;]*\bWHERE\b)", re.IGNORECASE | re.DOTALL
        ),
        explanation=(
            "Unfiltered table-wide UPDATE detected. Safe when backfilling newly added columns "
            "during Expand, but risky if overwriting legacy columns in place."
        ),
        affected_versions=("V1 (previous application)", "V2 (new application)"),
        recommendation=(
            "Verify this UPDATE only backfills new columns without mutating legacy values, "
            "and confirm exact data integrity with automated reconciliation tests."
        ),
        rollback_impact=RollbackClassification.CONDITIONAL_ROLLBACK,
    ),
    ScanRule(
        rule_id="ENUM_OR_CONSTRAINT_CHANGE",
        severity=RiskSeverity.WARNING,
        pattern=re.compile(
            r"\b(?:ALTER\s+TYPE\b[^;]*\bADD\s+VALUE\b|ADD\s+CONSTRAINT\b|DROP\s+CONSTRAINT\b)",
            re.IGNORECASE | re.DOTALL,
        ),
        explanation=(
            "Modifies an enum type or table constraint. Previous application versions may not "
            "recognise new enum values or may violate stricter constraints on rollback."
        ),
        affected_versions=("V1 (previous application)", "API clients"),
        recommendation=(
            "Test both V1 and V2 writes against the updated constraint/enum in an isolated DB."
        ),
        rollback_impact=RollbackClassification.CONDITIONAL_ROLLBACK,
    ),
)

_ROLLBACK_RANK: dict[RollbackClassification, int] = {
    RollbackClassification.SAFE_ROLLBACK: 0,
    RollbackClassification.CONDITIONAL_ROLLBACK: 1,
    RollbackClassification.ROLL_FORWARD_ONLY: 2,
    RollbackClassification.MANUAL_RECOVERY_REQUIRED: 3,
}


def _strip_sql_comments(sql: str) -> str:
    """Replace SQL comments with spaces while preserving line numbers."""
    # Preserve newlines inside block comments
    def _replace_block(match: re.Match[str]) -> str:
        return "\n" * match.group(0).count("\n")

    cleaned = re.sub(r"/\*.*?\*/", _replace_block, sql, flags=re.DOTALL)
    cleaned = re.sub(r"--[^\n]*", "", cleaned)
    return cleaned


def _split_statements_with_lines(sql: str) -> list[tuple[int, str]]:
    """Split SQL text into (start_line_number, statement) pairs, respecting $$ blocks."""
    cleaned = _strip_sql_comments(sql)
    statements: list[tuple[int, str]] = []
    current: list[str] = []
    in_dollar_quote = False
    start_line = 1
    line_num = 1
    i = 0
    n = len(cleaned)

    while i < n:
        if cleaned[i : i + 2] == "$$":
            in_dollar_quote = not in_dollar_quote
            current.append("$$")
            i += 2
            continue

        ch = cleaned[i]
        if ch == "\n":
            line_num += 1

        if not current and ch.isspace():
            i += 1
            continue

        if not current:
            start_line = line_num

        current.append(ch)
        if ch == ";" and not in_dollar_quote:
            stmt = "".join(current).strip()
            if stmt:
                statements.append((start_line, stmt))
            current = []
        i += 1

    trailing = "".join(current).strip()
    if trailing:
        statements.append((start_line, trailing))

    return statements


def _is_backfill_only_update(stmt: str, added_columns: set[str]) -> bool:
    """Return True if an unfiltered UPDATE only assigns columns added in this migration."""
    if not added_columns:
        return False
    match = re.search(r"\bUPDATE\s+\w+\s+SET\s+(.*?)(?:;|$)", stmt, re.IGNORECASE | re.DOTALL)
    if not match:
        return False
    set_clause = match.group(1)
    assigned = re.findall(r"(\w+)\s*=", set_clause)
    return bool(assigned) and all(col.lower() in added_columns for col in assigned)


def scan_sql(
    sql: str,
    migration_file: str = "<inline>",
    exceptions: list[ScannerExceptionRule] | None = None,
) -> ScanReport:
    """Scan raw SQL migration text and return a structured ScanReport."""
    statements = _split_statements_with_lines(sql)
    exception_map = {ex.rule_id.upper(): ex for ex in (exceptions or [])}
    today = date.today().isoformat()

    # Track columns added in this same migration so safe Expand backfills are recognised
    added_columns: set[str] = set()
    for _, stmt in statements:
        for col in re.findall(
            r"\bADD\s+COLUMN\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)", stmt, re.IGNORECASE
        ):
            added_columns.add(col.lower())

    findings: list[ScanFinding] = []
    highest_severity = RiskSeverity.SAFE
    rollback_class = RollbackClassification.SAFE_ROLLBACK

    for line_no, stmt in statements:
        # Skip PL/pgSQL trigger function definitions from destructive UPDATE checks
        is_function_def = bool(re.match(r"^CREATE\s+(?:OR\s+REPLACE\s+)?FUNCTION\b", stmt, re.I))

        for rule in RULES:
            if is_function_def and rule.rule_id == "DESTRUCTIVE_UPDATE_OVERWRITE":
                continue
            if rule.rule_id == "DESTRUCTIVE_UPDATE_OVERWRITE" and _is_backfill_only_update(
                stmt, added_columns
            ):
                continue

            if rule.pattern.search(stmt):
                suppressed = False
                suppression_note = None
                ex = exception_map.get(rule.rule_id)
                if ex and ex.expires_at >= today:
                    suppressed = True
                    suppression_note = (
                        f"Reviewed exception by {ex.owner} until {ex.expires_at}: {ex.reason}"
                    )

                findings.append(
                    ScanFinding(
                        rule_id=rule.rule_id,
                        severity=rule.severity,
                        line_number=line_no,
                        statement=" ".join(stmt.split())[:220],
                        explanation=rule.explanation,
                        affected_versions=list(rule.affected_versions),
                        recommendation=rule.recommendation,
                        suppressed=suppressed,
                        suppression_note=suppression_note,
                    )
                )

                if not suppressed:
                    if SEVERITY_ORDER[rule.severity] > SEVERITY_ORDER[highest_severity]:
                        highest_severity = rule.severity
                    if _ROLLBACK_RANK[rule.rollback_impact] > _ROLLBACK_RANK[rollback_class]:
                        rollback_class = rule.rollback_impact

    return ScanReport(
        migration_file=migration_file,
        highest_severity=highest_severity,
        rollback_classification=rollback_class,
        findings=findings,
    )


def scan_file(
    path: str | Path,
    exceptions: list[ScannerExceptionRule] | None = None,
) -> ScanReport:
    """Read a SQL migration file from disk and scan it."""
    file_path = Path(path)
    sql = file_path.read_text(encoding="utf-8")
    return scan_sql(sql, migration_file=str(file_path), exceptions=exceptions)
