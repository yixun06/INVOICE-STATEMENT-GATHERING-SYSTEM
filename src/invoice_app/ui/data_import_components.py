"""Small presentation primitives for the Data Import workflow.

These helpers render information that has already been decided by the existing
workflow contracts.  They do not calculate readiness, validation, recovery, or
reconciliation outcomes.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import streamlit as st


def render_workflow_stepper(steps: Sequence[str], current_step: int) -> None:
    """Render one compact, accessible workflow-progress layer."""

    with st.container(horizontal=True, gap="small"):
        for index, label in enumerate(steps, start=1):
            if index < current_step:
                state = "Completed"
                icon = ":material/check_circle:"
                color = "green"
            elif index == current_step:
                state = "Current"
                icon = ":material/play_circle:"
                color = "blue"
            else:
                state = "Pending"
                icon = ":material/schedule:"
                color = "gray"
            st.badge(
                f"{index}. {label} · {state}",
                icon=icon,
                color=color,
            )


def render_authoritative_status(
    *,
    title: str,
    message: str,
    state: str,
) -> None:
    """Render the workflow's supplied status as the primary page message."""

    content = f"**{title}**\n\n{message}"
    if state == "ready":
        st.success(content, icon=":material/check_circle:")
    elif state == "blocked":
        st.error(content, icon=":material/error:")
    else:
        st.info(content, icon=":material/info:")


def render_summary_items(items: Sequence[Any]) -> None:
    """Render a compact responsive summary from existing contract items."""

    if not items:
        return
    with st.container(horizontal=True, gap="small"):
        for item in items:
            st.metric(item.label, item.value, border=True)
