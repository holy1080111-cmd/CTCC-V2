"""Native bootstrap financial assembly, never an armed account/order issuer.

History is acquired before the short-lived current source. Each original owner
is MOVEd exactly once, then held behind a single task-local assembly owner.
Missing historical native ownership, funding, finality, seed and HWM remain
unknown even when all fresh source/custody checks succeed.
"""

from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from threading import get_ident
from weakref import WeakKeyDictionary, ref

from app.database.repositories import public_runtime_bundle_custody as custody
from app.domain import native_clock
from app.domain.source_primitives import canonical, sha, utc_from_ns
from app.exchange.okx.private_rest import (
    DemoPinnedReadSession,
    OkxDemoPrivateRestClient,
)
from app.trade_evidence import inventory_math
from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_current_source_v4 as current
from app.trade_qualification import account_current_source_verifier as current_verifier
from app.trade_qualification import account_financial_operands_v2 as financial_v2
from app.trade_qualification import account_history_source_bundle_v1 as history_bundle
from app.trade_qualification import account_history_source_v1 as history
from app.trade_qualification import account_lifecycle_history as lifecycle
from app.trade_qualification import account_materializer as mapping
from app.trade_qualification import account_observation_index as observed
from app.trade_qualification import account_risk_windows_v1 as windows
from app.trade_qualification import controlled_writer_coverage_v1 as coverage
from app.trade_qualification import portfolio
from app.trade_qualification import reservations as financial_reservations
from app.trade_qualification.account_runtime import ControlledDemoAccountSession

_HOOK_KEY = object()
_OWNERS = WeakKeyDictionary()
_MAX_OWNERS = 256
_ROOT = Path(__file__).resolve().parents[2]
_FINANCIAL_PATHS = (
    "app/trade_evidence/inventory_math.py",
    "app/trade_qualification/reservations.py",
    "app/trade_qualification/account_financial_operands_v2.py",
    "app/trade_qualification/account_history_risk_v8.py",
    "app/trade_qualification/account_funding_bill_audit.py",
    "app/trade_qualification/settled_funding_history_source.py",
    "app/trade_qualification/account_history_query_verifier.py",
    "docs/native_account_financial_operands_v2.md",
    "app/trade_qualification/native_account_risk_assembly_v1.py",
    "app/trade_qualification/account_risk_windows_v1.py",
    "app/trade_qualification/portfolio.py",
    "app/trade_qualification/account_current_source_v4.py",
    "app/trade_qualification/account_current_source_verifier.py",
    "app/trade_qualification/account_materializer.py",
    "app/trade_qualification/account_history_source_v1.py",
    "app/trade_qualification/account_history_source_bundle_v1.py",
    "app/trade_qualification/account_history_native_stage_v1.py",
    "app/trade_qualification/account_lifecycle_history.py",
    "app/trade_qualification/account_observation_index.py",
    "app/trade_qualification/account_capture.py",
    "app/trade_qualification/account_capture_journal.py",
    "app/trade_qualification/account_positions_history_v1.py",
    "app/trade_qualification/account_positions_history_collector_v1.py",
    "app/trade_qualification/account_native_clock.py",
    "app/trade_qualification/account_native_proof.py",
    "app/trade_qualification/account_native_proof_storage.py",
    "app/trade_qualification/account_native_runtime.py",
    "app/database/repositories/account_capture_journal.py",
    "app/database/repositories/account_observation_index.py",
    "app/database/repositories/public_runtime_bundle_custody.py",
    "app/domain/native_clock.py",
    "app/domain/source_primitives.py",
    "docs/native_account_risk_assembly_v1.md",
)
SOURCE_PATHS = tuple(sorted(set(coverage.WRITER_PATHS) | set(_FINANCIAL_PATHS)))
_MISSING = (
    "historical_native_prefix_unproven",
    "funding_accrual_and_account_attribution_unproven",
    "account_wide_history_tail_and_finality_unproven",
    "source_derived_loss_streak_seed_unknown",
    "preserved_native_hwm_and_drawdown_window_unproven",
    "complete_portfolio_and_protection_unavailable",
    "operational_coverage_control_transition_unimplemented",
)


class NativeAccountRiskAssemblyError(ValueError):
    """Fixed privacy-safe codes only."""


def _deny(code="native_financial_assembly_unavailable"):
    raise NativeAccountRiskAssemblyError(code) from None


class OwnedNativeAccountRiskAssemblyV1:
    __slots__ = ("__weakref__",)

    def __new__(cls):
        _deny("native_financial_owner_not_constructible")

    def __copy__(self):
        _deny("native_financial_owner_not_transferable")

    def __deepcopy__(self, memo):
        _deny("native_financial_owner_not_transferable")

    def __reduce_ex__(self, protocol):
        _deny("native_financial_owner_not_transferable")


