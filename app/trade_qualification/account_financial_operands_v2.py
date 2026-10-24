"""Bounded original native-position/fill/bill operands, never risk authority.

Native archives retain their original format. V8 supplies the existing closed
run arithmetic; account bills supply payment candidates, never accrual times.
Pure replay can produce financial facts but cannot issue a native owner.
"""

from dataclasses import dataclass

from app.domain.source_primitives import canonical, sha
from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_funding_bill_audit as bills
from app.trade_qualification import account_history_risk_v8 as risk
from app.trade_qualification import account_lifecycle_history as lifecycle
from app.trade_qualification import account_observation_index as observed
from app.trade_qualification import account_positions_history_collector_v1 as positions
from app.trade_qualification import account_positions_history_v1 as position_pages

POLICY_BYTES = canonical(
    {
        "version": "ctcc.original_account_financial_operands.v2",
        "v8_arithmetic_policy_sha256": risk.POLICY_SHA256,
        "bill_candidate_policy_sha256": bills.POLICY_SHA256,
        "bill_consistency_policy_sha256": bills.CONSISTENCY_POLICY_SHA256,
        "position_archive": positions.NATIVE_POSITION_VERSION,
        "source": "original_B1_B3_and_original_native_positions_archive",
        "net": "bounded_normal_closed_run_only_not_daily_or_weekly_cashflow",
        "funding": "payment_candidates_not_accrual_or_empty_zero",
        "historical_prefix_tail_streak_HWM": "unknown_not_issued",
        "execution_authority": False,
    }
)
POLICY_SHA256 = sha(POLICY_BYTES)
_MISSING = (
    "historical_native_prefix_unproven",
    "account_funding_accrual_attribution_unproven",
    "account_wide_execution_window_finality_unproven",
    "current_history_tail_unproven",
    "loss_streak_seed_unknown",
    "historical_native_hwm_unknown",
    "complete_account_snapshot_unavailable",
)


class AccountFinancialOperandsError(ValueError):
    """Fixed source-safe errors; no original account rows in exception text."""


def _deny(code="account_financial_operands_source_invalid"):
    raise AccountFinancialOperandsError(code) from None


@dataclass(frozen=True, slots=True, repr=False)
class NativePositionSourceV2:
    """Original replay inputs only, with no native or account authority."""

    plan: position_pages.PositionHistoryPlanV1
    identity: positions._NativePositionIdentityV1
    archive: bytes
    expected_archive_sha256: str


@dataclass(frozen=True, slots=True, repr=False)
class AccountFinancialOperandsV2:
    """Inert exact-source financial observations; never accepted as an owner."""

    receipt_json: bytes

    @property
    def receipt_sha256(self):
        return sha(self.receipt_json)


def _native_position_rows(source, packets, references, scope, checked_at):
    if type(source) is not NativePositionSourceV2:
        _deny("account_financial_original_native_position_source_required")
    plan, identity = source.plan, source.identity
    replay = positions._verify_native_source_archive(
        plan,
        identity,
        source.archive,
        expected_sha256=source.expected_archive_sha256,
    )
    if (
        identity.uid != scope.account_id
        or scope.environment != "demo"
        or scope.settlement_currency != "USDT"
        or identity.account_level != "2"
        or identity.position_mode != "net_mode"
    ):
        _deny("account_financial_position_scope_or_mode_unsupported")
    for packet, reference in zip(packets, references, strict=True):
        pages = [p for p in packet.observations if p.request.stream == "config_after"]
        if len(pages) != 1 or len(pages[0].rows) != 1:
            _deny("account_financial_original_identity_required")
        config = lifecycle._json(pages[0].rows[0].canonical_json.encode())
        if (
            (
                config.get("uid"),
                config.get("mainUid"),
                config.get("acctLv"),
                config.get("posMode"),
            )
            != (
                identity.uid,
                identity.main_uid,
                identity.account_level,
                identity.position_mode,
            )
            or reference.session_binding_sha256 != plan.session_binding_sha256
            or packet.plan.origin != plan.origin
            or packet.plan.registration_region != plan.registration_region
        ):
            _deny("account_financial_original_position_session_changed")
    archive = capture._decode_json(
        source.archive, limit=positions.MAX_ARCHIVE_BYTES, wire=False
    )[0]
    after = positions._read_record(archive["config_after"])
    if after.completed > checked_at:
        _deny("account_financial_position_unavailable_at_cutoff")
    pages = positions._unfreeze_pages(plan, archive["history_archive"])
    rows = []
    for index, page in enumerate(pages):
        raw_rows = capture._decode_json(
            page.raw_body, limit=position_pages.MAX_PAGE_BYTES, wire=True
        )[0]["data"]
        for ordinal, row in enumerate(raw_rows):
            rows.append(
                {
                    "raw": row,
                    "row_sha256": page.rows[ordinal].row_sha256,
                    "locator": {
                        "archive_sha256": replay.archive_sha256,
                        "page_index": index,
                        "row_ordinal": ordinal,
                        "page_sha256": page.page_sha256,
                    },
                }
            )
    gaps = set()
    if replay.account_usd_contract_settle_currency != scope.settlement_currency:
        gaps.add("native_position_account_settlement_setting_unknown_or_different")
    return replay, rows, gaps


