"""Session-only navigation protection for active workflow operations."""

from __future__ import annotations

from collections.abc import MutableMapping
from typing import Any


WORKFLOW_ACTIVITY_KEY = "workflow_activity"
WORKFLOW_NAVIGATION_BLOCKED_KEY = "workflow_navigation_blocked"
ALLOWED_ACTIVITY_NAMES = frozenset({"Processing", "Validating", "Revalidating"})


def begin_workflow_activity(state: MutableMapping[str, Any], name: str) -> None:
    if name not in ALLOWED_ACTIVITY_NAMES:
        raise ValueError(f"Unsupported workflow activity: {name}")
    state[WORKFLOW_ACTIVITY_KEY] = name


def end_workflow_activity(state: MutableMapping[str, Any]) -> None:
    state.pop(WORKFLOW_ACTIVITY_KEY, None)
    state.pop(WORKFLOW_NAVIGATION_BLOCKED_KEY, None)


def request_navigation(state: MutableMapping[str, Any], page: str) -> bool:
    """Navigate when idle; retain the current page while a real operation runs."""

    activity = state.get(WORKFLOW_ACTIVITY_KEY)
    if activity in ALLOWED_ACTIVITY_NAMES:
        state[WORKFLOW_NAVIGATION_BLOCKED_KEY] = {"page": page, "activity": activity}
        return False
    state["navigation"] = page
    return True
