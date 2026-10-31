"""Payload-free stage timings; counters are one event per measured call."""
from contextlib import contextmanager
from functools import wraps
import logging
from time import perf_counter

_LOG = logging.getLogger(__name__)
# Dedicated stderr handler makes INFO timings visible to systemd without changing
# application/root logging policy. One handler even across module reloads.
if not _LOG.handlers:
    _LOG.addHandler(logging.StreamHandler())
_LOG.setLevel(logging.INFO)
_LOG.propagate = False

def log_elapsed(stage, elapsed, **counts):
    _LOG.info("StatementPerf stage=%s elapsed_ms=%.3f count=1 success=True %s",
              stage, elapsed * 1000,
              " ".join(f"{key}={value}" for key, value in counts.items()))

@contextmanager
def measure(stage, **counts):
    start = perf_counter()
    ok = False
    try:
        yield counts
        ok = True
    finally:
        ok = ok and counts.pop("_success", True)
        _LOG.info("StatementPerf stage=%s elapsed_ms=%.3f count=1 success=%s %s",
                  stage, (perf_counter() - start) * 1000, ok,
                  " ".join(f"{key}={value}" for key, value in counts.items()))

def measured(stage):
    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            with measure(stage) as counts:
                result = function(*args, **kwargs)
                if stage == "source_parse":
                    counts["income_rows"] = len(result.income_rows)
                    counts["detail_rows"] = len(result.service_fee_details) + len(result.shipping_fee_discrepancies)
                elif stage == "statement_state_load":
                    counts["statement_rows_loaded"] = len(result.committed_statement_rows)
                    counts["component_rows_loaded"] = len(result.committed_financial_component_rows)
                elif stage == "bundle_build":
                    counts["bundles"] = len(result)
                elif stage == "candidate_plan_prepare":
                    counts["statement_rows"] = len(result.rows)
                    counts["financial_components"] = len(result.financial_component_rows)
                return result
        return wrapped
    return decorate
