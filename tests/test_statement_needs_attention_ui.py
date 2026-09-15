from __future__ import annotations

from streamlit.testing.v1 import AppTest

from src.invoice_app.services.import_result_contract import (
    CommitReadiness,
    ImportResult,
    ReconciliationException,
    ReconciliationResult,
    RecoveryAction,
    SessionState,
    SourceSummary,
    ValidationIssue,
    ValidationResult,
)
from src.invoice_app.services.validation_recovery import REMOVE_STAGED_SOURCE
from src.invoice_app.ui import data_import


def _remove_action() -> RecoveryAction:
    return RecoveryAction(
        action_id="remove-statement",
        action_type=REMOVE_STAGED_SOURCE,
        label="Remove staged source",
        affected_item="statement.xlsx",
        allowed=True,
        destructive=True,
        requires_revalidation=True,
    )


def _issue(
    reason: str,
    *,
    order_id: str | None = None,
    blocking: bool = True,
    layer: str = "statement_reconciliation_v2",
) -> ValidationIssue:
    return ValidationIssue(
        layer=layer,
        severity="error" if blocking else "warning",
        blocking=blocking,
        reason=reason,
        affected_item="statement.xlsx",
        evidence={} if order_id is None else {"order_id": order_id},
        suggested_action="Review the existing evidence.",
        recovery_actions=(_remove_action(),) if blocking else (),
    )


def _result(
    issues: tuple[ValidationIssue, ...],
    *,
    known_orders: tuple[str, ...] = (),
) -> ImportResult:
    return ImportResult(
        source_type=data_import.SHOPEE_WEEKLY_STATEMENT,
        batch_status="Not Ready" if issues else "Ready to Commit",
        source_summary=SourceSummary(title="Statement"),
        validation=ValidationResult(
            blocking_issues=tuple(issue for issue in issues if issue.blocking),
            warnings=tuple(issue for issue in issues if not issue.blocking),
        ),
        reconciliation=ReconciliationResult(
            available=True,
            status="Available",
            exceptions=tuple(
                ReconciliationException(status="Existing exception", affected_item=order_id)
                for order_id in known_orders
            ),
        ),
        commit_readiness=CommitReadiness(
            ready=not any(issue.blocking for issue in issues),
            status="Not Ready" if issues else "Ready to Commit",
        ),
        session_state=SessionState(
            applied_to_current_session=True,
            label="Applied to Current Session",
        ),
    )


def test_same_order_issues_become_one_entry_and_preserve_every_message():
    messages = (
        "ORDER-1: no persisted Invoice order coverage is available.",
        "ORDER-1: product identity is unresolved.",
        "ORDER-1: merchandise Product Price is not reconciled.",
        "ORDER-1: final seller settlement is unexplained.",
    )
    issues = tuple(_issue(message) for message in messages)

    presentation = data_import._statement_issue_presentation(
        _result(issues, known_orders=("ORDER-1",)),
        issues,
    )

    assert len(presentation.order_groups) == 1
    assert presentation.order_groups[0].order_id == "ORDER-1"
    assert len(presentation.order_groups[0].issues) == 4
    assert tuple(issue.reason for issue in presentation.order_groups[0].issues) == messages
    assert data_import._statement_issue_summary(presentation.order_groups[0]).endswith(
        "+3 more"
    )


def test_different_orders_remain_separate_and_source_conflict_is_not_hidden():
    issues = (
        _issue("ORDER-1: product identity is unresolved."),
        _issue(
            "ORDER-2: SOURCE_CONFLICT remains blocking.",
            order_id="ORDER-2",
            layer="source_conflict",
        ),
    )

    presentation = data_import._statement_issue_presentation(
        _result(issues, known_orders=("ORDER-1", "ORDER-2")),
        issues,
    )

    assert tuple(group.order_id for group in presentation.order_groups) == (
        "ORDER-1",
        "ORDER-2",
    )
    assert presentation.order_groups[1].issues[0] is issues[1]


def test_statement_issue_without_order_id_stays_at_statement_level():
    issue = _issue(
        "Statement and Invoice product populations are not fully accounted for."
    )

    presentation = data_import._statement_issue_presentation(
        _result((issue,), known_orders=("ORDER-1",)),
        (issue,),
    )

    assert presentation.order_groups == ()
    assert presentation.statement_issues == (issue,)


class _Context:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class _ColumnConfig:
    @staticmethod
    def TextColumn(*_args, **_kwargs):
        return object()

    @staticmethod
    def NumberColumn(*_args, **_kwargs):
        return object()

    @staticmethod
    def ButtonColumn(*_args, **_kwargs):
        return object()


class _FakeStreamlit:
    def __init__(self):
        self.session_state = {}
        self.column_config = _ColumnConfig()
        self.buttons: list[str] = []
        self.errors: list[str] = []
        self.successes: list[str] = []
        self.frames: list[list[dict[str, object]]] = []

    def error(self, message, **_kwargs):
        self.errors.append(message)

    def success(self, message, **_kwargs):
        self.successes.append(message)

    def info(self, *_args, **_kwargs):
        return None

    def warning(self, *_args, **_kwargs):
        return None

    def button(self, label, **_kwargs):
        self.buttons.append(label)
        return False

    def subheader(self, *_args, **_kwargs):
        return None

    def dataframe(self, rows, **_kwargs):
        self.frames.append(rows)
        return None

    def expander(self, *_args, **_kwargs):
        return _Context()

    def container(self, **_kwargs):
        return _Context()

    def caption(self, *_args, **_kwargs):
        return None

    def write(self, *_args, **_kwargs):
        return None

    def markdown(self, *_args, **_kwargs):
        return None

    def rerun(self):
        raise AssertionError("No action was clicked in this test.")


