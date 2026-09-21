from __future__ import annotations

from types import SimpleNamespace

from streamlit.testing.v1 import AppTest

from src.invoice_app.services.exception_presentation import (
    build_exception_work_queue,
    missing_invoice_order_ids,
)
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
    source_specific_details: dict | None = None,
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
        source_specific_details=source_specific_details or {},
    )


def test_same_order_issues_become_one_entry_and_preserve_every_message():
    messages = (
        "ORDER-1: no persisted Invoice order coverage is available.",
        "ORDER-1: product identity is unresolved.",
        "ORDER-1: merchandise Product Price is not reconciled.",
        "ORDER-1: final seller settlement is unexplained.",
    )
    issues = tuple(_issue(message) for message in messages)

    queue = build_exception_work_queue(
        _result(issues, known_orders=("ORDER-1",))
    )

    assert len(queue.items) == 1
    assert queue.items[0].order_id == "ORDER-1"
    assert queue.items[0].issue_count == 4
    assert tuple(issue.reason for issue in queue.items[0].issues) == messages
    assert queue.items[0].summary.endswith("+3 more")
    assert queue.source_issue_count == queue.represented_issue_count == 4
    assert missing_invoice_order_ids(queue) == ("ORDER-1",)


def test_multiple_missing_invoice_orders_are_each_presented_once():
    issues = (
        _issue("ORDER-1: no persisted Invoice order coverage is available."),
        _issue("ORDER-1: product identity is unresolved."),
        _issue("ORDER-2: no persisted Invoice order coverage is available."),
        _issue("ORDER-2: final seller settlement is unexplained."),
    )

    queue = build_exception_work_queue(
        _result(issues, known_orders=("ORDER-1", "ORDER-2"))
    )

    assert missing_invoice_order_ids(queue) == ("ORDER-1", "ORDER-2")
    assert len(queue.items) == 2
    assert queue.source_issue_count == queue.represented_issue_count == 4


def test_different_orders_remain_separate_and_source_conflict_is_not_hidden():
    issues = (
        _issue("ORDER-1: product identity is unresolved."),
        _issue(
            "ORDER-2: SOURCE_CONFLICT remains blocking.",
            order_id="ORDER-2",
            layer="source_conflict",
        ),
    )

    queue = build_exception_work_queue(
        _result(issues, known_orders=("ORDER-1", "ORDER-2"))
    )

    assert tuple(item.order_id for item in queue.items) == (
        "ORDER-1",
        "ORDER-2",
    )
    assert queue.items[1].issues[0].reason == issues[1].reason


def test_statement_issue_without_order_id_stays_at_statement_level():
    issue = _issue(
        "Statement and Invoice product populations are not fully accounted for."
    )

    queue = build_exception_work_queue(
        _result((issue,), known_orders=("ORDER-1",))
    )

    assert len(queue.items) == 1
    assert queue.items[0].scope == "Statement"
    assert queue.items[0].order_id is None
    assert queue.items[0].issues[0].reason == issue.reason


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
        self.expanders: list[str] = []

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

    def expander(self, label, **_kwargs):
        self.expanders.append(label)
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


def test_needs_attention_renderer_is_one_compact_queue(monkeypatch):
    issues = (
        _issue("ORDER-1: product identity is unresolved."),
        _issue("ORDER-1: merchandise Product Price is not reconciled."),
    )
    queue = build_exception_work_queue(
        _result(issues, known_orders=("ORDER-1",))
    )
    fake = _FakeStreamlit()
    monkeypatch.setattr(data_import, "st", fake)

    data_import._render_needs_attention_queue(
        queue,
        key_prefix="statement_exception_queue",
        humanize_statement=True,
    )

    assert fake.errors == []
    assert fake.buttons == ["Remove staged source"]
    affected_rows = next(frame for frame in fake.frames if "Order ID" in frame[0])
    assert len(affected_rows) == 1
    assert "Issues" not in affected_rows[0]
    assert "Available action" not in affected_rows[0]
    assert affected_rows[0]["Problem"] == (
        "This Statement item needs review before the batch can continue."
    )
    assert affected_rows[0]["What differs"] == (
        "The current result does not provide a structured source comparison for "
        "this item. Open the technical details to inspect the original evidence."
    )
    assert affected_rows[0]["Next step"] == (
        "Inspect the original evidence and use only the safe recovery action shown there."
    )
    assert affected_rows[0]["Resolve in"] == "Statement review"
    assert affected_rows[0]["Action"] == "View evidence"
    assert "Batch option — does not fix this order" in fake.expanders


