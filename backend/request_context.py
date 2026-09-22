"""
Per-request viewer identity, set once by main.py's middleware and read by
databricks_client.py (which credential to authenticate with) and cache.py
(to keep one viewer's cached results from being served to another).

Plain module-level ContextVars rather than a parameter threaded through
every fetch_*/build_* function and panel endpoint - those all stay zero-arg
and unchanged; anyio's thread pool (which FastAPI/Starlette use to run sync
endpoints) propagates contextvars automatically, so setting these in
middleware is enough for them to be visible deep in a panel's call stack.

The one place this does NOT propagate automatically is a raw
concurrent.futures.ThreadPoolExecutor (catalog_service.py's _tree_pool,
grants_service.py's _grants_pool) - callers submitting to those must wrap
with contextvars.copy_context().run (see copy_context_run below).

Unset (both None) whenever there's no forwarded viewer token - i.e. local
dev, or any deployment without Databricks Apps user authorization enabled -
and databricks_client.py falls back to the app/service-principal identity
exactly as before.
"""

import contextvars

user_token: contextvars.ContextVar[str | None] = contextvars.ContextVar("user_token", default=None)
user_email: contextvars.ContextVar[str | None] = contextvars.ContextVar("user_email", default=None)


def submit_with_context(pool, fn, *args, **kwargs):
    """pool.submit(fn, *args, **kwargs), but carrying the current viewer's
    context into the worker thread - use this instead of pool.submit(...)
    on any raw ThreadPoolExecutor that fetch_* functions reading user_token/
    user_email get submitted to."""
    ctx = contextvars.copy_context()
    return pool.submit(ctx.run, fn, *args, **kwargs)
