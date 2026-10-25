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
from datetime import timedelta as _V3Timedelta
from pathlib import Path
from threading import get_ident
from types import FunctionType as _V3FunctionType
from types import MappingProxyType as _V3MappingProxyType
from types import MethodType as _V3MethodType
from weakref import WeakKeyDictionary, ref

from sqlalchemy.sql.elements import TextClause as _V3TextClause

import app.database.repositories.account_capture_journal as _v3_module_0
import app.database.repositories.account_history_partition as _v3_module_1
import app.database.repositories.account_observation_index as _v3_module_2
import app.database.repositories.public_runtime_bundle_custody as _v3_module_3
import app.database.repositories.qualification_ledger as _v3_module_4
import app.domain.account_history_partition_catalog_v1 as _v3_module_5
import app.domain.account_history_partition_v1 as _v3_module_6
import app.domain.source_primitives as _v3_module_7
import app.trade_qualification.account_current_source_v4 as _v3_module_8
import app.trade_qualification.account_financial_operands_v2 as _v3_module_9
import app.trade_qualification.account_history_native_stage_v1 as _v3_module_10
import app.trade_qualification.account_history_risk_v8 as _v3_module_11
import app.trade_qualification.account_history_source_bundle_v1 as _v3_module_12
import app.trade_qualification.account_history_source_v1 as _v3_module_13
import app.trade_qualification.account_lifecycle_history as _v3_module_14
import app.trade_qualification.account_native_proof as _v3_module_15
import app.trade_qualification.account_native_proof_storage as _v3_module_16
import app.trade_qualification.account_native_runtime as _v3_module_17
import app.trade_qualification.account_observation_index as _v3_module_18
import app.trade_qualification.account_risk_windows_v1 as _v3_module_19
import app.trade_qualification.controlled_writer_coverage_v1 as _v3_module_20
import app.trade_qualification.portfolio as _v3_module_21
import app.trade_qualification.reservations as _v3_module_22
import scripts.verify_account_history_partition_schema_v1 as _v3_module_23
from app.database.repositories import (
    account_history_partition as _v3_partition_repository,
)
from app.database.repositories import public_runtime_bundle_custody as custody
from app.domain import account_history_partition_catalog_v1 as _v3_partition_policy
from app.domain import account_history_partition_v1 as _v3_partition_records
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
from app.trade_qualification import native_financial_timing_v1 as _native_timing
from app.trade_qualification import portfolio
from app.trade_qualification import reservations as financial_reservations
from app.trade_qualification.account_runtime import ControlledDemoAccountSession
from scripts import verify_account_history_partition_schema_v1 as _v3_partition_adapter

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
    if _native_timing.ENABLED:
        _native_timing.begin("inventory.financial")
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
        if _native_timing.ENABLED:
            _native_timing.source_file("inventory.financial", len(raw))
        if rows.get(name) != sha(raw):
            _deny("native_financial_packaged_source_changed")
        files[name] = pin
    if _native_timing.ENABLED:
        _native_timing.end("inventory.financial")
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


# V3 is a separate owner and policy. Original V1/V2 contracts above stay exact.
_V3_OWNERS = WeakKeyDictionary()
_V3_SOURCE_PATHS = tuple(
    sorted(
        set(SOURCE_PATHS)
        | {
            "app/database/models/__init__.py",
            "app/database/models/account_history_partition.py",
            "app/database/repositories/account_history_partition.py",
            "app/domain/account_history_partition_v1.py",
            "app/domain/account_history_partition_catalog_v1.py",
            "migrations/versions/0050_account_history_partition.py",
            "scripts/verify_account_history_partition_schema_v1.py",
            "docs/native_account_financial_operands_v3.md",
        }
    )
)
_V3_POLICY_BYTES = canonical(
    {
        "schema_version": "ctcc.native_account_financial_operands.policy.v3",
        "source_paths": list(_V3_SOURCE_PATHS),
        "native_registration": "same_invocation_latest_only",
        "original_lifetimes": "unchanged_shortest_history_coverage_current_control",
        "cross_database_atomicity": False,
        "unknown_commit": "burn_retain_no_retry",
        "capture33_streaming_implemented": False,
        "account_complete": False,
        "execution_authority": False,
    }
)
_V3_POLICY_SHA256 = sha(_V3_POLICY_BYTES)


class OwnedNativeAccountFinancialOperandsV3:
    __slots__ = ("__weakref__",)

    def __new__(cls):
        _deny("native_financial_v3_owner_not_constructible")

    def __copy__(self):
        _deny("native_financial_v3_owner_not_transferable")

    def __deepcopy__(self, memo):
        _deny("native_financial_v3_owner_not_transferable")

    def __reduce_ex__(self, protocol):
        _deny("native_financial_v3_owner_not_transferable")


@dataclass(frozen=True, slots=True, repr=False)
class NativeAccountFinancialOperandsV3:
    """Portable diagnostic facts, never an account revision or capability."""

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


def _v3_data_pin(value, depth=0):
    """Pin plain source data structurally; never invoke a foreign serializer."""
    if depth > 24:
        _deny("native_financial_v3_policy_depth")
    kind = type(value)
    if value is None or kind in (str, bytes, int, bool, float):
        return kind, value
    if kind in (tuple, list):
        return kind, tuple(_v3_data_pin(part, depth + 1) for part in value)
    if kind in (dict, _V3MappingProxyType):
        return kind, tuple(
            (_v3_data_pin(key, depth + 1), _v3_data_pin(part, depth + 1))
            for key, part in value.items()
        )
    if kind in (set, frozenset):
        return kind, frozenset(_v3_data_pin(part, depth + 1) for part in value)
    if kind is _V3TextClause:
        return (
            kind,
            value.text,
            tuple(
                (
                    key,
                    id(part.type),
                    _v3_data_pin(part.value, depth + 1),
                    part.required,
                    part.expanding,
                )
                for key, part in value._bindparams.items()
            ),
        )
    return "exact_identity", id(value)


def _v3_callable_pin(value):
    if type(value) is _V3MethodType:
        original = value.__func__
        return (
            original,
            original.__code__,
            original.__globals__,
            (id(value.__self__), _v3_data_pin(original.__defaults__)),
            _v3_data_pin(original.__kwdefaults__),
        )
    if type(value) is property:
        return (
            value,
            value.fget.__code__ if value.fget is not None else None,
            value.fget.__globals__ if value.fget is not None else None,
            _v3_data_pin(value.fset),
            _v3_data_pin(value.fdel),
        )
    if type(value) is _V3FunctionType:
        return (
            value,
            value.__code__,
            value.__globals__,
            _v3_data_pin(value.__defaults__),
            _v3_data_pin(value.__kwdefaults__),
        )
    return value, None, None, None, None


def _v3_check_declared():
    for owner, name, expected in _V3_PIN_FUNCTIONS:
        found = _v3_callable_pin(getattr(owner, name))
        if (
            found[0] is not expected[0]
            or found[1] is not expected[1]
            or found[2] is not expected[2]
            or found[3:] != expected[3:]
        ):
            _deny("native_financial_v3_function_or_alias_changed")
    for owner, name, expected in _V3_PIN_DATA:
        if _v3_data_pin(getattr(owner, name)) != expected:
            _deny("native_financial_v3_policy_or_sql_changed")
    for name in (
        "AccountHistoryPartitionError",
        "expected_account_history_partition_schema",
        "verify_account_history_partition_catalog",
        "canonical",
        "digest",
    ):
        if getattr(_v3_partition_repository, name) is not getattr(
            _v3_partition_policy, name
        ) or getattr(_v3_partition_adapter, name) is not getattr(
            _v3_partition_policy, name
        ):
            _deny("native_financial_v3_partition_policy_alias_changed")
    if (
        _v3_partition_policy.canonical is not _v3_partition_records.canonical
        or _v3_partition_policy.digest is not _v3_partition_records.digest
        or _v3_partition_policy.expected_account_history_partition_schema.__globals__
        is not vars(_v3_partition_policy)
        or _v3_partition_policy.verify_account_history_partition_catalog.__globals__
        is not vars(_v3_partition_policy)
        or _v3_partition_records.canonical.__globals__
        is not vars(_v3_partition_records)
        or _v3_partition_records.digest.__globals__ is not vars(_v3_partition_records)
    ):
        _deny("native_financial_v3_partition_policy_globals_changed")


def _package_inventory_v3():
    if _native_timing.ENABLED:
        _native_timing.begin("inventory.v3")
    original = _package_inventory()
    manifest, manifest_pin = coverage._read_file(
        _ROOT / "MANIFEST.sha256", 2 * 1024 * 1024
    )
    rows = {}
    for line in manifest.decode("ascii").splitlines():
        digest, separator, name = line.partition("  ")
        if not separator or coverage._SHA.fullmatch(digest) is None or name in rows:
            _deny("native_financial_v3_manifest_invalid")
        rows[name] = digest
    files = {}
    for name in _V3_SOURCE_PATHS:
        raw, pin = coverage._read_file(_ROOT / name, 4 * 1024 * 1024)
        if _native_timing.ENABLED:
            _native_timing.source_file("inventory.v3", len(raw))
        if rows.get(name) != sha(raw):
            _deny("native_financial_v3_packaged_source_changed")
        files[name] = pin
    if _native_timing.ENABLED:
        _native_timing.end("inventory.v3")
    return {
        "original": original,
        "manifest": manifest_pin,
        "manifest_sha256": sha(manifest),
        "files": files,
        "source_sha256": sha(canonical({name: pin[2] for name, pin in files.items()})),
    }


def _check_v3(state):
    _v3_check_declared()
    actual = _context()
    expected = state["v3_context"]
    if actual[:3] != expected[:3] or actual[3] is not expected[3]():
        _deny("native_financial_v3_context_changed")
    if _package_inventory_v3() != state["v3_inventory"]:
        _deny("native_financial_v3_source_inventory_changed")
    if "context" in state:
        _check(state)
    if "v3_repository" in state:
        repository = state["v3_repository"]
        native = history._financial_state_v1(state["history"], key=_HOOK_KEY)
        if (
            type(repository)
            is not _v3_partition_repository.AccountHistoryPartitionRepository
            or repository.session_factory is not native["factory"]
            or repository.clock is not state["v3_repository_clock"]
            or type(repository._poisoned) is not bool
        ):
            _deny("native_financial_v3_partition_adapter_changed")
    if "v3_anchors" in state:
        anchors, stamp = _financial_lifetimes_v2(state, state["v3_view"])
        if anchors != state["v3_anchors"]:
            _deny("native_financial_v3_original_lifetimes_changed")
        return stamp
    return None


def _release_v3(state):
    # The original release attempts both bindings and clears even after failure.
    _release(state)


def _move_assembly_owner_v3(owner, initial):
    state = (
        _OWNERS.pop(owner, None)
        if type(owner) is OwnedNativeAccountRiskAssemblyV1
        else None
    )
    if state is None:
        _deny("native_financial_v3_original_owner_unavailable")
    try:
        state.update(initial)
        _check_v3(state)
        if (
            state["inventory"]["manifest_sha256"]
            != state["v3_inventory"]["manifest_sha256"]
        ):
            _deny("native_financial_v3_original_manifest_changed")
        return state
    except BaseException:
        _release_v3(state)
        raise