def test_statement_queue_uses_structured_v2_evidence_for_product_identity():
    unresolved = SimpleNamespace(
        identity_scope=SimpleNamespace(value="UNRESOLVED"),
        product_id="PRODUCT-1",
        statement_members=(
            SimpleNamespace(source_row_number=42, sequence_no="SKU-1"),
        ),
        diagnostic="Strong family identity conflicts with Product Name evidence.",
    )
    order_result = SimpleNamespace(
        summary=SimpleNamespace(
            identity_scope=SimpleNamespace(value="UNRESOLVED"),
            merchandise_reconciled=True,
            settlement_basis=SimpleNamespace(value="EXACT"),
            reasons=(),
        ),
        evidence=SimpleNamespace(
            order_id="ORDER-1",
            # Missing refund evidence must not be treated as missing Invoice
            # coverage when the actual blocker is product identity.
            refund=SimpleNamespace(invoice_source_exists=False),
            identities=(unresolved,),
        ),
    )
    result = _result(
        (_issue("ORDER-1: product identity is unresolved."),),
        known_orders=("ORDER-1",),
        source_specific_details={
            "reconciliation_v2": SimpleNamespace(
                orders=(order_result,),
                product_family_snapshot=SimpleNamespace(
                    candidates=(
                        SimpleNamespace(
                            product_id="PRODUCT-1",
                            product_name="Cranberry 100g",
                            seller_sku="CRAN-100",
                            parent_sku="CRANBERRY",
                        ),
                    )
                ),
            ),
            "statement": SimpleNamespace(
                sku_rows=(
                    SimpleNamespace(
                        source_row_number=42,
                        sequence_no="SKU-1",
                        product_id="PRODUCT-1",
                        product_name="Cranberry 100 g",
                    ),
                )
            ),
            "invoice_items": (
                SimpleNamespace(
                    order_id="ORDER-1",
                    item_index=0,
                    product_name="Cranberry 100g",
                    seller_sku="CRAN-100",
                    resolved_seller_sku=None,
                ),
            ),
        },
    )

    queue = build_exception_work_queue(result)

    guidance = queue.items[0].statement_guidance
    assert guidance is not None
    assert guidance.category == "Product identity cannot be proven"
    assert guidance.fact_summary == (
        "Statement product name: Cranberry 100 g; Invoice product name: "
        "Cranberry 100g. Strong family identity conflicts with Product Name evidence."
    )
    assert guidance.resolution_area == "Statement review"
    assert guidance.evidence_rows[0] == {
        "Source": "Statement",
        "Field": "Product name",
        "Value": "Cranberry 100 g",
        "Reference": "SKU row 42",
    }
    assert any(row["Source"] == "Product Master" for row in guidance.evidence_rows)


def test_view_details_action_selects_the_clicked_order(monkeypatch):
    fake = _FakeStreamlit()
    fake.session_state["queue_click"] = {"row": 1}
    monkeypatch.setattr(data_import, "st", fake)

    data_import._select_exception_queue_item(
        ("order:ORDER-1", "order:ORDER-2"),
        "queue_click",
        "queue_selected",
    )

    assert fake.session_state["queue_selected"] == "order:ORDER-2"


def test_ready_statement_keeps_existing_success_path(monkeypatch):
    result = _result(())
    fake = _FakeStreamlit()
    monkeypatch.setattr(data_import, "st", fake)

    data_import._render_contract_validation(result)

    assert fake.errors == []
    assert fake.buttons == []
    assert fake.successes == []


