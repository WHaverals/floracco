"""Suite-wide isolation for the Phase-A write lock (docs/multi_user_safety.md §0).

The cross-process flock resolves its lockfile at call time from the data root;
with no env set that is the REAL repo data dir. Point it at each test's tmp dir
so the suite never touches (or waits on) the live lockfile.
"""

from __future__ import annotations

import pytest

from workflows import locks


@pytest.fixture(autouse=True)
def _isolated_write_lock(tmp_path, monkeypatch):
    monkeypatch.setattr(locks, "write_lock_path", lambda: tmp_path / ".write.lock")