def _bill_observations(sources, packets, scope, members):
    audits, candidates, gaps = [], {}, set()
    original = {
        (row["identity_sha256"], row["row_sha256"]): row
        for row in members
        if row["family"] == "bills"
    }
    versions = {}
    for identity, row_sha in original:
        versions.setdefault(identity, set()).add(row_sha)
    for (chain, reference), packet in zip(sources, packets, strict=True):
        if type(packet.plan) is not capture.AllProductDemoAccountCapturePlan:
            gaps.add("funding_bill_audit_original_v5_source_required")
            continue
        audit = bills.audit_funding_bills(chain, reference=reference, scope=scope)
        consistent = bills.audit_funding_bill_consistency(
            chain, reference=reference, scope=scope
        )
        data = lifecycle._json(audit.receipt_json)
        comparison = lifecycle._json(consistent.receipt_json)
        gaps.update(data["blocking_reasons"])
        gaps.update(comparison["blocking_reasons"])
        audits.append(
            {
                "capture_id": reference.capture_id,
                "candidate_receipt_sha256": audit.receipt_sha256,
                "consistency_receipt_sha256": consistent.receipt_sha256,
                "candidate_receipt": data,
                "consistency_receipt": comparison,
            }
        )
        for entry in data["account_bill_rows"]:
            if entry["classification"] != "account_funding_payment_candidate":
                continue
            key = (entry["identity_sha256"], entry["row_sha256"])
            member = original.get(key)
            if member is None:
                _deny("account_financial_original_bill_locator_missing")
            if key not in candidates:
                observations = [
                    item
                    for item in comparison["candidate_field_observations"]
                    if (item["identity_sha256"], item["row_sha256"]) == key
                ]
                if len(observations) != 1:
                    _deny("account_financial_original_bill_consistency_missing")
                agreement = observations[0]["payment_balance_change_numeric_agreement"]
                fee_zero = observations[0]["fee_field_observed_zero"]
                conflict = len(versions[key[0]]) != 1
                amount = risk._fraction(lifecycle._number(member["raw"].get("pnl")))
                candidates[key] = {
                    **{
                        name: entry[name]
                        for name in (
                            "identity_sha256",
                            "row_sha256",
                            "classification",
                            "account_bill_type",
                            "account_bill_subtype",
                            "payment_sign",
                            "source_balance_update_at",
                        )
                    },
                    "observed_signed_pnl": amount,
                    "payment_balance_change_numeric_agreement": agreement,
                    "fee_field_observed_zero": fee_zero,
                    "source_identity_conflict": conflict,
                    "reconciled_candidate_amount": amount
                    if agreement is True and fee_zero is True and not conflict
                    else None,
                    "source_locators": member["locators"],
                    "effective_accrual_at": None,
                    "account_funding_event": None,
                    "complete_payment_or_accrual_verified": False,
                }
    return audits, list(candidates.values()), gaps


