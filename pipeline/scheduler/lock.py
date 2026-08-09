"""One shared cross-process lock for the three S6 schedulers.

daily.py starts at 07:00 and has taken 30-60+ minutes on a full scaled-
universe run; weekly.py starts Saturday 07:30, monthly.py the 1st at 08:00 --
close enough that a slow daily run and the next scheduled job can genuinely
overlap, and two schedulers writing to the same data/stockbot.db at once is
exactly what produced the "database is locked" failures seen when a manual
test run and a pytest run hit the real database at the same time.

A plain file, not a DB row: the lock has to be acquirable before anything
proves a database connection even works, and must not itself depend on the
resource it's protecting.

No OS process-liveness check (no os.kill/psutil): mtime staleness is
portable across Windows and POSIX and simple to reason about, and the
threshold (STALE_AFTER_SECONDS) is set well above the longest real run this
system has ever logged, so a live job is never mistaken for stale.
"""

from __future__ import annotations

import os
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
LOCK_PATH = REPO_ROOT / "data" / "scheduler.lock"

STALE_AFTER_SECONDS = 4 * 3600  # generous: the longest documented run is ~1h
RETRY_ATTEMPTS = 6
RETRY_DELAY_SECONDS = 10


@dataclass
class LockResult:
    acquired: bool
    held_by: str | None = None  # contents of the lock file, when not acquired


def _read_holder(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return None


def _try_acquire_once(job: str) -> bool:
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        # O_EXCL: fails if the file already exists. Atomic across processes
        # on both Windows and POSIX -- this is the actual mutex, everything
        # else here is staleness bookkeeping around it.
        fd = os.open(LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        try:
            age = time.time() - LOCK_PATH.stat().st_mtime
        except OSError:
            age = 0
        if age > STALE_AFTER_SECONDS:
            # A prior job crashed without releasing the lock, or is running
            # far longer than any real run ever has. Reclaim it rather than
            # wedging every future run forever.
            try:
                LOCK_PATH.unlink()
            except OSError:
                return False
            return _try_acquire_once(job)
        return False

    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(f"{job} pid={os.getpid()} acquired_at={time.time()}\n")
    return True


@contextmanager
def scheduler_lock(job: str):
    """Yields a LockResult. Retries briefly, then yields acquired=False
    rather than blocking indefinitely -- these run on a schedule, and a
    scheduler that's still waiting when its own next trigger fires would
    only compound the pile-up. Always releases on the way out if acquired.
    """
    acquired = False
    holder = None
    for attempt in range(RETRY_ATTEMPTS):
        if _try_acquire_once(job):
            acquired = True
            break
        holder = _read_holder(LOCK_PATH)
        if attempt < RETRY_ATTEMPTS - 1:
            time.sleep(RETRY_DELAY_SECONDS)

    try:
        yield LockResult(acquired=acquired, held_by=holder)
    finally:
        if acquired:
            try:
                LOCK_PATH.unlink()
            except OSError:
                pass