@dataclass(frozen=True, slots=True, repr=False)
class NativeAccountRiskAssemblyV1:
    """Measured bootstrap operands; this portable result is never a capability."""

    financial_windows: windows.AccountRiskWindowsV1 = field(repr=False)
    risk_evaluation: portfolio.NativeAccountRiskWindowsEvaluationV1 = field(repr=False)
    receipt_json: bytes = field(repr=False)
    account_complete: bool = field(default=False, init=False)
    account_revision_published: bool = field(default=False, init=False)
    native_history_prefix_verified: bool = field(default=False, init=False)
    flat_start_permission: bool = field(default=False, init=False)
    post_g12_eligible: bool = field(default=False, init=False)
    execution_authority: bool = field(default=False, init=False)

    @property
    def receipt_sha256(self):
        return sha(self.receipt_json)


def _context():
    task = asyncio.current_task()
    if task is None or task.cancelling():
        _deny("native_financial_context_unavailable")
    return os.getpid(), get_ident(), asyncio.get_running_loop(), task


def _package_inventory():
    """Pin the union, without treating the old writer list as financial proof."""
    writer = coverage._package_inventory()
    manifest, manifest_pin = coverage._read_file(
        _ROOT / "MANIFEST.sha256", 2 * 1024 * 1024
    )
    rows = {}
    for line in manifest.decode("ascii").splitlines():
        digest, separator, name = line.partition("  ")
        if not separator or coverage._SHA.fullmatch(digest) is None or name in rows:
            _deny("native_financial_manifest_invalid")
        rows[name] = digest
    files = {}
    for name in SOURCE_PATHS:
        raw, pin = coverage._read_file(_ROOT / name, 4 * 1024 * 1024)
        if rows.get(name) != sha(raw):
            _deny("native_financial_packaged_source_changed")
        files[name] = pin
    return {
        "writer": writer,
        "manifest": manifest_pin,
        "manifest_sha256": sha(manifest),
        "files": files,
        "source_sha256": sha(canonical({name: pin[2] for name, pin in files.items()})),
    }


def _check(state):
    actual = _context()
    expected = state["context"]
    if actual[:3] != expected[:3] or actual[3] is not expected[3]():
        _deny("native_financial_context_changed")
    if any(
        getattr(owner, name) is not function for owner, name, function in _FUNCTION_PINS
    ):
        _deny("native_financial_function_changed")
    if _package_inventory() != state["inventory"]:
        _deny("native_financial_source_inventory_changed")
    if state["lease"]._owner is not state["client"]:
        _deny("native_financial_lease_changed")
    if capture.plan_sha256(state["plan"]) != state["plan_sha256"]:
        _deny("native_financial_plan_changed")
    state["lease"].verify_current()
    for expected_pin in state["factories"]:
        found = history._factory_pin(expected_pin[0])
        if (
            found[0] is not expected_pin[0]
            or found[1] is not expected_pin[1]
            or found[2:] != expected_pin[2:]
        ):
            _deny("native_financial_factory_changed")
    # Each source also owns private cloned factories and its original clock.
    # Check both synchronously after cross-source awaits, without reanchoring.
    if "history" in state:
        history._financial_state_v1(state["history"], key=_HOOK_KEY)
    if "coverage" in state:
        coverage._check_binding(state["coverage"])


def _release(state):
    coverage_binding = state.pop("coverage", None)
    history_binding = state.pop("history", None)
    try:
        if coverage_binding is not None:
            coverage._release_financial_assembly_v1(coverage_binding, key=_HOOK_KEY)
    finally:
        try:
            if history_binding is not None:
                history._release_financial_assembly_v1(history_binding, key=_HOOK_KEY)
        finally:
            state.clear()


async def _replay(state):
    _check(state)
    facts, _history_stamp = await history._replay_financial_assembly_v1(
        state["history"], key=_HOOK_KEY
    )
    _check(state)
    view = await coverage._replay_financial_assembly_v1(
        state["coverage"], key=_HOOK_KEY
    )
    _check(state)
    again, _history_stamp = await history._replay_financial_assembly_v1(
        state["history"], key=_HOOK_KEY
    )
    if facts.bundle_raw != again.bundle_raw:
        _deny("native_financial_history_changed")
    _check(state)
    # Last database await checks B5 and Control again, under their original lease.
    view = await coverage._replay_financial_assembly_v1(
        state["coverage"], key=_HOOK_KEY
    )
    _check(state)
    actual = history._identity(facts.packet)
    source = view["current"]
    identity = view["identity"]
    if (
        facts.scope != view["scope"]
        or (identity["environment"], identity["settlement_currency"])
        != ("demo", "USDT")
        or (
            identity["uid"],
            identity["main_uid"],
            identity["account_level"],
            identity["position_mode"],
        )
        != (actual.uid, actual.main_uid, actual.account_level, actual.position_mode)
        or facts.scope.account_id != actual.uid
        or facts.scope.settlement_currency != "USDT"
        or source.reference.session_binding_sha256
        != facts.reference.session_binding_sha256
        or facts.reference.session_binding_sha256
        != sha(state["lease"].binding_id.encode("ascii"))
        or facts.native_history_prefix_verified is not False
        or facts.funding_source_status != "NOT_ACQUIRED"
        or facts.funding_accrual_events is not None
        or facts.account_complete is not False
        or facts.execution_authority is not False
        or view["control"].state.emergency_stop is not True
        or view["control"].state.arm_request_id is not None
    ):
        _deny("native_financial_original_sources_mismatch")
    return facts, view