def derive_account_financial_operands_v2(
    sources,
    *,
    scope,
    window,
    index_batches,
    expected_index_head_sha256,
    expected_source_set_sha256,
    position_source,
    checked_at,
):
    """Reopen all original rows; no totals, complete flags, seed or peak inputs."""
    try:
        checked_at = capture._utc(checked_at)
        if type(window) is not observed.ExecutionWindow or window.ended_at > checked_at:
            _deny("account_financial_original_cutoff_invalid")
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
        old, data = (
            lifecycle._json(history.receipt_json),
            lifecycle._json(typed.receipt_json),
        )
        packets = [lifecycle._packet(chain, ref) for chain, ref in sources]
        if any(p.completed_at > checked_at for p in packets) or any(
            point.readback_at > checked_at for chain, _ in sources for point in chain
        ):
            _deny("account_financial_sources_unavailable_at_cutoff")
        members = lifecycle._source_rows(data["source_index"], packets, sources)
        replay, position_rows, position_gaps = _native_position_rows(
            position_source, packets, [ref for _, ref in sources], scope, checked_at
        )
        # A missing global USD-contract setting cannot erase an independently
        # reconciled USDT position run whose original row/witness currency is
        # exact. It still blocks complete-account claims below.
        runs = [
            risk._run(
                part,
                members,
                packets,
                position_rows,
                {},
                scope.settlement_currency,
            )
            for part in old["observed_lifecycles"]
        ]
        used = [
            r["position_history_row"]["row_sha256"]
            for r in runs
            if r["net_outcome_arithmetic_reconciled"]
        ]
        if len(used) != len(set(used)):
            _deny("account_financial_position_total_reused")
        audits, candidates, bill_gaps = _bill_observations(
            sources, packets, scope, members
        )
        missing = (
            set(_MISSING)
            | set(old["blocking_reasons"])
            | set(data["blocking_reasons"])
            | position_gaps
            | bill_gaps
        )
        unjoined = [
            {"row_sha256": r["row_sha256"], "locator": r["locator"]}
            for r in position_rows
            if r["row_sha256"] not in used
        ]
        if unjoined:
            missing.add("account_wide_position_history_rows_unjoined")
        raw = canonical(
            {
                "schema_version": "ctcc.original_account_financial_operands.v2",
                "policy_sha256": POLICY_SHA256,
                "source_set_sha256": expected_source_set_sha256,
                "index_head_sha256": expected_index_head_sha256,
                "lifecycle_receipt_sha256": history.receipt_sha256,
                "typed_receipt_sha256": typed.receipt_sha256,
                "checked_at": checked_at.isoformat(),
                "window": observed.window_document(window),
                "original_fill_cashflows": data["cashflows"],
                "retained_source_index": data["source_index"],
                "retained_account_bills": old["bills"],
                "native_position_archive_sha256": replay.archive_sha256,
                "native_position_chain_sha256": replay.chain.chain_sha256,
                "observed_account_usd_settle_currency": replay.account_usd_contract_settle_currency,
                "observed_position_margin_currencies": list(
                    replay.observed_margin_currencies
                ),
                "closed_runs": runs,
                "reconciled_run_count": len(used),
                "unjoined_position_history_rows": unjoined,
                "funding_bill_audits": audits,
                "funding_payment_candidates": candidates,
                "funding_accrual_events": None,
                "daily_net_pnl": None,
                "weekly_net_pnl": None,
                "loss_streak_seed": None,
                "loss_streak": None,
                "current_history_tail_finality": None,
                "historical_native_hwm": None,
                "historical_native_prefix_verified": False,
                "source_authenticity_verified": False,
                "missing_obligations": sorted(missing),
                "account_complete": False,
                "account_revision_published": False,
                "clear_stop_eligible": False,
                "arm_eligible": False,
                "execution_authority": False,
                "admission": "DENY",
            }
        )
        if len(raw) > observed.MAX_RECEIPT_BYTES:
            _deny("account_financial_operands_receipt_bound")
        return AccountFinancialOperandsV2(raw)
    except AccountFinancialOperandsError:
        raise
    except Exception:  # noqa: BLE001 -- keep original financial/transport data private.
        _deny()
