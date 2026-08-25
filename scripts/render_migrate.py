"""Run `alembic upgrade head`, repairing a stale `alembic_version` first.

Why this exists
---------------
Commits 9361da1 and 671b538 deleted four revisions that had already been
applied to the deployed database:

    20260625_sessions_setnull                        (down: e8c1f4a2b7d3)
    20260625_make_sessions_tractor_nullable_setnull  (down: e8c1f4a2b7d3)
    h1i2j3k4l5m6  add implement preset range columns (down: g2h3i4j5k6l7)
    i2j3k4l5m6n7  merge of the two branches above    (head at 671b538^)

They were replaced by a rewritten linear chain that does not contain them, and
the model layer was reverted to match (`implements.preset_*_min/max` are gone,
`sessions.tractor_id` is `nullable=False` / `ondelete=RESTRICT` again).

The database, however, still holds the old revision id in `alembic_version`.
Alembic resolves that id against the migration files on disk, finds nothing,
and aborts the deploy:

    CommandError: Can't locate revision identified by 'i2j3k4l5m6n7'

No new migration can fix that -- the version pointer has to be rewritten before
`upgrade` runs. This script does exactly that and nothing else.

What it does
------------
1. Reads `alembic_version`.
2. Any revision alembic cannot resolve is mapped, via RETIRED_REVISIONS below,
   to the point in the current chain that represents the same schema state.
3. If several rows map (the pre-merge branched state stamps two rows), the one
   furthest along the current chain wins and the table is collapsed to it.
4. Runs `alembic upgrade head`.

It is idempotent and a no-op on a healthy database: a resolvable version, an
empty table, and a database with no `alembic_version` at all are all left
untouched and handed straight to `upgrade`.

Schema left behind by the retired revisions
-------------------------------------------
Re-stamping does not undo what those migrations did, and the current chain does
not reverse it either. A database that had them applied keeps:

  * `implements.preset_speed_kmh_min/max`, `preset_depth_cm_min/max` -- four
    spare nullable columns no model maps any more; inert.
  * `sessions.tractor_id` nullable with an ON DELETE SET NULL foreign key, where
    a fresh database gets NOT NULL / ON DELETE RESTRICT.

The second is a real divergence between an upgraded and a fresh database. It is
left alone deliberately: tightening the column would fail against any existing
NULL row, so it belongs in its own reviewed migration, not in a deploy hook.

Usage
-----
    python scripts/render_migrate.py

Point Render's pre-deploy (or build) command at this instead of
`alembic upgrade head`.
"""

from __future__ import annotations

import os
import sys

# Import alembic BEFORE the project root goes on sys.path. The repo has an
# `alembic/` directory of its own, and it has no __init__.py, so a project root
# sitting ahead of site-packages turns `import alembic` into an empty namespace
# package -- `alembic.config` then does not exist. Append rather than insert for
# the same reason: site-packages must win.
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.append(PROJECT_ROOT)

from app.config import settings  # noqa: E402

# Revision id no longer on disk -> the revision in the current chain that
# represents the same schema state (its down_revision, for each retired one).
RETIRED_REVISIONS = {
    "20260625_sessions_setnull": "e8c1f4a2b7d3",
    "20260625_make_sessions_tractor_nullable_setnull": "e8c1f4a2b7d3",
    "h1i2j3k4l5m6": "g2h3i4j5k6l7",
    "i2j3k4l5m6n7": "g2h3i4j5k6l7",
}


def _chain_order(script: ScriptDirectory) -> dict:
    """revision id -> position from base, for the current linear chain."""
    revisions = list(script.walk_revisions())  # head first
    return {rev.revision: index for index, rev in enumerate(reversed(revisions))}


def _current_versions(engine) -> list:
    if not inspect(engine).has_table("alembic_version"):
        return []
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT version_num FROM alembic_version")).fetchall()
    return [row[0] for row in rows]


def _repair(engine, script: ScriptDirectory, versions: list) -> None:
    order = _chain_order(script)
    unknown = [v for v in versions if v not in order]
    if not unknown:
        print("alembic_version is resolvable ({0}); no repair needed.".format(
            ", ".join(versions)))
        return

    unmapped = [v for v in unknown if v not in RETIRED_REVISIONS]
    if unmapped:
        raise SystemExit(
            "alembic_version holds {0}, which is neither in the current chain nor "
            "in RETIRED_REVISIONS. Refusing to guess -- add a mapping to "
            "scripts/render_migrate.py once you know which schema state it "
            "corresponds to.".format(", ".join(sorted(unmapped)))
        )

    # Every row maps to a point in the current chain. Rows alembic could already
    # resolve stay in the running too, so a partially-migrated branch state
    # collapses to whichever point is furthest along.
    candidates = [RETIRED_REVISIONS[v] for v in unknown]
    candidates += [v for v in versions if v in order]

    missing = [c for c in candidates if c not in order]
    if missing:
        raise SystemExit(
            "RETIRED_REVISIONS maps to {0}, which is not in the current chain. "
            "The mapping is stale.".format(", ".join(sorted(missing)))
        )

    target = max(candidates, key=lambda rev: order[rev])

    print("Repairing alembic_version: {0} -> {1}".format(", ".join(versions), target))
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM alembic_version"))
        conn.execute(
            text("INSERT INTO alembic_version (version_num) VALUES (:v)"),
            {"v": target},
        )


def main() -> None:
    config = Config(os.path.join(PROJECT_ROOT, "alembic.ini"))
    config.set_main_option("script_location", os.path.join(PROJECT_ROOT, "alembic"))
    script = ScriptDirectory.from_config(config)

    heads = script.get_heads()
    if len(heads) != 1:
        raise SystemExit(
            "Expected exactly one head, found {0}: {1}. Merge them before "
            "deploying.".format(len(heads), ", ".join(heads))
        )

    engine = create_engine(settings.DATABASE_URL, future=True)
    try:
        versions = _current_versions(engine)
        if not versions:
            print("No alembic_version rows; running a full upgrade.")
        else:
            _repair(engine, script, versions)
    finally:
        engine.dispose()

    print("Upgrading to head ({0}).".format(heads[0]))
    command.upgrade(config, "head")
    print("Done.")


if __name__ == "__main__":
    main()
