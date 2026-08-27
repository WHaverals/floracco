"""Process-wide write serialization for the review server.

Phase A of docs/multi_user_safety.md (§0): every mutating endpoint runs its whole
check → write → log body under ONE lock, which is what makes the multi-file write
sequence (main.db + corrections.db + the JSONL/CSV stores) atomic against the
server's other request threads — all endpoints are sync ``def``, so even the
single uvicorn worker runs them on a ~40-thread pool.

Two layers, fixed order (thread lock first, so exactly one flock holder per
process):

* a ``threading.Lock`` — the in-process serializer;
* an ``fcntl.flock`` on a lockfile at the data root — the cross-process
  extension (deploy-shell scripts, ``db_import``, a future ``--workers > 1``).
  Acquired NON-blocking with a short bounded retry: an external maintenance
  hold must yield a clean 503, never freeze request threads (§9 — an unbounded
  wait here would starve the shared thread pool, reads included).

The lockfile lives at the DATA ROOT, outside every rsync'd subtree, and is
never unlinked by anyone: flock identity lives in the inode, so deleting and
recreating the file would hand a second "exclusive" lock to another process
(§9, verified). Crash safety needs no cleanup — the kernel releases a flock
when its file descriptor closes, including on SIGKILL.
"""

from __future__ import annotations

import fcntl
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

_THREAD_LOCK = threading.Lock()

# ~2.5 s worst case before giving up — long enough to ride out another process's
# brief write, far too short to pile requests onto the thread pool.
FLOCK_ATTEMPTS = 10
FLOCK_RETRY_SECONDS = 0.25


class WriteLockBusy(RuntimeError):
    """Another process holds the data-root write lock (maintenance in progress)."""


def write_lock_path() -> Path:
    """Resolved at CALL time (never import time) so tests can point it at a tmp
    dir and a changed FLORACCO_DATA_DIR is honored without a restart."""
    root = os.getenv("FLORACCO_DATA_DIR")
    base = Path(root).expanduser() if root else Path(__file__).resolve().parents[1] / "data"
    return base / ".write.lock"


@contextmanager
def write_lock(*, attempts: int | None = None) -> Iterator[None]:
    """The server-side write lock: thread lock, then bounded-retry flock.

    Raises :class:`WriteLockBusy` when another process holds the flock past the
    retry budget — callers turn that into a 503.
    """
    tries = attempts if attempts is not None else FLOCK_ATTEMPTS
    with _THREAD_LOCK:
        path = write_lock_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            for attempt in range(tries):
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if attempt == tries - 1:
                        raise WriteLockBusy(
                            "another process holds the write lock "
                            f"({path}) — maintenance in progress"
                        ) from None
                    time.sleep(FLOCK_RETRY_SECONDS)
            yield
        finally:
            os.close(fd)  # the kernel releases the flock with the fd


@contextmanager
def maintenance_lock(*, wait: bool = False) -> Iterator[None]:
    """For offline tools (db_import, reset scripts): exclusive flock on the same
    lockfile. Non-blocking by default — refuse loudly rather than queue behind a
    live server; ``wait=True`` opts into blocking for unattended runs."""
    path = write_lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))
        except BlockingIOError:
            raise WriteLockBusy(
                f"the write lock at {path} is held (a live server?) — "
                "stop it first, or pass wait=True / --wait"
            ) from None
        yield
    finally:
        os.close(fd)
