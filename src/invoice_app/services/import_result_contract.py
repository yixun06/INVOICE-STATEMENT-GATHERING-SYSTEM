"""Typed presentation contract for Data Import results.

This module intentionally describes only the UI-facing result shape.  Existing
parsers and services keep ownership of their source-specific return objects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True)
class SummaryItem:
    label: str
    value: Any


@dataclass(frozen=True)
class SourceSummary:
    title: str
    items: tuple[SummaryItem, ...] = ()
    empty_message: str | None = None


@dataclass(frozen=True)
class RecoveryAction:
    action_id: str
    action_type: str
    label: str
    affected_item: str | None
    allowed: bool
    destructive: bool
    requires_revalidation: bool


@dataclass(frozen=True)
class ValidationIssue:
    """A UI-facing validation issue.

    ``severity`` describes how the issue should be presented.  ``blocking``
    is deliberately independent so future workflows can express a blocking
    warning, or a non-blocking error, without changing this contract.
    """

    layer: str
    severity: str
    blocking: bool
    reason: str
    affected_item: str | None = None
    evidence: Mapping[str, Any] = field(default_factory=dict)
    suggested_action: str | None = None
    recovery_action: str | None = None
    recovery_actions: tuple[RecoveryAction, ...] = ()
    status: str = "Open"


@dataclass(frozen=True)
class ValidationResult:
    blocking_issues: tuple[ValidationIssue, ...] = ()
    warnings: tuple[ValidationIssue, ...] = ()

    @property
    def has_blocking_issues(self) -> bool:
        return bool(self.blocking_issues)


@dataclass(frozen=True)
class ReconciliationException:
    status: str
    affected_item: str | None = None
    evidence: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ReconciliationResult:
    available: bool
    status: str
    summary: tuple[SummaryItem, ...] = ()
    exceptions: tuple[ReconciliationException, ...] = ()
    source_specific_details: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CommitReadiness:
    ready: bool
    status: str
    reasons: tuple[str, ...] = ()
    database_commit_available: bool = False


@dataclass(frozen=True)
class SessionState:
    applied_to_current_session: bool
    label: str
    batch_id: str | None = None
    database_state: str = "Not committed (Future Database Phase)"


@dataclass(frozen=True)
class ImportResult:
    source_type: str
    batch_status: str
    source_summary: SourceSummary
    validation: ValidationResult
    reconciliation: ReconciliationResult
    commit_readiness: CommitReadiness
    session_state: SessionState
    source_specific_details: Mapping[str, Any] = field(default_factory=dict)
