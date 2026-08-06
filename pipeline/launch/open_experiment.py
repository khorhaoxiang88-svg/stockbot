"""Open the official forward experiment.

A one-time, one-way action: from this moment, generated_at >= this
experiment's started_at is the permanent, structural line official_candidates
/ official_positions / official_benchmark_positions (migration 022) draw.
Everything before it -- the Phase F fixture, every Phase S paper trade -- is
permanently excluded from official statistics, enforced by those views, not
by convention.

Refuses to run unless the frozen config is locked and matches exactly
(reuses selection.compute.verify_frozen_config_lock, the same check official
candidate generation itself uses) -- launching under a drifted or unlocked
config would mean nobody actually reviewed what got launched.
"""

from __future__ import annotations

import argparse
import sys
import uuid
from pathlib import Path

PIPELINE_DIR = Path(__file__).resolve().parent.parent
if str(PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(PIPELINE_DIR))

import migrate  # noqa: E402
from config_loader import DEFAULT_CONFIG_PATH, load_config  # noqa: E402
from scoring.compute import config_hash  # noqa: E402
from selection.compute import verify_frozen_config_lock  # noqa: E402
from universe import membership as M  # noqa: E402
from universe.sec_client import utc_today  # noqa: E402


class LaunchError(RuntimeError):
    pass


def open_experiment(conn, cfg: dict, running_hash: str, pool_versions: list[str],
                     as_of_date: str) -> dict:
    existing_active = conn.execute(
        "SELECT experiment_id, started_at FROM experiments WHERE status = 'active'"
    ).fetchone()
    if existing_active is not None:
        raise LaunchError(
            f"an experiment is already active: {existing_active['experiment_id']} "
            f"(started {existing_active['started_at']}). Only one experiment may be "
            f"active at a time; end it before opening another."
        )

    # Refuses loudly on a missing or mismatched lock -- the same check
    # selection/compute.py runs before generating an official candidate.
    verify_frozen_config_lock(conn, cfg, running_hash)

    experiment_id = f"exp-{uuid.uuid4().hex[:12]}"
    started_at = M.utc_now_iso()
    conn.execute(
        "INSERT INTO experiments (experiment_id, strategy_version, "
        "selection_rule_version, protocol_version, config_hash, started_at, status) "
        "VALUES (?, ?, ?, ?, ?, ?, 'active')",
        (
            experiment_id,
            int(cfg["strategy_version"]),
            int(cfg["selection_rule_version"]),
            int(cfg["protocol_version"]),
            running_hash,
            started_at,
        ),
    )

    snapshot_id = M.compute_snapshot(
        conn, as_of_date, "monthly_membership",
        pool_versions=pool_versions, is_official=True,
    )
    security_count = conn.execute(
        "SELECT security_count FROM universe_snapshot_runs WHERE snapshot_id = ?",
        (snapshot_id,),
    ).fetchone()["security_count"]

    return {
        "experiment_id": experiment_id,
        "started_at": started_at,
        "strategy_version": int(cfg["strategy_version"]),
        "selection_rule_version": int(cfg["selection_rule_version"]),
        "protocol_version": int(cfg["protocol_version"]),
        "config_hash": running_hash,
        "snapshot_id": snapshot_id,
        "security_count": security_count,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Open the official forward experiment")
    parser.add_argument("--db", default=str(migrate.DEFAULT_DB_PATH))
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    parser.add_argument(
        "--pool", action="append", required=True,
        help="universe_candidate_pool version(s) to include in the launch snapshot, "
        "alongside the Phase F fixture (always included, see "
        "membership.securities_to_evaluate); repeatable",
    )
    parser.add_argument("--as-of", default=None, help="defaults to today (UTC)")
    args = parser.parse_args(argv)

    cfg = dict(load_config(Path(args.config)))
    running_hash = config_hash(Path(args.config))
    as_of_date = args.as_of or utc_today()

    conn = migrate.connect(Path(args.db))
    try:
        conn.execute("BEGIN")
        result = open_experiment(conn, cfg, running_hash, args.pool, as_of_date)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()

    print("EXPERIMENT OPENED")
    print(f"  experiment_id            {result['experiment_id']}")
    print(f"  started_at (UTC)         {result['started_at']}")
    print(f"  strategy_version         {result['strategy_version']}")
    print(f"  selection_rule_version   {result['selection_rule_version']}")
    print(f"  protocol_version         {result['protocol_version']}")
    print(f"  config_hash              {result['config_hash']}")
    print(f"  official snapshot_id     {result['snapshot_id']}")
    print(f"  securities in snapshot   {result['security_count']}")
    print()
    print("Everything generated before started_at is permanently excluded from")
    print("official statistics (official_candidates / official_positions /")
    print("official_benchmark_positions, migration 022).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
