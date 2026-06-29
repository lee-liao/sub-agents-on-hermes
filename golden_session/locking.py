"""Single-writer-per-session-id serialization (F8).

Concurrent writes to one `.jsonl` transcript corrupt it (doc 02 gotcha 4). This
module serializes any two calls that target the *same* session id, while leaving
calls on *different* sids fully parallel — so concurrent forks off GOLD (each
writing a distinct output file) stay fast and safe.

Two layers, because a session id can be hit from two threads *or* two processes:

* an in-process ``threading.Lock`` per sid (cheap, covers the common case and
  the F8 acceptance test which uses threads), and
* a cross-process advisory lock via an atomic ``O_EXCL`` lock file (covers two
  `golden_session` CLI processes racing the same sid).

The lock file is intentionally simple (no fcntl/msvcrt) so it behaves identically
on the Linux container and a Windows dev box. Stale locks (crashed holder) are
reclaimed after ``stale_after`` seconds.
"""

from __future__ import annotations

import contextlib
import errno
import os
import threading
import time
from typing import Iterator

from .errors import LockTimeout

# One threading.Lock per sid, shared across all GoldenSession instances in this
# process. Guarded by _registry_lock so the dict itself is thread-safe.
_registry_lock = threading.Lock()
_sid_locks: dict[str, threading.Lock] = {}


def _thread_lock_for(sid: str) -> threading.Lock:
    with _registry_lock:
        lock = _sid_locks.get(sid)
        if lock is None:
            lock = threading.Lock()
            _sid_locks[sid] = lock
        return lock


@contextlib.contextmanager
def session_lock(
    sid: str,
    lock_dir: str,
    *,
    timeout: float = 120.0,
    poll: float = 0.05,
    stale_after: float = 900.0,
) -> Iterator[None]:
    """Serialize all access to ``sid``. Blocks (up to ``timeout``) then raises.

    Acquires the in-process lock first (so threads queue without touching the
    filesystem), then the cross-process lock file.
    """
    os.makedirs(lock_dir, exist_ok=True)
    lock_path = os.path.join(lock_dir, f"{_safe(sid)}.lock")
    deadline = time.monotonic() + timeout

    tlock = _thread_lock_for(sid)
    # Wait for the in-process lock within the same overall deadline.
    if not tlock.acquire(timeout=max(0.0, deadline - time.monotonic())):
        raise LockTimeout(f"timed out waiting for in-process lock on {sid}")
    try:
        _acquire_file_lock(lock_path, deadline, poll, stale_after)
        try:
            yield
        finally:
            _release_file_lock(lock_path)
    finally:
        tlock.release()


def _acquire_file_lock(
    lock_path: str, deadline: float, poll: float, stale_after: float
) -> None:
    while True:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            return
        except FileExistsError:
            if _is_stale(lock_path, stale_after):
                # Crashed holder: reclaim and retry immediately.
                with contextlib.suppress(FileNotFoundError, PermissionError):
                    os.unlink(lock_path)
                continue
            if time.monotonic() >= deadline:
                raise LockTimeout(
                    f"timed out waiting for cross-process lock {lock_path}"
                )
            time.sleep(poll)
        except OSError as exc:  # pragma: no cover - unexpected fs error
            if exc.errno == errno.EEXIST:
                time.sleep(poll)
                continue
            raise


def _release_file_lock(lock_path: str) -> None:
    with contextlib.suppress(FileNotFoundError, PermissionError):
        os.unlink(lock_path)


def _is_stale(lock_path: str, stale_after: float) -> bool:
    try:
        age = time.time() - os.path.getmtime(lock_path)
    except FileNotFoundError:
        return False
    return age > stale_after


def _safe(sid: str) -> str:
    """Make an arbitrary session id safe to use as a filename component."""
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in sid)
