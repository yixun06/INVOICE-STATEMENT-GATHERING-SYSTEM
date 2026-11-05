"""Opt-in Analysis diagnostics. Durations are inclusive unless stage says otherwise.

Events go to stderr (systemd journal when run as a service). Never pass business data.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import json
import os
import sys
from time import perf_counter_ns
from uuid import uuid4

_context = ContextVar("analysis_perf_context", default=None)

def enabled():
    return os.environ.get("INVOICEGATHER_PERF_LOG") == "1"

@contextmanager
def run(market):
    if not enabled():
        yield
        return
    state = {"run_id": uuid4().hex, "market": market, "trigger": "UNKNOWN", "cache_state": "UNKNOWN"}
    token = _context.set(state)
    try:
        with stage("backend_total"):
            yield
    finally:
        _context.reset(token)

def update(**values):
    state = _context.get()
    if state is not None:
        state.update(values)

@contextmanager
def stage(name, **counts):
    state = _context.get()
    if state is None:
        yield counts
        return
    start = perf_counter_ns()
    try:
        yield counts
    finally:
        event = {"event": "analysis_perf", "run_id": state["run_id"], "market": state["market"], "stage": name, "elapsed_ms": (perf_counter_ns()-start)/1000000, "trigger": state["trigger"], "cache_state": state["cache_state"]}
        event.update({k:v for k,v in counts.items() if k in ("query_count", "row_count") and isinstance(v,int)})
        try:
            print(json.dumps(event, separators=(",", ":")), file=sys.stderr, flush=True)
        except (OSError, ValueError):
            pass

def timed(name):
    def decorate(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            with stage(name):
                return fn(*args, **kwargs)
        return wrapped
    return decorate

def read_rows(connection, query, convert, name):
    """Execute once; driver buffering can make execute/fetch durations overlap."""
    with stage(name + "_execute", query_count=1):
        rows = connection.execute(query).mappings()
    with stage(name + "_fetch_convert") as counts:
        result = tuple(convert(row) for row in rows)
        counts["row_count"] = len(result)
    return result
