"""Original-row financial windows; never complete account or clock authority.

Public replay verifies retained source bytes. Only the separate native assembler
can establish live source ownership. Funding, finality, streak seed and HWM are
deliberately unresolved by this version.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from fractions import Fraction
from weakref import WeakKeyDictionary

from app.domain.source_primitives import canonical, sha
from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_lifecycle_history as lifecycle
from app.trade_qualification import account_observation_index as observed
from app.trade_qualification.reservations import LedgerScope

POLICY_BYTES = canonical(
    {
        "version": "ctcc.account_risk_windows.v1",
        "typed_policy_sha256": observed.TYPED_POLICY_SHA256,
        "lifecycle_policy_sha256": lifecycle.POLICY_SHA256,
        "daily": "UTC_midnight_inclusive_through_original_history_end_inclusive",
        "weekly": "original_checked_at_minus_7d_strict_through_history_end_inclusive",
        "fill_cashflow": "original_fillPnl_plus_signed_fee_at_fillTime",
        "loss": "max_zero_negative_net_sum_exact_rational",
        "bill_mirrors": "corroborate_only_never_sum_twice",
        "funding_finality_streak_HWM": "unknown_not_zero",
        "execution_authority": False,
    }
)
POLICY_SHA256 = sha(POLICY_BYTES)
_RESULTS = WeakKeyDictionary()
_BASE_GAPS = frozenset(
    {
        "account_funding_accrual_attribution_unproven",
        "account_wide_execution_window_finality_unproven",
        "current_history_tail_unproven",
        "loss_streak_seed_unknown",
        "historical_native_hwm_unknown",
        "complete_account_snapshot_unavailable",
        "historical_native_prefix_unproven",
    }
)


class AccountRiskWindowsError(ValueError):
    """Fixed source-safe rejection codes only."""


def _deny(code="account_risk_windows_source_invalid"):
    raise AccountRiskWindowsError(code) from None


def _ratio(value):
    return {"numerator": str(value.numerator), "denominator": str(value.denominator)}


def _read_ratio(value):
    if type(value) is not dict or set(value) != {"numerator", "denominator"}:
        _deny("account_risk_windows_amount_invalid")
    for item in value.values():
        if type(item) is not str or not 1 <= len(item) <= 256:
            _deny("account_risk_windows_amount_invalid")
    try:
        result = Fraction(int(value["numerator"]), int(value["denominator"]))
    except (ValueError, ZeroDivisionError):
        _deny("account_risk_windows_amount_invalid")
    if _ratio(result) != value:
        _deny("account_risk_windows_amount_invalid")
    return result


def _fill_window_totals(cashflows, *, day_start, week_start, ended_at):
    """Algebra on replayed rows only; this helper grants no source authority."""
    daily = weekly = Fraction(0)
    identities = set()
    for row in cashflows:
        identity = row["identity_sha256"]
        if identity in identities:
            _deny("account_risk_windows_duplicate_fill")
        identities.add(identity)
        at = lifecycle._utc(datetime.fromisoformat(row["execution_at"]))
        gross = _read_ratio(row["gross_fill_pnl"])
        fee = _read_ratio(row["signed_fee"])
        amount = _read_ratio(row["observed_fill_cashflow"])
        if amount != gross + fee:
            _deny("account_risk_windows_fill_component_conflict")
        if day_start <= at <= ended_at:
            daily += amount
        if week_start < at <= ended_at:
            weekly += amount
    return daily, weekly


def _advance_loss_streak(seed, net_outcomes):
    """Completed lifecycle algebra; never a replacement for a source seed."""
    if seed is None:
        return None
    if type(seed) is not int or not 0 <= seed <= 2048:
        _deny("account_risk_windows_streak_seed_invalid")
    for amount in net_outcomes:
        if amount is None:
            return None
        if type(amount) is not Fraction:
            _deny("account_risk_windows_streak_amount_invalid")
        if amount < 0:
            seed += 1
        elif amount > 0:
            seed = 0
    return seed


@dataclass(frozen=True, slots=True, repr=False)
class _WindowFacts:
    scope: LedgerScope
    checked_at: datetime
    started_at: datetime
    ended_at: datetime
    daily_fill_pnl: Fraction | None
    weekly_fill_pnl: Fraction | None
    daily_net_pnl: None
    weekly_net_pnl: None
    loss_streak: None
    missing_obligations: tuple[str, ...]
    receipt_json: bytes


class AccountRiskWindowsV1:
    """A retained-source replay result, not a native capability or snapshot."""

    __slots__ = ("__weakref__",)

    def __new__(cls):
        _deny("account_risk_windows_not_constructible")

    @property
    def receipt_json(self):
        return _checked_result(self).receipt_json

    @property
    def receipt_sha256(self):
        return sha(self.receipt_json)


def _checked_result(value):
    facts = _RESULTS.get(value) if type(value) is AccountRiskWindowsV1 else None
    if facts is None:
        _deny("account_risk_windows_replay_required")
    return facts


def derive_account_risk_windows_v1(
    sources,
    *,
    scope,
    window,
    index_batches,
    expected_index_head_sha256,
    expected_source_set_sha256,
    checked_at,
):
    """Reopen original B1/B3 data; no caller totals/complete/seed are accepted."""
    try:
        checked_at = capture._utc(checked_at)
        if type(window) is not observed.ExecutionWindow:
            _deny("account_risk_windows_original_window_required")
        day_start = checked_at.replace(hour=0, minute=0, second=0, microsecond=0)
        week_start = checked_at - timedelta(days=7)
        if window.ended_at > checked_at:
            _deny("account_risk_windows_future_cutoff")
        history = lifecycle.replay_account_lifecycle_history(
            sources,
            scope=scope,
            window=window,
            index_batches=index_batches,
            expected_index_head_sha256=expected_index_head_sha256,
            expected_source_set_sha256=expected_source_set_sha256,
        )
        typed = observed.replay_observation_index_typed_v1(
            sources, scope=scope, window=window
        )
        old = lifecycle._json(history.receipt_json)
        data = lifecycle._json(typed.receipt_json)
        if any(
            capture._utc(datetime.fromisoformat(item["observed_completed_at"]))
            > checked_at
            for item in data["captures"]
        ) or any(
            point.readback_at > checked_at for chain, _ in sources for point in chain
        ):
            _deny("account_risk_windows_unavailable_at_cutoff")
        gaps = (
            set(_BASE_GAPS)
            | set(data["blocking_reasons"])
            | set(old["blocking_reasons"])
        )
        if window.started_at > week_start or window.ended_at < day_start:
            gaps.add("account_risk_windows_coverage_incomplete")
        daily, weekly = _fill_window_totals(
            data["cashflows"],
            day_start=day_start,
            week_start=week_start,
            ended_at=window.ended_at,
        )
        # A conflicting/unsupported/incomplete observation cannot become an
        # apparently complete zero or a usable subtotal by ignoring its blockers.
        fill_known = not data["blocking_reasons"]
        daily = (
            daily
            if (fill_known and window.started_at <= day_start <= window.ended_at)
            else None
        )
        weekly = (
            weekly
            if (fill_known and window.started_at <= week_start < window.ended_at)
            else None
        )
        receipt = canonical(
            {
                "schema_version": "ctcc.account_risk_windows.v1",
                "policy_sha256": POLICY_SHA256,
                "scope_sha256": sha(
                    canonical(
                        [
                            scope.environment,
                            scope.account_id,
                            scope.settlement_currency,
                        ]
                    )
                ),
                "source_set_sha256": expected_source_set_sha256,
                "index_head_sha256": expected_index_head_sha256,
                "typed_receipt_sha256": typed.receipt_sha256,
                "lifecycle_receipt_sha256": history.receipt_sha256,
                "checked_at": checked_at.isoformat(),
                "window": observed.window_document(window),
                "daily_start_inclusive": day_start.isoformat(),
                "weekly_start_exclusive": week_start.isoformat(),
                "daily_fill_pnl": None if daily is None else _ratio(daily),
                "weekly_fill_pnl": None if weekly is None else _ratio(weekly),
                "daily_net_pnl": None,
                "weekly_net_pnl": None,
                "loss_streak": _advance_loss_streak(None, ()),
                "historical_native_prefix_verified": False,
                "source_authenticity_verified": False,
                "history_tail_finality": None,
                "historical_native_hwm": None,
                "original_fill_cashflows": data["cashflows"],
                "retained_source_index": data["source_index"],
                "retained_bills": old["bills"],
                "missing_obligations": sorted(gaps),
                "account_complete": False,
                "account_revision_published": False,
                "execution_authority": False,
                "admission": "DENY",
            }
        )
        if len(receipt) > observed.MAX_RECEIPT_BYTES:
            _deny("account_risk_windows_receipt_bound")
        result = object.__new__(AccountRiskWindowsV1)
        _RESULTS[result] = _WindowFacts(
            scope,
            checked_at,
            window.started_at,
            window.ended_at,
            daily,
            weekly,
            None,
            None,
            None,
            tuple(sorted(gaps)),
            receipt,
        )
        return result
    except AccountRiskWindowsError:
        raise
    except Exception:  # noqa: BLE001 -- no raw financial/source exception text
        _deny()
