"""Bring the configured database to the current schema revision.

This is what Docker runs before the server starts, and what you run locally
after pulling a change that added a migration. It is *not* what the app does
on import: silently mutating a production schema on boot is how `create_all`
used to hide drift.

A database that predates migrations -- tables, no `alembic_version` row -- is
stamped rather than upgraded, but only after the live schema is checked against
the models. A mismatch is refused rather than papered over.

Usage, from the backend/ directory:
    python -m scripts.migrate
"""

from __future__ import annotations

from app.db.schema import SchemaError, ensure_schema, head_revision


def main() -> int:
    try:
        outcome = ensure_schema()
    except SchemaError as exc:
        print(f"Refused: {exc}")
        return 1

    head = head_revision()
    messages = {
        "current": f"Already at {head}.",
        "upgraded": f"Applied migrations; now at {head}.",
        "stamped": (
            f"Existing schema matched the models; recorded revision {head} "
            "without changing any tables."
        ),
    }
    print(messages[outcome])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
