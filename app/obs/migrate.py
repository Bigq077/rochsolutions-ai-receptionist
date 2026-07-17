"""
app/obs/migrate.py
------------------
Idempotent schema migration for the durable call store (spec §5.1).

    python -m app.obs.migrate

Creates the `calls` table if it does not already exist. This is a deliberately
lightweight migration (SQLAlchemy `create_all(checkfirst=True)`) rather than
Alembic — no new infra stack is introduced (CLAUDE.md rule 7). When the schema
later evolves (e.g. Phase 3 adds judge columns), revisit whether Alembic is
warranted and ask before adding it.

Requires DATABASE_URL to be set. Exits non-zero if it is not, so the ops runbook
fails loudly rather than silently doing nothing.
"""
from __future__ import annotations

import sys

from app import config
from app.obs import store


def main() -> int:
    if not config.DATABASE_URL:
        print("ERROR: OBS_DATABASE_URL (or DATABASE_URL) is not set — nothing to migrate.", file=sys.stderr)
        print("Set OBS_DATABASE_URL (SQLAlchemy URL, e.g. "
              "postgresql+psycopg2://user:pass@host:5432/db) and retry.", file=sys.stderr)
        return 1

    ok = store.init_db()
    if not ok:
        print("ERROR: migration did not run (no engine).", file=sys.stderr)
        return 1

    print("OK: `calls` table ensured.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
