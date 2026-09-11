"""
Tiny short-TTL in-process cache with single-flight de-duplication.

Splitting each page into one file/endpoint per panel (per the current
folder layout) means several panels on the same page independently need
the same expensive Databricks data (SCIM users/groups, Unity Catalog
grants, the full catalog tree, PII columns) - e.g. loading Access
governance now fires one HTTP request per panel, all in parallel, each
redoing the same SCIM + grants fetch. A plain TTL cache doesn't help here
by itself: since the frontend fires all of a page's panel requests at
once (not staggered), every one of them misses the cache before the
first fetch has even finished.

So this also de-duplicates concurrent calls: if a call for the same
underlying fetch is already in flight when another one comes in, the
second call waits for the first's result instead of starting its own
Databricks round-trip. Combined with the TTL, one page load now makes
one real Databricks call per shared fetcher, not one per panel.

This does not change the one-file-per-panel structure or response
shapes - it only makes the shared fetch functions those panels call
cheaper to call repeatedly and concurrently.
"""

import threading
import time
from functools import wraps

_lock = threading.Lock()
_store: dict[tuple, tuple[float, object]] = {}  # key -> (cached_at, result)
_inflight: dict[tuple, "_Session"] = {}          # key -> in-progress call


class _Session:
    __slots__ = ("event", "result", "error")

    def __init__(self):
        self.event = threading.Event()
        self.result = None
        self.error = None


def ttl_cache(seconds: float):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            key = (fn.__module__, fn.__qualname__, args, tuple(sorted(kwargs.items())))
            now = time.monotonic()

            with _lock:
                hit = _store.get(key)
                if hit is not None and now - hit[0] < seconds:
                    return hit[1]

                session = _inflight.get(key)
                is_leader = session is None
                if is_leader:
                    session = _Session()
                    _inflight[key] = session

            if not is_leader:
                session.event.wait()
                if session.error is not None:
                    raise session.error
                return session.result

            try:
                result = fn(*args, **kwargs)
                session.result = result
                with _lock:
                    _store[key] = (time.monotonic(), result)
                return result
            except BaseException as exc:
                session.error = exc
                raise
            finally:
                with _lock:
                    _inflight.pop(key, None)
                session.event.set()
        return wrapper
    return decorator
