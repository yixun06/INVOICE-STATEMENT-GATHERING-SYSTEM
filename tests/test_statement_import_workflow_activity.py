from pathlib import Path

from src.invoice_app.services.workflow_navigation import (
    begin_workflow_activity,
    end_workflow_activity,
)


def test_statement_check_uses_a_supported_workflow_activity() -> None:
    """The Statement UI must not register an activity rejected by navigation."""

    source = Path("src/invoice_app/ui/data_import.py").read_text(encoding="utf-8")

    assert 'begin_workflow_activity(st.session_state, "Validating")' in source
    assert "Checking statement" not in source

    state: dict[str, object] = {}
    begin_workflow_activity(state, "Validating")
    assert state["workflow_activity"] == "Validating"
    end_workflow_activity(state)
    assert "workflow_activity" not in state