def _calculate(state, facts, view):
    _check(state)
    data = history_bundle._json(
        facts.batches[-1].document_json, observed.MAX_SOURCE_BYTES
    )
    window_data = data["window"]
    window = observed.ExecutionWindow(
        datetime.fromisoformat(window_data["started_at"]),
        datetime.fromisoformat(window_data["ended_at"]),
    )
    # Source-owned checks retain both original clocks. This sample is only a
    # calculation cutoff and is never written over an exchange/source timestamp.
    history._financial_state_v1(state["history"], key=_HOOK_KEY)
    _coverage_state, stamp = coverage._check_binding(state["coverage"])
    derived = windows.derive_account_risk_windows_v1(
        facts.sources,
        scope=facts.scope,
        window=window,
        index_batches=facts.batches,
        expected_index_head_sha256=facts.batches[-1].event_sha256,
        expected_source_set_sha256=lifecycle.source_set_sha256(facts.sources),
        checked_at=utc_from_ns(stamp["utc_ns"]),
    )
    current_source = view["current"]
    operands = current.CurrentAccountOperandsV4(
        current_source.packet,
        current_source.reference,
        current_source.current_receipt_json,
        canonical(
            {
                "schema_version": "ctcc.native_financial_current_view.v1",
                "execution_authority": False,
            }
        ),
    )
    evaluated = portfolio.evaluate_native_account_risk_windows_v1(
        derived, current_account_operands=operands
    )
    if (
        type(derived) is not windows.AccountRiskWindowsV1
        or type(evaluated) is not portfolio.NativeAccountRiskWindowsEvaluationV1
    ):
        _deny("native_financial_consumer_result_invalid")
    _check(state)
    history._financial_state_v1(state["history"], key=_HOOK_KEY)
    coverage._check_binding(state["coverage"])
    return derived, evaluated


async def acquire_native_account_risk_assembly_v1(
    *,
    read_session,
    private_client,
    scope_config,
    current_account_plan,
    session_factory,
    custody_session_factory,
    history_raw_root,
    history_companion_root,
    account_proof_root,
    account_native_root,
):
    """Acquire original sources; no caller owner/clock/seed/claims/flags accepted."""
    state = None
    history_owner = coverage_owner = None
    successful = False
    try:
        context = _context()
        inventory = _package_inventory()
        if (
            len(_OWNERS) >= _MAX_OWNERS
            or type(read_session) is not DemoPinnedReadSession
            or type(private_client) is not OkxDemoPrivateRestClient
            or read_session._owner is not private_client
            or type(scope_config) is not history.NativeHistoryScopeV1
            or type(current_account_plan) is not capture.CurrentDemoAccountCapturePlanV7
        ):
            _deny("native_financial_acquisition_inputs_invalid")
        scope_config.__post_init__()
        plan = capture._checked_plan(
            current_account_plan, capture.plan_sha256(current_account_plan)
        )
        if (
            plan.expected_uid != scope_config.expected_uid
            or plan.expected_main_uid != scope_config.expected_main_uid
            or plan.session_binding_id != read_session.binding_id
            or plan.settlement_currency != "USDT"
            or plan.environment != "demo"
            or plan.origin != read_session._origin
            or plan.registration_region != "global"
            or plan.registration_evidence_sha256
            != scope_config.registration_evidence_sha256
            or plan.leverage_instrument_ids != scope_config.leverage_instrument_ids
        ):
            _deny("native_financial_plan_scope_mismatch")
        roots = (
            history_raw_root,
            history_companion_root,
            account_proof_root,
            account_native_root,
        )
        if any(
            type(p) is not type(Path()) or not p.is_absolute() for p in roots
        ) or any(
            a == z or a in z.parents or z in a.parents
            for i, a in enumerate(roots)
            for z in roots[i + 1 :]
        ):
            _deny("native_financial_distinct_roots_required")
        state = {
            "context": (*context[:3], ref(context[3])),
            "inventory": inventory,
            "lease": read_session,
            "client": private_client,
            "plan": plan,
            "plan_sha256": capture.plan_sha256(plan),
            "factories": (
                history._factory_pin(session_factory),
                history._factory_pin(custody_session_factory),
            ),
        }
        _check(state)
        history_owner = await history.acquire_native_history_source_facts_v1(
            read_session=read_session,
            private_client=private_client,
            scope_config=scope_config,
            session_factory=session_factory,
            custody_session_factory=custody_session_factory,
            raw_root=history_raw_root,
            companion_root=history_companion_root,
        )
        _check(state)
        state["history"] = history._take_financial_assembly_v1(
            history_owner, key=_HOOK_KEY
        )
        history_owner = None
        session = ControlledDemoAccountSession.from_pinned_read_session(
            read_session=read_session,
            plan=plan,
            expected_plan_sha256=capture.plan_sha256(plan),
        )
        coverage_owner = await coverage.acquire_controlled_demo_writer_coverage_v1(
            account_session=session,
            private_client=private_client,
            session_factory=session_factory,
            private_proof_root=account_proof_root,
            native_proof_root=account_native_root,
            custody_repository=custody.RuntimeBundleCustodyRepository(
                custody_session_factory
            ),
        )
        _check(state)
        state["coverage"] = coverage._take_financial_assembly_v1(
            coverage_owner, key=_HOOK_KEY
        )
        coverage_owner = None
        facts, view = await _replay(state)
        _calculate(state, facts, view)
        # Calculation and its source reads cannot silently overrun either clock.
        await _replay(state)
        owner = object.__new__(OwnedNativeAccountRiskAssemblyV1)
        _OWNERS[owner] = state
        successful = True
        return owner
    except asyncio.CancelledError:
        raise
    except NativeAccountRiskAssemblyError:
        raise
    except Exception:  # noqa: BLE001 -- original account/SQL/credential data stays private.
        _deny("native_financial_acquisition_failed")
    finally:
        # A source acquired immediately before a failing outer guard still burns.
        try:
            if history_owner is not None:
                history._release_financial_assembly_v1(history_owner, key=_HOOK_KEY)
        finally:
            try:
                if coverage_owner is not None:
                    coverage._release_financial_assembly_v1(
                        coverage_owner, key=_HOOK_KEY
                    )
            finally:
                if state is not None and not successful:
                    _release(state)


