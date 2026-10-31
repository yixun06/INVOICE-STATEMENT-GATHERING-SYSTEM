from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from src.invoice_app.services import statement_performance
from src.invoice_app.services.postgres_statement_repository import _timed_candidate_rows
from src.invoice_app.ui import data_import


def test_check_timer_emits_redacted_success_before_rerun(monkeypatch):
    class State(dict):
        __getattr__ = dict.__getitem__
        __setattr__ = dict.__setitem__

    class Rerun(Exception):
        pass

    state = State()
    source = SimpleNamespace(name="private-source.xlsx")
    review = SimpleNamespace(stage=object())
    logged = []
    fake_st = SimpleNamespace(
        session_state=state,
        subheader=lambda *a, **k: None,
        caption=lambda *a, **k: None,
        file_uploader=lambda *a, **k: source,
        container=lambda *a, **k: nullcontext(),
        button=lambda label, **k: label == "Check statement",
        rerun=lambda: (_ for _ in ()).throw(Rerun()),
    )
    monkeypatch.setattr(data_import, "st", fake_st)
    monkeypatch.setattr(data_import, "statement_stage_review_consistency", lambda state: SimpleNamespace(coherent=True))
    monkeypatch.setattr(data_import, "_weekly_stage", lambda: None)
    monkeypatch.setattr(data_import, "_weekly_review", lambda: state.get("weekly_statement_review"))
    monkeypatch.setattr(data_import, "_required_persistence_market_context", lambda: SimpleNamespace(statement_order_existence_gate=True))
    monkeypatch.setattr(data_import, "configured_uat2_data_settings", lambda *a, **k: SimpleNamespace(create_statement_writer=lambda: object(), create_full_order_current_repository=lambda: object()))
    monkeypatch.setattr(data_import, "review_statement_upload", lambda *a, **k: review)
    monkeypatch.setattr(data_import, "record_statement_workflow_transition", lambda *a, **k: None)
    monkeypatch.setattr(data_import, "_set_step", lambda step: state.update(step=step))
    monkeypatch.setattr(statement_performance._LOG, "info", lambda template, *args: logged.append(template % args))

    with pytest.raises(Rerun):
        data_import._render_weekly_statement_upload()
    assert state["weekly_statement_review"] is review
    assert state["step"] == 3
    line = next(line for line in logged if "stage=check_statement_total" in line)
    assert "success=True" in line
    start = next(line for line in logged if "stage=check_statement_invocation" in line)
    assert start.split("invocation=")[1] == line.split("invocation=")[1]
    assert "private-source.xlsx" not in " ".join(logged)
    assert any("stage=review_session_state_write" in line for line in logged)

    class Rows:
        def __init__(self):
            self.batches = iter([[{"order_id": "PRIVATE-ORDER", "amount": "987654.32"}], []])

        def mappings(self):
            return self

        def fetchmany(self, size):
            return next(self.batches)

    connection = SimpleNamespace(execute=lambda query: Rows())
    output = []
    _timed_candidate_rows(connection, object(), "component", output, lambda row: row, 1)
    assert len(output) == 1
    assert any("stage=candidate_component_model_cells" in line for line in logged)
    assert "PRIVATE-ORDER" not in " ".join(logged)
    assert "987654.32" not in " ".join(logged)
