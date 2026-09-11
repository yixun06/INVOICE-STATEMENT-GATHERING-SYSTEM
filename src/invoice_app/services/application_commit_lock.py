"""Single-instance guard for authoritative UAT2 persistence commits.

This lock is intentionally process-local.  It is valid only while the current
deployment runs one InvoiceGather application process with shared memory.
"""

from __future__ import annotations

from contextlib import contextmanager
from threading import Lock
from typing import Iterator


COMMIT_IN_PROGRESS_MESSAGE = "Another commit is currently in progress. Please try again shortly."


class ApplicationCommitInProgress(RuntimeError):
    """A second authoritative commit was requested while one is active."""

    def __init__(self) -> None:
        super().__init__(COMMIT_IN_PROGRESS_MESSAGE)


class ApplicationCommitLock:
    """Non-blocking shared commit lock for the single running application."""

    def __init__(self) -> None:
        self._lock = Lock()

    @contextmanager
    def acquire(self) -> Iterator[None]:
        if not self._lock.acquire(blocking=False):
            raise ApplicationCommitInProgress()
        try:
            yield
        finally:
            self._lock.release()


APPLICATION_COMMIT_LOCK = ApplicationCommitLock()