def consume_owned_native_account_risk_assembly_v1(owner):
    """CALL-TIME burn; never-awaited or cancelled continuations cannot remint."""
    state = (
        _OWNERS.pop(owner, None)
        if type(owner) is OwnedNativeAccountRiskAssemblyV1
        else None
    )
    if state is None:
        _deny("native_financial_owner_unavailable")
    try:
        _check(state)
    except BaseException:
        _release(state)
        raise

    async def consume():
        try:
            facts, view = await _replay(state)
            derived, evaluated = _calculate(state, facts, view)
            await _replay(state)
            receipt = canonical(
                {
                    "schema_version": "ctcc.native_account_risk_assembly.v1",
                    "source_inventory_sha256": state["inventory"]["source_sha256"],
                    "history_bundle_sha256": facts.bundle_sha256,
                    "history_index_head_sha256": facts.batches[-1].event_sha256,
                    "current_packet_sha256": view["current"].reference.packet_sha256,
                    "coverage_journal_sha256": view["coverage"].journal_sha256,
                    "financial_windows_sha256": derived.receipt_sha256,
                    "risk_evaluation_sha256": evaluated.receipt_sha256,
                    "historical_native_prefix_verified": False,
                    "funding_source_status": "NOT_ACQUIRED",
                    "missing_obligations": list(_MISSING),
                    "bootstrap_only": True,
                    "account_complete": False,
                    "account_revision_published": False,
                    "flat_start_permission": False,
                    "post_g12_eligible": False,
                    "execution_authority": False,
                    "admission": "DENY",
                }
            )
            return NativeAccountRiskAssemblyV1(derived, evaluated, receipt)
        except asyncio.CancelledError:
            raise
        except NativeAccountRiskAssemblyError:
            raise
        except Exception:  # noqa: BLE001 -- burned state stays burned; no private error text.
            _deny("native_financial_consumption_failed")
        finally:
            _release(state)

    return consume()


@dataclass(frozen=True, slots=True, repr=False)
class NativeAccountFinancialOperandsV2:
    """Bounded source facts; no account revision, Control or order capability."""

    financial_operands: financial_v2.AccountFinancialOperandsV2 = field(repr=False)
    financial_windows: windows.AccountRiskWindowsV1 = field(repr=False)
    risk_evaluation: portfolio.NativeAccountRiskWindowsEvaluationV1 = field(repr=False)
    receipt_json: bytes = field(repr=False)
    account_complete: bool = field(default=False, init=False)
    account_revision_published: bool = field(default=False, init=False)
    native_history_prefix_verified: bool = field(default=False, init=False)
    clear_stop_eligible: bool = field(default=False, init=False)
    arm_eligible: bool = field(default=False, init=False)
    execution_authority: bool = field(default=False, init=False)

    @property
    def receipt_sha256(self):
        return sha(self.receipt_json)


