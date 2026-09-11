"""In-memory cache of generated RCAs, keyed by run_id (as a string).

Root cause analysis is generated lazily — only when a user opens a
specific failed run's Job details drawer — since it costs a model
serving call per run. This cache means reopening the same run doesn't
re-run the model, and lets the failed-jobs table and stats cards show
which runs already have a completed RCA without regenerating one.
"""

import threading

_lock = threading.Lock()
_store: dict[str, dict] = {}


def get_cached(run_id) -> dict | None:
    with _lock:
        return _store.get(str(run_id))


def store(run_id, entry: dict) -> None:
    with _lock:
        _store[str(run_id)] = entry


def count() -> int:
    with _lock:
        return len(_store)
