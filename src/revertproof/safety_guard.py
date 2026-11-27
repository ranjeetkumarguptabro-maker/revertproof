"""Production database safety guard & connection validator.

Prevents RevertProof from EVER running destructive migration rehearsals against
production hostnames or production database names.
"""

from __future__ import annotations

import os
import re
from urllib.parse import urlparse

DEFAULT_BLOCKED_PATTERNS: tuple[str, ...] = (
    "prod",
    "production",
    "live",
    "primary-db",
    "master-db",
)


class ProductionDatabaseBlockedError(PermissionError):
    """Raised when a target database URL matches production safety blocklists."""


def validate_safe_test_database_url(
    database_url: str,
    blocked_patterns: list[str] | None = None,
) -> None:
    """Block any database URL whose host or database name matches a production pattern."""
    if database_url.startswith("sqlite://"):
        return

    parsed = urlparse(database_url)
    host = (parsed.hostname or "").lower()
    dbname = (parsed.path or "").lstrip("/").lower()

    extra = [
        p.strip().lower()
        for p in os.getenv("RP_BLOCKED_DB_PATTERNS", "").split(",")
        if p.strip()
    ]
    patterns = set(DEFAULT_BLOCKED_PATTERNS)
    if blocked_patterns:
        patterns.update(p.lower() for p in blocked_patterns)
    patterns.update(extra)

    # Check word/segment boundaries so "revertproof_test" isn't falsely flagged, while
    # "prod-db.rds.amazonaws.com", "bank_prod", "production" ARE blocked.
    for pat in patterns:
        regex = re.compile(rf"(?:^|[._\-/]){re.escape(pat)}(?:$|[._\-/0-9])", re.IGNORECASE)
        if regex.search(host) or regex.search(dbname):
            raise ProductionDatabaseBlockedError(
                f"SAFETY BLOCK: Refusing to run destructive migration tests against suspected "
                f"production database (matched pattern '{pat}' in host='{host}' db='{dbname}'). "
                f"Point RevertProof only at an isolated disposable test database."
            )