def _financial_lifetimes_v2(state, view):
    _check(state)
    history_state = history._financial_state_v1(state["history"], key=_HOOK_KEY)
    coverage_state, stamp = coverage._check_binding(state["coverage"])
    anchors = {
        "history_original_anchor": dict(history_state["original"]),
        "coverage_original_anchor": dict(coverage_state["anchor"]),
        "history_original_deadline_ns": history_state["original"]["monotonic_ns"]
        + 60_000_000_000,
        "coverage_original_deadline_ns": coverage_state["anchor"]["monotonic_ns"]
        + 60_000_000_000,
        "current_original_deadline_ns": view["current"].monotonic_deadline_ns,
        "current_original_expires_at": view["current"].expires_at.isoformat(),
        "control_original_lease_until": view["control"].state.lease_until.isoformat(),
    }
    anchors["shorter_monotonic_deadline_ns"] = min(
        anchors[name]
        for name in (
            "history_original_deadline_ns",
            "coverage_original_deadline_ns",
            "current_original_deadline_ns",
        )
    )
    if (
        stamp["monotonic_ns"] >= anchors["shorter_monotonic_deadline_ns"]
        or utc_from_ns(stamp["utc_ns"]) >= view["current"].expires_at
        or utc_from_ns(stamp["utc_ns"]) >= view["control"].state.lease_until
    ):
        _deny("native_financial_v2_original_deadline_expired")
    return anchors, stamp


def _calculate_financial_operands_v2(state, facts, view):
    _check(state)
    derived, evaluated = _calculate(state, facts, view)
    original = history_bundle._json(facts.bundle_raw)
    plan_document = original["position_plan"]
    position_plan = financial_v2.position_pages.PositionHistoryPlanV1(
        **{
            **plan_document,
            "created_at": datetime.fromisoformat(plan_document["created_at"]),
        }
    )
    identity_document = original["position_identity"]
    if identity_document["declared_scope_currency"] != "USDT":
        _deny("native_financial_v2_position_scope_changed")
    identity = financial_v2.positions._NativePositionIdentityV1(
        **{
            key: value
            for key, value in identity_document.items()
            if key != "declared_scope_currency"
        }
    )
    position_source = financial_v2.NativePositionSourceV2(
        position_plan,
        identity,
        facts.positions.raw_archive,
        facts.positions.archive_sha256,
    )
    anchors, stamp = _financial_lifetimes_v2(state, view)
    window_document = history_bundle._json(
        facts.batches[-1].document_json, observed.MAX_SOURCE_BYTES
    )["window"]
    operands = financial_v2.derive_account_financial_operands_v2(
        facts.sources,
        scope=facts.scope,
        window=observed.ExecutionWindow(
            datetime.fromisoformat(window_document["started_at"]),
            datetime.fromisoformat(window_document["ended_at"]),
        ),
        index_batches=facts.batches,
        expected_index_head_sha256=facts.batches[-1].event_sha256,
        expected_source_set_sha256=lifecycle.source_set_sha256(facts.sources),
        position_source=position_source,
        checked_at=utc_from_ns(stamp["utc_ns"]),
    )
    if _financial_lifetimes_v2(state, view)[0] != anchors:
        _deny("native_financial_v2_original_lifetimes_changed")
    return operands, derived, evaluated, anchors


def consume_owned_native_account_financial_operands_v2(owner):
    """Consume the same single-MOVE owner, burning it before any await."""
    state = (
        _OWNERS.pop(owner, None)
        if type(owner) is OwnedNativeAccountRiskAssemblyV1
        else None
    )
    if state is None:
        _deny("native_financial_owner_unavailable")
    try:
        _check(state)
    except BaseException:
        _release(state)
        raise

    async def consume():
        try:
            facts, view = await _replay(state)
            operands, derived, evaluated, anchors = _calculate_financial_operands_v2(
                state, facts, view
            )
            final_facts, final_view = await _replay(state)
            if (
                final_facts.bundle_raw != facts.bundle_raw
                or final_view["current"].reference != view["current"].reference
                or final_view["coverage"].journal_sha256
                != view["coverage"].journal_sha256
            ):
                _deny("native_financial_v2_final_sources_changed")
            if _financial_lifetimes_v2(state, final_view)[0] != anchors:
                _deny("native_financial_v2_original_lifetimes_changed")
            receipt = canonical(
                {
                    "schema_version": "ctcc.native_account_financial_operands.v2",
                    "policy_sha256": financial_v2.POLICY_SHA256,
                    "source_inventory_sha256": state["inventory"]["source_sha256"],
                    "history_bundle_sha256": facts.bundle_sha256,
                    "history_index_head_sha256": facts.batches[-1].event_sha256,
                    "current_packet_sha256": view["current"].reference.packet_sha256,
                    "coverage_journal_sha256": view["coverage"].journal_sha256,
                    "financial_operands_sha256": operands.receipt_sha256,
                    "financial_windows_sha256": derived.receipt_sha256,
                    "risk_evaluation_sha256": evaluated.receipt_sha256,
                    "original_source_lifetimes": anchors,
                    "historical_native_prefix_verified": False,
                    "funding_accrual_events": None,
                    "complete_daily_net_pnl": None,
                    "complete_weekly_net_pnl": None,
                    "loss_streak_seed": None,
                    "historical_native_hwm": None,
                    "current_history_tail_finality": None,
                    "missing_obligations": list(_MISSING),
                    "account_complete": False,
                    "account_revision_published": False,
                    "clear_stop_eligible": False,
                    "arm_eligible": False,
                    "execution_authority": False,
                    "admission": "DENY",
                }
            )
            if _financial_lifetimes_v2(state, final_view)[0] != anchors:
                _deny("native_financial_v2_original_lifetimes_changed")
            return NativeAccountFinancialOperandsV2(
                operands, derived, evaluated, receipt
            )
        except asyncio.CancelledError:
            raise
        except NativeAccountRiskAssemblyError:
            raise
        except Exception:  # noqa: BLE001 -- preserve burned sources, no private error text.
            _deny("native_financial_v2_consumption_failed")
        finally:
            _release(state)

    return consume()


