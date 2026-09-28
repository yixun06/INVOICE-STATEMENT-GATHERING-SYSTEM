"""Small, fail-closed market identities shared at dependency boundaries.

This is deliberately not a plugin framework.  It records only the stable
market facts needed to prevent a future Shopee SG workflow from borrowing
Shopee MY persistence, Product Master, or reporting dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, MutableMapping


class MarketKey(str, Enum):
    SHOPEE_MY = "shopee_my"
    SHOPEE_SG = "shopee_sg"


class MarketConfigurationUnavailable(RuntimeError):
    """A requested market has no approved/configured resource boundary."""


class MarketStateIsolationError(RuntimeError):
    """An active batch cannot be reinterpreted as another market."""


@dataclass(frozen=True)
class MarketContext:
    key: MarketKey
    display_name: str
    persisted_platform: str
    currency_code: str
    currency_display: str
    cross_platform_eligible: bool
    statement_commit_available: bool
    weekly_billing_available: bool

    @property
    def state_namespace(self) -> str:
        return self.key.value


SHOPEE_MY = MarketContext(
    key=MarketKey.SHOPEE_MY,
    display_name="Shopee MY",
    persisted_platform="Shopee",
    currency_code="MYR",
    currency_display="RM",
    cross_platform_eligible=True,
    statement_commit_available=True,
    weekly_billing_available=True,
)

SHOPEE_SG = MarketContext(
    key=MarketKey.SHOPEE_SG,
    display_name="Shopee SG",
    persisted_platform="Shopee SG",
    currency_code="SGD",
    currency_display="SGD",
    cross_platform_eligible=False,
    statement_commit_available=False,
    weekly_billing_available=False,
)


MARKET_CONTEXTS: Mapping[MarketKey, MarketContext] = {
    SHOPEE_MY.key: SHOPEE_MY,
    SHOPEE_SG.key: SHOPEE_SG,
}


def resolve_market_context(value: MarketContext | MarketKey | str | None = None) -> MarketContext:
    """Resolve the current MY default or one explicit immutable market."""

    if value is None:
        return SHOPEE_MY
    if isinstance(value, MarketContext):
        known = MARKET_CONTEXTS.get(value.key)
        if known != value:
            raise MarketConfigurationUnavailable(
                f"Unrecognized market context for {value.key.value!r}."
            )
        return known
    try:
        key = value if isinstance(value, MarketKey) else MarketKey(str(value))
    except ValueError as error:
        raise MarketConfigurationUnavailable(f"Unsupported market context: {value!r}.") from error
    return MARKET_CONTEXTS[key]


def require_capability(context: MarketContext, capability: str) -> None:
    """Fail before a disabled market can borrow an MY-only runtime path."""

    enabled = {
        "statement_commit": context.statement_commit_available,
        "weekly_billing": context.weekly_billing_available,
    }.get(capability)
    if enabled is None:
        raise ValueError(f"Unknown market capability: {capability}.")
    if not enabled:
        raise MarketConfigurationUnavailable(
            f"{context.display_name} {capability.replace('_', ' ')} is unavailable "
            "until approved source and configuration are supplied."
        )


def market_state_key(context: MarketContext | MarketKey | str, domain: str, name: str) -> str:
    """Return a deterministic market-scoped Streamlit/session-state key."""

    resolved = resolve_market_context(context)
    if not domain or not name or "." in domain or "." in name:
        raise ValueError("State domain and name must be non-empty single key segments.")
    return f"{domain}.{resolved.state_namespace}.{name}"


ACTIVE_IMPORT_MARKET_KEY = "data_import.active_market"


def bind_active_import_market(
    state: MutableMapping[str, Any], context: MarketContext | MarketKey | str
) -> MarketContext:
    """Bind an active batch to one market; changing it requires batch disposal first."""

    resolved = resolve_market_context(context)
    current = state.get(ACTIVE_IMPORT_MARKET_KEY)
    if current not in (None, "", resolved.key.value):
        raise MarketStateIsolationError(
            "The active import batch belongs to another market; finish or discard it before switching."
        )
    state[ACTIVE_IMPORT_MARKET_KEY] = resolved.key.value
    return resolved


def active_import_market(state: Mapping[str, Any]) -> MarketContext | None:
    value = state.get(ACTIVE_IMPORT_MARKET_KEY)
    if value in (None, ""):
        return None
    return resolve_market_context(str(value))