def test_statement_refresh_loads_fresh_product_master_and_replaces_session_review(
    monkeypatch,
):
    fake = _FakeStreamlit()
    old_review = SimpleNamespace(stage="old stage")
    refreshed = SimpleNamespace(stage="fresh stage")
    fake.session_state.update(
        {
            "weekly_statement_review": old_review,
            "weekly_statement_stage": old_review.stage,
            "weekly_statement_review_stale_reason": "Refresh required.",
            "statement_exception_queue_selected": "order:OLD",
        }
    )
    events: list[str] = []
    settings = SimpleNamespace(
        create_repository=lambda: "fresh repository",
        create_statement_writer=lambda: "fresh writer",
    )

    monkeypatch.setattr(data_import, "st", fake)
    monkeypatch.setattr(
        data_import,
        "configured_uat2_data_settings",
        lambda: settings,
    )
    monkeypatch.setattr(
        data_import,
        "clear_product_master_source_cache",
        lambda: events.append("cache cleared"),
    )
    monkeypatch.setattr(
        data_import,
        "load_configured_product_price_master",
        lambda: ("fresh master", "Google Sheets"),
    )

    def refresh(review, **kwargs):
        assert review is old_review
        assert kwargs == {
            "repository": "fresh repository",
            "writer": "fresh writer",
            "product_master": "fresh master",
        }
        events.append("review rebuilt")
        return refreshed

    monkeypatch.setattr(data_import, "refresh_statement_review", refresh)

    assert data_import._refresh_statement_review(old_review) is True
    assert events == ["cache cleared", "review rebuilt"]
    assert fake.session_state["weekly_statement_review"] is refreshed
    assert fake.session_state["weekly_statement_stage"] == "fresh stage"
    assert "weekly_statement_review_stale_reason" not in fake.session_state
    assert "statement_exception_queue_selected" not in fake.session_state


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
    data_import._render_validation_status(result)
    data_import._render_contract_validation(result)


def test_actual_streamlit_many_mismatch_smoke_is_compact_and_discloses_all_issues():
    app = AppTest.from_function(_many_mismatch_statement_app, default_timeout=20)

    app.run()

    assert app.exception == []
    assert len(app.error) == 1
    assert "Statement blocked" in app.error[0].value
    assert "Missing Invoice Orders · 187" in app.error[0].value
    assert "unresolved issue" not in app.error[0].value
    missing_orders = next(
        frame.value
        for frame in app.dataframe
        if "Missing Invoice Order ID" in frame.value.columns
    )
    assert tuple(missing_orders["Missing Invoice Order ID"]) == tuple(
        f"ORDER-{index:03d}" for index in range(1, 188)
    )
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
        if "Problem" in frame.value.columns
    )
    assert len(affected) == 1
    assert affected.iloc[0]["Order ID"] == "—"
    assert affected.iloc[0]["Problem"] == (
        "This Statement item needs review before the batch can continue."
    )
    assert sum(
        item.value.startswith("- ORDER-") for item in app.markdown
    ) == 748

    app.session_state["statement_exception_queue_selected"] = "order:ORDER-001"
    app.run()

    detail_messages = {item.value for item in app.markdown}
    assert {
        "- ORDER-001: no persisted Invoice order coverage is available.",
        "- ORDER-001: product identity is unresolved.",
        "- ORDER-001: merchandise Product Price is not reconciled.",
        "- ORDER-001: final seller settlement is unexplained.",
    } <= detail_messages


def _single_missing_invoice_app() -> None:
    from src.invoice_app.services.import_result_contract import (
        CommitReadiness,
        ImportResult,
        ReconciliationException,
        ReconciliationResult,
        SessionState,
        SourceSummary,
        ValidationIssue,
        ValidationResult,
    )
    from src.invoice_app.ui import data_import

    import streamlit as st

    variant = st.session_state.get("statement_test_variant", "missing_invoice")
    note = ValidationIssue(
        layer="statement_reconciliation_v2",
        severity="warning",
        blocking=False,
        reason=(
            "Statement quantity is not provided by the source; "
            "no quantity-match claim is made."
        ),
        affected_item="statement.xlsx",
    )
    group_note = ValidationIssue(
        layer="statement_reconciliation_v2",
        severity="warning",
        blocking=False,
        reason=(
            "Product-group scope is reconciled from authoritative source evidence; "
            "no item-level allocation is claimed."
        ),
        affected_item="statement.xlsx",
    )
    if variant == "notes_only":
        issues = (group_note, note)
        order_id = "ORDER-NOTE"
    elif variant == "no_notes":
        issues = ()
        order_id = "ORDER-CLEAR"
    elif variant == "blocker_and_notes":
        issues = (
            ValidationIssue(
                layer="statement_reconciliation_v2",
                severity="error",
                blocking=True,
                reason="ORDER-BLOCKED: product identity is unresolved.",
                affected_item="statement.xlsx",
            ),
            note,
        )
        order_id = "ORDER-BLOCKED"
    else:
        issues = (
            ValidationIssue(
                layer="statement_reconciliation_v2",
                severity="error",
                blocking=True,
                reason="ORDER-ONLY: no persisted Invoice order coverage is available.",
                affected_item="statement.xlsx",
            ),
            ValidationIssue(
                layer="statement_reconciliation_v2",
                severity="error",
                blocking=True,
                reason="ORDER-ONLY: product identity is unresolved.",
                affected_item="statement.xlsx",
            ),
            note,
        )
        order_id = "ORDER-ONLY"
    result = ImportResult(
        source_type=data_import.SHOPEE_WEEKLY_STATEMENT,
        batch_status="Not Ready" if any(issue.blocking for issue in issues) else "Ready to Commit",
        source_summary=SourceSummary(title="Statement"),
        validation=ValidationResult(
            blocking_issues=tuple(issue for issue in issues if issue.blocking),
            warnings=tuple(issue for issue in issues if not issue.blocking),
        ),
        reconciliation=ReconciliationResult(
            available=True,
            status="Available",
            exceptions=(
                ReconciliationException(
                    status="Existing exception",
                    affected_item=order_id,
                ),
            ),
        ),
        commit_readiness=CommitReadiness(
            ready=not any(issue.blocking for issue in issues),
            status="Not Ready" if any(issue.blocking for issue in issues) else "Ready to Commit",
        ),
        session_state=SessionState(
            applied_to_current_session=True,
            label="Applied to Current Session",
        ),
    )
    data_import._render_validation_status(result)
    data_import._render_contract_validation(result)
    st.button("Continue to reconcile", disabled=not result.commit_readiness.ready)