_FUNCTION_PINS = (
    (native_clock, "native_stamp", native_clock.native_stamp),
    (coverage, "_package_inventory", coverage._package_inventory),
    (coverage, "_read_file", coverage._read_file),
    (coverage, "_check_binding", coverage._check_binding),
    (
        coverage,
        "acquire_controlled_demo_writer_coverage_v1",
        coverage.acquire_controlled_demo_writer_coverage_v1,
    ),
    (coverage, "_take_financial_assembly_v1", coverage._take_financial_assembly_v1),
    (coverage, "_replay_financial_assembly_v1", coverage._replay_financial_assembly_v1),
    (
        coverage,
        "_release_financial_assembly_v1",
        coverage._release_financial_assembly_v1,
    ),
    (
        history,
        "acquire_native_history_source_facts_v1",
        history.acquire_native_history_source_facts_v1,
    ),
    (history, "_take_financial_assembly_v1", history._take_financial_assembly_v1),
    (history, "_replay_financial_assembly_v1", history._replay_financial_assembly_v1),
    (history, "_release_financial_assembly_v1", history._release_financial_assembly_v1),
    (history, "_check_pins", history._check_pins),
    (history, "_factory_pin", history._factory_pin),
    (history, "_financial_state_v1", history._financial_state_v1),
    (history, "_population", history._population),
    (history, "_same_population", history._same_population),
    (history, "_replay_history_state_v1", history._replay_history_state_v1),
    (history, "_identity", history._identity),
    (
        history_bundle,
        "replay_history_source_bundle_v1",
        history_bundle.replay_history_source_bundle_v1,
    ),
    (history_bundle, "_json", history_bundle._json),
    (
        observed,
        "replay_observation_index_typed_v1",
        observed.replay_observation_index_typed_v1,
    ),
    (windows, "derive_account_risk_windows_v1", windows.derive_account_risk_windows_v1),
    (windows, "_fill_window_totals", windows._fill_window_totals),
    (windows, "_read_ratio", windows._read_ratio),
    (windows, "_ratio", windows._ratio),
    (windows, "_advance_loss_streak", windows._advance_loss_streak),
    (windows, "_checked_result", windows._checked_result),
    (
        lifecycle,
        "replay_account_lifecycle_history",
        lifecycle.replay_account_lifecycle_history,
    ),
    (lifecycle, "source_set_sha256", lifecycle.source_set_sha256),
    (lifecycle, "_utc", lifecycle._utc),
    (lifecycle, "_json", lifecycle._json),
    (lifecycle, "_number", lifecycle._number),
    (lifecycle, "_fraction", lifecycle._fraction),
    (lifecycle, "_index", lifecycle._index),
    (lifecycle, "_packet", lifecycle._packet),
    (lifecycle, "_source_rows", lifecycle._source_rows),
    (lifecycle, "_lifecycle", lifecycle._lifecycle),
    (observed, "_replay", observed._replay),
    (observed, "_pins", observed._pins),
    (observed, "_amount", observed._amount),
    (observed, "_fraction", observed._fraction),
    (observed, "reference_document", observed.reference_document),
    (observed, "window_document", observed.window_document),
    (observed, "_compare_verified", observed._compare_verified),
    (observed, "_compare_verified_typed", observed._compare_verified_typed),
    (observed, "_compare_verified_internal", observed._compare_verified_internal),
    (observed, "_typed_receipt", observed._typed_receipt),
    (capture, "freeze_demo_account_packet", capture.freeze_demo_account_packet),
    (capture, "verify_demo_account_packet", capture.verify_demo_account_packet),
    (
        current_verifier,
        "verify_current_account_sources",
        current_verifier.verify_current_account_sources,
    ),
    (current_verifier, "_verify", current_verifier._verify),
    (
        current_verifier,
        "_verify_current_only_chain",
        current_verifier._verify_current_only_chain,
    ),
    (current_verifier, "_number", current_verifier._number),
    (mapping, "_balance", mapping._balance),
    (mapping, "_fraction", mapping._fraction),
    (portfolio, "_utc", portfolio._utc),
    (
        portfolio,
        "evaluate_native_account_risk_windows_v1",
        portfolio.evaluate_native_account_risk_windows_v1,
    ),
    (
        custody.RuntimeBundleCustodyRepository,
        "verify_deployment",
        custody.RuntimeBundleCustodyRepository.verify_deployment,
    ),
    (
        custody.RuntimeBundleCustodyRepository,
        "read_retained_bundle",
        custody.RuntimeBundleCustodyRepository.read_retained_bundle,
    ),
)


