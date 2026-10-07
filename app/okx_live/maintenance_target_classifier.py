"""Structural Live maintenance target classification; never dispatch authority.

The current reconcile DTO does not prove page-chain completeness, source freshness,
credential-session binding, or an account revision. Even a REVIEW_ONLY result is
only input for a future trusted maintenance permit issuer, never a POST permit.
The parser rejects absent required numeric and boolean fields; retained raw is
also checked here to detect later DTO/source disagreement.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Literal

from app.domain.okx_live import (
    OkxLiveOrderView,
    OkxLivePositionView,
    OkxLiveReconcileResult,
)

TargetKind = Literal[
    "ordinary_unfilled",
    "exact_position",
    "protective",
    "partially_filled",
    "unknown",
]
Disposition = Literal["REVIEW_ONLY", "DENY"]


@dataclass(frozen=True, slots=True)
class LiveMaintenanceTargetAssessment:
    action: Literal["cancel_order", "close_position"]
    kind: TargetKind
    disposition: Disposition
    reason: str
    account_uid: str | None = None
    main_uid: str | None = None
    instrument_id: str | None = None
    target_id: str | None = None
    position_side: str | None = None
    margin_mode: str | None = None
    size: Decimal | None = None

    @property
    def authorizes_post(self) -> Literal[False]:
        return False


def _decimal(value: object) -> Decimal | None:
    if type(value) is not str or not value:
        return None
    try:
        result = Decimal(value)
    except InvalidOperation:
        return None
    return result if result.is_finite() else None


def _bool(value: object) -> bool | None:
    if type(value) is bool:
        return value
    if type(value) is str and value.lower() in {"true", "false"}:
        return value.lower() == "true"
    return None


def _identity_matches(
    snapshot: OkxLiveReconcileResult, *, pinned_uid: str, pinned_main_uid: str
) -> bool:
    config = snapshot.account_config
    return (
        type(pinned_uid) is str
        and bool(pinned_uid.strip())
        and type(pinned_main_uid) is str
        and bool(pinned_main_uid.strip())
        and config.uid == pinned_uid
        and config.main_uid == pinned_main_uid
        and config.is_sub_account is (pinned_uid != pinned_main_uid)
        and config.position_mode in {"net_mode", "long_short_mode"}
    )


def _assessment(
    action: Literal["cancel_order", "close_position"],
    kind: TargetKind,
    reason: str,
    *,
    snapshot: OkxLiveReconcileResult,
    instrument_id: str,
    target_id: str | None = None,
    position_side: str | None = None,
    margin_mode: str | None = None,
    size: Decimal | None = None,
) -> LiveMaintenanceTargetAssessment:
    return LiveMaintenanceTargetAssessment(
        action=action,
        kind=kind,
        disposition=(
            "REVIEW_ONLY" if kind in {"ordinary_unfilled", "exact_position"} else "DENY"
        ),
        reason=reason,
        account_uid=snapshot.account_config.uid,
        main_uid=snapshot.account_config.main_uid,
        instrument_id=instrument_id,
        target_id=target_id,
        position_side=position_side,
        margin_mode=margin_mode,
        size=size,
    )


def _order_raw_matches(order: OkxLiveOrderView) -> bool:
    raw = order.raw
    size = _decimal(raw.get("sz"))
    filled = _decimal(raw.get("accFillSz"))
    attached = raw.get("attachAlgoOrds")
    return (
        raw.get("ordId") == order.order_id
        and raw.get("clOrdId") == order.client_order_id
        and raw.get("instId") == order.instrument_id
        and raw.get("side") == order.side
        and raw.get("posSide") == order.position_side
        and raw.get("ordType") == order.order_type
        and raw.get("state") == order.state
        and size == order.size
        and filled == order.accumulated_fill_size
        and _bool(raw.get("reduceOnly")) is order.reduce_only
        and type(attached) is list
        and attached == order.attached_algo_orders
    )


def _position_raw_matches(position: OkxLivePositionView) -> bool:
    raw = position.raw
    return (
        raw.get("posId") == position.position_id
        and raw.get("instId") == position.instrument_id
        and raw.get("posSide") == position.position_side
        and raw.get("mgnMode") == position.margin_mode
        and _decimal(raw.get("pos")) == position.size
        and _decimal(raw.get("availPos")) == position.available_size
    )


def classify_live_cancel_target(
    snapshot: OkxLiveReconcileResult,
    *,
    pinned_uid: str,
    pinned_main_uid: str,
    instrument_id: str,
    order_id: str | None = None,
    client_order_id: str | None = None,
) -> LiveMaintenanceTargetAssessment:
    """Classify a pending ordinary order without authorizing cancellation."""
    action = "cancel_order"

    def result(
        kind: TargetKind, reason: str, order: OkxLiveOrderView | None = None
    ) -> LiveMaintenanceTargetAssessment:
        return _assessment(
            action,
            kind,
            reason,
            snapshot=snapshot,
            instrument_id=instrument_id,
            target_id=order.order_id
            if order is not None
            else order_id or client_order_id,
            position_side=order.position_side if order is not None else None,
            size=order.size if order is not None else None,
        )

    if not _identity_matches(
        snapshot, pinned_uid=pinned_uid, pinned_main_uid=pinned_main_uid
    ):
        return result("unknown", "account_identity_not_exact")
    if (
        type(instrument_id) is not str
        or not instrument_id
        or bool(order_id) == bool(client_order_id)
        or order_id is not None
        and type(order_id) is not str
        or client_order_id is not None
        and type(client_order_id) is not str
    ):
        return result("unknown", "target_identifier_not_exact")
    matches = [
        item
        for item in snapshot.pending_orders
        if (
            item.order_id == order_id
            if order_id
            else item.client_order_id == client_order_id
        )
    ]
    if len(matches) != 1:
        return result("unknown", "target_not_unique_pending")
    order = matches[0]
    if order.instrument_id != instrument_id or any(
        other is not order
        and (
            other.order_id == order.order_id
            or order.client_order_id is not None
            and other.client_order_id == order.client_order_id
        )
        for other in snapshot.pending_orders
    ):
        return result("unknown", "pending_target_identity_conflict", order)
    if any(
        item.order_id == order.order_id
        or order.client_order_id is not None
        and item.client_order_id == order.client_order_id
        for item in snapshot.recent_orders
    ):
        return result("unknown", "pending_history_identity_conflict", order)
    if not _order_raw_matches(order):
        return result("unknown", "order_raw_evidence_incomplete_or_conflicting", order)
    if order.size <= 0 or not order.size.is_finite():
        return result("unknown", "order_size_invalid", order)
    if order.accumulated_fill_size < 0 or not order.accumulated_fill_size.is_finite():
        return result("unknown", "order_fill_invalid", order)
    if order.accumulated_fill_size > order.size:
        return result("unknown", "order_fill_exceeds_size", order)
    if order.accumulated_fill_size > 0:
        return result("partially_filled", "order_has_fill", order)
    if order.state != "live" or order.order_type not in {"limit", "post_only"}:
        return result("unknown", "order_state_or_type_not_ordinary", order)
    if order.side not in {"buy", "sell"}:
        return result("unknown", "order_side_invalid", order)
    if snapshot.account_config.position_mode == "net_mode":
        if order.position_side != "net":
            return result("unknown", "order_position_mode_mismatch", order)
    elif order.position_side not in {"long", "short"}:
        return result("unknown", "order_position_mode_mismatch", order)
    elif (order.side, order.position_side) not in {
        ("buy", "long"),
        ("sell", "short"),
    }:
        return result("protective", "order_may_reduce_hedged_position", order)
    if order.reduce_only or order.attached_algo_orders:
        return result("protective", "order_has_protection_or_reduce_only", order)
    if any(
        item.instrument_id == instrument_id for item in snapshot.pending_algo_orders
    ):
        return result("protective", "instrument_has_pending_algo", order)
    for position in snapshot.positions:
        if position.instrument_id == instrument_id:
            if not _position_raw_matches(position):
                return result("unknown", "position_raw_evidence_incomplete", order)
            if position.size != 0:
                return result("protective", "instrument_has_position", order)
    return result("ordinary_unfilled", "ordinary_unfilled_structural_match_only", order)


def classify_live_close_target(
    snapshot: OkxLiveReconcileResult,
    *,
    pinned_uid: str,
    pinned_main_uid: str,
    instrument_id: str,
    position_id: str,
    direction: Literal["long", "short"],
    margin_mode: Literal["cross", "isolated"],
) -> LiveMaintenanceTargetAssessment:
    """Classify one exact position without authorizing a market close."""
    action = "close_position"

    def result(
        kind: TargetKind, reason: str, position: OkxLivePositionView | None = None
    ) -> LiveMaintenanceTargetAssessment:
        return _assessment(
            action,
            kind,
            reason,
            snapshot=snapshot,
            instrument_id=instrument_id,
            target_id=position.position_id if position is not None else position_id,
            position_side=position.position_side if position is not None else None,
            margin_mode=position.margin_mode if position is not None else None,
            size=abs(position.size) if position is not None else None,
        )

    if not _identity_matches(
        snapshot, pinned_uid=pinned_uid, pinned_main_uid=pinned_main_uid
    ):
        return result("unknown", "account_identity_not_exact")
    if (
        type(instrument_id) is not str
        or not instrument_id
        or type(position_id) is not str
        or not position_id
        or direction not in {"long", "short"}
        or margin_mode not in {"cross", "isolated"}
    ):
        return result("unknown", "position_target_not_exact")
    matches = [item for item in snapshot.positions if item.position_id == position_id]
    if len(matches) != 1 or matches[0].instrument_id != instrument_id:
        return result("unknown", "position_not_unique_or_instrument_mismatch")
    position = matches[0]
    if not _position_raw_matches(position):
        return result(
            "unknown", "position_raw_evidence_incomplete_or_conflicting", position
        )
    if (
        position.size == 0
        or not position.size.is_finite()
        or not position.available_size.is_finite()
        or position.available_size < 0
        or position.available_size > abs(position.size)
        or position.margin_mode != margin_mode
    ):
        return result("unknown", "position_size_or_margin_mismatch", position)
    if snapshot.account_config.position_mode == "net_mode":
        if position.position_side != "net" or (position.size > 0) != (
            direction == "long"
        ):
            return result("unknown", "position_side_or_direction_mismatch", position)
    elif position.position_side != direction or position.size <= 0:
        return result("unknown", "position_side_or_direction_mismatch", position)
    if any(
        item is not position and item.instrument_id == instrument_id and item.size != 0
        for item in snapshot.positions
    ):
        return result("unknown", "multiple_instrument_positions", position)
    if position.available_size != abs(position.size):
        return result("unknown", "position_available_size_incomplete", position)
    if any(item.instrument_id == instrument_id for item in snapshot.pending_orders):
        return result("protective", "instrument_has_pending_order", position)
    if any(
        item.instrument_id == instrument_id for item in snapshot.pending_algo_orders
    ):
        return result("protective", "instrument_has_pending_algo", position)
    return result("exact_position", "exact_position_structural_match_only", position)
