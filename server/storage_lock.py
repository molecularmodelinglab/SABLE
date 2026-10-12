"""Per-cache-path locks for cooperating processes on a shared local filesystem.

The filesystem must support advisory file locks and atomic hard-link creation
(NTFS on Windows, a local Linux filesystem, or equivalent shared-volume semantics).
Lock files live outside run artifacts and must not be unlinked: waiters may still
hold the old inode. The OS releases the lock if its owning process dies.
"""

from __future__ import annotations

from contextlib import contextmanager
import errno
from hashlib import sha256
import os
from pathlib import Path
from threading import Lock
import time
from weakref import WeakValueDictionary


_locks = WeakValueDictionary()
_registry_lock = Lock()


def coordination_path(base: Path) -> Path:
    canonical = base.resolve()
    key = sha256(os.path.normcase(str(canonical)).encode("utf-8")).hexdigest()
    return canonical.parent.parent / ".sable-storage" / key


@contextmanager
def run_storage_lock(base: Path):
    """Serialize threads and processes for one canonical run directory only."""
    state = coordination_path(base)
    state.mkdir(parents=True, exist_ok=True)
    lock_path = state / "lock"
    key = os.path.normcase(str(lock_path))
    with _registry_lock:
        thread_lock = _locks.get(key)
        if thread_lock is None:
            thread_lock = Lock()
            _locks[key] = thread_lock

    with thread_lock, lock_path.open("a+b") as handle:
        if os.name == "nt":
            import msvcrt

            if os.fstat(handle.fileno()).st_size == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            while True:
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError as exc:
                    if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                        raise
                    time.sleep(0.05)
            try:
                yield state
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield state
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