def test_needs_attention_renderer_keeps_actions_in_the_workflow_footer(monkeypatch):
    issues = (
        _issue("ORDER-1: product identity is unresolved."),
        _issue("ORDER-1: merchandise Product Price is not reconciled."),
    )
    result = _result(issues, known_orders=("ORDER-1",))
    fake = _FakeStreamlit()
    monkeypatch.setattr(data_import, "st", fake)

    data_import._render_weekly_statement_needs_attention(result, issues)

    assert len(fake.errors) == 1
    assert "1 order" in fake.errors[0]
    assert fake.buttons == []
    affected_rows = next(frame for frame in fake.frames if "Order ID" in frame[0])
    assert len(affected_rows) == 1
    assert affected_rows[0]["Issue Count"] == 2


def test_view_details_action_selects_the_clicked_order(monkeypatch):
    fake = _FakeStreamlit()
    fake.session_state["weekly_statement_issue_order_click"] = {"row": 1}
    monkeypatch.setattr(data_import, "st", fake)

    data_import._select_statement_issue_order(("ORDER-1", "ORDER-2"))

    assert fake.session_state["weekly_statement_issue_order_id"] == "ORDER-2"


def test_ready_statement_keeps_existing_success_path(monkeypatch):
    result = _result(())
    fake = _FakeStreamlit()
    monkeypatch.setattr(data_import, "st", fake)

    data_import._render_contract_validation(result)

    assert fake.errors == []
    assert fake.buttons == []
    assert fake.successes == []


def _many_mismatch_statement_app() -> None:
    from src.invoice_app.services.import_result_contract import (
        CommitReadiness,
        ImportResult,
        ReconciliationException,
        ReconciliationResult,
        RecoveryAction,
        SessionState,
        SourceSummary,
        SummaryItem,
        ValidationIssue,
        ValidationResult,
    )
    from src.invoice_app.services.validation_recovery import REMOVE_STAGED_SOURCE
    from src.invoice_app.ui import data_import

    order_ids = tuple(f"ORDER-{index:03d}" for index in range(1, 188))
    removal = RecoveryAction(
        action_id="remove-statement",
        action_type=REMOVE_STAGED_SOURCE,
        label="Remove staged source",
        affected_item="statement.xlsx",
        allowed=True,
        destructive=True,
        requires_revalidation=True,
    )
    issues = tuple(
        ValidationIssue(
            layer="statement_reconciliation_v2",
            severity="error",
            blocking=True,
            reason=f"{order_id}: {message}",
            affected_item="statement.xlsx",
            recovery_actions=(removal,),
        )
        for order_id in order_ids
        for message in (
            "no persisted Invoice order coverage is available.",
            "product identity is unresolved.",
            "merchandise Product Price is not reconciled.",
            "final seller settlement is unexplained.",
        )
    ) + (
        ValidationIssue(
            layer="statement_reconciliation_v2",
            severity="error",
            blocking=True,
            reason=(
                "Statement and Invoice product populations are not fully "
                "accounted for."
            ),
            affected_item="statement.xlsx",
            recovery_actions=(removal,),
        ),
    )
    result = ImportResult(
        source_type=data_import.SHOPEE_WEEKLY_STATEMENT,
        batch_status="Not Ready",
        source_summary=SourceSummary(
            title="Statement",
            items=(
                SummaryItem("Statement Period", "31/08/2026 - 06/09/2026"),
                SummaryItem("Review status", "Not Ready"),
                SummaryItem("Order Rows", 187),
                SummaryItem("SKU Rows", 280),
                SummaryItem("Total Released", "RM 10,000.00"),
                SummaryItem("Adjustment Total", "RM 100.00"),
            ),
        ),
        validation=ValidationResult(blocking_issues=issues),
        reconciliation=ReconciliationResult(
            available=True,
            status="Available",
            exceptions=tuple(
                ReconciliationException(
                    status="Existing exception",
                    affected_item=order_id,
                )
                for order_id in order_ids
            ),
        ),
        commit_readiness=CommitReadiness(ready=False, status="Not Ready"),
        session_state=SessionState(
            applied_to_current_session=True,
            label="Applied to Current Session",
        ),
    )
    data_import._render_source_summary(result)
    data_import._render_weekly_statement_needs_attention(
        result,
        issues,
    )


def test_actual_streamlit_many_mismatch_smoke_is_compact_and_discloses_all_issues():
    app = AppTest.from_function(_many_mismatch_statement_app, default_timeout=20)

    app.run()

    assert app.exception == []
    assert len(app.error) == 1
    assert "187 orders" in app.error[0].value
    assert {metric.label for metric in app.metric} == {
        "Statement Period",
        "Review status",
        "Order Rows",
        "SKU Rows",
        "Total Released",
        "Adjustment Total",
    }
    assert app.button == []
    affected = next(
        frame.value
        for frame in app.dataframe
        if "Order ID" in frame.value.columns
    )
    assert len(affected) == 187
    assert affected["Order ID"].is_unique
    assert set(affected["Issue Count"]) == {4}

    app.session_state["weekly_statement_issue_order_id"] = "ORDER-001"
    app.run()

    detail_messages = {item.value for item in app.markdown}
    assert {
        "- ORDER-001: no persisted Invoice order coverage is available.",
        "- ORDER-001: product identity is unresolved.",
        "- ORDER-001: merchandise Product Price is not reconciled.",
        "- ORDER-001: final seller settlement is unexplained.",
    } <= detail_messages
