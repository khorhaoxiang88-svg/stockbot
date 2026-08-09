"""Publishes a small, public-safe status.json to a dedicated `bot-status`
branch of this same GitHub repo, via a separate git worktree -- never the
main checkout, so a publish can never touch a file the currently-running
pipeline stages depend on.

Free-only by design (no Cloudflare D1/R2, no paid backend): the public site
reads this by fetching raw.githubusercontent.com directly. That means the
data is only as fresh as the last push, and pushes are deliberately rate-
limited (HEARTBEAT_SECONDS) so a fast-moving orchestration stage does not
turn into dozens of commits.

SAFE fields only -- aggregate counts, dates, state strings. Never a symbol,
a price, a file path, a stack trace, an API key, or anything else that
identifies a specific security or exposes internals. See _build_payload's
own field list; nothing outside it is ever included.
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

PIPELINE_DIR = Path(__file__).resolve().parent.parent
if str(PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(PIPELINE_DIR))

REPO_ROOT = PIPELINE_DIR.parent
WORKTREE_DIR = REPO_ROOT.parent / "stockbot-bot-status"
BRANCH = "bot-status"
STATUS_FILENAME = "status.json"

HEARTBEAT_SECONDS = 5 * 60  # never push more often than this if nothing changed

# Schedule constants mirrored from the actual Windows Scheduled Tasks
# (see README's S6 section) -- Asia/Kuala_Lumpur is UTC+8 with no DST, so
# these UTC times are fixed, not computed from a timezone library.
DAILY_UTC_HOUR = 23  # 07:00 local (previous UTC day)
WEEKLY_WEEKDAY = 5  # Saturday (Mon=0)
WEEKLY_UTC_HOUR = 23  # 07:30 local -> 23:30 UTC previous day; hour truncated for the estimate
MONTHLY_UTC_HOUR = 0  # 08:00 local -> 00:00 UTC same day (1st of month)

ORCHESTRATE_STAGES = {"prices", "form4", "xbrl", "universe", "dilution", "riskflags"}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def next_scheduled_run(now: datetime | None = None) -> str:
    """Earliest of the next daily/weekly/monthly trigger, as UTC ISO-8601.

    A simple forward search (next 32 days, hour-granularity) rather than
    per-job closed-form arithmetic -- easier to verify by eye and cheap
    enough (at most a few hundred iterations) that performance is a
    non-issue for something computed at most once every few minutes.
    """
    from datetime import timedelta

    now = now or _utc_now()
    candidates: list[datetime] = []

    probe = now.replace(minute=0, second=0, microsecond=0)
    for _ in range(32 * 24):
        probe = probe + timedelta(hours=1)
        if probe <= now:
            continue
        if probe.hour == DAILY_UTC_HOUR:
            candidates.append(probe)
            break
    probe = now.replace(minute=0, second=0, microsecond=0)
    for _ in range(32 * 24):
        probe = probe + timedelta(hours=1)
        if probe <= now:
            continue
        if probe.weekday() == WEEKLY_WEEKDAY and probe.hour == WEEKLY_UTC_HOUR:
            candidates.append(probe)
            break
    probe = now.replace(minute=0, second=0, microsecond=0)
    for _ in range(32 * 24):
        probe = probe + timedelta(hours=1)
        if probe <= now:
            continue
        if probe.day == 1 and probe.hour == MONTHLY_UTC_HOUR:
            candidates.append(probe)
            break

    return _iso(min(candidates)) if candidates else _iso(now)


@dataclass
class RunContext:
    """What the caller currently knows, mid-run. All optional -- a
    between-runs publish (idle) passes none of these."""
    scanner_state: str  # 'running' | 'succeeded' | 'partial' | 'failed' | 'idle'
    current_stage: str | None = None
    run_id: str | None = None


def _progress_for_stage(conn, stage: str, run_id: str | None) -> dict | None:
    """(completed, total) for an orchestrated stage, or None for stages
    that don't have a per-item concept (scoring, selection, ...).

    total is an ESTIMATE: the count of currently-included securities in the
    newest monthly universe snapshot, since orchestration_progress itself
    has no upfront "expected total" column. completed is a real count of
    distinct securities this run has recorded success for. Honest
    approximation, not a fabricated precise number -- documented here so a
    future reader doesn't mistake it for exact.
    """
    if stage not in ORCHESTRATE_STAGES or not run_id:
        return None
    total_row = conn.execute(
        """
        SELECT COUNT(*) AS n FROM universe_snapshots u
         WHERE u.status = 'included'
           AND u.snapshot_id = (
               SELECT r.snapshot_id FROM universe_snapshot_runs r
                WHERE r.run_type = 'monthly_membership'
                ORDER BY r.effective_at DESC LIMIT 1
           )
        """
    ).fetchone()
    total = int(total_row["n"]) if total_row and total_row["n"] else None
    completed_row = conn.execute(
        "SELECT COUNT(DISTINCT item_key) AS n FROM orchestration_progress "
        "WHERE run_id = ? AND stage = ? AND status = 'success'",
        (run_id, stage),
    ).fetchone()
    completed = int(completed_row["n"]) if completed_row else 0
    if total is None:
        return None
    return {"completed": completed, "total": total}


def compute_status(conn, context: RunContext) -> dict:
    """The full public-safe payload. Every field is a count, a date, or a
    short state/stage string -- see the module docstring."""
    now = _utc_now()

    score_row = conn.execute(
        "SELECT score_date, COUNT(*) AS ranked_count FROM scores "
        "WHERE rankable = 1 AND score_date = (SELECT MAX(score_date) FROM scores)"
    ).fetchone()
    latest_score_date = score_row["score_date"] if score_row else None
    ranked_count = int(score_row["ranked_count"]) if score_row and score_row["score_date"] else 0

    selection_row = conn.execute(
        "SELECT run_id, records_written, status FROM pipeline_runs "
        "WHERE stage = 'selection' ORDER BY started_at DESC LIMIT 1"
    ).fetchone()
    if selection_row is None:
        selection_status, selected_count = "no_run_yet", 0
    else:
        selected_count = int(selection_row["records_written"] or 0)
        stale = conn.execute(
            "SELECT COUNT(*) AS n FROM suppressed_signals "
            "WHERE run_id = ? AND suppression_reason = 'stale_source'",
            (selection_row["run_id"],),
        ).fetchone()["n"]
        if selected_count == 0 and stale:
            selection_status = "blocked_stale_source"
        elif selected_count == 0:
            selection_status = "zero_candidates"
        else:
            selection_status = "published"

    last_success_row = conn.execute(
        "SELECT MAX(finished_at) AS t FROM pipeline_runs "
        "WHERE stage = 'scheduler_daily' AND status = 'success'"
    ).fetchone()
    last_success_at = last_success_row["t"] if last_success_row else None

    return {
        "schema_version": 1,
        "generated_at": _iso(now),
        "scanner_state": context.scanner_state,
        "current_stage": context.current_stage,
        "progress": _progress_for_stage(conn, context.current_stage, context.run_id)
        if context.current_stage else None,
        "last_activity_at": _iso(now),
        "last_success_at": last_success_at,
        "latest_score_date": latest_score_date,
        "ranked_count": ranked_count,
        "latest_selection_status": selection_status,
        "selected_count": selected_count,
        "next_scheduled_run": next_scheduled_run(now),
    }


def _run(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=str(cwd), capture_output=True, text=True)


def ensure_worktree() -> Path:
    """Create the bot-status worktree (and orphan branch, if the branch
    doesn't exist remotely or locally yet) if it isn't already set up.
    Idempotent -- safe to call on every publish."""
    if (WORKTREE_DIR / ".git").exists():
        return WORKTREE_DIR

    branch_exists = _run(
        ["git", "rev-parse", "--verify", f"refs/remotes/origin/{BRANCH}"], REPO_ROOT
    ).returncode == 0

    if branch_exists:
        _run(["git", "fetch", "origin", BRANCH], REPO_ROOT)
        result = _run(
            ["git", "worktree", "add", str(WORKTREE_DIR), BRANCH], REPO_ROOT
        )
    else:
        result = _run(
            ["git", "worktree", "add", "--detach", str(WORKTREE_DIR)], REPO_ROOT
        )
        if result.returncode == 0:
            _run(["git", "checkout", "--orphan", BRANCH], WORKTREE_DIR)
            _run(["git", "rm", "-rf", "--quiet", "."], WORKTREE_DIR)
    if result.returncode != 0:
        raise RuntimeError(f"could not create bot-status worktree: {result.stderr}")
    return WORKTREE_DIR


def _last_published_status() -> dict | None:
    path = WORKTREE_DIR / STATUS_FILENAME
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _meaningfully_changed(previous: dict | None, current: dict) -> bool:
    if previous is None:
        return True
    compare_keys = {k for k in current if k not in ("generated_at", "last_activity_at", "next_scheduled_run")}
    return any(previous.get(k) != current.get(k) for k in compare_keys)


def _seconds_since_last_commit(worktree: Path) -> float:
    result = _run(["git", "log", "-1", "--format=%ct"], worktree)
    if result.returncode != 0 or not result.stdout.strip():
        return float("inf")
    try:
        last_commit_epoch = int(result.stdout.strip())
    except ValueError:
        return float("inf")
    return _utc_now().timestamp() - last_commit_epoch


def publish(status: dict, force: bool = False) -> bool:
    """Writes status.json into the bot-status worktree and pushes it, but
    only when the content meaningfully changed OR the heartbeat interval
    has elapsed -- this is what keeps an hour-long orchestration stage from
    producing dozens of near-identical commits. Returns whether a push
    actually happened."""
    worktree = ensure_worktree()
    previous = _last_published_status()

    if not force and not _meaningfully_changed(previous, status):
        if _seconds_since_last_commit(worktree) < HEARTBEAT_SECONDS:
            return False

    (worktree / STATUS_FILENAME).write_text(
        json.dumps(status, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    _run(["git", "add", STATUS_FILENAME], worktree)
    diff_check = _run(["git", "diff", "--cached", "--quiet"], worktree)
    if diff_check.returncode == 0:
        return False  # nothing actually changed on disk, don't create an empty commit

    commit = _run(
        ["git", "commit", "-m", f"status: {status['scanner_state']} @ {status['generated_at']}"],
        worktree,
    )
    if commit.returncode != 0:
        print(f"publish_status: commit failed: {commit.stderr}", file=sys.stderr)
        return False

    push = _run(["git", "push", "origin", BRANCH], worktree)
    if push.returncode != 0:
        print(f"publish_status: push failed: {push.stderr}", file=sys.stderr)
        return False
    return True


def publish_for(conn, context: RunContext, force: bool = False) -> dict | None:
    """Convenience: compute + publish in one call, returns the payload
    whether or not it was actually pushed (callers that just want the
    numbers -- e.g. a CLI printout -- don't need to check the bool).

    Never raises. This is a side channel, called many times per real
    scheduler run (before/after every stage) -- a transient failure here
    (a SQLite lock, a network blip on the git push) must never be able to
    take down the actual pipeline run calling it. Confirmed the hard way:
    an unguarded call crashed the whole Aug 9 resume attempt on a
    "database is locked" before the prices stage even started, publishing
    nothing had run yet. Returns None on failure so a caller CAN check, but
    none of the current callers need to -- they're fire-and-forget.
    """
    try:
        status = compute_status(conn, context)
        publish(status, force=force)
        return status
    except Exception as exc:  # noqa: BLE001 -- isolation boundary, must not raise
        print(f"publish_status: publish_for failed (non-fatal): {exc}", file=sys.stderr)
        return None