# Fixed source-declared function inventory, captured once at module import.
# Every original cross-await _check therefore also fences the V2 reducer.
_V2_DEPENDENCY_NAMES = (
    (
        financial_v2,
        (
            "_deny",
            "_native_position_rows",
            "_bill_observations",
            "derive_account_financial_operands_v2",
            "canonical",
            "sha",
            "POLICY_BYTES",
            "POLICY_SHA256",
            "risk",
            "bills",
            "positions",
            "position_pages",
            "lifecycle",
            "observed",
            "capture",
            "NativePositionSourceV2",
            "AccountFinancialOperandsV2",
        ),
    ),
    (
        financial_v2.risk,
        (
            "_deny",
            "_number",
            "_fraction",
            "_amount",
            "_time",
            "_position_rows",
            "_funding_sources",
            "_witnesses",
            "_run",
            "replay_account_history_risk_v8",
            "Fraction",
            "canonical",
            "sha",
            "capture",
            "lifecycle",
            "observed",
            "positions",
            "position_pages",
            "funding",
            "POLICY_BYTES",
            "POLICY_SHA256",
        ),
    ),
    (
        financial_v2.bills,
        (
            "_deny",
            "_bounded_decimal",
            "audit_funding_bill_consistency",
            "verify_funding_bill_consistency",
            "_audit_consistency",
            "_payment_sign",
            "_classify_account_bill",
            "audit_funding_bills",
            "verify_funding_bill_audit",
            "_audit",
            "Decimal",
            "checked_bootstrap",
            "LedgerScope",
            "capture",
            "journal",
            "history",
            "observed",
            "POLICY_BYTES",
            "POLICY_SHA256",
            "CONSISTENCY_POLICY_BYTES",
            "CONSISTENCY_POLICY_SHA256",
        ),
    ),
    (
        financial_v2.positions,
        (
            "_deny",
            "_hash",
            "_canonical",
            "_utc",
            "_config_page",
            "_record",
            "_read_record",
            "freeze_source_bound_archive",
            "verify_source_bound_archive",
            "_unfreeze_pages",
            "_checked_read_session",
            "_signed_get",
            "collect_demo_position_history",
            "_signed_get_native",
            "_collect_native_position_history",
            "_native_config_currency",
            "_native_margin_currencies",
            "_native_config_page",
            "_freeze_native_source_archive",
            "_verify_native_source_archive",
            "_NativePositionIdentityV1",
            "_NativePositionArchiveV1",
            "NATIVE_POSITION_VERSION",
            "MAX_ARCHIVE_BYTES",
            "MAX_CONFIG_BYTES",
            "capture",
            "history",
        ),
    ),
    (
        financial_v2.position_pages,
        (
            "_deny",
            "_hash",
            "_canonical",
            "_utc",
            "_milliseconds",
            "request_for_page",
            "_row",
            "parse_page",
            "verify_chain",
            "freeze_chain",
            "verify_archived_chain",
            "PositionHistoryPlanV1",
            "PositionHistoryPageV1",
            "PositionHistoryChainV1",
            "MAX_PAGE_BYTES",
            "MAX_ARCHIVE_BYTES",
        ),
    ),
    (
        financial_v2.bills.history,
        (
            "_deny",
            "_same_json",
            "_time",
            "_chain",
            "_family",
            "_lineages",
            "_page_metadata",
            "_joined_pages",
            "_union",
            "_has_measured_overlap",
            "_coverage",
            "verify_history_query_chain",
            "verify_history_query_chain_v2",
            "verify_algo_history_query_chain_v6",
            "_verify",
            "compare_history_query_chains",
            "compare_history_query_chains_v2",
            "_compare_history_query_chains",
        ),
    ),
    (
        capture,
        (
            "stream_family",
            "_fail",
            "_bounded_api",
            "_utc",
            "_sha",
            "_canonical",
            "streams_for_plan",
            "is_all_product_plan",
            "_guard",
            "_plain",
            "_copy",
            "_json_value",
            "_digest_record",
            "plan_sha256",
            "_copy_plan",
            "_checked_plan",
            "_milliseconds",
            "account_request",
            "_pairs",
            "_invalid_json_number",
            "_json_tree",
            "_decode_json",
            "_required_text",
            "_fill_identifiers",
            "_time_record",
            "_gateway_time",
            "_number_record",
            "_row_record",
            "parse_demo_account_observation",
            "_replay_observation",
            "verify_demo_account_records",
            "_replay_packet",
            "freeze_demo_account_packet",
            "verify_demo_account_packet",
        ),
    ),
    (
        lifecycle,
        (
            "deny",
            "_json",
            "_utc",
            "_number",
            "_fraction",
            "source_set_sha256",
            "_index",
            "_packet",
            "_source_rows",
            "_lifecycle",
            "replay_account_lifecycle_history",
            "checked_bootstrap",
            "LedgerScope",
            "_membership",
            "reduce_inventory",
            "Fraction",
            "Decimal",
            "journal",
            "observed",
            "capture",
            "POLICY_BYTES",
            "POLICY_SHA256",
        ),
    ),
    (
        observed,
        (
            "deny",
            "reference_document",
            "window_document",
            "source_reference",
            "_pins",
            "_amount",
            "_fraction",
            "_compare_verified",
            "_typed_receipt",
            "_compare_verified_typed",
            "_compare_verified_internal",
            "replay_observation_index",
            "replay_observation_index_typed_v1",
            "_replay",
            "checked_bootstrap",
            "LedgerScope",
            "Decimal",
            "Fraction",
            "capture",
            "journal",
            "query",
            "MAX_CAPTURES",
            "MAX_ROWS",
            "MAX_SOURCE_BYTES",
            "MAX_RECEIPT_BYTES",
            "POLICY_BYTES",
            "POLICY_SHA256",
            "TYPED_POLICY_BYTES",
            "TYPED_POLICY_SHA256",
        ),
    ),
    (
        financial_v2.bills.journal,
        (
            "digest",
            "_sha",
            "canonical",
            "_check_owned_packet_for_storage",
            "checked_event",
            "_failure",
            "bounded_finalization",
            "start_owned_journal",
            "capture",
        ),
    ),
    (
        financial_reservations,
        (
            "_quote_scalar_guard",
            "checked_bootstrap",
            "checked",
            "canonical",
            "digest",
            "decode",
            "checked_reservation_request",
            "decode_reservation_request",
            "_ceil",
            "exact_coverage",
            "claim_stamps",
            "validate_claims",
            "reservation_id",
            "_check_original_range_location",
            "_check_range_reservation_admission",
            "replay_range_reservation",
            "_require_owned_recheck_for_reservation",
            "prepare_reservation",
            "LedgerScope",
        ),
    ),
    (inventory_math, ("_positive_native_fraction", "reduce_inventory")),
)
_FUNCTION_PINS += tuple(
    (module, name, getattr(module, name))
    for module, names in _V2_DEPENDENCY_NAMES
    for name in names
)
_FUNCTION_PINS += (
    (sys.modules[__name__], "canonical", canonical),
    (sys.modules[__name__], "sha", sha),
    (sys.modules[__name__], "utc_from_ns", utc_from_ns),
    (sys.modules[__name__], "datetime", datetime),
    (sys.modules[__name__], "financial_v2", financial_v2),
    (sys.modules[__name__], "history_bundle", history_bundle),
    (sys.modules[__name__], "history", history),
    (sys.modules[__name__], "coverage", coverage),
    (sys.modules[__name__], "observed", observed),
    (sys.modules[__name__], "lifecycle", lifecycle),
    (sys.modules[__name__], "windows", windows),
    (sys.modules[__name__], "portfolio", portfolio),
    (sys.modules[__name__], "capture", capture),
    (sys.modules[__name__], "_financial_lifetimes_v2", _financial_lifetimes_v2),
    (
        sys.modules[__name__],
        "_calculate_financial_operands_v2",
        _calculate_financial_operands_v2,
    ),
    (
        sys.modules[__name__],
        "consume_owned_native_account_financial_operands_v2",
        consume_owned_native_account_financial_operands_v2,
    ),
    (
        sys.modules[__name__],
        "NativeAccountFinancialOperandsV2",
        NativeAccountFinancialOperandsV2,
    ),
    (
        financial_v2.positions._NativePositionIdentityV1,
        "__post_init__",
        financial_v2.positions._NativePositionIdentityV1.__post_init__,
    ),
    (
        financial_v2.positions._NativePositionIdentityV1,
        "sha256",
        financial_v2.positions._NativePositionIdentityV1.sha256,
    ),
    (
        financial_v2.position_pages.PositionHistoryPlanV1,
        "__post_init__",
        financial_v2.position_pages.PositionHistoryPlanV1.__post_init__,
    ),
    (
        financial_v2.position_pages.PositionHistoryPlanV1,
        "sha256",
        financial_v2.position_pages.PositionHistoryPlanV1.sha256,
    ),
)