async def acquire_native_account_risk_assembly_v3(
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
    """Measure V3 union before V1 acquisition; MOVE in the same original task."""
    original_owner = None
    state = None
    successful = False
    try:
        _v3_check_declared()
        context = _context()
        initial = {
            "v3_context": (*context[:3], ref(context[3])),
            "v3_inventory": _package_inventory_v3(),
        }
        _check_v3(initial)
        if len(_V3_OWNERS) >= _MAX_OWNERS:
            _deny("native_financial_v3_owner_bound")
        original_owner = await acquire_native_account_risk_assembly_v1(
            read_session=read_session,
            private_client=private_client,
            scope_config=scope_config,
            current_account_plan=current_account_plan,
            session_factory=session_factory,
            custody_session_factory=custody_session_factory,
            history_raw_root=history_raw_root,
            history_companion_root=history_companion_root,
            account_proof_root=account_proof_root,
            account_native_root=account_native_root,
        )
        # No await or caller exposure between receiving and burning the V1 owner.
        state = _move_assembly_owner_v3(original_owner, initial)
        original_owner = None
        _check_v3(state)
        owner = object.__new__(OwnedNativeAccountFinancialOperandsV3)
        _V3_OWNERS[owner] = state
        successful = True
        return owner
    except asyncio.CancelledError:
        raise
    except NativeAccountRiskAssemblyError:
        raise
    except Exception:  # noqa: BLE001 -- never leak account, source or credential errors.
        _deny("native_financial_v3_acquisition_failed")
    finally:
        try:
            if original_owner is not None:
                remaining = _OWNERS.pop(original_owner, None)
                if remaining is not None:
                    _release_v3(remaining)
        finally:
            if state is not None and not successful:
                _release_v3(state)


def _v3_scope(facts):
    return _v3_partition_records.HistoryPartitionScopeV1(
        facts.scope.environment,
        facts.scope.account_id,
        facts.scope.settlement_currency,
    )


def _partition_fences_v3(facts, view):
    observation = view["coverage"]
    raw = observation.audit_json
    if type(raw) is not str or sha(raw.encode("ascii")) != observation.audit_sha256:
        _deny("native_financial_v3_coverage_audit_changed")
    audit = history_bundle._json(raw.encode("ascii"), 262144)
    if (
        audit["environment"] != facts.scope.environment
        or audit["account_id"] != facts.scope.account_id
        or audit["requested_settlement_currency"] != "USDT"
        or type(audit["currency_states"]) is not list
    ):
        _deny("native_financial_v3_account_scope_changed")
    states = []
    currencies = set()
    for row in audit["currency_states"]:
        item = financial_reservations.LedgerScopeState.model_validate_json(
            canonical(row),
            strict=True,
        )
        financial_reservations.checked_bootstrap(
            item, financial_reservations.LedgerScopeState
        )
        scope = item.scope
        if (
            scope.environment != facts.scope.environment
            or scope.account_id != facts.scope.account_id
            or scope.settlement_currency in currencies
        ):
            _deny("native_financial_v3_currency_state_conflict")
        currencies.add(scope.settlement_currency)
        if scope.settlement_currency == "USDT":
            states.append(item)
    revision = view["control"].state.revision
    if len(states) != 1 or type(revision) is not int or revision < 1:
        _deny("native_financial_v3_account_revision_unknown")
    return revision, states[0].account_revision, states[0].ledger_revision


def _latest_native_linkage_v3(state, facts):
    _check_v3(state)
    native = history._financial_state_v1(state["history"], key=_HOOK_KEY)
    original = history_bundle._json(facts.bundle_raw)
    temporal = original["temporal"]
    document = history_bundle._json(
        facts.batches[-1].document_json, observed.MAX_SOURCE_BYTES
    )
    reference = observed.reference_document(facts.reference)
    if (
        facts.bundle_sha256 != sha(facts.bundle_raw)
        or native["source_sha256"] != facts.bundle_sha256
        or native["facts"].bundle_raw != facts.bundle_raw
        or original["policy_sha256"] != history_bundle.POLICY_SHA256
        or native["original"] != temporal["original_started"]
        or native["custody_proof_sha256"] != facts.proof_sha256
        or facts.proof_sha256 != sha(history.journal.canonical(temporal))
        or native["reference"] != facts.reference
        or document["reference"] != reference
        or document["sequence"] != facts.batches[-1].sequence
        or sha(facts.batches[-1].document_json) != facts.batches[-1].event_sha256
    ):
        _deny("native_financial_v3_original_linkage_changed")
    linkage = {
        "schema_version": _v3_partition_records.LINKAGE_SCHEMA,
        "scope": [
            facts.scope.environment,
            facts.scope.account_id,
            facts.scope.settlement_currency,
        ],
        "b3_sequence": facts.batches[-1].sequence,
        "b3_event_sha256": facts.batches[-1].event_sha256,
        "b3_anchor_sha256": document["anchor_sha256"],
        "latest_source_reference": reference,
        "native_invocation_id": temporal["stage"]["invocation_id"],
        "custody_bundle_id": native["custody_id"],
        "bundle_sha256": facts.bundle_sha256,
        "proof_sha256": facts.proof_sha256,
        "native_policy_sha256": original["policy_sha256"],
        "source_manifest_sha256": state["v3_inventory"]["manifest_sha256"],
        "raw_inventory": {
            name: list(pin) for name, pin in native["raw_inventory"].items()
        },
        "companion_inventory": {
            name: list(pin) for name, pin in native["companion_inventory"].items()
        },
        "raw_root": {
            "path": str(native["raw_root"]),
            "device": native["root_identity"][0],
            "inode": native["root_identity"][1],
        },
        "companion_root": {
            "path": str(native["companion_root"]),
            "device": native["companion_identity"][0],
            "inode": native["companion_identity"][1],
        },
        "original_started": dict(native["original"]),
        "native_started": dict(temporal["stage"]["started"]),
        "source_readback_complete": dict(temporal["source_readback_complete"]),
    }
    raw = _v3_partition_records.canonical(linkage)
    record = _v3_partition_records.derive_native_member_registration_v1(
        scope=_v3_scope(facts),
        b3_document_raw=facts.batches[-1].document_json,
        native_linkage_raw=raw,
        expected_b3_policy_sha256=document["policy_sha256"],
        expected_b3_event_sha256=facts.batches[-1].event_sha256,
    )
    return raw, record, document["policy_sha256"]


def _v3_partition_readback(state, facts, view, readback):
    _check_v3(state)
    documents = tuple(batch.document_json for batch in facts.batches)
    fences = _partition_fences_v3(facts, view)
    if (
        type(readback) is not _v3_partition_repository.HistoryPartitionReadbackV1
        or readback.scope != _v3_scope(facts)
        or readback.b3_documents != documents
        or readback.head_sequence != facts.batches[-1].sequence
        or readback.head_sha256 != facts.batches[-1].event_sha256
        or (
            readback.control_revision,
            readback.account_revision,
            readback.ledger_revision,
        )
        != fences
        or readback.account_complete is not False
        or readback.execution_authority is not False
    ):
        _deny("native_financial_v3_partition_sources_changed")
    if tuple(row.b3_sequence for row in readback.members) != tuple(
        sorted({row.b3_sequence for row in readback.members})
    ):
        _deny("native_financial_v3_member_identity_conflict")
    return fences


def _partition_checkpoint_v3(facts, before, registration, source_policy):
    head = facts.batches[-1].sequence
    if any(row.b3_sequence == head for row in before.members):
        _deny("native_financial_v3_existing_member_not_adoptable")
    if head % _v3_partition_records.PARTITION_SPAN:
        return None
    if head != _v3_partition_records.PARTITION_SPAN:
        # V1 source cannot acquire capture33. Only separate Slice B may advance.
        _deny("native_financial_v3_streaming_source_unavailable")
    known = {row.b3_sequence: row.record_json for row in before.members}
    known[head] = registration.record_json
    proposal = _v3_partition_records.build_partition_checkpoint_v1(
        scope=_v3_scope(facts),
        b3_documents=before.b3_documents[-32:],
        native_registrations=tuple(
            known.get(number) for number in range(head - 31, head + 1)
        ),
        previous_checkpoints=tuple(row.record_json for row in before.checkpoints),
        expected_b3_policy_sha256=source_policy,
        expected_b3_head_sha256=facts.batches[-1].event_sha256,
    )
    return proposal.record_json


async def _replay_partition_sources_v3(state, facts=None, view=None):
    _check_v3(state)
    fresh_facts, fresh_view = await _replay(state)
    _check_v3(state)
    source_identity = (
        fresh_facts.bundle_raw,
        tuple(batch.document_json for batch in fresh_facts.batches),
        canonical(observed.reference_document(fresh_view["current"].reference)),
        fresh_view["current"].current_receipt_json,
        fresh_view["coverage"].journal_sha256,
        fresh_view["coverage"].audit_json,
        _partition_fences_v3(fresh_facts, fresh_view),
        fresh_view["control"].state.emergency_stop,
        fresh_view["control"].state.arm_request_id,
    )
    if "v3_original_source_identity" not in state:
        state["v3_original_source_identity"] = source_identity
    elif source_identity != state["v3_original_source_identity"]:
        _deny("native_financial_v3_original_sources_changed")
    anchors, _stamp = _financial_lifetimes_v2(state, fresh_view)
    if "v3_anchors" not in state:
        state["v3_anchors"] = anchors
        state["v3_view"] = fresh_view
    elif anchors != state["v3_anchors"]:
        _deny("native_financial_v3_original_lifetimes_changed")
    if facts is not None and (
        fresh_facts.bundle_raw != facts.bundle_raw
        or tuple(batch.document_json for batch in fresh_facts.batches)
        != tuple(batch.document_json for batch in facts.batches)
        or fresh_view["current"].reference != view["current"].reference
        or fresh_view["current"].current_receipt_json
        != view["current"].current_receipt_json
        or fresh_view["coverage"].journal_sha256 != view["coverage"].journal_sha256
        or fresh_view["coverage"].audit_json != view["coverage"].audit_json
        or fresh_view["control"].state != view["control"].state
        or _partition_fences_v3(fresh_facts, fresh_view)
        != _partition_fences_v3(facts, view)
    ):
        _deny("native_financial_v3_original_sources_changed")
    _check_v3(state)
    return fresh_facts, fresh_view


def _v3_readback_identity(value):
    return (
        value.scope,
        value.head_sequence,
        value.head_sha256,
        value.control_revision,
        value.account_revision,
        value.ledger_revision,
        value.b3_documents,
        tuple(
            (row.b3_sequence, row.record_json, row.record_sha256, row.recorded_at)
            for row in value.members
        ),
        tuple(
            (row.partition_number, row.record_json, row.record_sha256, row.recorded_at)
            for row in value.checkpoints
        ),
    )


async def _register_latest_partition_v3(state, facts, view):
    _check_v3(state)
    native = history._financial_state_v1(state["history"], key=_HOOK_KEY)
    original_factory = native["factory"]
    factory_pin = history._factory_pin(original_factory)

    def original_clock():
        return utc_from_ns(_check_v3(state)["utc_ns"])

    repository = _v3_partition_repository.AccountHistoryPartitionRepository(
        original_factory, clock=original_clock
    )
    state["v3_repository"] = repository
    state["v3_repository_clock"] = original_clock
    await repository.verify_deployment()
    _check_v3(state)
    before = await repository.read(_v3_scope(facts))
    _check_v3(state)
    fences = _v3_partition_readback(state, facts, view, before)
    linkage, registration, source_policy = _latest_native_linkage_v3(state, facts)
    checkpoint = _partition_checkpoint_v3(facts, before, registration, source_policy)
    facts, view = await _replay_partition_sources_v3(state, facts, view)
    if history._factory_pin(original_factory) != factory_pin:
        _deny("native_financial_v3_partition_factory_changed")
    # UTC expiry only shortens the original monotonic lifetimes. No renewal.
    anchors = state["v3_anchors"]
    expiry = min(
        view["current"].expires_at,
        view["control"].state.lease_until,
        utc_from_ns(anchors["history_original_anchor"]["utc_ns"])
        + _V3Timedelta(seconds=60),
        utc_from_ns(anchors["coverage_original_anchor"]["utc_ns"])
        + _V3Timedelta(seconds=60),
    )
    _check_v3(state)
    # CORE burns its sole attempt synchronously, then commits/closes and opens a
    # distinct main session/transaction for its exact independent readback.
    committed = await repository.append_member_and_checkpoint(
        _v3_scope(facts),
        b3_document_raw=facts.batches[-1].document_json,
        native_linkage_raw=linkage,
        expected_b3_policy_sha256=source_policy,
        expected_b3_event_sha256=facts.batches[-1].event_sha256,
        expected_previous_checkpoint_sha256=before.checkpoints[-1].record_sha256
        if before.checkpoints
        else None,
        expected_control_revision=fences[0],
        expected_account_revision=fences[1],
        expected_ledger_revision=fences[2],
        expires_at=expiry,
        checkpoint_raw=checkpoint,
    )
    _check_v3(state)
    _v3_partition_readback(state, facts, view, committed)
    matched = tuple(
        row
        for row in committed.members
        if row.b3_sequence == facts.batches[-1].sequence
    )
    expected_members = (*before.members, *matched)
    expected_checkpoints = (
        before.checkpoints
        if checkpoint is None
        else (*before.checkpoints, committed.checkpoints[-1])
    )
    if (
        len(matched) != 1
        or matched[0].record_json != registration.record_json
        or matched[0].record_sha256 != registration.record_sha256
        or committed.members != expected_members
        or committed.checkpoints != expected_checkpoints
        or (
            checkpoint is not None
            and committed.checkpoints[-1].record_json != checkpoint
        )
        or committed.transaction_id == before.transaction_id
    ):
        _deny("native_financial_v3_committed_registration_changed")
    # Replay the complete original source/custody in the separate DB after main
    # close/commit. A mismatch preserves durable rows and grants no result.
    facts, view = await _replay_partition_sources_v3(state, facts, view)
    final = await repository.read(_v3_scope(facts))
    _check_v3(state)
    _v3_partition_readback(state, facts, view, final)
    if (
        _v3_readback_identity(final) != _v3_readback_identity(committed)
        or final.transaction_id == committed.transaction_id
    ):
        _deny("native_financial_v3_final_main_readback_changed")
    if history._factory_pin(original_factory) != factory_pin:
        _deny("native_financial_v3_partition_factory_changed")
    return facts, view, registration, checkpoint, final


async def _consume_financial_v3(state):
    try:
        facts, view = await _replay_partition_sources_v3(state)
        _anchors, stamp = _financial_lifetimes_v2(state, view)
        remaining = (
            state["v3_anchors"]["shorter_monotonic_deadline_ns"] - stamp["monotonic_ns"]
        ) / 1e9
        if remaining <= 0:
            _deny("native_financial_v3_original_deadline_expired")
        async with asyncio.timeout(remaining):
            if _native_timing.ENABLED:
                _native_timing.begin("v3.register")
            (
                facts,
                view,
                registration,
                checkpoint,
                final,
            ) = await _register_latest_partition_v3(state, facts, view)
            if _native_timing.ENABLED:
                _native_timing.end("v3.register")
            _check_v3(state)
            if _native_timing.ENABLED:
                _native_timing.begin("v3.calculate")
            operands, derived, evaluated, anchors = _calculate_financial_operands_v2(
                state, facts, view
            )
            if _native_timing.ENABLED:
                _native_timing.end("v3.calculate")
            if _native_timing.ENABLED:
                _native_timing.begin("v3.replay")
            await _replay_partition_sources_v3(state, facts, view)
            if _native_timing.ENABLED:
                _native_timing.end("v3.replay")
            if _native_timing.ENABLED:
                _native_timing.begin("v3.original_sources")
            original_sources = _v3_original_b1_sources(state, facts, view)
            if _native_timing.ENABLED:
                _native_timing.end("v3.original_sources")
            if _native_timing.ENABLED:
                _native_timing.begin("v3.final_fence")
            fence = await state["v3_repository"].read_final_source_fence(
                _v3_scope(facts),
                capture_ids=tuple(
                    reference.capture_id for _chain, reference in original_sources
                ),
            )
            if _native_timing.ENABLED:
                _native_timing.end("v3.final_fence")
            # This is the sole last main DB observation. No later separate
            # database await can make these original B1/B3/DB50 fences stale.
            final = _v3_verify_final_main_fence(
                state, facts, view, original_sources, final, fence
            )
            _check_v3(state)
            receipt = canonical(
                {
                    "schema_version": "ctcc.native_account_financial_operands.v3",
                    "policy_sha256": _V3_POLICY_SHA256,
                    "financial_policy_sha256": financial_v2.POLICY_SHA256,
                    "source_inventory_sha256": state["v3_inventory"]["source_sha256"],
                    "source_manifest_sha256": state["v3_inventory"]["manifest_sha256"],
                    "history_bundle_sha256": facts.bundle_sha256,
                    "history_index_head_sha256": facts.batches[-1].event_sha256,
                    "current_packet_sha256": view["current"].reference.packet_sha256,
                    "coverage_journal_sha256": view["coverage"].journal_sha256,
                    "latest_native_registration_sha256": registration.record_sha256,
                    "latest_partition_checkpoint_sha256": None
                    if checkpoint is None
                    else sha(checkpoint),
                    "partition_readback_sha256": sha(
                        canonical(
                            {
                                "head": final.head_sha256,
                                "members": [row.record_sha256 for row in final.members],
                                "checkpoints": [
                                    row.record_sha256 for row in final.checkpoints
                                ],
                                "revisions": [
                                    final.control_revision,
                                    final.account_revision,
                                    final.ledger_revision,
                                ],
                            }
                        )
                    ),
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
                    "capture33_streaming_implemented": False,
                    "cross_database_atomicity": False,
                    "final_main_snapshot_only": True,
                    "final_main_observed_at": fence.observed_at.isoformat(),
                    "final_main_B1_source_sha256": sha(
                        canonical(
                            [
                                [
                                    capture_id,
                                    [
                                        [
                                            sha(point.event.event_json),
                                            None
                                            if point.event.raw_body is None
                                            else sha(point.event.raw_body),
                                            None
                                            if point.event.packet_payload is None
                                            else sha(point.event.packet_payload),
                                            point.db_recorded_at.isoformat(),
                                        ]
                                        for point in chain
                                    ],
                                ]
                                for capture_id, chain in fence.b1_chains
                            ]
                        )
                    ),
                    "freshness_or_revision_renewed": False,
                    "missing_obligations": list(_MISSING),
                    "account_complete": False,
                    "account_revision_published": False,
                    "clear_stop_eligible": False,
                    "arm_eligible": False,
                    "execution_authority": False,
                    "admission": "DENY",
                }
            )
            if len(receipt) > observed.MAX_RECEIPT_BYTES:
                _deny("native_financial_v3_receipt_bound")
            _check_v3(state)
            return NativeAccountFinancialOperandsV3(
                operands, derived, evaluated, receipt
            )
    except asyncio.CancelledError:
        raise
    except NativeAccountRiskAssemblyError:
        raise
    except Exception:  # noqa: BLE001 -- uncertain commit/readback retains evidence, no retry.
        _deny("native_financial_v3_consumption_failed")
    finally:
        _release_v3(state)


class _NativeFinancialConsumptionV3:
    """Fixed awaitable releases burned sources when closed without awaiting."""

    __slots__ = ("_state", "_coroutine", "_started", "_closed")  # noqa: RUF023 -- preserve reviewed layout.

    def __init__(self, state):
        self._state = state
        self._coroutine = _consume_financial_v3(state)
        self._started = False
        self._closed = False

    def __await__(self):
        if self._started or self._closed:
            _deny("native_financial_v3_consumption_already_used")
        self._started = True
        return _await_financial_consumption_v3(self).__await__()

    def close(self):
        if not self._closed:
            self._closed = True
            try:
                self._coroutine.close()
            finally:
                if self._state:
                    _release_v3(self._state)

    def __del__(self):
        try:
            self.close()
        except BaseException:  # noqa: S110, BLE001 -- destructor cannot restore burned authority.
            # Destruction grants nothing; synchronous owner was already burned.
            pass


def consume_owned_native_account_financial_operands_v3(owner):
    """Synchronous one-use burn before guards or creating the fixed awaitable."""
    state = (
        _V3_OWNERS.pop(owner, None)
        if type(owner) is OwnedNativeAccountFinancialOperandsV3
        else None
    )
    if state is None:
        _deny("native_financial_v3_owner_unavailable")
    try:
        _check_v3(state)
        return _NativeFinancialConsumptionV3(state)
    except BaseException:
        _release_v3(state)
        raise


def _v3_original_b1_sources(state, facts, view):
    """Recover exact original current B1 bytes; never issue or refresh a source."""
    _check_v3(state)
    covered, _stamp = coverage._check_binding(state["coverage"])
    current_document = current.decode(
        covered["account"]["bundle_raw"], custody.MAX_BUNDLE_BYTES
    )
    current_chain = current._chain_replay(current_document["chain"])
    if observed.source_reference(current_chain) != view["current"].reference:
        _deny("native_financial_v3_current_b1_reference_changed")
    sources = (*facts.sources, (current_chain, view["current"].reference))
    capture_ids = tuple(reference.capture_id for _chain, reference in sources)
    if len(set(capture_ids)) != len(capture_ids):
        _deny("native_financial_v3_final_b1_identity_conflict")
    return sources


def _v3_b1_fence_identity(chain):
    if type(chain) is not tuple or not chain:
        _deny("native_financial_v3_final_b1_missing")
    result = []
    for point in chain:
        if (
            type(point) is not history.journal.JournalReadback
            or type(point.event) is not history.journal._JournalEvent
        ):
            _deny("native_financial_v3_final_b1_type_changed")
        point.__post_init__()
        result.append(
            (
                point.event.event_json,
                point.event.raw_body,
                point.event.packet_payload,
                point.db_recorded_at,
            )
        )
    return tuple(result)


def _v3_verify_final_main_fence(state, facts, view, original_sources, committed, fence):
    _check_v3(state)
    if (
        type(fence) is not _v3_partition_repository.HistoryFinalSourceFenceReadbackV1
        or fence.execution_authority is not False
        or fence.account_complete is not False
        or type(fence.b1_chains) is not tuple
        or len(fence.b1_chains) != len(original_sources)
    ):
        _deny("native_financial_v3_final_main_type_changed")
    final = fence.partition
    _v3_partition_readback(state, facts, view, final)
    if (
        _v3_readback_identity(final) != _v3_readback_identity(committed)
        or final.transaction_id == committed.transaction_id
        or fence.observed_at < final.observed_at
    ):
        _deny("native_financial_v3_final_main_fence_changed")
    for (old_chain, reference), entry in zip(
        original_sources, fence.b1_chains, strict=True
    ):
        if (
            type(entry) is not tuple
            or len(entry) != 2
            or entry[0] != reference.capture_id
        ):
            _deny("native_financial_v3_final_b1_identity_changed")
        actual = entry[1]
        if _v3_b1_fence_identity(actual) != _v3_b1_fence_identity(old_chain):
            _deny("native_financial_v3_final_b1_source_changed")
        if any(
            new.readback_at < old.readback_at
            for old, new in zip(old_chain, actual, strict=True)
        ):
            _deny("native_financial_v3_final_b1_readback_regressed")
    _check_v3(state)
    return final


async def _await_financial_consumption_v3(consumer):
    """This fixed coroutine retains the wrapper through every suspension."""
    try:
        return await consumer._coroutine
    finally:
        consumer._closed = True
        if consumer._state:
            _release_v3(consumer._state)


# Finite source-declared inventory; never inferred from a caller's owner or DTO.
_V3_DECLARED_BINDINGS = (
    (
        _v3_module_0,
        (
            "json",
            "re",
            "dataclass",
            "datetime",
            "select",
            "DemoAccountCaptureEvent",
            "QualificationLedgerRepository",
            "capture",
            "source_join",
            "funding_bills",
            "observed",
            "_ISSUER",
            "MAX_CHAIN_BYTES",
            "MAX_EVENTS",
            "AccountJournalError",
            "JournalReadback",
            "_JournalEvent",
            "canonical",
            "checked_event",
            "digest",
            "LedgerScope",
            "checked_bootstrap",
            "LockedAccountSourceJoinReadback",
            "AccountCaptureJournalRepository",
        ),
    ),
    (
        _v3_module_1,
        (
            "_final_fence_asyncio",
            "_final_fence_re",
            "_FinalFenceJournalRepository",
            "_final_fence_observed",
            "_FinalFenceLedgerScope",
            "_final_fence_checked_bootstrap",
            "dataclass",
            "UTC",
            "datetime",
            "text",
            "AsyncConnection",
            "AsyncSession",
            "async_sessionmaker",
            "policy",
            "records",
            "AccountHistoryPartitionError",
            "expected_account_history_partition_schema",
            "verify_account_history_partition_catalog",
            "canonical",
            "digest",
            "_APPEND_SQL",
            "_READ_SQL",
            "read_account_history_partition_catalog",
            "read_account_history_partition_retention",
            "HistoryNativeMemberReadbackV1",
            "HistoryCheckpointReadbackV1",
            "HistoryPartitionReadbackV1",
            "_utc",
            "_readback",
            "AccountHistoryPartitionRepository",
            "HistoryFinalSourceFenceReadbackV1",
            "_read_final_source_fence_controlled_v1",
            "_final_fence_partition_identity_v1",
            "_read_final_source_fence_v1",
        ),
    ),
    (
        _v3_module_2,
        (
            "json",
            "dataclass",
            "datetime",
            "timedelta",
            "and_",
            "func",
            "or_",
            "select",
            "HWMRow",
            "Batch",
            "Coverage",
            "Fact",
            "Finding",
            "AccountCaptureJournalRepository",
            "capture",
            "journal",
            "observed",
            "PAGE_SIZE",
            "MAX_REVISION",
            "ObservationBatchReadback",
            "BalanceSourcePage",
            "_keys",
            "_scope_hash",
            "_window",
            "_membership",
            "_bytes",
            "AccountObservationIndexRepository",
        ),
    ),
    (
        _v3_module_3,
        (
            "asyncio",
            "hashlib",
            "inspect",
            "os",
            "re",
            "ssl",
            "threading",
            "time",
            "dataclass",
            "field",
            "replace",
            "datetime",
            "CodeType",
            "FunctionType",
            "WeakKeyDictionary",
            "asyncpg",
            "text",
            "AsyncAdapt_asyncpg_dbapi",
            "PGDialect_asyncpg",
            "create_engine",
            "AsyncEngine",
            "AsyncSession",
            "async_sessionmaker",
            "Session",
            "AsyncAdaptedQueuePool",
            "NullPool",
            "immutabledict",
            "only_once",
            "MAX_BUNDLE_BYTES",
            "_SHA",
            "_ID",
            "_ISSUER",
            "_RECEIPTS",
            "_RECEIPT_LIFETIME_NS",
            "_BUNDLE_ID_DOMAIN",
            "_SAFE_CATALOG_SEARCH_PATH",
            "_SET_CATALOG_SEARCH_PATH_SQL",
            "_CATALOG_SEARCH_PATH_SQL",
            "_code",
            "_CONNECT_CODE",
            "_ON_CONNECT_CODE",
            "_FIRST_CONNECT_CODE",
            "_ONLY_ONCE_CODE",
            "_DIALECT_CONNECT_CODE",
            "_closure",
            "_native_connection_parameters",
            "_native_engine",
            "_FUNCTIONS",
            "_CONSTRAINT_DEFINITION_SHA256",
            "_ROLE_SQL",
            "_FUNCTION_SQL",
            "_TRIGGER_SQL",
            "_COLUMN_SQL",
            "_CONSTRAINT_SQL",
            "_DIAGNOSTIC_CATEGORIES",
            "_ROLE_DIAGNOSTIC_CONDITIONS",
            "RuntimeBundleCustodyError",
            "_failed_role_conditions",
            "_catalog_query",
            "_sha",
            "runtime_bundle_id_v1",
            "_pins",
            "_owner",
            "_Deployment",
            "RetainedRuntimeBundleReadback",
            "OwnedRuntimeBundleCustodyReceipt",
            "_consume_runtime_bundle_custody_receipt",
            "RuntimeBundleCustodyRepository",
            "__all__",
        ),
    ),
    (
        _v3_module_4,
        (
            "asyncio",
            "hashlib",
            "json",
            "time",
            "dataclass",
            "datetime",
            "timedelta",
            "Decimal",
            "Fraction",
            "func",
            "select",
            "text",
            "pg_insert",
            "defer",
            "QualificationAccountScope",
            "QualificationReservation",
            "QualificationReservationTransition",
            "DemoControlRepository",
            "bound",
            "ControlScope",
            "EVENT_OBSERVATION_VERSION",
            "LedgerEventObservation",
            "BUCKET_SETTLEMENT_CURRENCY",
            "MAX_PORTFOLIO_MARGIN_PCT",
            "MAX_PORTFOLIO_STOP_RISK_PCT",
            "MAX_SINGLE_POSITION_MARGIN_USDT",
            "MAX_TRADE_RISK_PCT",
            "require_aware",
            "AccountLedgerClaims",
            "LedgerScope",
            "LedgerScopeState",
            "QualificationLedgerError",
            "ReservationReceipt",
            "ReservationRequestV2",
            "ReservationRequestV3",
            "RiskCoverage",
            "canonical",
            "checked",
            "checked_bootstrap",
            "checked_reservation_request",
            "claim_stamps",
            "decode",
            "decode_reservation_request",
            "digest",
            "prepare_reservation",
            "reservation_id",
            "validate_claims",
            "_plain",
            "SubmissionExecutionBinding",
            "build_submission_intent",
            "replay_submission_intent",
            "_canonical_json_sha256",
            "_CONTROL_RESERVATION_READBACK_DB_TIMEOUT_MS",
            "_CONTROL_RESERVATION_READBACK_CANCEL_AFTER_SECONDS",
            "LedgerCaptureCheckpoint",
            "LedgerBootstrapCheckpoint",
            "PortfolioLocalCheckpoint",
            "_ConsumedEventJournalInspection",
            "_portfolio_checkpoint_wire",
            "_checkpoint_credential_session_binding",
            "_require_event_journal_session",
            "_EVENT_GUARD_FUNCTIONS",
            "_event_journal_schema_mismatch_detail",
            "_require_event_journal_schema",
            "_account_uid_lock_key",
            "_require_event_journal_write_schema",
            "QualificationLedgerRepository",
        ),
    ),
    (
        _v3_module_5,
        (
            "copy",
            "json",
            "re",
            "records",
            "PG17_DEPARSED_BASELINE",
            "PG17_DEPARSED_BASELINE_STATUS",
            "CATALOG_SCHEMA",
            "TABLES",
            "PARENTS",
            "SCOPE_COLUMNS",
            "MAX_READ_BYTES",
            "canonical",
            "digest",
            "AccountHistoryPartitionError",
            "deny",
            "_columns",
            "_SCOPE_CHECK",
            "TABLE_SPECS",
            "_array",
            "_table_sql",
            "TABLE_SQL",
            "INDEX_SQL",
            "UID_SQL",
            "_JSON_BODY",
            "_LOCK_BODY",
            "_MEMBER_BODY",
            "_JSON_CHECK_BODY",
            "_CHECKPOINT_BODY",
            "_APPEND_BODY",
            "_READ_BODY",
            "_IMMUTABLE_BODY",
            "_LINKAGE_KEYS",
            "_SUBSTITUTIONS",
            "_body",
            "FUNCTION_SPECS",
            "FUNCTION_SIGNATURES",
            "FUNCTION_SQL",
            "TRIGGER_SPECS",
            "_PAIR_BODY",
            "TRIGGER_SQL",
            "CATALOG_SQL",
            "ddl_identity",
            "expected_account_history_partition_schema",
            "_oid",
            "_rows",
            "deparser_sha256",
            "verify_account_history_partition_catalog",
            "verify_runtime_role",
        ),
    ),
    (
        _v3_module_6,
        (
            "hashlib",
            "json",
            "re",
            "dataclass",
            "UTC",
            "datetime",
            "timedelta",
            "PurePosixPath",
            "PureWindowsPath",
            "PARTITION_SPAN",
            "MAX_RECORD_BYTES",
            "MAX_CHAIN_BYTES",
            "MAX_JSON_NODES",
            "MAX_JSON_DEPTH",
            "MAX_SEQUENCE",
            "MEMBER_SCHEMA",
            "LINKAGE_SCHEMA",
            "CHECKPOINT_SCHEMA",
            "HistoryPartitionError",
            "_deny",
            "canonical",
            "digest",
            "POLICY_BYTES",
            "POLICY_SHA256",
            "_REFERENCE_KEYS",
            "_COUNTS",
            "_unknowns",
            "_hash",
            "_number",
            "_json",
            "HistoryPartitionScopeV1",
            "HistoryPartitionRecordV1",
            "_scope",
            "_reference",
            "_ascii",
            "_b3",
            "_inventory",
            "_root_locator",
            "derive_native_member_registration_v1",
            "_checkpoint",
            "_replay",
            "build_partition_checkpoint_v1",
            "replay_partition_checkpoints_v1",
        ),
    ),
    (
        _v3_module_7,
        (
            "hashlib",
            "json",
            "UTC",
            "datetime",
            "timedelta",
            "MappingProxyType",
            "Annotated",
            "BaseModel",
            "ConfigDict",
            "Field",
            "model_validator",
            "MAX_RAW",
            "MAX_TIMESTAMP_NS",
            "MAX_CLOCK_DEVIATION_NS",
            "MAX_CLOCK_BATCH_NS",
            "EPOCH",
            "Ns",
            "PublicReceiptError",
            "sha",
            "_plain",
            "canonical",
            "_DEMO_V2_ROUTES",
            "_DEMO_V2_ROLE_PATHS",
            "_DEMO_V2_USER_AGENTS",
            "_DEMO_V2_QUOTE_ORDER",
            "demo_public_v2_route",
            "demo_public_v2_headers",
            "demo_public_v3_time_headers",
            "demo_public_v3_time_policy_sha256",
            "demo_public_v2_policy_document",
            "demo_public_v2_policy_sha256",
            "demo_public_v2_quote_transport_sha256",
            "_pairs",
            "decode",
            "utc_from_ns",
            "ClockStamp",
            "validate_stamps",
            "PUBLIC_V4_CURRENT_SOURCE_POLICY",
            "PUBLIC_V4_CURRENT_SOURCE_POLICY_SHA256",
        ),
    ),
    (
        _v3_module_8,
        (
            "asyncio",
            "os",
            "dataclass",
            "field",
            "datetime",
            "Path",
            "get_ident",
            "Literal",
            "WeakKeyDictionary",
            "custody",
            "native_stamp",
            "canonical",
            "decode",
            "sha",
            "utc_from_ns",
            "validate_stamps",
            "replay_operational_proof_v4",
            "capture",
            "journal",
            "current",
            "native",
            "proof",
            "storage",
            "runtime",
            "observed",
            "operational",
            "public_storage",
            "ControlledDemoAccountSession",
            "_route_proof",
            "LedgerScope",
            "SCHEMA",
            "POLICY_BYTES",
            "POLICY_SHA256",
            "_CURRENT",
            "_INITIAL",
            "_MAX_NATIVE_WALL_SECONDS",
            "_InitialAccountScopeV4",
            "CurrentAccountSourceV4Error",
            "_deny",
            "OwnedCurrentDemoAccountSourceV4",
            "RecordedCurrentAccountBundleV4",
            "CurrentAccountOperandsV4",
            "_time",
            "_unhex",
            "_chain_document",
            "_chain_replay",
            "replay_current_account_bundle_v4",
            "_freeze_bundle",
            "_check_context",
            "_check_owner",
            "acquire_initial_demo_account_source_v4",
            "_acquire_native",
            "_constrain_budget",
            "_check_current",
            "consume_owned_current_demo_account_v4",
            "_take_original_frame_v1",
            "_replay_original_frame_v1",
            "_take_controlled_writer_coverage_v1",
            "_replay_controlled_writer_coverage_v1",
        ),
    ),
    (
        _v3_module_9,
        (
            "dataclass",
            "canonical",
            "sha",
            "capture",
            "bills",
            "risk",
            "lifecycle",
            "observed",
            "positions",
            "position_pages",
            "POLICY_BYTES",
            "POLICY_SHA256",
            "_MISSING",
            "AccountFinancialOperandsError",
            "_deny",
            "NativePositionSourceV2",
            "AccountFinancialOperandsV2",
            "_native_position_rows",
            "_bill_observations",
            "derive_account_financial_operands_v2",
        ),
    ),
    (
        _v3_module_10,
        (
            "asyncio",
            "hashlib",
            "json",
            "contextmanager",
            "WeakKeyDictionary",
            "httpx",
            "canonical",
            "sha",
            "utc_from_ns",
            "validate_stamps",
            "capture",
            "collector",
            "native",
            "_OBSERVERS",
            "MAX_POSITION_PAGES",
            "MAX_POSITION_RAW_BYTES",
            "MAX_POSITION_EVENTS",
            "HistoryNativeStageError",
            "_PositionObserver",
            "_state",
            "_positions_observer",
            "_clock",
            "_known_tokens",
            "_transport",
            "_event",
            "_begin",
            "_phase",
            "_chunk",
            "_response_closed",
            "_retain",
            "_flush",
            "_publish_retained",
            "_finished",
            "_abort",
        ),
    ),
    (
        _v3_module_11,
        (
            "json",
            "dataclass",
            "datetime",
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
            "AccountHistoryRiskError",
            "_deny",
            "PositionHistorySourceV8",
            "SettledFundingSourceV8",
            "AccountHistoryRiskOperandsV8",
            "_number",
            "_fraction",
            "_amount",
            "_time",
            "_position_rows",
            "_funding_sources",
            "_witnesses",
            "_run",
            "replay_account_history_risk_v8",
        ),
    ),
    (
        _v3_module_12,
        (
            "hashlib",
            "json",
            "re",
            "dataclass",
            "field",
            "datetime",
            "pairwise",
            "ObservationBatchReadback",
            "MAX_BUNDLE_BYTES",
            "MAX_TIMESTAMP_NS",
            "canonical",
            "sha",
            "utc_from_ns",
            "validate_stamps",
            "capture",
            "journal",
            "lifecycle",
            "native",
            "proof",
            "observed",
            "positions",
            "position_pages",
            "LedgerScope",
            "SCHEMA",
            "PROOF_SCHEMA",
            "MAX_JSON_DEPTH",
            "MAX_JSON_NODES",
            "POLICY_SHA256",
            "HistorySourceBundleError",
            "_deny",
            "_json",
            "_encoded_size",
            "_hex",
            "chain_document",
            "_row_skeleton",
            "_preflight_freeze",
            "_chain",
            "batch_document",
            "HistorySourceFactsV1",
            "freeze_history_source_bundle_v1",
            "replay_history_source_bundle_v1",
            "_positions_trace",
            "_trace_event",
            "_temporal",
            "_b1_temporal",
        ),
    ),
    (
        _v3_module_13,
        (
            "asyncio",
            "os",
            "re",
            "uuid",
            "dataclass",
            "timedelta",
            "get_ident",
            "WeakKeyDictionary",
            "ref",
            "AsyncSession",
            "async_sessionmaker",
            "custody",
            "AccountCaptureJournalRepository",
            "AccountObservationIndexRepository",
            "QualificationLedgerRepository",
            "canonical",
            "sha",
            "utc_from_ns",
            "validate_stamps",
            "DemoPinnedReadSession",
            "OkxDemoPrivateRestClient",
            "bootstrap",
            "capture",
            "journal",
            "collector",
            "stage_api",
            "bundle",
            "lifecycle",
            "native",
            "proof",
            "storage",
            "runtime",
            "observed",
            "positions",
            "position_pages",
            "ControlledDemoAccountSession",
            "LedgerScope",
            "_OWNERS",
            "_FINANCIAL",
            "_SESSION_USES",
            "_MAX_SESSION_TOMBSTONES",
            "_MAX_RAW_ROOT_BYTES",
            "_MAX_COMPANION_ROOT_BYTES",
            "NativeHistorySourceError",
            "_deny",
            "NativeHistoryScopeV1",
            "OwnedNativeHistorySourceFactsV1",
            "_FinancialHistoryBindingV1",
            "_context",
            "_factory_pin",
            "_clone_factory",
            "_check_pins",
            "_checked_await",
            "_native_plan",
            "_identity",
            "_population",
            "_same_population",
            "_part",
            "_check_original_wire_secrets",
            "_file_pin",
            "_produced_raw_inventory",
            "_inventory",
            "acquire_native_history_source_facts_v1",
            "_replay_history_state_v1",
            "consume_owned_native_history_source_facts_v1",
            "_financial_key_v1",
            "_take_financial_assembly_v1",
            "_financial_state_v1",
            "_release_financial_assembly_v1",
            "_replay_financial_assembly_v1",
        ),
    ),
    (
        _v3_module_14,
        (
            "json",
            "dataclass",
            "UTC",
            "datetime",
            "Decimal",
            "Fraction",
            "pairwise",
            "ObservationBatchReadback",
            "_membership",
            "InventoryMathError",
            "reduce_inventory",
            "capture",
            "journal",
            "observed",
            "LedgerScope",
            "checked_bootstrap",
            "POLICY_BYTES",
            "POLICY_SHA256",
            "AccountLifecycleHistoryError",
            "deny",
            "AccountLifecycleHistoryReplay",
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
        ),
    ),
    (
        _v3_module_15,
        (
            "re",
            "dataclass",
            "datetime",
            "pairwise",
            "urlsplit",
            "public_clock",
            "canonical",
            "decode",
            "sha",
            "utc_from_ns",
            "validate_stamps",
            "capture",
            "journal",
            "current",
            "gateway",
            "history",
            "native",
            "observed",
            "row_gateway",
            "AccountTimeProbePlanV1",
            "AccountTimeProbeReceiptV1",
            "verify_account_time",
            "LedgerScope",
            "checked_bootstrap",
            "SCHEMA",
            "V3_SCHEMA",
            "EXPOSED_V4_SCHEMA",
            "EXPOSED_9549_SCHEMA",
            "V7_FLAT_SCHEMA",
            "V7_9549_DIAGNOSTIC_SCHEMA",
            "ALGO_V6_SCHEMA",
            "MAX_PROOF",
            "FILES",
            "POLICY_BYTES",
            "POLICY_SHA256",
            "V3_POLICY_BYTES",
            "V3_POLICY_SHA256",
            "EXPOSED_V4_POLICY_BYTES",
            "EXPOSED_V4_POLICY_SHA256",
            "EXPOSED_9549_POLICY_BYTES",
            "EXPOSED_9549_POLICY_SHA256",
            "V7_FLAT_POLICY_BYTES",
            "V7_FLAT_POLICY_SHA256",
            "V7_9549_DIAGNOSTIC_POLICY_BYTES",
            "V7_9549_DIAGNOSTIC_POLICY_SHA256",
            "ALGO_V6_POLICY_BYTES",
            "ALGO_V6_POLICY_SHA256",
            "contract_for_plan",
            "current_use_contract_for_plan",
            "contract_for_exposed_v6_plan",
            "contract_for_exposed_v6_owned_before",
            "contract_for_v7_9549_diagnostic_plan",
            "contract_for_v7_9549_diagnostic_owned_before",
            "NativeAccountProofError",
            "NativeAccountProofReplay",
            "NativeAlgoHistoryV6ProofReplay",
            "_canonical_document",
            "_exact_contract",
            "_stamp",
            "scope_sha256",
            "_source",
            "_time_trace",
            "_clocks",
            "_replay",
            "replay_native_account_proof",
            "_exchange_plan",
        ),
    ),
    (
        _v3_module_16,
        (
            "os",
            "contextmanager",
            "datetime",
            "PosixPath",
            "WindowsPath",
            "native_stamp",
            "canonical",
            "sha",
            "utc_from_ns",
            "validate_stamps",
            "_posix_root",
            "_root_path",
            "_windows_native_path",
            "_windows_root",
            "proof",
            "READBACK_SCHEMA",
            "V3_READBACK_SCHEMA",
            "EXPOSED_V4_READBACK_SCHEMA",
            "EXPOSED_9549_READBACK_SCHEMA",
            "V7_FLAT_READBACK_SCHEMA",
            "V7_9549_DIAGNOSTIC_READBACK_SCHEMA",
            "ALGO_V6_READBACK_SCHEMA",
            "MAX_PART",
            "_readback_schema",
            "_root_context",
            "_root_identity",
            "_companion_attempt",
            "_publish_part",
            "_seal_companion",
            "read_native_account_companion",
        ),
    ),
    (
        _v3_module_17,
        (
            "asyncio",
            "json",
            "os",
            "re",
            "dataclass",
            "field",
            "datetime",
            "PosixPath",
            "WindowsPath",
            "get_ident",
            "WeakKeyDictionary",
            "ref",
            "httpx",
            "AsyncEngine",
            "AsyncSession",
            "async_sessionmaker",
            "AccountCaptureJournalRepository",
            "LockedAccountSourceJoinReadback",
            "AccountCurrentHistoryObservationRepository",
            "CurrentHistoryObservationReadback",
            "AccountCurrentObservationRepository",
            "CurrentObservationReadback",
            "HISTORY_PREFIX_POLICY_SHA256",
            "AccountHistoryPrefixRepository",
            "HistoryPrefixReadback",
            "QualificationLedgerRepository",
            "canonical",
            "sha",
            "utc_from_ns",
            "validate_stamps",
            "bootstrap",
            "capture",
            "journal",
            "boundary",
            "collector",
            "source_join",
            "current",
            "funding_bills",
            "gateway",
            "history",
            "native",
            "exposed",
            "proof",
            "storage",
            "observed",
            "row_gateway",
            "time_source",
            "ControlledDemoAccountSession",
            "LedgerScope",
            "_CAPTURES",
            "_ORIGIN_LEASES",
            "_RAW_PACKET_LEASES",
            "_PRE_HISTORY_READBACK_TIMEOUT_SECONDS",
            "_TYPED_HISTORY_COMMIT_POLICY_BYTES",
            "_TYPED_HISTORY_COMMIT_POLICY_SHA256",
            "NativeAccountOriginError",
            "_OriginLease",
            "NativeAccountRawPacketError",
            "_RawPacketLease",
            "_ObservedNativeDemoAccountRawPacket",
            "_ObservedDemoAccountOrigin",
            "_TimeProbeTrace",
            "_configured_factory",
            "_CapturedCurrentNativeAccount",
            "_CurrentNativeHandoff",
            "InitialNativeAccountDiagnostic",
            "_mint_demo_account_origin",
            "_consume_demo_account_origin",
            "_mint_native_demo_raw_packet",
            "_consume_native_demo_raw_packet",
            "NativeCurrentHistoryJoinDiagnostic",
            "_time_probe",
            "_secret_checked",
            "_recheck_original_db_chain",
            "_capture_initial_current",
            "_consume_current_native_capture",
            "capture_initial_native_account",
            "capture_native_exposed_account_observation",
            "capture_native_v7_9549_chronology_diagnostic",
            "_CommittedHistoryPreRead",
            "_read_committed_history_before_http",
            "capture_native_current_history_join",
            "capture_native_current_history_funding_join",
            "capture_native_current_history_index_join_v1",
            "capture_native_current_history_typed_join_v1",
            "_capture_native_current_history_join",
        ),
    ),
    (
        _v3_module_18,
        (
            "json",
            "dataclass",
            "datetime",
            "timedelta",
            "Decimal",
            "Fraction",
            "capture",
            "journal",
            "query",
            "LedgerScope",
            "checked_bootstrap",
            "MAX_CAPTURES",
            "MAX_ROWS",
            "MAX_SOURCE_BYTES",
            "MAX_RECEIPT_BYTES",
            "POLICY_BYTES",
            "POLICY_SHA256",
            "_TYPED_SOURCE_TS_DOMAINS",
            "TYPED_POLICY_BYTES",
            "TYPED_POLICY_SHA256",
            "AccountObservationError",
            "deny",
            "CaptureReference",
            "ExecutionWindow",
            "ObservationReplay",
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
        ),
    ),
    (
        _v3_module_19,
        (
            "dataclass",
            "datetime",
            "timedelta",
            "Fraction",
            "WeakKeyDictionary",
            "canonical",
            "sha",
            "capture",
            "lifecycle",
            "observed",
            "LedgerScope",
            "POLICY_BYTES",
            "POLICY_SHA256",
            "_RESULTS",
            "_BASE_GAPS",
            "AccountRiskWindowsError",
            "_deny",
            "_ratio",
            "_read_ratio",
            "_fill_window_totals",
            "_advance_loss_streak",
            "_WindowFacts",
            "AccountRiskWindowsV1",
            "_checked_result",
            "derive_account_risk_windows_v1",
        ),
    ),
    (
        _v3_module_20,
        (
            "asyncio",
            "os",
            "re",
            "secrets",
            "sys",
            "dataclass",
            "field",
            "datetime",
            "Path",
            "get_ident",
            "CoroutineType",
            "Literal",
            "WeakKeyDictionary",
            "ref",
            "controls",
            "custody",
            "QualificationLedgerRepository",
            "catalog_policy",
            "native_clock",
            "primitives",
            "canonical",
            "decode",
            "sha",
            "utc_from_ns",
            "validate_stamps",
            "OkxPrivateApiError",
            "live_authority",
            "account",
            "current",
            "proof",
            "storage",
            "runtime",
            "control",
            "execution_authority",
            "private_write_authority",
            "LedgerScope",
            "_HOOK_KEY",
            "_BINDINGS",
            "_OWNERS",
            "_FINANCIAL",
            "_MAX_OWNERS",
            "_MAX_SOURCE_FILE",
            "_ROOT",
            "_SHA",
            "_PRIVATE_NAMES",
            "WRITER_PATHS",
            "_DENY_SOURCE",
            "POLICY_BYTES",
            "POLICY_SHA256",
            "ControlledWriterCoverageError",
            "_deny",
            "_Opaque",
            "_ActivationBindingV1",
            "OwnedControlledDemoWriterCoverageV1",
            "ControlledDemoWriterCoverageFactsV1",
            "_context",
            "_read_file",
            "_guard_denials",
            "_package_inventory",
            "_factory_pins",
            "_source_files",
            "_source_material",
            "_check_binding",
            "_repository_binding_v1",
            "_repository_replay_v1",
            "_checked_await",
            "_read_control",
            "_control_pins",
            "_template",
            "_bind_control",
            "_observation",
            "acquire_controlled_demo_writer_coverage_v1",
            "consume_owned_controlled_demo_writer_coverage_v1",
            "_financial_key_v1",
            "_take_financial_assembly_v1",
            "_release_financial_assembly_v1",
            "_replay_financial_assembly_v1",
            "_catalog_policy_digest",
            "_CATALOG_POLICY_SHA256",
            "_FUNCTION_PINS",
            "_check_function_pins",
        ),
    ),
    (
        _v3_module_21,
        (
            "hashlib",
            "json",
            "UTC",
            "datetime",
            "timedelta",
            "Context",
            "Decimal",
            "DecimalException",
            "localcontext",
            "Fraction",
            "Annotated",
            "Literal",
            "WeakKeyDictionary",
            "BaseModel",
            "Field",
            "TypeAdapter",
            "field_validator",
            "model_validator",
            "BUCKET_SETTLEMENT_CURRENCY",
            "MAX_PORTFOLIO_MARGIN_PCT",
            "MAX_PORTFOLIO_STOP_RISK_PCT",
            "MAX_SINGLE_POSITION_MARGIN_USDT",
            "MAX_TRADE_RISK_PCT",
            "rounded_up_amount",
            "Price",
            "QualificationModel",
            "ReportId",
            "Text",
            "Digest",
            "Amount",
            "SignedAmount",
            "Rate",
            "Count",
            "LimitCount",
            "Leverage",
            "_PRICE",
            "_AMOUNT",
            "_LEVERAGE",
            "_REPORT",
            "_TEXT",
            "_INVALID",
            "_utc",
            "ObservedSource",
            "EvidenceStamp",
            "ContractRiskSpec",
            "PositionExposure",
            "PendingReservation",
            "RealizedOutcome",
            "PortfolioRiskSnapshot",
            "PortfolioRiskPolicy",
            "DemoRiskAuthority",
            "PortfolioRiskResult",
            "_check_tree",
            "_revalidate",
            "_canonical",
            "_fingerprint",
            "_display",
            "_evaluate_portfolio",
            "evaluate_portfolio",
            "evaluate_portfolio_with_risk_windows_v2",
            "_NATIVE_FINANCIAL_EVALUATIONS",
            "NativeAccountRiskWindowsEvaluationV1",
            "evaluate_native_account_risk_windows_v1",
        ),
    ),
    (
        _v3_module_22,
        (
            "hashlib",
            "json",
            "datetime",
            "timezone",
            "Decimal",
            "Fraction",
            "Annotated",
            "Literal",
            "ZoneInfo",
            "BaseModel",
            "ConfigDict",
            "Field",
            "field_validator",
            "model_validator",
            "evaluate_current_risk",
            "PortfolioInputs",
            "PreEvidenceRun",
            "_copy",
            "_preflight",
            "HistoryPreEvidenceRunV2",
            "HistoryPreEvidenceRunV3",
            "HistoryPreEvidenceRunV4",
            "HistoryPreEvidenceRunV5",
            "ExecutableQuote",
            "_revalidate",
            "build_original_range_anchor_zone",
            "evaluate_location",
            "Price",
            "ReportId",
            "require_aware",
            "DemoRiskAuthority",
            "PendingReservation",
            "PortfolioRiskSnapshot",
            "RecheckOrigin",
            "copy_recheck_origin",
            "_plain",
            "Digest",
            "Revision",
            "State",
            "MAX_JSON_BYTES",
            "STAMP_FIELDS",
            "QualificationLedgerError",
            "LedgerModel",
            "LedgerScope",
            "AccountLedgerClaims",
            "ReservationRequest",
            "ReservationReplayBindingV2",
            "ReservationRequestV2",
            "ReservationReplayBindingV3",
            "ReservationRequestV3",
            "ScenarioOperands",
            "RiskCoverage",
            "ReservationReceipt",
            "LedgerScopeState",
            "_CHILDREN",
            "_MODELS",
            "_QUOTE_SCALARS",
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
        ),
    ),
    (
        _v3_module_23,
        (
            "_policy",
            "AccountHistoryPartitionError",
            "expected_account_history_partition_schema",
            "verify_account_history_partition_catalog",
            "canonical",
            "digest",
            "read_account_history_partition_catalog",
            "read_account_history_partition_retention",
        ),
    ),
    (
        sys.modules[__name__],
        (
            "asyncio",
            "os",
            "sys",
            "dataclass",
            "field",
            "datetime",
            "Path",
            "get_ident",
            "WeakKeyDictionary",
            "ref",
            "custody",
            "native_clock",
            "canonical",
            "sha",
            "utc_from_ns",
            "DemoPinnedReadSession",
            "OkxDemoPrivateRestClient",
            "inventory_math",
            "capture",
            "current",
            "current_verifier",
            "financial_v2",
            "history_bundle",
            "history",
            "lifecycle",
            "mapping",
            "observed",
            "windows",
            "coverage",
            "portfolio",
            "financial_reservations",
            "ControlledDemoAccountSession",
            "_V3FunctionType",
            "_V3MethodType",
            "_V3MappingProxyType",
            "_V3TextClause",
            "_V3Timedelta",
            "_v3_partition_repository",
            "_v3_partition_records",
            "_v3_partition_policy",
            "_v3_partition_adapter",
            "_v3_module_0",
            "_v3_module_1",
            "_v3_module_2",
            "_v3_module_3",
            "_v3_module_4",
            "_v3_module_5",
            "_v3_module_6",
            "_v3_module_7",
            "_v3_module_8",
            "_v3_module_9",
            "_v3_module_10",
            "_v3_module_11",
            "_v3_module_12",
            "_v3_module_13",
            "_v3_module_14",
            "_v3_module_15",
            "_v3_module_16",
            "_v3_module_17",
            "_v3_module_18",
            "_v3_module_19",
            "_v3_module_20",
            "_v3_module_21",
            "_v3_module_22",
            "_v3_module_23",
            "_HOOK_KEY",
            "_OWNERS",
            "_MAX_OWNERS",
            "_ROOT",
            "_FINANCIAL_PATHS",
            "SOURCE_PATHS",
            "_MISSING",
            "NativeAccountRiskAssemblyError",
            "_deny",
            "OwnedNativeAccountRiskAssemblyV1",
            "NativeAccountRiskAssemblyV1",
            "_context",
            "_package_inventory",
            "_check",
            "_release",
            "_replay",
            "_calculate",
            "acquire_native_account_risk_assembly_v1",
            "consume_owned_native_account_risk_assembly_v1",
            "NativeAccountFinancialOperandsV2",
            "_financial_lifetimes_v2",
            "_calculate_financial_operands_v2",
            "consume_owned_native_account_financial_operands_v2",
            "_FUNCTION_PINS",
            "_V2_DEPENDENCY_NAMES",
            "_V3_OWNERS",
            "_V3_SOURCE_PATHS",
            "_V3_POLICY_BYTES",
            "_V3_POLICY_SHA256",
            "OwnedNativeAccountFinancialOperandsV3",
            "NativeAccountFinancialOperandsV3",
            "_v3_data_pin",
            "_v3_callable_pin",
            "_v3_check_declared",
            "_package_inventory_v3",
            "_check_v3",
            "_release_v3",
            "_move_assembly_owner_v3",
            "acquire_native_account_risk_assembly_v3",
            "_v3_scope",
            "_partition_fences_v3",
            "_latest_native_linkage_v3",
            "_v3_partition_readback",
            "_partition_checkpoint_v3",
            "_replay_partition_sources_v3",
            "_v3_readback_identity",
            "_register_latest_partition_v3",
            "_consume_financial_v3",
            "_NativeFinancialConsumptionV3",
            "consume_owned_native_account_financial_operands_v3",
            "_v3_original_b1_sources",
            "_v3_b1_fence_identity",
            "_v3_verify_final_main_fence",
            "_await_financial_consumption_v3",
        ),
    ),
    (
        _v3_module_0.LockedAccountSourceJoinReadback,
        (
            "receipt_sha256",
            "account_complete",
            "execution_authority",
            "__init__",
            "__eq__",
        ),
    ),
    (
        _v3_module_0.AccountCaptureJournalRepository,
        (
            "__init__",
            "_lock",
            "_event",
            "read_chain",
            "_read_chain_locked",
            "read_locked_current_history_join",
            "read_locked_current_history_funding_join",
            "read_locked_current_history_typed_join",
            "_read_locked_current_history_join",
            "_append",
            "read_event",
            "recover_interrupted",
        ),
    ),
    (_v3_module_1.HistoryNativeMemberReadbackV1, ("__init__", "__eq__")),
    (_v3_module_1.HistoryCheckpointReadbackV1, ("__init__", "__eq__")),
    (
        _v3_module_1.HistoryPartitionReadbackV1,
        ("execution_authority", "account_complete", "__init__", "__eq__"),
    ),
    (
        _v3_module_1.AccountHistoryPartitionRepository,
        (
            "__init__",
            "_settings",
            "verify_deployment",
            "_read_in_session",
            "read",
            "append_member_and_checkpoint",
            "_append",
        ),
    ),
    (
        _v3_module_1.HistoryFinalSourceFenceReadbackV1,
        ("account_complete", "execution_authority", "__init__", "__eq__"),
    ),
    (_v3_module_1.AccountHistoryPartitionRepository, ("read_final_source_fence",)),
    (
        _v3_module_2.ObservationBatchReadback,
        ("execution_authority", "__init__", "__eq__"),
    ),
    (_v3_module_2.BalanceSourcePage, ("__init__", "__eq__")),
    (
        _v3_module_2.AccountObservationIndexRepository,
        (
            "__init__",
            "_batch",
            "_rows",
            "_source",
            "_checked_document",
            "_stored_membership",
            "_witnesses",
            "_derive_with_current_source",
            "_derive",
            "read",
            "read_balance_source_page",
            "_require_hwm_prefix_locked",
            "append",
            "_append",
        ),
    ),
    (
        _v3_module_3.RuntimeBundleCustodyError,
        ("__init__", "diagnostic_category", "diagnostic_conditions"),
    ),
    (_v3_module_3._Deployment, ("__init__", "__eq__")),
    (_v3_module_3.RetainedRuntimeBundleReadback, ("__init__", "__eq__")),
    (
        _v3_module_3.OwnedRuntimeBundleCustodyReceipt,
        ("__init__", "__copy__", "__deepcopy__", "__reduce_ex__"),
    ),
    (
        _v3_module_3.RuntimeBundleCustodyRepository,
        (
            "__init__",
            "_check_factory",
            "_catalog",
            "verify_deployment",
            "_require_deployment",
            "_read",
            "seal_bundle",
            "read_retained_bundle",
            "read_retained_bundle_by_pins",
        ),
    ),
    (_v3_module_4.LedgerCaptureCheckpoint, ("__init__", "__eq__")),
    (
        _v3_module_4.LedgerBootstrapCheckpoint,
        ("account_initialized", "execution_authority", "__init__", "__eq__"),
    ),
    (
        _v3_module_4.PortfolioLocalCheckpoint,
        ("state_sha256", "known_flat", "__init__", "__eq__"),
    ),
    (
        _v3_module_4._ConsumedEventJournalInspection,
        ("sha256", "execution_authority", "__init__", "__eq__"),
    ),
    (
        _v3_module_4.QualificationLedgerRepository,
        (
            "__init__",
            "_keys",
            "_locked",
            "_now",
            "_late_guard",
            "_revision",
            "_claims",
            "_require_owned_account_revision",
            "_set_claims",
            "_receipt",
            "_active",
            "_state",
            "reconcile_scope",
            "read_scope",
            "_event_rows_locked",
            "read_event_observation",
            "inspect_consumed_event_journal",
            "read_capture_checkpoint",
            "_bootstrap_checkpoint",
            "initialize_capture_scope",
            "read_bootstrap_checkpoint",
            "read_portfolio_checkpoint",
            "_read_portfolio_checkpoint_locked",
            "_coverage_caps",
            "reserve",
            "reserve_control_bound",
            "_control_locked",
            "_reserve",
            "_journal",
            "_transition",
            "_without_self",
            "consume_once",
            "consume_with_submission_intent",
            "read_submission_intent",
            "consume_with_control_bound_submission_intent",
            "read_control_bound_submission_intent",
            "_read_submission_intent",
            "mark_uncertain",
            "_require_no_uncertain",
            "_require_resolved_submissions",
            "_require_compatible_currency",
            "record_submission_observation",
            "reconcile_reservation",
        ),
    ),
    (_v3_module_6.HistoryPartitionScopeV1, ("__post_init__", "__init__", "__eq__")),
    (_v3_module_6.HistoryPartitionRecordV1, ("record_sha256", "__init__", "__eq__")),
    (_v3_module_7.ClockStamp, ("plain_clock_stamp",)),
    (
        _v3_module_8.OwnedCurrentDemoAccountSourceV4,
        ("__new__", "__copy__", "__deepcopy__", "__reduce_ex__"),
    ),
    (_v3_module_8.RecordedCurrentAccountBundleV4, ("__init__", "__eq__")),
    (_v3_module_8.CurrentAccountOperandsV4, ("receipt_sha256", "__init__", "__eq__")),
    (_v3_module_9.NativePositionSourceV2, ("__init__", "__eq__")),
    (_v3_module_9.AccountFinancialOperandsV2, ("receipt_sha256", "__init__", "__eq__")),
    (
        _v3_module_10._PositionObserver,
        ("__new__", "__copy__", "__deepcopy__", "__reduce_ex__"),
    ),
    (_v3_module_11.PositionHistorySourceV8, ("__init__", "__eq__")),
    (_v3_module_11.SettledFundingSourceV8, ("__init__", "__eq__")),
    (
        _v3_module_11.AccountHistoryRiskOperandsV8,
        ("receipt_sha256", "__init__", "__eq__"),
    ),
    (_v3_module_12.HistorySourceFactsV1, ("__init__", "__eq__")),
    (_v3_module_13.NativeHistoryScopeV1, ("__post_init__", "__init__", "__eq__")),
    (
        _v3_module_13.OwnedNativeHistorySourceFactsV1,
        ("__new__", "__copy__", "__deepcopy__", "__reduce_ex__"),
    ),
    (_v3_module_13._FinancialHistoryBindingV1, ("__new__", "__reduce_ex__")),
    (
        _v3_module_14.AccountLifecycleHistoryReplay,
        (
            "receipt_sha256",
            "execution_authority",
            "account_complete",
            "admission",
            "__init__",
            "__eq__",
        ),
    ),
    (
        _v3_module_15.NativeAccountProofReplay,
        ("execution_authority", "account_complete", "__init__", "__eq__"),
    ),
    (
        _v3_module_15.NativeAlgoHistoryV6ProofReplay,
        (
            "execution_authority",
            "account_complete",
            "flat_start_permission",
            "source_authenticity_verified",
            "snapshot",
            "owner",
            "admission",
            "__init__",
            "__eq__",
        ),
    ),
    (_v3_module_17._ObservedNativeDemoAccountRawPacket, ("__init__", "__eq__")),
    (_v3_module_17._ObservedDemoAccountOrigin, ("__init__", "__eq__")),
    (
        _v3_module_17._TimeProbeTrace,
        (
            "__init__",
            "_append",
            "begin_request",
            "headers",
            "chunk",
            "body_complete",
            "finish_request",
            "client_closed",
            "encoded",
        ),
    ),
    (_v3_module_17._CurrentNativeHandoff, ("__init__", "__eq__")),
    (
        _v3_module_17.InitialNativeAccountDiagnostic,
        (
            "receipt_sha256",
            "owner",
            "snapshot",
            "account_complete",
            "execution_authority",
            "__init__",
            "__eq__",
        ),
    ),
    (
        _v3_module_17.NativeCurrentHistoryJoinDiagnostic,
        (
            "receipt_sha256",
            "owner",
            "snapshot",
            "account_complete",
            "execution_authority",
            "__init__",
            "__eq__",
        ),
    ),
    (_v3_module_17._CommittedHistoryPreRead, ("__init__", "__eq__")),
    (_v3_module_18.CaptureReference, ("__post_init__", "__init__", "__eq__")),
    (_v3_module_18.ExecutionWindow, ("__post_init__", "__init__", "__eq__")),
    (
        _v3_module_18.ObservationReplay,
        (
            "receipt_sha256",
            "account_complete",
            "execution_authority",
            "admission",
            "__init__",
            "__eq__",
        ),
    ),
    (_v3_module_19._WindowFacts, ("__init__", "__eq__")),
    (_v3_module_19.AccountRiskWindowsV1, ("__new__", "receipt_json", "receipt_sha256")),
    (_v3_module_20._Opaque, ("__new__", "__copy__", "__deepcopy__", "__reduce_ex__")),
    (_v3_module_20.ControlledDemoWriterCoverageFactsV1, ("__init__", "__eq__")),
    (_v3_module_21.PortfolioRiskResult, ("no_authority", "consistent_result")),
    (
        _v3_module_21.NativeAccountRiskWindowsEvaluationV1,
        ("__new__", "receipt_json", "receipt_sha256"),
    ),
    (_v3_module_22.LedgerModel, ("false_means_false",)),
    (_v3_module_22.ReservationRequestV2, ("explicit_range_v5",)),
    (_v3_module_22.ReservationRequestV3, ("explicit_range_v5",)),
    (_v3_module_22.RiskCoverage, ("coverage",)),
    (_v3_module_22.ReservationReceipt, ("utc", "clocks")),
    (
        sys.modules[__name__].OwnedNativeAccountRiskAssemblyV1,
        ("__new__", "__copy__", "__deepcopy__", "__reduce_ex__"),
    ),
    (
        sys.modules[__name__].NativeAccountRiskAssemblyV1,
        ("receipt_sha256", "__init__", "__eq__"),
    ),
    (
        sys.modules[__name__].NativeAccountFinancialOperandsV2,
        ("receipt_sha256", "__init__", "__eq__"),
    ),
    (
        sys.modules[__name__].OwnedNativeAccountFinancialOperandsV3,
        ("__new__", "__copy__", "__deepcopy__", "__reduce_ex__"),
    ),
    (
        sys.modules[__name__].NativeAccountFinancialOperandsV3,
        ("receipt_sha256", "__init__", "__eq__"),
    ),
    (
        sys.modules[__name__]._NativeFinancialConsumptionV3,
        ("__init__", "__await__", "close", "__del__"),
    ),
)
_V3_DECLARED_DATA = (
    (_v3_module_0, ()),
    (
        _v3_module_1,
        (
            "AccountHistoryPartitionError",
            "expected_account_history_partition_schema",
            "verify_account_history_partition_catalog",
            "canonical",
            "digest",
            "_APPEND_SQL",
            "_READ_SQL",
        ),
    ),
    (_v3_module_2, ("PAGE_SIZE", "MAX_REVISION")),
    (
        _v3_module_3,
        (
            "MAX_BUNDLE_BYTES",
            "_SHA",
            "_ID",
            "_ISSUER",
            "_RECEIPTS",
            "_RECEIPT_LIFETIME_NS",
            "_BUNDLE_ID_DOMAIN",
            "_SAFE_CATALOG_SEARCH_PATH",
            "_SET_CATALOG_SEARCH_PATH_SQL",
            "_CATALOG_SEARCH_PATH_SQL",
            "_CONNECT_CODE",
            "_ON_CONNECT_CODE",
            "_FIRST_CONNECT_CODE",
            "_ONLY_ONCE_CODE",
            "_DIALECT_CONNECT_CODE",
            "_FUNCTIONS",
            "_CONSTRAINT_DEFINITION_SHA256",
            "_ROLE_SQL",
            "_FUNCTION_SQL",
            "_TRIGGER_SQL",
            "_COLUMN_SQL",
            "_CONSTRAINT_SQL",
            "_DIAGNOSTIC_CATEGORIES",
            "_ROLE_DIAGNOSTIC_CONDITIONS",
            "__all__",
        ),
    ),
    (
        _v3_module_4,
        (
            "_CONTROL_RESERVATION_READBACK_DB_TIMEOUT_MS",
            "_CONTROL_RESERVATION_READBACK_CANCEL_AFTER_SECONDS",
            "_EVENT_GUARD_FUNCTIONS",
        ),
    ),
    (
        _v3_module_5,
        (
            "PG17_DEPARSED_BASELINE",
            "PG17_DEPARSED_BASELINE_STATUS",
            "CATALOG_SCHEMA",
            "TABLES",
            "PARENTS",
            "SCOPE_COLUMNS",
            "MAX_READ_BYTES",
            "canonical",
            "digest",
            "_SCOPE_CHECK",
            "TABLE_SPECS",
            "TABLE_SQL",
            "INDEX_SQL",
            "UID_SQL",
            "_JSON_BODY",
            "_LOCK_BODY",
            "_MEMBER_BODY",
            "_JSON_CHECK_BODY",
            "_CHECKPOINT_BODY",
            "_APPEND_BODY",
            "_READ_BODY",
            "_IMMUTABLE_BODY",
            "_LINKAGE_KEYS",
            "_SUBSTITUTIONS",
            "FUNCTION_SPECS",
            "FUNCTION_SIGNATURES",
            "FUNCTION_SQL",
            "TRIGGER_SPECS",
            "_PAIR_BODY",
            "TRIGGER_SQL",
            "CATALOG_SQL",
        ),
    ),
    (
        _v3_module_6,
        (
            "PARTITION_SPAN",
            "MAX_RECORD_BYTES",
            "MAX_CHAIN_BYTES",
            "MAX_JSON_NODES",
            "MAX_JSON_DEPTH",
            "MAX_SEQUENCE",
            "MEMBER_SCHEMA",
            "LINKAGE_SCHEMA",
            "CHECKPOINT_SCHEMA",
            "POLICY_BYTES",
            "POLICY_SHA256",
            "_REFERENCE_KEYS",
            "_COUNTS",
        ),
    ),
    (
        _v3_module_7,
        (
            "MAX_RAW",
            "MAX_TIMESTAMP_NS",
            "MAX_CLOCK_DEVIATION_NS",
            "MAX_CLOCK_BATCH_NS",
            "EPOCH",
            "Ns",
            "_DEMO_V2_ROUTES",
            "_DEMO_V2_ROLE_PATHS",
            "_DEMO_V2_USER_AGENTS",
            "_DEMO_V2_QUOTE_ORDER",
            "PUBLIC_V4_CURRENT_SOURCE_POLICY",
            "PUBLIC_V4_CURRENT_SOURCE_POLICY_SHA256",
        ),
    ),
    (
        _v3_module_8,
        (
            "SCHEMA",
            "POLICY_BYTES",
            "POLICY_SHA256",
            "_CURRENT",
            "_INITIAL",
            "_MAX_NATIVE_WALL_SECONDS",
        ),
    ),
    (_v3_module_9, ("POLICY_BYTES", "POLICY_SHA256", "_MISSING")),
    (
        _v3_module_10,
        (
            "_OBSERVERS",
            "MAX_POSITION_PAGES",
            "MAX_POSITION_RAW_BYTES",
            "MAX_POSITION_EVENTS",
        ),
    ),
    (_v3_module_11, ("POLICY_BYTES", "POLICY_SHA256")),
    (
        _v3_module_12,
        ("SCHEMA", "PROOF_SCHEMA", "MAX_JSON_DEPTH", "MAX_JSON_NODES", "POLICY_SHA256"),
    ),
    (
        _v3_module_13,
        ("_MAX_SESSION_TOMBSTONES", "_MAX_RAW_ROOT_BYTES", "_MAX_COMPANION_ROOT_BYTES"),
    ),
    (_v3_module_14, ("POLICY_BYTES", "POLICY_SHA256")),
    (
        _v3_module_15,
        (
            "SCHEMA",
            "V3_SCHEMA",
            "EXPOSED_V4_SCHEMA",
            "EXPOSED_9549_SCHEMA",
            "V7_FLAT_SCHEMA",
            "V7_9549_DIAGNOSTIC_SCHEMA",
            "ALGO_V6_SCHEMA",
            "MAX_PROOF",
            "FILES",
            "POLICY_BYTES",
            "POLICY_SHA256",
            "V3_POLICY_BYTES",
            "V3_POLICY_SHA256",
            "EXPOSED_V4_POLICY_BYTES",
            "EXPOSED_V4_POLICY_SHA256",
            "EXPOSED_9549_POLICY_BYTES",
            "EXPOSED_9549_POLICY_SHA256",
            "V7_FLAT_POLICY_BYTES",
            "V7_FLAT_POLICY_SHA256",
            "V7_9549_DIAGNOSTIC_POLICY_BYTES",
            "V7_9549_DIAGNOSTIC_POLICY_SHA256",
            "ALGO_V6_POLICY_BYTES",
            "ALGO_V6_POLICY_SHA256",
        ),
    ),
    (
        _v3_module_16,
        (
            "READBACK_SCHEMA",
            "V3_READBACK_SCHEMA",
            "EXPOSED_V4_READBACK_SCHEMA",
            "EXPOSED_9549_READBACK_SCHEMA",
            "V7_FLAT_READBACK_SCHEMA",
            "V7_9549_DIAGNOSTIC_READBACK_SCHEMA",
            "ALGO_V6_READBACK_SCHEMA",
            "MAX_PART",
        ),
    ),
    (
        _v3_module_17,
        (
            "_CAPTURES",
            "_ORIGIN_LEASES",
            "_RAW_PACKET_LEASES",
            "_PRE_HISTORY_READBACK_TIMEOUT_SECONDS",
            "_TYPED_HISTORY_COMMIT_POLICY_BYTES",
            "_TYPED_HISTORY_COMMIT_POLICY_SHA256",
        ),
    ),
    (
        _v3_module_18,
        (
            "MAX_CAPTURES",
            "MAX_ROWS",
            "MAX_SOURCE_BYTES",
            "MAX_RECEIPT_BYTES",
            "POLICY_BYTES",
            "POLICY_SHA256",
            "_TYPED_SOURCE_TS_DOMAINS",
            "TYPED_POLICY_BYTES",
            "TYPED_POLICY_SHA256",
        ),
    ),
    (_v3_module_19, ("POLICY_BYTES", "POLICY_SHA256", "_RESULTS", "_BASE_GAPS")),
    (
        _v3_module_20,
        (
            "_HOOK_KEY",
            "_MAX_OWNERS",
            "_MAX_SOURCE_FILE",
            "_ROOT",
            "_SHA",
            "_PRIVATE_NAMES",
            "WRITER_PATHS",
            "_DENY_SOURCE",
            "POLICY_BYTES",
            "POLICY_SHA256",
            "_CATALOG_POLICY_SHA256",
            "_FUNCTION_PINS",
        ),
    ),
    (
        _v3_module_21,
        (
            "Digest",
            "Amount",
            "SignedAmount",
            "Rate",
            "Count",
            "LimitCount",
            "Leverage",
            "_PRICE",
            "_AMOUNT",
            "_LEVERAGE",
            "_REPORT",
            "_TEXT",
            "_INVALID",
            "_NATIVE_FINANCIAL_EVALUATIONS",
        ),
    ),
    (
        _v3_module_22,
        (
            "Digest",
            "Revision",
            "State",
            "MAX_JSON_BYTES",
            "STAMP_FIELDS",
            "_CHILDREN",
            "_MODELS",
            "_QUOTE_SCALARS",
        ),
    ),
    (
        _v3_module_23,
        (
            "AccountHistoryPartitionError",
            "expected_account_history_partition_schema",
            "verify_account_history_partition_catalog",
            "canonical",
            "digest",
        ),
    ),
    (
        sys.modules[__name__],
        (
            "_HOOK_KEY",
            "_MAX_OWNERS",
            "_ROOT",
            "_FINANCIAL_PATHS",
            "SOURCE_PATHS",
            "_MISSING",
            "_FUNCTION_PINS",
            "_V2_DEPENDENCY_NAMES",
            "_V3_SOURCE_PATHS",
            "_V3_POLICY_BYTES",
            "_V3_POLICY_SHA256",
        ),
    ),
)
_V3_DECLARED_BINDINGS = (
    *_V3_DECLARED_BINDINGS,
    (
        _native_timing,
        (
            "begin",
            "end",
            "source_file",
            "snapshot",
            "_self_pinned",
            "_tick",
            "_valid_state",
            "_label_index",
            "_FACTORY_INIT_PIN",
            "_CLOCK",
            "_NS",
            "_time",
            "_types",
        ),
    ),
    (_native_timing._types, ("FunctionType", "CodeType", "ModuleType")),
)
_V3_DECLARED_DATA = (
    *_V3_DECLARED_DATA,
    (
        _native_timing,
        (
            "ENABLED",
            "PROFILE",
            "SCHEMA",
            "LABELS",
            "MAX_INVOCATIONS",
            "MAX_READS",
            "MAX_TOTAL_PUBLIC_BYTES",
            "MAX_DEPTH",
            "_CLOCK",
            "_State",
            "_FACTORY",
            "_STATE",
            "_CLOCK_PIN",
            "_FACTORY_PIN",
            "_FACTORY_INIT_PIN",
            "_FACTORY_INIT_CODE",
            "_STATE_PIN",
            "_TIME_PIN",
            "_DATA_PIN",
            "_FUNCTION_PINS",
            "_SLOT_PINS",
            "_BUILTIN_PINS",
            "_NS_ID",
            "_FN_TYPE",
            "_CODE_TYPE",
            "_MODULE_TYPE",
            "_SELF_CHECK_PIN",
            "_SELF_CHECK_CODE",
            "_SELF_CHECK_DEFAULTS",
            "_TYPE",
            "_LEN",
            "_RANGE",
            "_ALL",
            "_ID",
            "_INT",
            "_STR",
            "_BOOL",
            "_LIST",
            "_TUPLE",
            "_GET",
            "_SET",
            "_NS_GET",
        ),
    ),
)

_V3_PIN_FUNCTIONS = tuple(
    (owner, name, _v3_callable_pin(getattr(owner, name)))
    for owner, names in _V3_DECLARED_BINDINGS
    for name in names
)
_V3_PIN_DATA = tuple(
    (owner, name, _v3_data_pin(getattr(owner, name)))
    for owner, names in _V3_DECLARED_DATA
    for name in names
)