def test_single_missing_invoice_is_primary_and_notes_are_secondary():
    app = AppTest.from_function(_single_missing_invoice_app)

    app.run()

    assert app.exception == []
    assert len(app.error) == 1
    blocker = app.error[0].value
    assert "Statement blocked" in blocker
    assert "Missing Invoice Order" in blocker
    assert "ORDER-ONLY" in blocker
    assert "Upload the missing invoice" in blocker
    assert "unresolved issue" not in blocker
    assert "work item" not in blocker
    assert "Missing Invoice details" in {item.label for item in app.expander}
    assert "Reconciliation notes" in {item.label for item in app.expander}
    assert any(
        "Statement quantity is not provided by the source" in item.value
        for item in (*app.caption, *app.markdown)
    )
    assert next(
        button for button in app.button if button.label == "Continue to reconcile"
    ).disabled


def test_ready_statement_notes_stay_secondary_and_do_not_block_continue():
    app = AppTest.from_function(_single_missing_invoice_app)
    app.session_state["statement_test_variant"] = "notes_only"

    app.run()

    assert app.exception == []
    assert any("Ready" in item.value for item in app.success)
    assert not any("Needs Attention" in item.value for item in app.error)
    assert "Needs Attention" not in {item.value for item in app.subheader}
    assert "Reconciliation notes" in {item.label for item in app.expander}
    assert any(
        "Statement quantity is not provided by the source" in item.value
        for item in (*app.caption, *app.markdown)
    )
    assert any("Product-group scope is reconciled" in item.value for item in app.markdown)
    assert not next(
        button for button in app.button if button.label == "Continue to reconcile"
    ).disabled


def test_ready_statement_without_notes_omits_secondary_sections():
    app = AppTest.from_function(_single_missing_invoice_app)
    app.session_state["statement_test_variant"] = "no_notes"

    app.run()

    assert app.exception == []
    assert any("Ready" in item.value for item in app.success)
    assert not any("Needs Attention" in item.value for item in app.error)
    assert "Needs Attention" not in {item.value for item in app.subheader}
    assert app.expander == []
    assert not next(
        button for button in app.button if button.label == "Continue to reconcile"
    ).disabled


def test_blocker_and_notes_render_in_separate_sections():
    app = AppTest.from_function(_single_missing_invoice_app)
    app.session_state["statement_test_variant"] = "blocker_and_notes"

    app.run()

    assert app.exception == []
    assert any("Needs Attention" in item.value for item in app.error)
    assert "Needs Attention" in {item.value for item in app.subheader}
    actionable = next(frame.value for frame in app.dataframe if "Problem" in frame.value.columns)
    assert len(actionable) == 1
    assert actionable.iloc[0]["Order ID"] == "ORDER-BLOCKED"
    assert actionable.iloc[0]["Problem"] == (
        "This Statement item needs review before the batch can continue."
    )
    assert "Reconciliation notes" in {item.label for item in app.expander}
    assert any(
        "Statement quantity is not provided by the source" in item.value
        for item in (*app.caption, *app.markdown)
    )
    assert next(
        button for button in app.button if button.label == "Continue to reconcile"
    ).disabled
