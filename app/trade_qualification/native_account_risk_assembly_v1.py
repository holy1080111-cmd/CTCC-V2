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
from datetime import UTC, datetime
from datetime import timedelta as _V3Timedelta
from hashlib import sha256 as _HWM_SHA256
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
    account_history_original_financial_read_v3 as _v4_reader,
)
from app.database.repositories import account_history_original_frame_v1 as _v4_wire
from app.database.repositories import (
    account_history_original_native_read_v1 as _v4_native_reader,
)
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
from app.trade_qualification import (
    account_financial_position_session_v3 as _v4_position_sessions,
)
from app.trade_qualification import account_history_global_financial_v2 as _v4_financial
from app.trade_qualification import account_history_original_stream_v1 as _v4_stream
from app.trade_qualification import account_history_source_bundle_v1 as history_bundle
from app.trade_qualification import account_history_source_v1 as history
from app.trade_qualification import account_lifecycle_history as lifecycle
from app.trade_qualification import account_materializer as mapping
from app.trade_qualification import account_native_sampled_hwm_v1 as _hwm_collector
from app.trade_qualification import account_observation_index as observed
from app.trade_qualification import (
    account_original_balance_measurement_v1 as _hwm_measurement,
)
from app.trade_qualification import account_risk_windows_v1 as windows
from app.trade_qualification import controlled_writer_coverage_v1 as coverage
from app.trade_qualification import history_json_lexical_guard_v1 as _json_lexical_guard
from app.trade_qualification import portfolio
from app.trade_qualification import reservations as financial_reservations
from app.trade_qualification.account_runtime import ControlledDemoAccountSession
from scripts import verify_account_history_partition_schema_v1 as _v3_partition_adapter

_HOOK_KEY = object()
_CURRENT_HISTORY_V4 = object()
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
    "app/trade_qualification/history_json_lexical_guard_v1.py",
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


def _check(
    state,
    *,
    _json_guard=_json_lexical_guard.require,
    _json_binding=_json_lexical_guard.BINDING,
    _json_module=_json_lexical_guard,
):
    if (
        _json_module is not _json_lexical_guard
        or _json_binding is not _json_module.BINDING
        or _json_guard is not _json_binding[0]
        or (_json_guard is not _json_module.require)
        or (_json_guard.__code__ != _json_binding[1])
        or (_json_guard.__code__.co_filename != _json_binding[1].co_filename)
        or (_json_guard.__defaults__ is not _json_module._ENTRY_DEFAULTS)
        or (_json_guard.__kwdefaults__ is not None)
        or (_json_guard.__closure__ is not None)
        or (_json_guard.__globals__ is not _json_module.__dict__)
        or (_json_guard.__builtins__ is not _json_module._BUILTINS)
    ):
        raise ValueError("history_json_guard_entry_changed") from None
    _json_guard()
    _origin_storage_v5()
    _origin_functions_v5()
    if (
        id(state) in _STATE_ORIGIN_STORAGE_V5
        or "_native_source_generation" in state
        or "_native_source_version" in state
    ):
        _check_native_origin_dispatch_v5(state)
    actual = _context()
    expected = state["context"]
    if actual[:3] != expected[:3] or actual[3] is not expected[3]():
        _deny("native_financial_context_changed")
    if any(
        (
            getattr(owner, name) is not function
            for owner, name, function in _FUNCTION_PINS
        )
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
    if "history" in state:
        history._financial_state_v1(state["history"], key=_HOOK_KEY)
    if "coverage" in state:
        coverage._check_binding(state["coverage"])


def _release(state):
    _forget_native_origin_v5(state)
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


def _calculate(
    state,
    facts,
    view,
    *,
    _json_guard=_json_lexical_guard.require,
    _json_binding=_json_lexical_guard.BINDING,
    _json_module=_json_lexical_guard,
):
    if (
        _json_module is not _json_lexical_guard
        or _json_binding is not _json_module.BINDING
        or _json_guard is not _json_binding[0]
        or (_json_guard is not _json_module.require)
        or (_json_guard.__code__ != _json_binding[1])
        or (_json_guard.__code__.co_filename != _json_binding[1].co_filename)
        or (_json_guard.__defaults__ is not _json_module._ENTRY_DEFAULTS)
        or (_json_guard.__kwdefaults__ is not None)
        or (_json_guard.__closure__ is not None)
        or (_json_guard.__globals__ is not _json_module.__dict__)
        or (_json_guard.__builtins__ is not _json_module._BUILTINS)
    ):
        raise ValueError("history_json_guard_entry_changed") from None
    _json_guard()
    _check(state)
    data = history_bundle._json(
        facts.batches[-1].document_json, observed.MAX_SOURCE_BYTES
    )
    window_data = data["window"]
    window = observed.ExecutionWindow(
        datetime.fromisoformat(window_data["started_at"]),
        datetime.fromisoformat(window_data["ended_at"]),
    )
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
    return (derived, evaluated)


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
    _json_guard=_json_lexical_guard.require,
    _json_binding=_json_lexical_guard.BINDING,
    _json_module=_json_lexical_guard,
):
    return await _acquire_native_account_risk_assembly(
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
        _json_guard=_json_guard,
        _json_binding=_json_binding,
        _json_module=_json_module,
    )


async def _acquire_native_account_risk_assembly(
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
    _history_version=None,
    _json_guard=_json_lexical_guard.require,
    _json_binding=_json_lexical_guard.BINDING,
    _json_module=_json_lexical_guard,
):
    """Acquire original sources; no caller owner/clock/seed/claims/flags accepted."""
    if (
        _json_module is not _json_lexical_guard
        or _json_binding is not _json_module.BINDING
        or _json_guard is not _json_binding[0]
        or (_json_guard is not _json_module.require)
        or (_json_guard.__code__ != _json_binding[1])
        or (_json_guard.__code__.co_filename != _json_binding[1].co_filename)
        or (_json_guard.__defaults__ is not _json_module._ENTRY_DEFAULTS)
        or (_json_guard.__kwdefaults__ is not None)
        or (_json_guard.__closure__ is not None)
        or (_json_guard.__globals__ is not _json_module.__dict__)
        or (_json_guard.__builtins__ is not _json_module._BUILTINS)
    ):
        raise ValueError("history_json_guard_entry_changed") from None
    _json_guard()
    if _history_version is not None and _history_version is not _CURRENT_HISTORY_V4:
        _deny("native_financial_v4_entry_required")
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
            or (read_session._owner is not private_client)
            or (type(scope_config) is not history.NativeHistoryScopeV1)
            or (
                type(current_account_plan)
                is not capture.CurrentDemoAccountCapturePlanV7
            )
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
            or (plan.settlement_currency != "USDT")
            or (plan.environment != "demo")
            or (plan.origin != read_session._origin)
            or (plan.registration_region != "global")
            or (
                plan.registration_evidence_sha256
                != scope_config.registration_evidence_sha256
            )
            or (plan.leverage_instrument_ids != scope_config.leverage_instrument_ids)
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
            (
                a == z or a in z.parents or z in a.parents
                for i, a in enumerate(roots)
                for z in roots[i + 1 :]
            )
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
        history_capture = (
            history.acquire_native_history_current_source_v2
            if _history_version is _CURRENT_HISTORY_V4
            else history.acquire_native_history_source_facts_v1
        )
        history_owner = await history_capture(
            read_session=read_session,
            private_client=private_client,
            scope_config=scope_config,
            session_factory=session_factory,
            custody_session_factory=custody_session_factory,
            raw_root=history_raw_root,
            companion_root=history_companion_root,
        )
        _check(state)
        history_take = (
            history._take_financial_assembly_v2
            if _history_version is _CURRENT_HISTORY_V4
            else history._take_financial_assembly_v1
        )
        state["history"] = history_take(history_owner, key=_HOOK_KEY)
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
        if _history_version is None:
            _calculate(state, facts, view)
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
                if state is not None and (not successful):
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


def _calculate_financial_operands_v2(
    state,
    facts,
    view,
    *,
    _json_guard=_json_lexical_guard.require,
    _json_binding=_json_lexical_guard.BINDING,
    _json_module=_json_lexical_guard,
):
    if (
        _json_module is not _json_lexical_guard
        or _json_binding is not _json_module.BINDING
        or _json_guard is not _json_binding[0]
        or (_json_guard is not _json_module.require)
        or (_json_guard.__code__ != _json_binding[1])
        or (_json_guard.__code__.co_filename != _json_binding[1].co_filename)
        or (_json_guard.__defaults__ is not _json_module._ENTRY_DEFAULTS)
        or (_json_guard.__kwdefaults__ is not None)
        or (_json_guard.__closure__ is not None)
        or (_json_guard.__globals__ is not _json_module.__dict__)
        or (_json_guard.__builtins__ is not _json_module._BUILTINS)
    ):
        raise ValueError("history_json_guard_entry_changed") from None
    _json_guard()
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
    return (operands, derived, evaluated, anchors)


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
    (
        sys.modules[__name__],
        "_acquire_native_account_risk_assembly",
        _acquire_native_account_risk_assembly,
    ),
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
    (history, "_acquire_native_history_source", history._acquire_native_history_source),
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
        if rows.get(name) != sha(raw):
            _deny("native_financial_v3_packaged_source_changed")
        files[name] = pin
    return {
        "original": original,
        "manifest": manifest_pin,
        "manifest_sha256": sha(manifest),
        "files": files,
        "source_sha256": sha(canonical({name: pin[2] for name, pin in files.items()})),
    }


def _check_v3(
    state,
    *,
    _json_guard=_json_lexical_guard.require,
    _json_binding=_json_lexical_guard.BINDING,
    _json_module=_json_lexical_guard,
):
    if (
        _json_module is not _json_lexical_guard
        or _json_binding is not _json_module.BINDING
        or _json_guard is not _json_binding[0]
        or (_json_guard is not _json_module.require)
        or (_json_guard.__code__ != _json_binding[1])
        or (_json_guard.__code__.co_filename != _json_binding[1].co_filename)
        or (_json_guard.__defaults__ is not _json_module._ENTRY_DEFAULTS)
        or (_json_guard.__kwdefaults__ is not None)
        or (_json_guard.__closure__ is not None)
        or (_json_guard.__globals__ is not _json_module.__dict__)
        or (_json_guard.__builtins__ is not _json_module._BUILTINS)
    ):
        raise ValueError("history_json_guard_entry_changed") from None
    _json_guard()
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
            or (type(repository._poisoned) is not bool)
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
        if (
            id(initial) in _STATE_ORIGIN_STORAGE_V5
            or "_native_source_generation" in initial
            or "_native_source_version" in initial
        ):
            _transfer_native_origin_v5(initial, state)
        _check_v3(state)
        if (
            state["inventory"]["manifest_sha256"]
            != state["v3_inventory"]["manifest_sha256"]
        ):
            _deny("native_financial_v3_original_manifest_changed")
        return state
    except BaseException:
        source_origin = _STATE_ORIGIN_STORAGE_V5.get(id(state))
        if (
            type(source_origin) is tuple
            and len(source_origin) == 6
            and source_origin[0] == "V5_CURRENT_FIRST_OFF"
            and source_origin[2] is state
        ):
            _release_current_first_partial_v5(state, None, None)
        else:
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
    _json_guard=_json_lexical_guard.require,
    _json_binding=_json_lexical_guard.BINDING,
    _json_module=_json_lexical_guard,
):
    """Measure V3 union before V1 acquisition; MOVE in the same original task."""
    if (
        _json_module is not _json_lexical_guard
        or _json_binding is not _json_module.BINDING
        or _json_guard is not _json_binding[0]
        or (_json_guard is not _json_module.require)
        or (_json_guard.__code__ != _json_binding[1])
        or (_json_guard.__code__.co_filename != _json_binding[1].co_filename)
        or (_json_guard.__defaults__ is not _json_module._ENTRY_DEFAULTS)
        or (_json_guard.__kwdefaults__ is not None)
        or (_json_guard.__closure__ is not None)
        or (_json_guard.__globals__ is not _json_module.__dict__)
        or (_json_guard.__builtins__ is not _json_module._BUILTINS)
    ):
        raise ValueError("history_json_guard_entry_changed") from None
    _json_guard()
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
            if state is not None and (not successful):
                _release_v3(state)


def _v3_scope(facts):
    return _v3_partition_records.HistoryPartitionScopeV1(
        facts.scope.environment,
        facts.scope.account_id,
        facts.scope.settlement_currency,
    )


def _partition_fences_v3(
    facts,
    view,
    *,
    _json_guard=_json_lexical_guard.require,
    _json_binding=_json_lexical_guard.BINDING,
    _json_module=_json_lexical_guard,
):
    if (
        _json_module is not _json_lexical_guard
        or _json_binding is not _json_module.BINDING
        or _json_guard is not _json_binding[0]
        or (_json_guard is not _json_module.require)
        or (_json_guard.__code__ != _json_binding[1])
        or (_json_guard.__code__.co_filename != _json_binding[1].co_filename)
        or (_json_guard.__defaults__ is not _json_module._ENTRY_DEFAULTS)
        or (_json_guard.__kwdefaults__ is not None)
        or (_json_guard.__closure__ is not None)
        or (_json_guard.__globals__ is not _json_module.__dict__)
        or (_json_guard.__builtins__ is not _json_module._BUILTINS)
    ):
        raise ValueError("history_json_guard_entry_changed") from None
    _json_guard()
    observation = view["coverage"]
    raw = observation.audit_json
    if type(raw) is not str or sha(raw.encode("ascii")) != observation.audit_sha256:
        _deny("native_financial_v3_coverage_audit_changed")
    audit = history_bundle._json(raw.encode("ascii"), 262144)
    if (
        audit["environment"] != facts.scope.environment
        or audit["account_id"] != facts.scope.account_id
        or audit["requested_settlement_currency"] != "USDT"
        or (type(audit["currency_states"]) is not list)
    ):
        _deny("native_financial_v3_account_scope_changed")
    states = []
    currencies = set()
    for row in audit["currency_states"]:
        item = financial_reservations.LedgerScopeState.model_validate_json(
            canonical(row), strict=True
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
    return (revision, states[0].account_revision, states[0].ledger_revision)


def _latest_native_linkage_v3(
    state,
    facts,
    *,
    _json_guard=_json_lexical_guard.require,
    _json_binding=_json_lexical_guard.BINDING,
    _json_module=_json_lexical_guard,
):
    if (
        _json_module is not _json_lexical_guard
        or _json_binding is not _json_module.BINDING
        or _json_guard is not _json_binding[0]
        or (_json_guard is not _json_module.require)
        or (_json_guard.__code__ != _json_binding[1])
        or (_json_guard.__code__.co_filename != _json_binding[1].co_filename)
        or (_json_guard.__defaults__ is not _json_module._ENTRY_DEFAULTS)
        or (_json_guard.__kwdefaults__ is not None)
        or (_json_guard.__closure__ is not None)
        or (_json_guard.__globals__ is not _json_module.__dict__)
        or (_json_guard.__builtins__ is not _json_module._BUILTINS)
    ):
        raise ValueError("history_json_guard_entry_changed") from None
    _json_guard()
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
        or (original["policy_sha256"] != history_bundle.POLICY_SHA256)
        or (native["original"] != temporal["original_started"])
        or (native["custody_proof_sha256"] != facts.proof_sha256)
        or (facts.proof_sha256 != sha(history.journal.canonical(temporal)))
        or (native["reference"] != facts.reference)
        or (document["reference"] != reference)
        or (document["sequence"] != facts.batches[-1].sequence)
        or (sha(facts.batches[-1].document_json) != facts.batches[-1].event_sha256)
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
    return (raw, record, document["policy_sha256"])


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
            (
                facts,
                view,
                registration,
                checkpoint,
                final,
            ) = await _register_latest_partition_v3(state, facts, view)
            _check_v3(state)
            operands, derived, evaluated, anchors = _calculate_financial_operands_v2(
                state, facts, view
            )
            await _replay_partition_sources_v3(state, facts, view)
            original_sources = _v3_original_b1_sources(state, facts, view)
            fence = await state["v3_repository"].read_final_source_fence(
                _v3_scope(facts),
                capture_ids=tuple(
                    reference.capture_id for _chain, reference in original_sources
                ),
            )
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
            "_INCOMING_CUSTODY_FK_SQL",
            "_INCOMING_CUSTODY_FK_SQL_CURRENT0054",
            "_HEAD_LAYOUT_SQL",
            "_HEAD_SQL",
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
            "_custody_foreign_key_identity",
            "_custody_foreign_key_identity_current0054",
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
            "_acquire_native_history_source",
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
            "_acquire_native_account_risk_assembly",
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
            "_INCOMING_CUSTODY_FK_SQL",
            "_INCOMING_CUSTODY_FK_SQL_CURRENT0054",
            "_HEAD_LAYOUT_SQL",
            "_HEAD_SQL",
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
        (
            "_MAX_SESSION_TOMBSTONES",
            "_MAX_RAW_ROOT_BYTES",
            "_MAX_COMPANION_ROOT_BYTES",
            "_PARTITION_CURRENT_V2",
        ),
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
            "_CURRENT_HISTORY_V4",
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


# V4 retains the same real history/coverage MOVE registries and DB0050 writer.
# Its CURRENT bundle is explicitly not a full prefix. Only the fixed DB0052
# reader below replays genesis through the original current head.
_V4_OWNERS = WeakKeyDictionary()
_V4_DATABASE_IDENTITY_SQL = (
    "SELECT current_database(),d.oid::bigint,inet_server_addr()::text,"
    "inet_server_port(),pg_postmaster_start_time() "
    "FROM pg_catalog.pg_database d WHERE d.datname=current_database()"
)
_V4_EXTRA_PATHS = (
    "app/trade_qualification/account_history_current_bundle_v2.py",
    "app/trade_qualification/account_original_page_memo_v1.py",
    "app/trade_qualification/account_history_original_stream_v1.py",
    "app/trade_qualification/account_history_global_financial_v1.py",
    "app/trade_qualification/account_history_global_financial_v2.py",
    "app/trade_qualification/account_original_balance_measurement_v1.py",
    "app/trade_qualification/account_financial_position_session_v3.py",
    "app/database/repositories/account_history_original_frame_v1.py",
    "app/database/repositories/account_history_original_native_read_v1.py",
    "app/database/repositories/account_history_original_financial_read_v2.py",
    "app/database/repositories/account_history_original_financial_read_v3.py",
    "app/domain/account_history_original_frame_v1.py",
    "migrations/versions/0052_original_history_frame_reader.py",
)
_V4_POLICY_SHA256 = sha(
    canonical(
        {
            "schema_version": "ctcc.native_account_history_financial.policy.v4",
            "original_v3_policy_sha256": _V3_POLICY_SHA256,
            "source_paths": list(_V4_EXTRA_PATHS),
            "current_bundle": "original_current_plus_all_original_declared_witnesses",
            "native_registration": "same_invocation_latest_only_original_db0050",
            "checkpoints": "every_original_exact32_boundary_before_following_capture",
            "prefix": "db0052_genesis_through_same_original_head",
            "maximum_new_request_seconds": 20,
            "source_lifetimes": "original_shortest_unchanged",
            "database_binding": "same_explicit_native_creator_route_and_actual_database_before_and_after",
            "financial_schema": "ctcc.global_original_financial_facts.v2",
            "position_session": "every_original_identity_and_latest_CURRENT_original_session",
            "unknown_commit": "retain_and_burn_never_retry",
            "account_complete": False,
            "execution_authority": False,
        }
    )
)


class OwnedNativeAccountHistoryFinancialV4(OwnedNativeAccountFinancialOperandsV3):
    __slots__ = ()


@dataclass(frozen=True, slots=True, repr=False)
class NativeAccountHistoryFinancialObservationV4:
    """Actual owned CURRENT joined to full original PG replay; no dispatch permit."""

    original_history: object = field(repr=False)
    current_account: object = field(repr=False)
    receipt_json: bytes = field(repr=False)
    account_complete: bool = field(default=False, init=False)
    account_revision_published: bool = field(default=False, init=False)
    native_history_prefix_verified: bool = field(default=False, init=False)
    execution_authority: bool = field(default=False, init=False)

    @property
    def receipt_sha256(self):
        return sha(self.receipt_json)


def _package_inventory_v4():
    base = _package_inventory_v3()
    manifest, _ = coverage._read_file(_ROOT / "MANIFEST.sha256", 2 * 1024 * 1024)
    if sha(manifest) != base["manifest_sha256"]:
        _deny("native_financial_v4_manifest_changed")
    rows = {}
    for line in manifest.decode("ascii").splitlines():
        digest, separator, name = line.partition("  ")
        if not separator or coverage._SHA.fullmatch(digest) is None or name in rows:
            _deny("native_financial_v4_manifest_invalid")
        rows[name] = digest
    extra = {}
    for name in _V4_EXTRA_PATHS:
        raw, pin = coverage._read_file(_ROOT / name, 4 * 1024 * 1024)
        if rows.get(name) != sha(raw):
            _deny("native_financial_v4_source_changed")
        extra[name] = pin
    return {"v3": base, "extra": extra}


def _check_v4(state):
    _check_native_origin_dispatch_v5(state)
    _check_v3(state)
    if _package_inventory_v4() != state["v4_inventory"]:
        _deny("native_financial_v4_source_changed")
    if history._factory_pin(state["v4_reader_factory"]) != state["v4_reader_pin"]:
        _deny("native_financial_v4_reader_factory_changed")
    original = state["v4_original_reader_pin"]
    if history._factory_pin(original[0]) != original:
        _deny("native_financial_v4_reader_factory_changed")
    writer_pin = state["v4_writer_pin"]
    if history._factory_pin(writer_pin[0]) != writer_pin:
        _deny("native_financial_v4_writer_factory_changed")
    for factory, expected in zip(
        (state["v4_writer_factory"], state["v4_reader_factory"]),
        state["v4_connection_pins"],
        strict=True,
    ):
        if _database_connection_pin_v4(factory) != expected:
            _deny("native_financial_v4_database_route_changed")
    for owner, name, expected in _V4_PIN_FUNCTIONS:
        actual = _v3_callable_pin(getattr(owner, name))
        if (
            actual[0] is not expected[0]
            or actual[1] is not expected[1]
            or actual[2] is not expected[2]
            or actual[3:] != expected[3:]
        ):
            _deny("native_financial_v4_callee_changed")
    for owner, name, expected in _V4_PIN_DATA:
        if _v3_data_pin(getattr(owner, name)) != expected:
            _deny("native_financial_v4_policy_changed")
    return (
        _financial_lifetimes_v2(state, state["v3_view"])[1]
        if "v3_view" in state
        else None
    )


def _database_connection_pin_v4(factory):
    """Pin real native creator parameters, not an overridable display URL.

    Values, credentials and route fingerprints remain private RAM. The source
    model is the existing native-engine boundary; hostile DBAs and arbitrary
    in-process replacement are outside that boundary. Only user/password may
    differ BETWEEN writer and reader. Each factory's complete parameters remain
    individually immutable during this invocation.
    """
    pin = history._factory_pin(factory)
    parameters = custody._closure(pin[1].sync_engine.pool._creator).get("cparams")
    allowed = {
        "host",
        "port",
        "database",
        "user",
        "password",
        "ssl",
        "timeout",
        "command_timeout",
        "server_settings",
        "statement_cache_size",
        "prepared_statement_cache_size",
        "max_cached_statement_lifetime",
        "max_cacheable_statement_size",
    }
    if (
        type(parameters) not in (dict, custody.immutabledict)
        or not set(parameters) <= allowed
        or not {"host", "port", "database", "user"} <= set(parameters)
        or type(parameters["host"]) is not str
        or not 1 <= len(parameters["host"]) <= 253
        or any(
            character
            not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:-"
            for character in parameters["host"]
        )
        or type(parameters["port"]) is not int
        or not 1 <= parameters["port"] <= 65535
        or type(parameters["database"]) is not str
        or not 1 <= len(parameters["database"]) <= 63
        or any(
            ord(character) < 32 or 0xD800 <= ord(character) <= 0xDFFF
            for character in parameters["database"]
        )
        or type(parameters["user"]) is not str
        or not parameters["user"]
    ):
        _deny("native_financial_v4_explicit_database_route_required")
    route = {
        name: parameters[name]
        for name in sorted(parameters)
        if name not in {"user", "password"}
    }
    return (type(parameters), _v3_data_pin(dict(parameters))), _v3_data_pin(route)


async def _read_database_identity_v4(state, factory):
    _check_v4(state)
    session = None
    try:
        session = factory()
        if (
            type(session) is not history.AsyncSession
            or session.get_bind().dialect.name != "postgresql"
        ):
            _deny("native_financial_v4_database_session_required")
        await session.begin()
        _check_v4(state)
        for statement in (
            "SET TRANSACTION ISOLATION LEVEL READ COMMITTED",
            "SET LOCAL search_path TO pg_catalog, public, pg_temp",
            "SET LOCAL TIME ZONE 'UTC'",
            "SET LOCAL lock_timeout='1000ms'",
            "SET LOCAL statement_timeout='10000ms'",
        ):
            await session.execute(_V3TextClause(statement))
            _check_v4(state)
        result = await session.execute(_V3TextClause(_V4_DATABASE_IDENTITY_SQL))
        _check_v4(state)
        identity = tuple(result.one())
        if (
            len(identity) != 5
            or type(identity[0]) is not str
            or type(identity[1]) is not int
            or not 0 < identity[1] < 2**32
            or type(identity[2]) is not str
            or not 1 <= len(identity[2]) <= 64
            or type(identity[3]) is not int
            or not 1 <= identity[3] <= 65535
            or type(identity[4]) is not datetime
            or identity[4].tzinfo is not UTC
        ):
            _deny("native_financial_v4_database_identity_invalid")
        await session.commit()
        _check_v4(state)
    finally:
        if session is not None:
            # Standard AsyncSession.__aexit__ creates a shielded child which
            # can outlive its cancelled caller. Own and drain our sole close
            # task explicitly under the existing failed-read cleanup budget.
            await _close_database_session_v4(session)
    _check_v4(state)
    return identity


async def _close_database_session_v4(session):
    async def close_once():
        async with asyncio.timeout(_v4_native_reader.FAILED_READ_CLOSE_SECONDS):
            await session.close()

    task = asyncio.create_task(close_once())
    cancelled = bool(asyncio.current_task().cancelling())
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
        except Exception:  # noqa: BLE001 -- fixed private cleanup error below.
            break
    if cancelled:
        if not task.cancelled():
            task.exception()
        raise asyncio.CancelledError() from None
    try:
        task.result()
    except asyncio.CancelledError:
        raise asyncio.CancelledError() from None
    except Exception:  # noqa: BLE001 -- never expose adapter/credential exception text.
        _deny("native_financial_v4_database_cleanup_uncertain")


async def _bind_databases_v4(state):
    _check_v4(state)
    writer = await _read_database_identity_v4(state, state["v4_writer_factory"])
    reader = await _read_database_identity_v4(state, state["v4_reader_factory"])
    if writer != reader:
        _deny("native_financial_v4_database_identity_changed")
    prior = state.get("v4_database_identity")
    if prior is not None and prior != writer:
        _deny("native_financial_v4_database_identity_changed")
    state["v4_database_identity"] = writer
    _check_v4(state)


async def acquire_native_account_history_financial_v4(
    *,
    read_session,
    private_client,
    scope_config,
    current_account_plan,
    session_factory,
    custody_session_factory,
    frame_session_factory,
    frame_collector_role_oid,
    history_raw_root,
    history_companion_root,
    account_proof_root,
    account_native_root,
):
    """Fixed real acquisitions; no source bytes, clocks, claims or pass flags."""
    original_owner = state = initial = None
    success = False
    try:
        if (
            type(frame_collector_role_oid) is not int
            or not 0 < frame_collector_role_oid < 2**32
        ):
            _deny("native_financial_v4_reader_role_invalid")
        if len(_V4_OWNERS) >= _MAX_OWNERS:
            _deny("native_financial_v4_owner_bound")
        reader_pin, reader, private_reader_pin = history._clone_factory(
            frame_session_factory
        )
        writer_pin = history._factory_pin(session_factory)
        writer_connection = _database_connection_pin_v4(session_factory)
        reader_connection = _database_connection_pin_v4(reader)
        if writer_connection[1] != reader_connection[1]:
            _deny("native_financial_v4_database_route_mismatch")
        context = _context()
        initial = {
            "v3_context": (*context[:3], ref(context[3])),
            "v3_inventory": _package_inventory_v3(),
            "v4_inventory": _package_inventory_v4(),
            "v4_reader_factory": reader,
            "v4_reader_pin": private_reader_pin,
            "v4_original_reader_pin": reader_pin,
            "v4_reader_role_oid": frame_collector_role_oid,
            "v4_writer_factory": session_factory,
            "v4_writer_pin": writer_pin,
            "v4_connection_pins": (writer_connection, reader_connection),
        }
        _register_v4_initial_origin_v5(initial)
        _check_v4(initial)
        await _bind_databases_v4(initial)
        original_owner = await _acquire_native_account_risk_assembly(
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
            _history_version=_CURRENT_HISTORY_V4,
        )
        state = _move_assembly_owner_v3(original_owner, initial)
        original_owner = None
        await _replay_partition_sources_v3(state)
        _check_v4(state)
        result = object.__new__(OwnedNativeAccountHistoryFinancialV4)
        _V4_OWNERS[result] = state
        success = True
        return result
    except asyncio.CancelledError:
        raise
    except NativeAccountRiskAssemblyError:
        raise
    except Exception:  # noqa: BLE001 -- no private transport/SQL/credential text.
        _deny("native_financial_v4_acquisition_failed")
    finally:
        try:
            if original_owner is not None:
                retained = _OWNERS.pop(original_owner, None)
                if retained is not None:
                    _release_v3(retained)
            if state is not None and not success:
                _release_v3(state)
        finally:
            if initial is not None:
                _forget_native_origin_v5(initial)


def _v4_partition_readback(state, facts, view, readback):
    _check_v4(state)
    if (
        type(readback) is not _v3_partition_repository.HistoryPartitionReadbackV1
        or readback.scope != _v3_scope(facts)
        or readback.head_sequence != facts.batches[-1].sequence
        or readback.head_sha256 != facts.batches[-1].event_sha256
        or len(readback.b3_documents) != readback.head_sequence
        or (
            readback.control_revision,
            readback.account_revision,
            readback.ledger_revision,
        )
        != _partition_fences_v3(facts, view)
    ):
        _deny("native_financial_v4_partition_changed")
    for batch in facts.batches:
        if readback.b3_documents[batch.sequence - 1] != batch.document_json:
            _deny("native_financial_v4_original_batch_changed")
    return (
        readback.control_revision,
        readback.account_revision,
        readback.ledger_revision,
    )


def _latest_native_linkage_v4(state, facts):
    from app.trade_qualification import account_history_current_bundle_v2 as bounded

    _check_v4(state)
    native = history._financial_state_v1(state["history"], key=_HOOK_KEY)
    original = history_bundle._json(facts.bundle_raw)
    temporal = original["temporal"]
    document = history_bundle._json(
        facts.batches[-1].document_json, observed.MAX_SOURCE_BYTES
    )
    if (
        type(facts) is not bounded.CurrentHistorySourceFactsV2
        or native.get("partition_current_key") is not history._PARTITION_CURRENT_V2
        or native["facts"].bundle_raw != facts.bundle_raw
        or native["source_sha256"] != facts.bundle_sha256
        or sha(facts.bundle_raw) != facts.bundle_sha256
        or original["policy_sha256"] != bounded.POLICY_SHA256
        or native["original"] != temporal["original_started"]
        or native["custody_proof_sha256"] != facts.proof_sha256
        or facts.proof_sha256 != sha(history.journal.canonical(temporal))
        or native["reference"] != facts.reference
        or document["reference"] != observed.reference_document(facts.reference)
    ):
        _deny("native_financial_v4_original_linkage_changed")
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
        "latest_source_reference": observed.reference_document(facts.reference),
        "native_invocation_id": temporal["stage"]["invocation_id"],
        "custody_bundle_id": native["custody_id"],
        "bundle_sha256": facts.bundle_sha256,
        "proof_sha256": facts.proof_sha256,
        "native_policy_sha256": bounded.POLICY_SHA256,
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
    registration = _v3_partition_records.derive_native_member_registration_v1(
        scope=_v3_scope(facts),
        b3_document_raw=facts.batches[-1].document_json,
        native_linkage_raw=raw,
        expected_b3_policy_sha256=document["policy_sha256"],
        expected_b3_event_sha256=facts.batches[-1].event_sha256,
    )
    return raw, registration, document["policy_sha256"]


def _partition_checkpoint_v4(facts, before, registration, source_policy):
    head = facts.batches[-1].sequence
    if any(row.b3_sequence == head for row in before.members):
        _deny("native_financial_v3_existing_member_not_adoptable")
    if len(before.checkpoints) != (head - 1) // _v3_partition_records.PARTITION_SPAN:
        _deny("native_financial_v4_original_checkpoint_missing")
    if head % _v3_partition_records.PARTITION_SPAN:
        return None
    known = {row.b3_sequence: row.record_json for row in before.members}
    known[head] = registration.record_json
    return _v3_partition_records.build_partition_checkpoint_v1(
        scope=_v3_scope(facts),
        b3_documents=before.b3_documents[-32:],
        native_registrations=tuple(
            known.get(number) for number in range(head - 31, head + 1)
        ),
        previous_checkpoints=tuple(row.record_json for row in before.checkpoints),
        expected_b3_policy_sha256=source_policy,
        expected_b3_head_sha256=facts.batches[-1].event_sha256,
    ).record_json


async def _register_latest_partition_v4(state, facts, view):
    _check_v4(state)
    native = history._financial_state_v1(state["history"], key=_HOOK_KEY)

    def clock():
        return utc_from_ns(_check_v4(state)["utc_ns"])

    repository = _v3_partition_repository.AccountHistoryPartitionRepository(
        native["factory"], clock=clock
    )
    state["v3_repository"], state["v3_repository_clock"] = repository, clock
    await repository.verify_deployment()
    _check_v4(state)
    before = await repository.read(_v3_scope(facts))
    fences = _v4_partition_readback(state, facts, view, before)
    linkage, registration, source_policy = _latest_native_linkage_v4(state, facts)
    checkpoint = _partition_checkpoint_v4(facts, before, registration, source_policy)
    facts, view = await _replay_partition_sources_v3(state, facts, view)
    _check_v4(state)
    anchors = state["v3_anchors"]
    expiry = min(
        view["current"].expires_at,
        view["control"].state.lease_until,
        utc_from_ns(anchors["history_original_anchor"]["utc_ns"])
        + _V3Timedelta(seconds=60),
        utc_from_ns(anchors["coverage_original_anchor"]["utc_ns"])
        + _V3Timedelta(seconds=60),
    )
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
    _v4_partition_readback(state, facts, view, committed)
    matched = tuple(
        row for row in committed.members if row.b3_sequence == committed.head_sequence
    )
    if (
        len(matched) != 1
        or matched[0].record_json != registration.record_json
        or committed.members != (*before.members, *matched)
        or committed.checkpoints
        != (
            before.checkpoints
            if checkpoint is None
            else (*before.checkpoints, committed.checkpoints[-1])
        )
        or (
            checkpoint is not None
            and committed.checkpoints[-1].record_json != checkpoint
        )
        or committed.transaction_id == before.transaction_id
    ):
        _deny("native_financial_v4_committed_registration_changed")
    facts, view = await _replay_partition_sources_v3(state, facts, view)
    final = await repository.read(_v3_scope(facts))
    _v4_partition_readback(state, facts, view, final)
    if (
        _v3_readback_identity(final) != _v3_readback_identity(committed)
        or final.transaction_id == committed.transaction_id
    ):
        _deny("native_financial_v4_final_registration_changed")
    return facts, view, registration, final, expiry


async def _observe_financial_v4(state):
    """One original registration/genesis/read; the caller retains source custody."""
    facts, view = await _replay_partition_sources_v3(state)
    _check_v4(state)
    remaining = (
        state["v3_anchors"]["shorter_monotonic_deadline_ns"]
        - _check_v4(state)["monotonic_ns"]
    ) / 1e9
    if remaining <= 0:
        _deny("native_financial_v3_original_deadline_expired")
    async with asyncio.timeout(remaining):
        (
            facts,
            view,
            registration,
            final,
            original_expiry,
        ) = await _register_latest_partition_v4(state, facts, view)
        stamp = _check_v4(state)
        # This is first construction of this fixed genesis request. Its
        # expiry can only shorten every existing source/Control lifetime.
        expires = min(
            original_expiry, utc_from_ns(stamp["utc_ns"]) + _V3Timedelta(seconds=20)
        )
        checkpoint = final.checkpoints[-1].record_sha256 if final.checkpoints else None
        request = _v4_wire.OriginalFrameRequestV1(
            _v3_scope(facts),
            final.head_sequence,
            final.head_sha256,
            0,
            None,
            final.control_revision,
            final.account_revision,
            final.ledger_revision,
            expires,
        )
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
                name: value
                for name, value in identity_document.items()
                if name != "declared_scope_currency"
            }
        )
        position_source = financial_v2.NativePositionSourceV2(
            position_plan,
            identity,
            facts.positions.raw_archive,
            facts.positions.archive_sha256,
        )
        result = await _v4_reader.replay_original_history_financial_from_pg_v3(
            state["v4_reader_factory"],
            genesis_request=request,
            expected_checkpoint_sha256=checkpoint,
            approved_collector_role_oid=state["v4_reader_role_oid"],
            expected_financial_policy_sha256=sha(
                _v4_stream._financial_stream_policy_bytes_v3()
            ),
            position_source=position_source,
        )
        _check_v4(state)
        if type(result) is not _v4_reader.OriginalHistoryFinancialPgReadObservationV3:
            _deny("native_financial_v4_original_stream_changed")
        facts, view = await _replay_partition_sources_v3(state, facts, view)
        await _bind_databases_v4(state)
        last = await state["v3_repository"].read(_v3_scope(facts))
        _v4_partition_readback(state, facts, view, last)
        if (
            _v3_readback_identity(last) != _v3_readback_identity(final)
            or last.transaction_id == final.transaction_id
        ):
            _deny("native_financial_v4_final_main_changed")
        stamp = _check_v4(state)
        receipt = canonical(
            {
                "schema_version": "ctcc.native_account_history_financial.observation.v4",
                "policy_sha256": _V4_POLICY_SHA256,
                "source_manifest_sha256": state["v3_inventory"]["manifest_sha256"],
                "current_native_bundle_sha256": facts.bundle_sha256,
                "current_native_registration_sha256": registration.record_sha256,
                "original_head_sequence": final.head_sequence,
                "original_head_sha256": final.head_sha256,
                "original_checkpoint_sha256": checkpoint,
                "original_pg_acquisition_sha256": result.acquisition_receipt_sha256,
                "original_financial_stream_sha256": result.replay.receipt_sha256,
                "original_lifetimes": state["v3_anchors"],
                "original_request_expires_at": expires.isoformat(),
                "final_observed_at": utc_from_ns(stamp["utc_ns"]).isoformat(),
                "current_owned_native_source_verified": True,
                "same_actual_writer_reader_database_verified": True,
                "original_db_prefix_replayed": True,
                "historical_native_prefix_verified": False,
                "funding_accrual_events": None,
                "loss_streak_seed": None,
                "historical_native_hwm": None,
                "current_history_tail_finality": None,
                "missing_obligations": list(_MISSING),
                "account_complete": False,
                "account_revision_published": False,
                "execution_authority": False,
                "admission": "DENY",
            }
        )
        if len(receipt) > observed.MAX_RECEIPT_BYTES:
            _deny("native_financial_v4_receipt_bound")
        _check_v4(state)
        observation = NativeAccountHistoryFinancialObservationV4(
            result, view["current"], receipt
        )
        return observation, facts, view, last, expires, stamp


async def _consume_financial_v4(state):
    try:
        (
            observation,
            _facts,
            _view,
            _last,
            _expires,
            _stamp,
        ) = await _observe_financial_v4(state)
        return observation
    except asyncio.CancelledError:
        raise
    except NativeAccountRiskAssemblyError:
        raise
    except Exception:  # noqa: BLE001 -- uncertainty burns the real owner, retains all evidence.
        _deny("native_financial_v4_consumption_failed")
    finally:
        _release_v3(state)


class _NativeFinancialConsumptionV4(_NativeFinancialConsumptionV3):
    __slots__ = ()

    def __init__(self, state):
        self._state = state
        self._coroutine = _consume_financial_v4(state)
        self._started = False
        self._closed = False


def consume_owned_native_account_history_financial_v4(owner):
    """Burn the actual same-task V4 owner before creating any continuation."""
    state = (
        _V4_OWNERS.pop(owner, None)
        if type(owner) is OwnedNativeAccountHistoryFinancialV4
        else None
    )
    if state is None:
        _deny("native_financial_v4_owner_unavailable")
    try:
        _check_v4(state)
        return _NativeFinancialConsumptionV4(state)
    except BaseException:
        _release_v3(state)
        raise


_V4_PIN_FUNCTIONS = tuple(
    (owner, name, _v3_callable_pin(getattr(owner, name)))
    for owner, names in (
        (
            sys.modules[__name__],
            (
                "_acquire_native_account_risk_assembly",
                "_package_inventory_v4",
                "_check_v4",
                "_latest_native_linkage_v4",
                "_partition_checkpoint_v4",
                "_register_latest_partition_v4",
                "_consume_financial_v4",
                "_observe_financial_v4",
                "_v4_partition_readback",
                "_database_connection_pin_v4",
                "_read_database_identity_v4",
                "_bind_databases_v4",
                "_close_database_session_v4",
            ),
        ),
        (
            history,
            (
                "_acquire_native_history_source",
                "acquire_native_history_current_source_v2",
                "_current_population_v2",
                "_take_financial_assembly_v2",
            ),
        ),
        (
            history.current_bundle,
            (
                "CurrentHistorySourceFactsV2",
                "_checked_sources",
                "freeze_current_history_source_bundle_v2",
                "replay_current_history_source_bundle_v2",
            ),
        ),
        (
            _v4_reader,
            (
                "OriginalHistoryFinancialPgReadObservationV3",
                "replay_original_history_financial_from_pg_v3",
            ),
        ),
        (
            _v4_native_reader,
            ("_pages", "_catalog", "_backend", "_check_request", "_execute_one"),
        ),
        (_v4_native_reader._Fence, ("__init__", "check")),
        (
            _v4_wire,
            (
                "decode_original_frame_wire_v1",
                "original_frame_width1_page_v1",
                "_decode_chain",
                "_preflight",
            ),
        ),
        (_v4_wire.OriginalFrameRequestV1, ("__post_init__",)),
        (_v4_wire.journal, ("_original_wire_event",)),
        (
            _v4_stream,
            (
                "_replay_engine",
                "_finish_replay_engine",
                "_finish_financial_stream_v3",
                "_financial_stream_policy_bytes_v3",
                "_original_receipt",
            ),
        ),
        (
            _v4_financial.OriginalHistoryFinancialAccumulatorV2,
            (
                "__init__",
                "consume_current",
                "finish",
                "cancel",
                "_open",
            ),
        ),
        (_v4_financial, ("_component_policies",)),
        (
            _hwm_measurement,
            (
                "_deny",
                "_utc",
                "_fraction",
                "_rational",
                "_read",
                "_measure_verified_packet_v1",
                "measure_original_balance_v1",
                "sampled_peak_from_original_measurements_v1",
            ),
        ),
        (
            _v4_position_sessions,
            (
                "original_account_observations_v3",
                "native_position_rows_for_original_prefix_v3",
            ),
        ),
    )
    for name in names
)
_V4_PIN_DATA = tuple(
    (owner, name, _v3_data_pin(getattr(owner, name)))
    for owner, names in (
        (
            sys.modules[__name__],
            (
                "_CURRENT_HISTORY_V4",
                "_V4_POLICY_SHA256",
                "_V4_EXTRA_PATHS",
                "_V4_DATABASE_IDENTITY_SQL",
            ),
        ),
        (history, ("_PARTITION_CURRENT_V2",)),
        (history.current_bundle, ("SCHEMA", "POLICY_SHA256")),
        (_v4_wire, ("journal",)),
        (_v4_wire.journal, ("_ISSUER", "_JournalEvent")),
        (_v4_stream, ("POLICY_SHA256",)),
        (_v4_financial, ("POLICY_SHA256",)),
        (
            _hwm_measurement,
            (
                "SCHEMA",
                "PEAK_SCHEMA",
                "POLICY_BYTES",
                "POLICY_SHA256",
                "MAX_MEASUREMENT_BYTES",
                "_FIELDS",
                "_MEMBER_DOMAIN",
            ),
        ),
        (_v4_position_sessions, ("POLICY_SHA256",)),
    )
    for name in names
)


# Fixed sampled-HWM purpose: retain the actual V4 source owners through the
# collector's existing custody commit and separate readback. This registry is
# not a portable observation issuer and is never populated from saved DTOs.
_HWM_SOURCE_KEY = _hwm_collector._SOURCE_KEY
_HWM_BINDINGS = WeakKeyDictionary()
_HWM_RELEASE_UNKNOWN = {}  # Strong bounded tombstones, including failed MOVE before return.
_HWM_EXTRA_PATHS = (
    "app/trade_qualification/account_original_balance_measurement_v1.py",
    "app/trade_qualification/account_native_sampled_hwm_v1.py",
)
_HWM_MAX_OBSERVATION_BYTES = _v4_financial.MAX_COMPACT_BYTES
_HWM_MAX_BINDINGS = 16
_HWM_ERROR_CODES = frozenset(
    {
        "native_hwm_fixed_collector_required",
        "native_hwm_owner_unavailable",
        "native_hwm_binding_unavailable",
        "native_hwm_binding_bound",
        "native_hwm_context_changed",
        "native_hwm_source_changed",
        "native_hwm_context_required",
        "native_hwm_recheck_failed",
        "native_hwm_observation_changed",
        "native_hwm_observation_bound",
        "native_hwm_original_expiry",
        "native_hwm_original_prefix_changed",
        "native_hwm_recheck_count",
        "native_hwm_release_unknown",
    }
)


class _NativeHWMBindingV1(OwnedNativeAccountFinancialOperandsV3):
    __slots__ = ()


def _native_hwm_key_v1(key):
    if key is not _HWM_SOURCE_KEY or _hwm_collector._SOURCE_KEY is not _HWM_SOURCE_KEY:
        _deny("native_hwm_fixed_collector_required")


def _package_inventory_hwm_v1():
    # _check_v4 already verifies the entire original inventory before and
    # after this purpose check. Read only the two new fixed dependencies here.
    manifest, manifest_pin = coverage._read_file(
        _ROOT / "MANIFEST.sha256", 2 * 1024 * 1024
    )
    rows = {}
    for line in manifest.decode("ascii").splitlines():
        digest, separator, name = line.partition("  ")
        if not separator or coverage._SHA.fullmatch(digest) is None or name in rows:
            _deny("native_hwm_source_changed")
        rows[name] = digest
    files = {}
    for name in _HWM_EXTRA_PATHS:
        raw, pin = coverage._read_file(_ROOT / name, 4 * 1024 * 1024)
        if rows.get(name) != sha(raw):
            _deny("native_hwm_source_changed")
        files[name] = pin
    return sha(manifest), manifest_pin, files


def _native_hwm_context_v1(state):
    # _check_binding verifies the original Task, CURRENT source, Control lease,
    # native factories and custody source; it does not acquire another owner.
    held, _stamp = coverage._check_binding(state["coverage"])
    factory, repository = held["factory"], held["custody"]
    if (
        type(factory) is not history.async_sessionmaker
        or factory.class_ is not history.AsyncSession
        or type(repository) is not custody.RuntimeBundleCustodyRepository
        or repository._poisoned is not False
    ):
        _deny("native_hwm_context_required")
    repository._check_factory()
    return factory, repository


def _check_native_hwm_v1(record):
    context = record["context"]
    actual = _context()
    if actual[:3] != context[:3] or actual[3] is not context[3]():
        _deny("native_hwm_context_changed")
    stamp = _check_v4(record["state"])
    if type(stamp) is not dict:
        _deny("native_hwm_context_required")
    if _package_inventory_hwm_v1() != record["inventory"]:
        _deny("native_hwm_source_changed")
    for owner, name, expected in _HWM_PIN_FUNCTIONS:
        actual_pin = _v3_callable_pin(getattr(owner, name))
        if (
            any(actual_pin[n] is not expected[n] for n in (0, 1, 2))
            or actual_pin[3:] != expected[3:]
        ):
            _deny("native_hwm_source_changed")
    for owner, name, expected in _HWM_PIN_DATA:
        if _v3_data_pin(getattr(owner, name)) != expected:
            _deny("native_hwm_source_changed")
    live = _native_hwm_context_v1(record["state"])
    if any(
        found is not expected
        for found, expected in zip(live, record["destination"], strict=True)
    ):
        _deny("native_hwm_context_changed")
    # The factory/custody checks can themselves perform synchronous source
    # readback. Measure again immediately before the caller starts its await.
    stamp = _check_v4(record["state"])
    expires = record.get("expires")
    if expires is not None and utc_from_ns(stamp["utc_ns"]) >= expires:
        _deny("native_hwm_original_expiry")
    return stamp


def _native_hwm_prefix_pin_v1(value):
    # Keep no externally mutable readback DTO or whole-prefix copy. Exact
    # original bytes and every member/checkpoint enter this private RAM pin.
    digest = _HWM_SHA256()

    def add(raw):
        if type(raw) is not bytes:
            _deny("native_hwm_original_prefix_changed")
        digest.update(len(raw).to_bytes(8, "big"))
        digest.update(raw)

    add(
        canonical(
            {
                "scope": list(_v3_partition_records._scope(value.scope)),
                "head_sequence": value.head_sequence,
                "head_sha256": value.head_sha256,
                "control_revision": value.control_revision,
                "account_revision": value.account_revision,
                "ledger_revision": value.ledger_revision,
                "b3_count": len(value.b3_documents),
                "member_count": len(value.members),
                "checkpoint_count": len(value.checkpoints),
            }
        )
    )
    for raw in value.b3_documents:
        add(raw)
    for row in value.members:
        add(
            canonical([row.b3_sequence, row.record_sha256, row.recorded_at.isoformat()])
        )
        add(row.record_json)
    for row in value.checkpoints:
        add(
            canonical(
                [row.partition_number, row.record_sha256, row.recorded_at.isoformat()]
            )
        )
        add(row.record_json)
    return digest.digest()


def _freeze_native_hwm_observation_v1(observation):
    if type(observation) is not NativeAccountHistoryFinancialObservationV4:
        _deny("native_hwm_observation_changed")
    acquired = observation.original_history
    if (
        type(acquired) is not _v4_reader.OriginalHistoryFinancialPgReadObservationV3
        or type(acquired.replay)
        is not _v4_stream.OriginalHistoryFinancialStreamReplayV3
        or type(acquired.replay.original_replay)
        is not _v4_stream.OriginalHistoryStreamReplayV1
        or type(acquired.replay.financial_facts)
        is not _v4_financial.GlobalFinancialHistoryFactsV2
    ):
        _deny("native_hwm_observation_changed")
    raw = (
        observation.receipt_json,
        acquired.acquisition_receipt_json,
        acquired.replay.receipt_json,
        acquired.replay.original_replay.receipt_json,
        acquired.replay.financial_facts.receipt_json,
    )
    if any(type(item) is not bytes or not item for item in raw):
        _deny("native_hwm_observation_changed")
    if sum(map(len, raw)) > _HWM_MAX_OBSERVATION_BYTES:
        _deny("native_hwm_observation_bound")
    if type(acquired.observed_frames) is not int or acquired.observed_frames < 1:
        _deny("native_hwm_observation_changed")
    times = (
        acquired.first_request_started_at,
        acquired.last_rpc_received_at,
        acquired.measured_validation_at,
        acquired.session_closed_at,
    )
    if (
        any(type(item) is not datetime or item.tzinfo is not UTC for item in times)
        or tuple(sorted(times)) != times
    ):
        _deny("native_hwm_observation_changed")
    return raw, acquired.observed_frames, times


def _materialize_native_hwm_observation_v1(frozen, current_account):
    # Fresh wrappers prevent a consumer-mutated frozen dataclass from polluting
    # later checks. Only the once-verified immutable bytes are retained.
    raw, frames, times = frozen
    replay = _v4_stream.OriginalHistoryFinancialStreamReplayV3(
        raw[2],
        _v4_stream.OriginalHistoryStreamReplayV1(raw[3]),
        _v4_financial.GlobalFinancialHistoryFactsV2(raw[4]),
    )
    acquired = _v4_reader.OriginalHistoryFinancialPgReadObservationV3(
        replay,
        raw[1],
        frames,
        *times,
    )
    return NativeAccountHistoryFinancialObservationV4(acquired, current_account, raw[0])


def _release_native_hwm_binding_v1(binding, *, key):
    _native_hwm_key_v1(key)
    if type(binding) is not _NativeHWMBindingV1:
        _deny("native_hwm_binding_unavailable")
    if binding in _HWM_RELEASE_UNKNOWN:
        _deny("native_hwm_release_unknown")  # Already attempted; never retry.
    record = _HWM_BINDINGS.pop(binding, None)
    if record is None:
        return  # A failed recheck already burned and released this exact binding.
    state = record["state"]
    try:
        # The original release clears state even when an underlying release
        # fails. Keep its exact original owner/factory/source references in
        # private RAM, including when take() has not returned the binding yet.
        record["release_source_state"] = dict(state)
        record["release_source_origin"] = _STATE_ORIGIN_STORAGE_V5.get(id(state))
        _release_v3(state)
    except BaseException as error:  # noqa: BLE001 -- retain exact source state before propagating cancellation or release failure.
        _HWM_RELEASE_UNKNOWN[binding] = record
        if isinstance(error, asyncio.CancelledError):
            raise asyncio.CancelledError() from None
        if isinstance(error, (KeyboardInterrupt, SystemExit)):
            raise type(error)() from None
        _deny("native_hwm_release_unknown")
    record.clear()


def _native_hwm_fail_v1(binding, error):
    release_error = None
    try:
        _release_native_hwm_binding_v1(binding, key=_HWM_SOURCE_KEY)
    except BaseException as caught:  # noqa: BLE001 -- preserve release failure while prioritizing sanitized control-flow rethrow.
        release_error = caught
    for failure in (error, release_error):
        if isinstance(failure, asyncio.CancelledError):
            raise asyncio.CancelledError() from None
        if isinstance(failure, (KeyboardInterrupt, SystemExit)):
            raise type(failure)() from None
    if release_error is not None:
        _deny("native_hwm_release_unknown")
    if (
        type(error) is NativeAccountRiskAssemblyError
        and len(error.args) == 1
        and type(error.args[0]) is str
        and error.args[0] in _HWM_ERROR_CODES
    ):
        _deny(error.args[0])
    _deny("native_hwm_recheck_failed")


def _take_native_hwm_binding_v1(owner, *, key):
    """Exclusive call-time MOVE of the real live V4 history/coverage state."""
    _native_hwm_key_v1(key)
    # Validate the fixed cap before it is used and before the original MOVE.
    # Later structural pin checks also reject mutation while a binding is held.
    if type(_HWM_MAX_BINDINGS) is not int or _HWM_MAX_BINDINGS != 16:
        _deny("native_hwm_binding_bound")
    # Reject before moving another real owner. No failed release may overflow
    # the bounded strong tombstones or disappear through weak-key collection.
    if len(_HWM_BINDINGS) + len(_HWM_RELEASE_UNKNOWN) >= _HWM_MAX_BINDINGS:
        _deny("native_hwm_binding_bound")
    state = (
        _V4_OWNERS.pop(owner, None)
        if type(owner) is OwnedNativeAccountHistoryFinancialV4
        else None
    )
    if state is None:
        _deny("native_hwm_owner_unavailable")
    binding = object.__new__(_NativeHWMBindingV1)
    record = {"state": state}
    _HWM_BINDINGS[binding] = record
    try:
        if len(_HWM_BINDINGS) + len(_HWM_RELEASE_UNKNOWN) > _HWM_MAX_BINDINGS:
            _deny("native_hwm_binding_bound")
        _check_v4(state)
        context = _context()
        record.update(
            {
                "context": (*context[:3], ref(context[3])),
                "inventory": _package_inventory_hwm_v1(),
                "destination": _native_hwm_context_v1(state),
                "phase": "NEW",
                "rechecks": 0,
            }
        )
        _check_native_hwm_v1(record)
        return binding
    except BaseException as error:  # noqa: BLE001 -- finalize moved ownership on every failure, including cancellation.
        _native_hwm_fail_v1(binding, error)


def _native_hwm_binding_v1(binding, *, key):
    """Synchronous last source/lease/factory check immediately before seal."""
    _native_hwm_key_v1(key)
    record = (
        _HWM_BINDINGS.get(binding) if type(binding) is _NativeHWMBindingV1 else None
    )
    if record is None:
        _deny("native_hwm_binding_unavailable")
    try:
        _check_native_hwm_v1(record)
        factory, repository = record["destination"]
        return _V3MappingProxyType(
            {"factory": factory, "custody_repository": repository}
        )
    except BaseException as error:  # noqa: BLE001 -- burn or retain the binding when the final source check fails.
        _native_hwm_fail_v1(binding, error)


async def _native_hwm_recheck_v1(binding, *, key):
    """One real prefix observation, then same-source checks; never renew."""
    _native_hwm_key_v1(key)
    record = (
        _HWM_BINDINGS.get(binding) if type(binding) is _NativeHWMBindingV1 else None
    )
    if record is None:
        _deny("native_hwm_binding_unavailable")
    try:
        state = record["state"]
        stamp = _check_native_hwm_v1(record)
        phase = record["phase"]
        if phase not in ("NEW", "READY") or record["rechecks"] >= 3:
            _deny("native_hwm_recheck_count")
        record["phase"] = "BUSY"  # Set before any uncertain registration await.
        remaining = (
            state["v3_anchors"]["shorter_monotonic_deadline_ns"] - stamp["monotonic_ns"]
        ) / 1e9
        if "expires" in record:
            remaining = min(
                remaining,
                (record["expires"] - utc_from_ns(stamp["utc_ns"])).total_seconds(),
            )
        if remaining <= 0:
            _deny("native_hwm_original_expiry")
        async with asyncio.timeout(remaining):
            if phase == "NEW":
                (
                    observation,
                    facts,
                    view,
                    final,
                    expires,
                    _stamp,
                ) = await _observe_financial_v4(state)
                record["expires"] = expires
                record["observation"] = _freeze_native_hwm_observation_v1(observation)
                record["prefix_pin"] = _native_hwm_prefix_pin_v1(final)
                record["transaction_id"] = final.transaction_id
            else:
                facts, view = await _replay_partition_sources_v3(state)
                _check_native_hwm_v1(record)
                await _bind_databases_v4(state)
                final = await state["v3_repository"].read(_v3_scope(facts))
                _v4_partition_readback(state, facts, view, final)
                if (
                    _native_hwm_prefix_pin_v1(final) != record["prefix_pin"]
                    or final.transaction_id == record["transaction_id"]
                ):
                    _deny("native_hwm_original_prefix_changed")
                record["transaction_id"] = final.transaction_id
                # No cached observation is treated as a fresh source check.
                # Recheck the actual held sources after the last DB await too.
                facts, view = await _replay_partition_sources_v3(state, facts, view)
            stamp = _check_native_hwm_v1(record)
            record["phase"] = "READY"
            record["rechecks"] += 1
            observation = _materialize_native_hwm_observation_v1(
                record["observation"], view["current"]
            )
            return facts, view, stamp, observation
    except BaseException as error:  # noqa: BLE001 -- finalize uncertain awaited work without losing the original binding.
        _native_hwm_fail_v1(binding, error)


_HWM_PIN_FUNCTIONS = tuple(
    (owner, name, _v3_callable_pin(getattr(owner, name)))
    for owner, names in (
        (
            sys.modules[__name__],
            (
                "_native_hwm_key_v1",
                "_package_inventory_hwm_v1",
                "_native_hwm_context_v1",
                "_check_native_hwm_v1",
                "_native_hwm_prefix_pin_v1",
                "_freeze_native_hwm_observation_v1",
                "_materialize_native_hwm_observation_v1",
                "_release_native_hwm_binding_v1",
                "_native_hwm_fail_v1",
                "_take_native_hwm_binding_v1",
                "_native_hwm_binding_v1",
                "_native_hwm_recheck_v1",
            ),
        ),
        (
            _hwm_collector,
            (
                "_deny",
                "_assembly",
                "_context",
                "_utc",
                "_view",
                "_remaining",
                "record_owned_native_sampled_hwm_v1",
            ),
        ),
        (
            _hwm_measurement,
            (
                "_deny",
                "_utc",
                "_fraction",
                "_rational",
                "_read",
                "_measure_verified_packet_v1",
                "measure_original_balance_v1",
                "sampled_peak_from_original_measurements_v1",
            ),
        ),
    )
    for name in names
)
_HWM_PIN_DATA = tuple(
    (owner, name, _v3_data_pin(getattr(owner, name)))
    for owner, names in (
        (
            sys.modules[__name__],
            (
                "_HWM_SOURCE_KEY",
                "_HWM_EXTRA_PATHS",
                "_HWM_MAX_OBSERVATION_BYTES",
                "_HWM_MAX_BINDINGS",
                "_HWM_ERROR_CODES",
            ),
        ),
        (
            _hwm_collector,
            (
                "_SOURCE_KEY",
                "SCHEMA",
                "POLICY_BYTES",
                "POLICY_SHA256",
                "MAX_RECORD_BYTES",
                "ERROR_CODES",
            ),
        ),
        (
            _hwm_measurement,
            (
                "SCHEMA",
                "PEAK_SCHEMA",
                "POLICY_BYTES",
                "POLICY_SHA256",
                "MAX_MEASUREMENT_BYTES",
                "_FIELDS",
                "_MEMBER_DOMAIN",
            ),
        ),
    )
    for name in names
)


# Additive OFF/bootstrap acquisition. Original V4 entries stay unchanged.
_V5_OWNERS = {}
_V5_OWNER_STORAGE = _V5_OWNERS
_V5_OWNER_STORAGE_ID = id(_V5_OWNER_STORAGE)
_V5_RELEASE_UNKNOWN = {}
_V5_RELEASE_STORAGE = _V5_RELEASE_UNKNOWN
_V5_RELEASE_STORAGE_ID = id(_V5_RELEASE_STORAGE)
_V5_API_ASOF_INFLIGHT = {}
_V5_API_ASOF_STORAGE = _V5_API_ASOF_INFLIGHT
_V5_API_ASOF_STORAGE_ID = id(_V5_API_ASOF_STORAGE)
_V5_SOURCE_POLICY_BYTES = canonical(
    {
        "version": "ctcc.native_current_first_off_foundation.v5",
        "acquisition": "fresh_CURRENT_then_fresh_history_same_original_session",
        "cutoff": "history_cutoff_covers_original_CURRENT_availability_boundary",
        "current_time": "never_relabelled_to_later_history_cutoff_or_now",
        "current_after_history": "freshness_state_change_terminal_finality_still_required",
        "arm": "OFF_owner_never_crosses_Arm_or_gains_submit_authority",
        "expiry": "original_anchors_and_shortest_deadline_unchanged",
        "account_complete": False,
        "account_revision_issued": False,
        "execution_authority": False,
    }
)
_V5_SOURCE_POLICY_SHA256 = sha(_V5_SOURCE_POLICY_BYTES)


class OwnedNativeAccountHistoryFoundationV5(OwnedNativeAccountFinancialOperandsV3):
    __slots__ = ()


def _release_current_first_foundation_v5(state):
    _release_current_first_partial_v5(state, None, None)


def _check_current_first_foundation_v5(state):
    stamp = _check_v4(state)
    if _origin_record_v5(state)[0] != "V5_CURRENT_FIRST_OFF":
        _deny("native_current_first_v5_source_version_changed")
    return stamp


async def _bind_current_first_order_v5(state):
    facts, view = await _replay_partition_sources_v3(state)
    _check_v4(state)
    source = view["current"]
    if type(source) is not current.RecordedCurrentAccountBundleV4:
        _deny("native_current_first_v5_current_source_changed")
    completed = source.packet.completed_at
    cutoff = facts.packet.plan.history_end
    first_history_request = min(
        page.request_started_at for page in facts.packet.observations
    )
    if not completed < first_history_request or cutoff < completed:
        _deny("native_current_first_v5_history_does_not_cover_CURRENT")
    pin = (
        source.reference.packet_sha256,
        completed.isoformat(),
        cutoff.isoformat(),
        first_history_request.isoformat(),
    )
    state["v5_order_pin"] = (*pin, sha(canonical(list(pin))))
    _check_current_first_foundation_v5(state)


async def _acquire_current_first_foundation_v5(
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
    _origin_ticket,
    _json_guard=_json_lexical_guard.require,
    _json_binding=_json_lexical_guard.BINDING,
    _json_module=_json_lexical_guard,
):
    """Acquire original sources; no caller owner/clock/seed/claims/flags accepted."""
    if (
        _json_module is not _json_lexical_guard
        or _json_binding is not _json_module.BINDING
        or _json_guard is not _json_binding[0]
        or (_json_guard is not _json_module.require)
        or (_json_guard.__code__ != _json_binding[1])
        or (_json_guard.__code__.co_filename != _json_binding[1].co_filename)
        or (_json_guard.__defaults__ is not _json_module._ENTRY_DEFAULTS)
        or (_json_guard.__kwdefaults__ is not None)
        or (_json_guard.__closure__ is not None)
        or (_json_guard.__globals__ is not _json_module.__dict__)
        or (_json_guard.__builtins__ is not _json_module._BUILTINS)
    ):
        raise ValueError("history_json_guard_entry_changed") from None
    _json_guard()
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
            or (read_session._owner is not private_client)
            or (type(scope_config) is not history.NativeHistoryScopeV1)
            or (
                type(current_account_plan)
                is not capture.CurrentDemoAccountCapturePlanV7
            )
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
            or (plan.settlement_currency != "USDT")
            or (plan.environment != "demo")
            or (plan.origin != read_session._origin)
            or (plan.registration_region != "global")
            or (
                plan.registration_evidence_sha256
                != scope_config.registration_evidence_sha256
            )
            or (plan.leverage_instrument_ids != scope_config.leverage_instrument_ids)
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
            (
                a == z or a in z.parents or z in a.parents
                for i, a in enumerate(roots)
                for z in roots[i + 1 :]
            )
        ):
            _deny("native_financial_distinct_roots_required")
        state = {
            "context": (*context[:3], ref(context[3])),
            "v5_inventory": _package_inventory_current_first_v5(),
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
        _register_v5_origin_v5(state, _origin_ticket, "ACQUIRING")
        _check(state)
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
        _capture_current_first_pre_take_v5(state, coverage_owner, "coverage")
        state["coverage"] = coverage._take_financial_assembly_v1(
            coverage_owner, key=_HOOK_KEY
        )
        coverage_owner = None
        history_capture = history._acquire_native_history_current_asof_v3
        history_owner = await history_capture(
            read_session=read_session,
            private_client=private_client,
            scope_config=scope_config,
            session_factory=session_factory,
            custody_session_factory=custody_session_factory,
            raw_root=history_raw_root,
            companion_root=history_companion_root,
            coverage_binding=state["coverage"],
            key=_HOOK_KEY,
        )
        _check(state)
        history_take = history._take_financial_assembly_v2
        _capture_current_first_pre_take_v5(state, history_owner, "history")
        state["history"] = history_take(history_owner, key=_HOOK_KEY)
        history_owner = None
        _facts, _view = await _replay(state)
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
        if not successful:
            _release_current_first_partial_v5(state, history_owner, coverage_owner)


async def acquire_native_account_history_foundation_v5(
    *,
    read_session,
    private_client,
    scope_config,
    current_account_plan,
    session_factory,
    custody_session_factory,
    frame_session_factory,
    frame_collector_role_oid,
    history_raw_root,
    history_companion_root,
    account_proof_root,
    account_native_root,
):
    """Fixed real acquisitions; no source bytes, clocks, claims or pass flags."""
    original_owner = state = initial = ticket = None
    success = False
    try:
        if (
            type(frame_collector_role_oid) is not int
            or not 0 < frame_collector_role_oid < 2**32
        ):
            _deny("native_financial_v4_reader_role_invalid")
        ticket = _new_acquisition_ticket_v5()
        reader_pin, reader, private_reader_pin = history._clone_factory(
            frame_session_factory
        )
        writer_pin = history._factory_pin(session_factory)
        writer_connection = _database_connection_pin_v4(session_factory)
        reader_connection = _database_connection_pin_v4(reader)
        if writer_connection[1] != reader_connection[1]:
            _deny("native_financial_v4_database_route_mismatch")
        context = _context()
        initial = {
            "v3_context": (*context[:3], ref(context[3])),
            "v5_inventory": _package_inventory_current_first_v5(),
            "v3_inventory": _package_inventory_v3(),
            "v4_inventory": _package_inventory_v4(),
            "v4_reader_factory": reader,
            "v4_reader_pin": private_reader_pin,
            "v4_original_reader_pin": reader_pin,
            "v4_reader_role_oid": frame_collector_role_oid,
            "v4_writer_factory": session_factory,
            "v4_writer_pin": writer_pin,
            "v4_connection_pins": (writer_connection, reader_connection),
        }
        _register_v5_origin_v5(initial, ticket, "INITIAL")
        _check_v4(initial)
        await _bind_databases_v4(initial)
        original_owner = await _acquire_current_first_foundation_v5(
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
            _origin_ticket=ticket,
        )
        state = _move_assembly_owner_v3(original_owner, initial)
        original_owner = None
        await _replay_partition_sources_v3(state)
        _check_v4(state)
        await _bind_current_first_order_v5(state)
        _check_current_first_foundation_v5(state)
        _mark_current_first_held_v5(state)
        result = object.__new__(OwnedNativeAccountHistoryFoundationV5)
        _V5_OWNERS[result] = state
        success = True
        return result
    except asyncio.CancelledError:
        raise
    except NativeAccountRiskAssemblyError:
        raise
    except Exception:  # noqa: BLE001 -- no private transport/SQL/credential text.
        _deny("native_financial_v4_acquisition_failed")
    finally:
        try:
            if original_owner is not None:
                retained = _OWNERS.pop(original_owner, None)
                if retained is not None:
                    _release_current_first_partial_v5(retained, None, None)
            if state is not None and not success:
                _release_current_first_partial_v5(state, None, None)
        finally:
            if initial is not None:
                _forget_native_origin_v5(initial)
            if ticket is not None:
                _V5_ACQUISITIONS.pop(ticket, None)


# Explicit internal source-generation dispatch. Every V4 check has a genuine
# native mint record; a missing/copied V5 marker cannot fall back to V4.
_STATE_ORIGINS_V5 = {}
_STATE_ORIGIN_STORAGE_V5 = _STATE_ORIGINS_V5
_STATE_ORIGIN_STORAGE_ID_V5 = id(_STATE_ORIGIN_STORAGE_V5)
_V5_ACQUISITIONS = {}
_V5_ACQUISITION_STORAGE = _V5_ACQUISITIONS
_V5_ACQUISITION_STORAGE_ID = id(_V5_ACQUISITION_STORAGE)
_V5_MAX_BINDINGS = 16
_V5_ORIGIN_MAX_STATES = 2 * _MAX_OWNERS + 2 * _V5_MAX_BINDINGS


def _origin_storage_v5():
    if (
        type(_V5_API_ASOF_INFLIGHT) is not dict
        or _V5_API_ASOF_INFLIGHT is not _V5_API_ASOF_STORAGE
        or id(_V5_API_ASOF_STORAGE) != _V5_API_ASOF_STORAGE_ID
        or type(_V5_OWNERS) is not dict
        or _V5_OWNERS is not _V5_OWNER_STORAGE
        or id(_V5_OWNER_STORAGE) != _V5_OWNER_STORAGE_ID
        or type(_V5_RELEASE_UNKNOWN) is not dict
        or _V5_RELEASE_UNKNOWN is not _V5_RELEASE_STORAGE
        or id(_V5_RELEASE_STORAGE) != _V5_RELEASE_STORAGE_ID
        or type(_STATE_ORIGINS_V5) is not dict
        or _STATE_ORIGINS_V5 is not _STATE_ORIGIN_STORAGE_V5
        or id(_STATE_ORIGIN_STORAGE_V5) != _STATE_ORIGIN_STORAGE_ID_V5
        or type(_V5_ACQUISITIONS) is not dict
        or _V5_ACQUISITIONS is not _V5_ACQUISITION_STORAGE
        or id(_V5_ACQUISITION_STORAGE) != _V5_ACQUISITION_STORAGE_ID
        or type(_V5_MAX_BINDINGS) is not int
        or _V5_MAX_BINDINGS != 16
        or type(_V5_ORIGIN_MAX_STATES) is not int
        or _V5_ORIGIN_MAX_STATES != 544
    ):
        _deny("native_source_generation_storage_changed")


def _origin_functions_v5():
    for owner, name, expected in _ORIGIN_PIN_FUNCTIONS_V5:
        actual = _v3_callable_pin(getattr(owner, name))
        if (
            any(actual[n] is not expected[n] for n in (0, 1, 2))
            or actual[3:] != expected[3:]
        ):
            _deny("native_source_generation_code_changed")
    for owner, name, expected in _ORIGIN_PIN_DATA_V5:
        if _v3_data_pin(getattr(owner, name)) != expected:
            _deny("native_source_generation_policy_changed")


def _origin_record_v5(state):
    _origin_storage_v5()
    _origin_functions_v5()
    record = _STATE_ORIGINS_V5.get(id(state))
    if (
        type(state) is not dict
        or type(record) is not tuple
        or len(record) != 6
        or record[2] is not state
        or state.get("_native_source_generation") is not record[1]
        or state.get("_native_source_version") != record[0]
    ):
        _deny("native_source_generation_missing_changed_or_copied")
    return record


def _register_v4_initial_origin_v5(state):
    _origin_storage_v5()
    _origin_functions_v5()
    if (
        type(state) is not dict
        or id(state) in _STATE_ORIGINS_V5
        or "_native_source_generation" in state
        or "_native_source_version" in state
        or len(_STATE_ORIGINS_V5) >= _V5_ORIGIN_MAX_STATES
    ):
        _deny("native_v4_source_generation_mint_denied")
    nonce = object()
    state["_native_source_generation"] = nonce
    state["_native_source_version"] = "V4_ORIGINAL_ORDER"
    _STATE_ORIGINS_V5[id(state)] = (
        "V4_ORIGINAL_ORDER",
        nonce,
        state,
        None,
        "INITIAL",
        None,
    )


def _new_acquisition_ticket_v5():
    _origin_storage_v5()
    _origin_functions_v5()
    if (
        len(_V5_ACQUISITIONS)
        + len(_V5_OWNERS)
        + len(_V5_API_ASOF_INFLIGHT)
        + len(_HWM_BINDINGS)
        + len(_HWM_RELEASE_UNKNOWN)
        + len(_V5_RELEASE_UNKNOWN)
        >= _V5_MAX_BINDINGS
    ):
        _deny("native_current_first_v5_binding_bound")
    context = _context()
    ticket = object()
    _V5_ACQUISITIONS[ticket] = (*context[:3], ref(context[3]))
    return ticket


def _check_acquisition_ticket_v5(ticket):
    _origin_storage_v5()
    context = _context()
    expected = _V5_ACQUISITIONS.get(ticket)
    if (
        type(expected) is not tuple
        or len(expected) != 4
        or expected[:3] != context[:3]
        or expected[3]() is not context[3]
    ):
        _deny("native_current_first_v5_acquisition_context_changed")


def _register_v5_origin_v5(state, ticket, phase):
    _origin_storage_v5()
    _origin_functions_v5()
    _check_acquisition_ticket_v5(ticket)
    if (
        type(state) is not dict
        or phase not in ("INITIAL", "ACQUIRING")
        or id(state) in _STATE_ORIGINS_V5
        or "_native_source_generation" in state
        or "_native_source_version" in state
        or len(_STATE_ORIGINS_V5) >= _V5_ORIGIN_MAX_STATES
    ):
        _deny("native_current_first_v5_generation_mint_denied")
    nonce = object()
    state["_native_source_generation"] = nonce
    state["_native_source_version"] = "V5_CURRENT_FIRST_OFF"
    _STATE_ORIGINS_V5[id(state)] = (
        "V5_CURRENT_FIRST_OFF",
        nonce,
        state,
        ticket,
        phase,
        None,
    )


def _transfer_native_origin_v5(initial, state):
    """One synchronous transfer immediately after the existing native MOVE."""
    before = _origin_record_v5(initial)
    if before[4] != "INITIAL" or state is initial:
        _deny("native_source_generation_transfer_denied")
    if before[0] == "V4_ORIGINAL_ORDER":
        if id(state) in _STATE_ORIGINS_V5:
            _deny("native_source_generation_transfer_denied")
        if (
            state.get("_native_source_generation") is not before[1]
            or state.get("_native_source_version") != before[0]
        ):
            _deny("native_source_generation_transfer_denied")
        nonce = object()
        state["_native_source_generation"] = nonce
        _STATE_ORIGINS_V5[id(state)] = (before[0], nonce, state, None, "HELD", None)
    elif before[0] == "V5_CURRENT_FIRST_OFF":
        _check_acquisition_ticket_v5(before[3])
        actual = _STATE_ORIGINS_V5.get(id(state))
        # The existing MOVE copied INITIAL's fields into the exact already
        # registered ACQUIRING state. Restore its own nonce, never copy mint.
        if (
            type(actual) is not tuple
            or len(actual) != 6
            or actual[0] != before[0]
            or actual[2] is not state
            or actual[3] is not before[3]
            or actual[4] != "ACQUIRING"
            or state.get("_native_source_generation") is not before[1]
            or state.get("_native_source_version") != before[0]
        ):
            _deny("native_source_generation_transfer_denied")
        state["_native_source_generation"] = actual[1]
    else:
        _deny("native_source_generation_transfer_denied")
    _STATE_ORIGINS_V5.pop(id(initial))
    _check_native_origin_dispatch_v5(state)


def _forget_native_origin_v5(state):
    """Cleanup only; no malformed state can acquire authority through release."""
    record = _STATE_ORIGIN_STORAGE_V5.get(id(state))
    if type(record) is tuple and len(record) == 6 and record[2] is state:
        _STATE_ORIGIN_STORAGE_V5.pop(id(state))


def _check_native_origin_dispatch_v5(state):
    record = _origin_record_v5(state)
    if record[0] == "V4_ORIGINAL_ORDER":
        if (
            record[3] is not None
            or record[4] not in ("INITIAL", "HELD")
            or record[5] is not None
        ):
            _deny("native_v4_source_generation_changed")
        return
    if record[0] != "V5_CURRENT_FIRST_OFF":
        _deny("native_source_version_unknown")
    _check_current_first_structure_v5(state, record)


def _mark_current_first_held_v5(state):
    record = _origin_record_v5(state)
    if record[0] != "V5_CURRENT_FIRST_OFF" or record[4] != "ACQUIRING":
        _deny("native_current_first_v5_early_phase_owner_denied")
    _check_acquisition_ticket_v5(record[3])
    pin = state.get("v5_order_pin")
    if (
        type(pin) is not tuple
        or len(pin) != 5
        or sha(canonical(list(pin[:4]))) != pin[4]
    ):
        _deny("native_current_first_v5_order_unverified")
    _STATE_ORIGINS_V5[id(state)] = (*record[:4], "HELD", pin)
    _check_native_origin_dispatch_v5(state)


def _check_current_first_structure_v5(state, origin):
    if (
        origin[0] != "V5_CURRENT_FIRST_OFF"
        or origin[4]
        not in ("INITIAL", "ACQUIRING", "HELD", "API_ASOF_BUSY", "API_ASOF_HELD")
        or _package_inventory_current_first_v5() != state["v5_inventory"]
    ):
        _deny("native_current_first_v5_source_changed")
    if origin[4] in ("INITIAL", "ACQUIRING"):
        _check_acquisition_ticket_v5(origin[3])
    for owner, name, expected in _V5_PIN_FUNCTIONS:
        actual = _v3_callable_pin(getattr(owner, name))
        if (
            any(actual[n] is not expected[n] for n in (0, 1, 2))
            or actual[3:] != expected[3:]
        ):
            _deny("native_current_first_v5_source_changed")
    for owner, name, expected in _V5_PIN_DATA:
        if _v3_data_pin(getattr(owner, name)) != expected:
            _deny("native_current_first_v5_source_changed")
    if origin[4] in ("HELD", "API_ASOF_BUSY", "API_ASOF_HELD"):
        if origin[4] == "API_ASOF_HELD" and (
            type(origin[5]) is not tuple or len(origin[5]) != 2
        ):
            _deny("native_current_first_v5_asof_binding_changed")
        order = origin[5][0] if origin[4] == "API_ASOF_HELD" else origin[5]
        if (
            state.get("v5_order_pin") != order
            or type(order) is not tuple
            or len(order) != 5
            or sha(canonical(list(order[:4]))) != order[4]
        ):
            _deny("native_current_first_v5_order_unverified")
    if (
        origin[4] == "API_ASOF_BUSY"
        and _V5_API_ASOF_INFLIGHT.get(id(state)) is not state
    ):
        _deny("native_current_first_v5_asof_custody_changed")
    if origin[4] in ("API_ASOF_BUSY", "API_ASOF_HELD"):
        _API_ASOF_REQUIRE_V3()
        if state.get("v5_api_inventory") != _package_inventory_api_asof_v3():
            _deny("native_current_first_v5_asof_source_changed")
    if origin[4] == "API_ASOF_HELD":
        if (
            type(origin[5]) is not tuple
            or len(origin[5]) != 2
            or state.get("v5_api_asof") != origin[5][1]
            or type(origin[5][1]) is not tuple
            or len(origin[5][1]) != 6
        ):
            _deny("native_current_first_v5_asof_binding_changed")
        value = origin[5][1]
        if (
            type(value[0]) is not bytes
            or len(value[0]) > _v4_financial.MAX_COMPACT_BYTES
            or sha(value[0]) != value[1]
            or value[2] != _api_asof_bank.WINDOW_OPERANDS_POLICY_SHA256_V3
            or value[3] != state["v3_view"]["current"].reference.packet_sha256
            or type(value[5]) is not datetime
            or value[5].tzinfo is not UTC
        ):
            _deny("native_current_first_v5_asof_binding_changed")


def _package_inventory_current_first_v5():
    manifest, pin = coverage._read_file(_ROOT / "MANIFEST.sha256", 2 * 1024 * 1024)
    rows = {}
    for line in manifest.decode("ascii").splitlines():
        digest, separator, name = line.partition("  ")
        if not separator or coverage._SHA.fullmatch(digest) is None or name in rows:
            _deny("native_current_first_v5_source_changed")
        rows[name] = digest
    name = "app/trade_qualification/account_current_first_off_foundation_v5.py"
    raw, source_pin = coverage._read_file(_ROOT / name, 4 * 1024 * 1024)
    if rows.get(name) != sha(raw):
        _deny("native_current_first_v5_source_changed")
    return sha(manifest), pin, source_pin


def _release_current_first_partial_v5(state, history_owner, coverage_owner):
    retained = {
        "state": dict(state) if type(state) is dict else None,
        "history_owner": history_owner,
        "coverage_owner": coverage_owner,
        "origin": _STATE_ORIGIN_STORAGE_V5.get(id(state)),
    }
    if type(state) is dict:
        history_binding = state.get("history")
        history_source = (
            history._FINANCIAL.get(history_binding)
            if history_binding is not None
            else None
        )
        retained["history_source"] = (
            dict(history_source) if type(history_source) is dict else history_source
        )
        coverage_binding = state.get("coverage")
        coverage_source = (
            coverage._BINDINGS.get(coverage_binding)
            if coverage_binding is not None
            else None
        )
        retained["coverage_source"] = (
            dict(coverage_source) if type(coverage_source) is dict else coverage_source
        )
        if type(coverage_source) is dict:
            account = coverage_source.get("account")
            retained["original_account"] = (
                dict(account) if type(account) is dict else account
            )
    if history_owner is not None:
        source = history._OWNERS.get(history_owner)
        retained["unmoved_history_source"] = (
            dict(source) if type(source) is dict else source
        )
    if coverage_owner is not None:
        binding = coverage._OWNERS.get(coverage_owner)
        source = coverage._BINDINGS.get(binding) if binding is not None else None
        retained["unmoved_coverage_source"] = (
            dict(source) if type(source) is dict else source
        )
        if type(source) is dict:
            account = source.get("account")
            retained["unmoved_original_account"] = (
                dict(account) if type(account) is dict else account
            )
    failure = None
    for kind in ("history_owner", "coverage_owner", "state"):
        try:
            if kind == "history_owner" and history_owner is not None:
                history._release_financial_assembly_v1(history_owner, key=_HOOK_KEY)
            elif kind == "coverage_owner" and coverage_owner is not None:
                coverage._release_financial_assembly_v1(coverage_owner, key=_HOOK_KEY)
            elif kind == "state" and type(state) is dict:
                _release(state)
        except BaseException as error:  # noqa: BLE001 -- attempt each distinct owned release once.
            if failure is None or isinstance(
                error, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)
            ):
                failure = error
    if failure is not None:
        _V5_RELEASE_UNKNOWN[object()] = retained
        if isinstance(failure, asyncio.CancelledError):
            raise asyncio.CancelledError() from None
        if isinstance(failure, (KeyboardInterrupt, SystemExit)):
            raise type(failure)() from None
        _deny("native_current_first_v5_release_unknown")


def _capture_current_first_pre_take_v5(state, owner, kind):
    """Retain exact source refs before a legacy take can pop and reject."""
    origin = _origin_record_v5(state)
    if origin[0] != "V5_CURRENT_FIRST_OFF" or origin[4] != "ACQUIRING":
        _deny("native_current_first_v5_pre_take_phase_changed")
    if kind == "history":
        source = (
            history._OWNERS.get(owner)
            if type(owner) is history.OwnedNativeHistoryCurrentSourceV2
            else None
        )
        if type(source) is not dict:
            _deny("native_current_first_v5_pre_take_source_missing")
        state["v5_pre_take_history"] = (owner, dict(source))
    elif kind == "coverage":
        binding = (
            coverage._OWNERS.get(owner)
            if type(owner) is coverage.OwnedControlledDemoWriterCoverageV1
            else None
        )
        source = coverage._BINDINGS.get(binding) if binding is not None else None
        if type(source) is not dict:
            _deny("native_current_first_v5_pre_take_source_missing")
        account = source.get("account")
        state["v5_pre_take_coverage"] = (
            owner,
            binding,
            dict(source),
            dict(account) if type(account) is dict else account,
        )
    else:
        _deny("native_current_first_v5_pre_take_purpose_changed")


def _release_current_first_owner_v5(owner, *, key):
    from app.trade_qualification.account_current_first_off_foundation_v5 import (
        _SOURCE_KEY,
    )

    if key is not _SOURCE_KEY:
        _deny("native_current_first_v5_fixed_purpose_required")
    state = (
        _V5_OWNERS.pop(owner, None)
        if type(owner) is OwnedNativeAccountHistoryFoundationV5
        else None
    )
    if state is None:
        _deny("native_current_first_v5_owner_unavailable")
    _release_current_first_partial_v5(state, None, None)


from app.database.repositories import (
    account_history_original_raw_proof_bank_read_v1 as _api_asof_reader,
)
from app.trade_qualification import (
    account_original_position_path_v1 as _api_asof_positions,
)
from app.trade_qualification import (
    account_original_raw_proof_bank_v1 as _api_asof_bank,
)


async def _collect_current_first_api_asof_v3(state, *, guard):
    """One actual original PG read, on the SAME original held source state."""
    guard()
    loop_start = asyncio.get_running_loop().time()
    stamp = _check_current_first_foundation_v5(state)
    anchors = state["v3_anchors"]
    until = min(
        datetime.fromisoformat(anchors["current_original_expires_at"]),
        datetime.fromisoformat(anchors["control_original_lease_until"]),
    )
    remaining = min(
        (anchors["shorter_monotonic_deadline_ns"] - stamp["monotonic_ns"]) / 1e9,
        (until - utc_from_ns(stamp["utc_ns"])).total_seconds(),
    )
    if remaining <= 0:
        _deny("native_current_first_v5_original_deadline_expired")
    async with asyncio.timeout_at(loop_start + remaining):
        facts, view = await _replay_partition_sources_v3(state)
        guard()
        _check_current_first_foundation_v5(state)
        # This is the existing source registration, not a qualification/account
        # claim or a second risk ledger. Its returned expiry never extends any
        # original source/Control lifetime.
        (
            facts,
            view,
            _registration,
            before,
            expires,
        ) = await _register_latest_partition_v4(state, facts, view)
        guard()
        _check_current_first_foundation_v5(state)
        original_prefix = _native_hwm_prefix_pin_v1(before)
        if expires > until:
            _deny("native_current_first_v5_registration_expiry_extended")
        request = _v4_wire.OriginalFrameRequestV1(
            _v3_scope(facts),
            before.head_sequence,
            before.head_sha256,
            0,
            None,
            before.control_revision,
            before.account_revision,
            before.ledger_revision,
            expires,
        )
        checkpoint = (
            before.checkpoints[-1].record_sha256 if before.checkpoints else None
        )
        original = history_bundle._json(facts.bundle_raw)
        position_plan = financial_v2.position_pages.PositionHistoryPlanV1(
            **{
                **original["position_plan"],
                "created_at": datetime.fromisoformat(
                    original["position_plan"]["created_at"]
                ),
            }
        )
        identity_document = original["position_identity"]
        if identity_document["declared_scope_currency"] != "USDT":
            _deny("native_current_first_v5_position_scope_changed")
        identity = financial_v2.positions._NativePositionIdentityV1(
            **{
                name: value
                for name, value in identity_document.items()
                if name != "declared_scope_currency"
            }
        )
        positions = financial_v2.NativePositionSourceV2(
            position_plan,
            identity,
            facts.positions.raw_archive,
            facts.positions.archive_sha256,
        )
        held, _stamp = coverage._check_binding(state["coverage"])
        account = held.get("account")
        if type(account) is not dict:
            _deny("native_current_first_v5_original_writer_changed")
        current._check_owner(account)
        writer = account["lineage_factory"]
        if _database_connection_pin_v4(writer)[1] != state["v4_connection_pins"][0][1]:
            _deny("native_current_first_v5_original_writer_changed")
        result = await _api_asof_reader.replay_original_history_api_population_from_pg_v2(
            state["v4_reader_factory"],
            genesis_request=request,
            expected_checkpoint_sha256=checkpoint,
            approved_collector_role_oid=state["v4_reader_role_oid"],
            lineage_session_factory=writer,
            expected_financial_policy_sha256=sha(
                _v4_stream._financial_stream_policy_bytes_v3()
            ),
            expected_raw_bank_policy_sha256=_api_asof_bank.POLICY_SHA256,
            expected_api_population_policy_sha256=_api_asof_bank.API_POPULATION_POLICY_SHA256_V2,
            position_source=positions,
        )
        guard()
        _check_current_first_foundation_v5(state)
        if (
            type(result)
            is not _api_asof_reader.OriginalAccountApiPopulationPgReadObservationV2
        ):
            _deny("native_current_first_v5_original_read_changed")
        facts, view = await _replay_partition_sources_v3(state, facts, view)
        guard()
        await _bind_databases_v4(state)
        guard()
        after = await state["v3_repository"].read(_v3_scope(facts))
        guard()
        _v4_partition_readback(state, facts, view, after)
        if (
            _native_hwm_prefix_pin_v1(after) != original_prefix
            or after.transaction_id == before.transaction_id
        ):
            _deny("native_current_first_v5_original_prefix_or_revision_changed")
        _check_current_first_foundation_v5(state)
        values = _api_asof_bank._derive_original_api_window_operands_v3(
            result.api_population,
            scope=_v3_scope(facts),
            checked_at=result.measured_validation_at,
            _held_current_record=view["current"],
            _original_position_source=positions,
        )
        guard()
        _check_current_first_foundation_v5(state)
        raw_bank = result.raw_proof_bank
        retained = (
            bytes(raw_bank.raw_source_json),
            bytes(raw_bank.financial_facts.receipt_json),
            bytes(raw_bank.receipt_json),
            bytes(result.api_population.receipt_json),
            bytes(result.acquisition_receipt_json),
            bytes(result.replay.original_replay.receipt_json),
        )
        charged = _api_asof_bank.decode(values.receipt_json)[
            "derivation_retained_original_bytes_before_receipt"
        ]
        if (
            type(charged) is not int
            or charged < sum(map(len, retained[:4]))
            or charged + len(values.receipt_json) + sum(map(len, retained[4:]))
            > _api_asof_bank.MAX_API_RETAINED_BYTES_V2
        ):
            _deny("native_current_first_v5_asof_total_retained_bound")
        return (
            bytes(values.receipt_json),
            sha(values.receipt_json),
            _api_asof_bank.WINDOW_OPERANDS_POLICY_SHA256_V3,
            view["current"].reference.packet_sha256,
            original_prefix,
            expires,
        ), retained


async def _continue_current_first_api_asof_impl_v3(owner, *, key, guard):
    if key is not _v5_public._SOURCE_KEY:
        _deny("native_current_first_v5_fixed_purpose_required")
    guard()
    _origin_storage_v5()
    state = (
        _V5_OWNERS.get(owner)
        if type(owner) is OwnedNativeAccountHistoryFoundationV5
        else None
    )
    if type(state) is not dict:
        _deny("native_current_first_v5_owner_unavailable")
    origin = _origin_record_v5(state)
    if origin[4] != "HELD" or id(state) in _V5_API_ASOF_INFLIGHT:
        _deny("native_current_first_v5_asof_phase_changed")
    _check_current_first_foundation_v5(state)
    # All admissions precede MOVE. The same state is strongly retained while
    # asynchronous PG work is in flight, and stays charged to the fixed cap.
    if len(_V5_API_ASOF_INFLIGHT) >= _V5_MAX_BINDINGS:
        _deny("native_current_first_v5_asof_binding_bound")
    state["v5_api_inventory"] = _package_inventory_api_asof_v3()
    _V5_OWNERS.pop(owner)
    _V5_API_ASOF_INFLIGHT[id(state)] = state
    _STATE_ORIGINS_V5[id(state)] = (*origin[:4], "API_ASOF_BUSY", origin[5])
    success = False
    try:
        value, retained = await _collect_current_first_api_asof_v3(state, guard=guard)
        guard()
        _check_current_first_foundation_v5(state)
        state["v5_api_asof"] = value
        state["v5_api_asof_original_raw"] = retained
        _STATE_ORIGINS_V5[id(state)] = (
            *origin[:4],
            "API_ASOF_HELD",
            (origin[5], value),
        )
        _check_current_first_foundation_v5(state)
        result = object.__new__(OwnedNativeAccountHistoryFoundationV5)
        _V5_OWNERS[result] = state
        success = True
        return result
    except asyncio.CancelledError:
        raise asyncio.CancelledError() from None
    except (KeyboardInterrupt, SystemExit) as error:
        raise type(error)() from None
    except Exception:  # noqa: BLE001 -- source/SQL/account values never enter errors.
        _deny("native_current_first_v5_asof_unavailable")
    finally:
        try:
            if not success:
                _release_current_first_partial_v5(state, None, None)
        finally:
            _V5_API_ASOF_INFLIGHT.pop(id(state), None)


from app.trade_qualification import (
    account_current_first_off_foundation_v5 as _v5_public,
)

_API_ASOF_SOURCE_PATHS_V3 = (
    "app/trade_qualification/account_history_source_v1.py",
    "app/trade_qualification/account_original_raw_proof_bank_v1.py",
    "app/trade_qualification/account_original_position_path_v1.py",
    "app/trade_qualification/account_history_original_stream_v1.py",
    "app/database/repositories/account_history_original_raw_proof_bank_read_v1.py",
    "app/database/repositories/collector_current_0054_multirole_catalog_v1.py",
    "app/database/repositories/collector_current_0054_catalog.py",
    "app/domain/collector_current_0054_catalog_v1.py",
    "app/domain/collector_current_0053_owner_installation_v1.py",
    "app/domain/account_revision_collector_lineage_schema_v1.py",
    "app/domain/account_issued_revision_schema_v1.py",
)


def _package_inventory_api_asof_v3():
    manifest, pin = coverage._read_file(_ROOT / "MANIFEST.sha256", 2 * 1024 * 1024)
    rows = {}
    for line in manifest.decode("ascii").splitlines():
        digest, separator, name = line.partition("  ")
        if not separator or coverage._SHA.fullmatch(digest) is None or name in rows:
            _deny("native_current_first_v5_asof_source_changed")
        rows[name] = digest
    found = {}
    for name in _API_ASOF_SOURCE_PATHS_V3:
        raw, source_pin = coverage._read_file(_ROOT / name, 4 * 1024 * 1024)
        if rows.get(name) != sha(raw):
            _deny("native_current_first_v5_asof_source_changed")
        found[name] = source_pin
    return sha(manifest), pin, found


def _api_asof_module_pin_v3(
    module,
    _module_type=type(sys),  # noqa: B008 -- freeze the module class at definition.
):
    """Pin the complete static namespace of the new actual source callees.

    These approved modules have no live owner registries. Mutable native
    assembly registries are deliberately excluded and retain their original
    identity/cap guards. No caller module or names list is accepted publicly.
    """
    if type(module) is not _module_type or type(module.__name__) is not str:
        _deny("native_current_first_v5_asof_callee_changed")
    namespace = module.__dict__
    if len(namespace) > 512 or any(type(name) is not str for name in namespace):
        _deny("native_current_first_v5_asof_namespace_bound")
    result = [("module_name", module.__name__)]
    # Only CODE symbol names are projected lexically. Original account rows,
    # pages, cursors, duplicates and their ordering are never sorted here.
    for name in sorted(namespace):
        if name.startswith("__"):
            continue
        value = namespace[name]
        if type(value) in (_V3FunctionType, _V3MethodType, property):
            pin = ("callable", _v3_callable_pin(value))
        else:
            pin = ("data", _v3_data_pin(value))
        own_class = []
        if (
            type(value) is type
            and type(value.__module__) is str
            and value.__module__ == module.__name__
        ):
            if len(value.__dict__) > 128:
                _deny("native_current_first_v5_asof_namespace_bound")
            for member in sorted(value.__dict__):
                item = value.__dict__[member]
                if type(item) in (classmethod, staticmethod):
                    own_class.append(
                        (member, ("callable", _v3_callable_pin(item.__func__)))
                    )
                elif type(item) in (_V3FunctionType, _V3MethodType, property):
                    own_class.append((member, ("callable", _v3_callable_pin(item))))
                else:
                    own_class.append((member, ("data", _v3_data_pin(item))))
        result.append((name, pin, tuple(own_class)))
    return tuple(result)


_API_ASOF_MODULES_V3 = (
    _api_asof_bank,
    _api_asof_positions,
    _api_asof_reader,
    _v4_stream,
    _hwm_measurement,
    mapping,
    _api_asof_reader.current_catalog,
    _api_asof_reader.current_catalog.policy,
    _api_asof_reader.current_catalog.policy.old53,
    _api_asof_reader.current_catalog.policy.issued,
    _api_asof_reader.current_catalog.policy.lineage,
    _v5_public,
)


def _make_api_asof_boundary_v3():
    # The immutable snapshots live in this closure, not a vacatable module
    # pin table. Imported callee module identities and own class methods are
    # included. No inert receipt/caller callback can replace this checker.
    namespace = globals()
    # Pin the actual dereference owners as well as their original namespaces.
    # The tuple lives only in this protected closure; rebinding a native alias
    # cannot leave the original module pins valid while selecting a new owner.
    owner_aliases = tuple(
        (name, namespace[name])
        for name in (
            "_api_asof_bank",
            "_api_asof_reader",
            "_api_asof_positions",
            "_v5_public",
            "_v4_financial",
            "_v4_stream",
            "_v4_wire",
            "_v4_position_sessions",
            "_hwm_measurement",
            "mapping",
            "history",
            "current",
            "coverage",
            "custody",
            "capture",
            "financial_v2",
            "history_bundle",
            "asyncio",
            "datetime",
            "ControlledDemoAccountSession",
            "_json_lexical_guard",
        )
    )
    modules = _API_ASOF_MODULES_V3
    module_reader = _api_asof_module_pin_v3
    module_reader_pin = _v3_callable_pin(module_reader)
    module_pins = tuple(module_reader(module) for module in modules)
    functions = tuple(
        (name, _v3_callable_pin(globals()[name]))
        for name in (
            "_collect_current_first_api_asof_v3",
            "_continue_current_first_api_asof_impl_v3",
            "_package_inventory_api_asof_v3",
            "_make_api_asof_boundary_v3",
        )
    )
    external_functions = tuple(
        (module, name, _v3_callable_pin(getattr(module, name)))
        for module, names in (
            (
                history,
                (
                    "_native_plan",
                    "_acquire_native_history_source",
                    "_acquire_native_history_current_asof_v3",
                ),
            ),
            (current, ("_check_owner",)),
            (coverage, ("_check_binding", "_replay_financial_assembly_v1")),
            (_v4_position_sessions, ("native_position_rows_for_original_prefix_v3",)),
        )
        for name in names
    )
    external_data = tuple(
        (history, name, _v3_data_pin(getattr(history, name)))
        for name in ("UTC", "datetime", "_PARTITION_CURRENT_V2")
    )
    paths = _API_ASOF_SOURCE_PATHS_V3
    implementation = _continue_current_first_api_asof_impl_v3
    implementation_pin = _v3_callable_pin(implementation)

    def require():
        if any(namespace.get(name) is not owner for name, owner in owner_aliases):
            _deny("native_current_first_v5_asof_owner_alias_changed")
        if (
            _API_ASOF_MODULES_V3 is not modules
            or _API_ASOF_SOURCE_PATHS_V3 is not paths
            or _api_asof_module_pin_v3 is not module_reader
            or _v3_callable_pin(module_reader) != module_reader_pin
        ):
            _deny("native_current_first_v5_asof_callee_changed")
        for name, expected in functions:
            actual = _v3_callable_pin(globals()[name])
            if (
                any(actual[n] is not expected[n] for n in (0, 1, 2))
                or actual[3:] != expected[3:]
            ):
                _deny("native_current_first_v5_asof_callee_changed")
        for module, expected in zip(modules, module_pins, strict=True):
            if module_reader(module) != expected:
                _deny("native_current_first_v5_asof_callee_changed")
        for module, name, expected in external_functions:
            actual = _v3_callable_pin(getattr(module, name))
            if (
                any(actual[n] is not expected[n] for n in (0, 1, 2))
                or actual[3:] != expected[3:]
            ):
                _deny("native_current_first_v5_asof_callee_changed")
        for module, name, expected in external_data:
            if _v3_data_pin(getattr(module, name)) != expected:
                _deny("native_current_first_v5_asof_callee_changed")

    require_pin = _v3_callable_pin(require)

    async def entry(owner, *, key):
        actual = _v3_callable_pin(require)
        if (
            _continue_current_first_api_asof_v3 is not entry
            or _API_ASOF_REQUIRE_V3 is not require
            or any(actual[n] is not require_pin[n] for n in (0, 1, 2))
            or actual[3:] != require_pin[3:]
            or _v3_callable_pin(implementation) != implementation_pin
        ):
            _deny("native_current_first_v5_asof_boundary_changed")
        require()
        return await implementation(owner, key=key, guard=require)

    return entry, require


_continue_current_first_api_asof_v3, _API_ASOF_REQUIRE_V3 = _make_api_asof_boundary_v3()

_ORIGIN_PIN_FUNCTIONS_V5 = tuple(
    (sys.modules[__name__], name, _v3_callable_pin(globals()[name]))
    for name in (
        "_origin_storage_v5",
        "_origin_functions_v5",
        "_origin_record_v5",
        "_register_v4_initial_origin_v5",
        "_new_acquisition_ticket_v5",
        "_check_acquisition_ticket_v5",
        "_register_v5_origin_v5",
        "_transfer_native_origin_v5",
        "_forget_native_origin_v5",
        "_check_native_origin_dispatch_v5",
        "_mark_current_first_held_v5",
        "_check_current_first_structure_v5",
        "_package_inventory_current_first_v5",
        "_release_current_first_partial_v5",
        "_capture_current_first_pre_take_v5",
        "_release_current_first_owner_v5",
        "_check",
        "_check_v4",
        "_release",
        "_API_ASOF_REQUIRE_V3",
        "_continue_current_first_api_asof_v3",
        "_move_assembly_owner_v3",
        "acquire_native_account_history_financial_v4",
    )
)
_ORIGIN_PIN_DATA_V5 = tuple(
    (sys.modules[__name__], name, _v3_data_pin(globals()[name]))
    for name in (
        "_V5_MAX_BINDINGS",
        "_V5_ORIGIN_MAX_STATES",
        "_MAX_OWNERS",
        "_STATE_ORIGIN_STORAGE_ID_V5",
        "_V5_ACQUISITION_STORAGE_ID",
        "_V5_OWNER_STORAGE_ID",
        "_V5_RELEASE_STORAGE_ID",
        "_V5_API_ASOF_STORAGE_ID",
    )
)
_V5_PIN_FUNCTIONS = (
    tuple(
        (sys.modules[__name__], name, _v3_callable_pin(globals()[name]))
        for name in (
            "_acquire_current_first_foundation_v5",
            "acquire_native_account_history_foundation_v5",
            "_release_current_first_foundation_v5",
            "_check_current_first_foundation_v5",
            "_bind_current_first_order_v5",
        )
    )
    + tuple(
        (_v5_public, name, _v3_callable_pin(getattr(_v5_public, name)))
        for name in (
            "_assembly",
            "acquire_current_first_off_foundation_v5",
            "release_current_first_off_foundation_v5",
        )
    )
    + tuple(
        (history, name, _v3_callable_pin(getattr(history, name)))
        for name in ("_native_plan", "_acquire_native_history_current_asof_v3")
    )
)
_V5_PIN_DATA = tuple(
    (sys.modules[__name__], name, _v3_data_pin(globals()[name]))
    for name in (
        "_V5_SOURCE_POLICY_BYTES",
        "_V5_SOURCE_POLICY_SHA256",
        "OwnedNativeAccountHistoryFoundationV5",
    )
) + tuple(
    (_v5_public, name, _v3_data_pin(getattr(_v5_public, name)))
    for name in ("_SOURCE_KEY", "MAX_BINDINGS")
)

# Additive current55 operator source purpose. Historical OFF/V4/V5 owners and
# old54 guards above are unchanged; neither can enter this fixed registry.
_CONTROLLED_SOURCE_KEY_V1 = object()
_CONTROL_OPERATOR_MAX_V1 = 16
_CONTROL_OPERATOR_ACTIVE_V1 = {}
_CONTROL_OPERATOR_UNKNOWN_V1 = {}


_CONTROLLED_SOURCE_PATHS_V1 = (
    "app/trade_qualification/native_account_risk_assembly_v1.py",
    "app/trade_qualification/native_demo_control_owner_v2.py",
    "app/trade_qualification/account_owned_financial_v1.py",
    "app/trade_qualification/account_ledger_claims_v2.py",
    "app/trade_qualification/portfolio.py",
    "app/trade_qualification/demo_control.py",
    "app/trade_qualification/demo_control_runtime.py",
    "app/trade_qualification/demo_control_clear_v2.py",
    "app/trade_qualification/demo_policy_counter_initialization_v1.py",
    "app/trade_qualification/account_original_balance_measurement_v1.py",
    "app/trade_qualification/account_current_source_v4.py",
    "app/database/repositories/demo_control_current55_v2.py",
    "app/database/repositories/demo_operator_acknowledgements_current55_v1.py",
    "app/database/repositories/account_revision_collector_lineage_current55_v1.py",
    "app/database/repositories/account_revision_collector_lineage.py",
    "app/database/repositories/qualification_native_checkpoint_v2.py",
    "app/database/repositories/qualification_ledger.py",
    "app/database/repositories/public_runtime_bundle_custody.py",
    "app/database/repositories/collector_current_0055_multirole_catalog_v1.py",
    "app/domain/collector_current_0055_catalog_v1.py",
    "app/domain/demo_operator_ack_schema_v55.py",
    "app/database/models/demo_operator_acknowledgements_v55.py",
    "migrations/versions/0055_operator_recovery_acknowledgements.py",
)


def _package_inventory_controlled55_v1():
    descriptor_name = "app/exchange/okx/native_demo_source_identity_v2.py"
    descriptor_path = _ROOT / descriptor_name
    if not descriptor_path.exists():
        # Historical operator-only carrier, with the actual original strict
        # shared-source hashes. Missing new source cannot enable Armed V6.
        baseline = coverage._package_inventory()
    else:
        from app.exchange.okx import native_demo_source_identity_v2 as descriptor

        expected_paths = (
            "app/exchange/okx/private_rest.py",
            "app/trade_qualification/private_write_authority.py",
            "app/exchange/okx/native_demo_write_v2.py",
            "app/exchange/okx/native_demo_transport_v2.py",
            "app/exchange/okx/native_demo_vendor_integrity_v2.py",
        )
        actual_contract = descriptor.read_native_demo_source_contract_v2()
        if (
            type(actual_contract) is not tuple
            or len(actual_contract) != 5
            or any(
                type(row) is not tuple
                or len(row) != 2
                or type(row[0]) is not str
                or type(row[1]) is not str
                or coverage._SHA.fullmatch(row[1]) is None
                for row in actual_contract
            )
            or tuple(row[0] for row in actual_contract) != expected_paths
        ):
            _deny("native_controlled55_exact_root_source_contract_required")
        # A rejected new contract never falls back to old deny-source hashes.
        coverage._guard_denials()
        manifest, manifest_pin = coverage._read_file(
            _ROOT / "MANIFEST.sha256", 2 * 1024 * 1024
        )
        rows = {}
        for line in manifest.decode("ascii").splitlines():
            digest, separator, name = line.partition("  ")
            if (
                not separator
                or coverage._SHA.fullmatch(digest) is None
                or not name
                or name in rows
                or ".." in Path(name).parts
            ):
                _deny("native_controlled55_writer_manifest_changed")
            rows[name] = digest
        approved = dict(actual_contract)
        files = {}
        for name in dict.fromkeys(
            (*coverage.WRITER_PATHS, *expected_paths, descriptor_name)
        ):
            raw, file_pin = coverage._read_file(_ROOT / name, 4 * 1024 * 1024)
            digest = sha(raw)
            expected = approved.get(name, coverage._DENY_SOURCE.get(name))
            if rows.get(name) != digest or (
                expected is not None and expected != digest
            ):
                _deny("native_controlled55_actual_writer_source_changed")
            files[name] = file_pin
        baseline = {
            "manifest": manifest_pin,
            "files": files,
            "manifest_sha256": sha(manifest),
            "writer_sha256": sha(
                canonical({name: pin[2] for name, pin in files.items()})
            ),
        }
    manifest, pin = coverage._read_file(_ROOT / "MANIFEST.sha256", 2 * 1024 * 1024)
    rows = {}
    for line in manifest.decode("ascii").splitlines():
        digest, separator, name = line.partition("  ")
        if not separator or coverage._SHA.fullmatch(digest) is None or name in rows:
            _deny("native_control_operator_source_manifest_changed")
        rows[name] = digest
    found = []
    for name in _CONTROLLED_SOURCE_PATHS_V1:
        raw, file_pin = coverage._read_file(_ROOT / name, 4 * 1024 * 1024)
        if rows.get(name) != sha(raw):
            _deny("native_control_operator_source_files_changed")
        found.append((name, file_pin))
    return baseline, sha(manifest), pin, tuple(found)


class _OwnedControlOperatorSourceV1:
    __slots__ = ("__weakref__",)

    def __new__(cls):
        _deny("native_control_operator_not_constructible")


@dataclass(frozen=True, slots=True, repr=False)
class ControlOperatorRootsV1:
    private_proof_root: Path = field(repr=False)
    native_proof_root: Path = field(repr=False)

    def __post_init__(self):
        roots = (self.private_proof_root, self.native_proof_root)
        if any(
            type(root) is not type(Path()) or not root.is_absolute() for root in roots
        ) or (
            roots[0] == roots[1]
            or roots[0] in roots[1].parents
            or roots[1] in roots[0].parents
        ):
            _deny("native_control_operator_distinct_roots_required")


@dataclass(frozen=True, slots=True, repr=False)
class _ControlOperatorReplayV1:
    purpose: str
    command_id: str
    confirmation: str = field(repr=False)
    session_factory: object = field(repr=False)
    scope: object = field(repr=False)
    previous_control: object = field(repr=False)
    previous_control_event_sha256: str
    current: object = field(repr=False)
    lineage: object = field(repr=False)
    configured_policy: object = field(repr=False)
    original_measurement: bytes = field(repr=False)
    expires_at: datetime
    monotonic_deadline_ns: int


def _make_control_operator_module_binding_v1():
    # Bound exactly once during the trusted configured façade bootstrap. The
    # public entry never chooses callees through a later sys.modules/package
    # replacement, and no caller modules/factories/callbacks are parameters.
    class Binding:
        __slots__ = (
            "active",
            "control_active",
            "control_owners",
            "control_unknown",
            "kernel",
            "modules",
            "operator_key",
            "owner_type",
            "revoke",
            "unknown",
        )

        def __init__(self):
            self.modules = None
            self.kernel = None
            self.operator_key = None

    captured = Binding()

    def bind(key):
        from app.database.repositories import (
            account_revision_collector_lineage_current55_v1 as lineage55,
        )
        from app.database.repositories import (
            collector_current_0055_multirole_catalog_v1 as catalog55,
        )
        from app.database.repositories import (
            demo_operator_acknowledgements_current55_v1 as operator55,
        )
        from app.trade_qualification import demo_control_clear_v2 as clear55
        from app.trade_qualification import (
            demo_policy_counter_initialization_v1 as counter55,
        )
        from app.trade_qualification import native_demo_control_owner_v2 as owner55

        if captured.modules is not None or key is not owner55._SOURCE_KEY:
            _deny("native_control_operator_bootstrap_already_bound_or_foreign")
        captured.modules = (
            owner55,
            operator55,
            lineage55,
            catalog55,
            clear55,
            counter55,
        )
        captured.kernel = owner55._REQUIRE_KERNEL
        captured.operator_key = operator55._SOURCE_KEY
        captured.active = _CONTROL_OPERATOR_ACTIVE_V1
        captured.unknown = _CONTROL_OPERATOR_UNKNOWN_V1
        captured.control_active = owner55._ACTIVE
        captured.control_owners = owner55._OWNERS
        captured.control_unknown = owner55._UNKNOWN
        captured.revoke = owner55.original.DemoControlOwner._revoke
        captured.owner_type = owner55.original.DemoControlOwner

    def get():
        if captured.modules is None or captured.kernel is None:
            _deny("native_control_operator_configured_bootstrap_required")
        captured.kernel()
        return captured.modules

    def retain_unknown(state, *, key):
        # Tightening/retention does not require a still-valid lease or a healthy
        # mutable callee namespace. It grants no DB/source/trading authority.
        if (
            captured.modules is None
            or key is not captured.operator_key
            or type(state) is not dict
            or (
                captured.active.get(id(state)) is not state
                and captured.unknown.get(id(state)) is not state
            )
        ):
            _deny("native_control_operator_original_unknown_source_required")
        control_state = state["control"]
        owner = control_state["owner"]
        if type(owner) is not captured.owner_type or (
            captured.control_active.get(id(control_state)) is not control_state
            and captured.control_unknown.get(id(control_state)) is not control_state
        ):
            _deny("native_control_operator_original_safety_owner_required")
        captured.revoke(owner, stop=True)
        captured.active.pop(id(state), None)
        captured.unknown.setdefault(id(state), state)
        state["phase"] = "UNKNOWN"
        captured.control_active.pop(id(control_state), None)
        captured.control_owners.pop(control_state["public_owner"], None)
        captured.control_unknown.setdefault(id(control_state), control_state)
        control_state["phase"] = "UNKNOWN"

    return bind, get, retain_unknown


(
    _bind_control_operator_modules_v1,
    _control_operator_modules_v1,
    _retain_unknown_control_operator_source_v1,
) = _make_control_operator_module_binding_v1()


def _control_operator_registry_v1():
    # Strong maps are pinned by the configured owner at module bootstrap. No
    # weak entry can disappear while cancellation/unknown commit is retained.
    if (
        type(_CONTROL_OPERATOR_ACTIVE_V1) is not dict
        or _CONTROL_OPERATOR_ACTIVE_V1 is not _CONTROL_OPERATOR_STORAGE_V1[0]
        or type(_CONTROL_OPERATOR_UNKNOWN_V1) is not dict
        or _CONTROL_OPERATOR_UNKNOWN_V1 is not _CONTROL_OPERATOR_STORAGE_V1[1]
        or _CONTROL_OPERATOR_MAX_V1 != 16
    ):
        _deny("native_control_operator_registry_changed")


def _control_operator_check_v1(state):
    _control_operator_registry_v1()
    owner55, _, _, _, _, _ = _control_operator_modules_v1()
    if (
        type(state) is not dict
        or _CONTROL_OPERATOR_ACTIVE_V1.get(id(state)) is not state
        or state["phase"] not in ("ACQUIRING", "READY", "ISSUING")
        or state["modules"] != _control_operator_modules_v1()
    ):
        _deny("native_control_operator_source_unavailable")
    stamp = owner55._check_operator_control_v1(
        state["control"], key=_CONTROLLED_SOURCE_KEY_V1
    )
    context = _context()
    expected = state["context"]
    if context[:3] != expected[:3] or context[3] is not expected[3]():
        _deny("native_control_operator_original_context_changed")
    if _package_inventory_controlled55_v1() != state["inventory"]:
        _deny("native_control_operator_source_files_changed")
    if state["current"] is not None:
        old_lineage = owner55.legacy_lineage

        replay, sampled = current._replay_account_revision_lineage_v1(
            state["current"], key=old_lineage._SOURCE_KEY
        )
        if (
            replay.packet.plan != state["capture_plan"]
            or capture.plan_sha256(replay.packet.plan) != state["capture_plan_sha256"]
            or replay.expires_at < state["expires_at"]
            or replay.monotonic_deadline_ns < state["monotonic_deadline_ns"]
            or replay.reference != state["current_reference"]
            or utc_from_ns(sampled["utc_ns"]) >= state["expires_at"]
            or sampled["monotonic_ns"] >= state["monotonic_deadline_ns"]
        ):
            _deny("native_control_operator_original_CURRENT_changed")
        stamp = sampled
    if (
        utc_from_ns(stamp["utc_ns"]) >= state["expires_at"]
        or stamp["monotonic_ns"] >= state["monotonic_deadline_ns"]
    ):
        _deny("native_control_operator_original_expiry")
    if state.get("mirror_binding") is not None:
        _check_control_operator_mirror_budget_v6(state)
    return stamp


def _control_operator_remaining_v1(state):
    stamp = _control_operator_check_v1(state)
    remaining = min(
        (state["expires_at"] - utc_from_ns(stamp["utc_ns"])).total_seconds(),
        (state["monotonic_deadline_ns"] - stamp["monotonic_ns"]) / 1_000_000_000,
    )
    if remaining <= 0:
        _deny("native_control_operator_original_expiry")
    return remaining


def perform_owned_control_operator_v1(
    owner, *, purpose, confirmation, private_proof_root, native_proof_root
):
    """Synchronous MOVE of a real stopped/disarmed owner, then fresh acquisition.

    Calling this with a confirmation does not assert flatness. All exchange,
    lineage, role, local unknown and durable acknowledgement operands are
    obtained below from that same held source. No Arm is performed.
    """
    owner55, operator55, lineage55, catalog55, clear55, counter55 = (
        _control_operator_modules_v1()
    )
    owner55._REQUIRE_KERNEL()
    expected_confirmation = {
        "clear_stop": clear55.CLEAR_CONFIRMATION_V2,
        "policy_counter_initialize": counter55.COUNTER_CONFIRMATION_V1,
    }.get(purpose)
    if (
        type(confirmation) is not str
        or expected_confirmation is None
        or confirmation != expected_confirmation
    ):
        _deny("native_control_operator_exact_confirmation_required")
    roots = ControlOperatorRootsV1(private_proof_root, native_proof_root)
    roots.__post_init__()
    _control_operator_registry_v1()
    if len(_CONTROL_OPERATOR_ACTIVE_V1) + len(_CONTROL_OPERATOR_UNKNOWN_V1) >= 16:
        _deny("native_control_operator_binding_bound")
    control_state = owner55._take_operator_control_v1(
        owner, key=_CONTROLLED_SOURCE_KEY_V1
    )
    state = None
    try:
        import secrets

        context = _context()
        stamp = owner55._check_operator_control_v1(
            control_state, key=_CONTROLLED_SOURCE_KEY_V1
        )
        modules = (owner55, operator55, lineage55, catalog55, clear55, counter55)
        state = {
            "context": (*context[:3], ref(context[3])),
            "control": control_state,
            "modules": modules,
            "inventory": _package_inventory_controlled55_v1(),
            "purpose": purpose,
            "confirmation": confirmation,
            "command_id": secrets.token_hex(32),
            "roots": roots,
            "current": None,
            "lineage": None,
            "phase": "ACQUIRING",
            "expires_at": control_state["operator_expires_at"],
            "monotonic_deadline_ns": control_state["operator_monotonic_deadline_ns"],
            "last": stamp,
        }
        _CONTROL_OPERATOR_ACTIVE_V1[id(state)] = state
        _control_operator_check_v1(state)
        return _acquire_control_operator_v1(state)
    except BaseException:
        if state is not None:
            _CONTROL_OPERATOR_ACTIVE_V1.pop(id(state), None)
            _CONTROL_OPERATOR_UNKNOWN_V1.setdefault(id(state), state)
        owner55._retain_operator_control_v1(
            control_state, key=_CONTROLLED_SOURCE_KEY_V1
        )
        raise


async def _actual_control_operator_deployment_v1(state):
    _control_operator_check_v1(state)
    await state["control"]["repository"].verify_native_deployment_v2()
    _control_operator_check_v1(state)


async def _acquire_control_operator_v1(state):
    owner55, operator55, lineage55, _, _, _ = state["modules"]
    try:
        async with asyncio.timeout(_control_operator_remaining_v1(state)) as budget:
            await _actual_control_operator_deployment_v1(state)
            control_state = state["control"]
            stamp = _control_operator_check_v1(state)
            now = utc_from_ns(stamp["utc_ns"])
            template = control_state["plan"]
            values = template.model_dump(mode="python")
            values.update(plan_id=state["command_id"], created_at=now)
            plan = capture.CurrentDemoAccountCapturePlanV7.model_validate(values)
            # Only native new invocation identity/time change. Every original
            # UID/routing/inventory/leverage/budget/scope field remains exact.
            original_values = template.model_dump(mode="python")
            if any(
                values[name] != original_values[name]
                for name in values
                if name not in {"plan_id", "created_at"}
            ):
                _deny("native_control_operator_original_plan_changed")
            state["capture_plan"] = plan
            state["capture_plan_sha256"] = capture.plan_sha256(plan)
            session = ControlledDemoAccountSession.from_pinned_read_session(
                read_session=control_state["read_session"],
                plan=plan,
                expected_plan_sha256=state["capture_plan_sha256"],
            )
            owner = await current.acquire_initial_demo_account_source_v4(
                account_session=session,
                private_client=control_state["private_client"],
                session_factory=control_state["factory"],
                private_proof_root=state["roots"].private_proof_root,
                native_proof_root=state["roots"].native_proof_root,
                custody_repository=control_state["custody_repository"],
            )
            # Retain the actual owner/state BEFORE a downstream MOVE could
            # reject. Cancellation and failed takes cannot drop this custody.
            state["unmoved_current_owner"] = owner
            _control_operator_check_v1(state)
            await _actual_control_operator_deployment_v1(state)
            old_lineage = owner55.legacy_lineage

            held = current._take_account_revision_lineage_v1(
                owner, key=old_lineage._SOURCE_KEY
            )
            state["current"] = held
            state["unmoved_current_owner"] = None
            replay, stamp = current._replay_account_revision_lineage_v1(
                held, key=old_lineage._SOURCE_KEY
            )
            state["current_reference"] = replay.reference
            state["expires_at"] = min(state["expires_at"], replay.expires_at)
            state["monotonic_deadline_ns"] = min(
                state["monotonic_deadline_ns"], replay.monotonic_deadline_ns
            )
            _control_operator_check_v1(state)
            shorter = (
                asyncio.get_running_loop().time()
                + _control_operator_remaining_v1(state)
            )
            if shorter < budget.when():
                budget.reschedule(shorter)
            _control_operator_check_v1(state)
            await _synchronize_control_operator_mirror_v6(state)
            _control_operator_check_v1(state)
            state["lineage"] = await lineage55._record_held_current_revision_lineage_v2(
                held, key=_CONTROLLED_SOURCE_KEY_V1
            )
            _control_operator_check_v1(state)
            await _actual_control_operator_deployment_v1(state)
            state["phase"] = "READY"
            cap = object.__new__(_OwnedControlOperatorSourceV1)
            state["source_cap"] = cap
            return await operator55.record_owned_operator_acknowledgement_v1(cap)
    except BaseException as error:  # noqa: BLE001 -- Retain unknown state; preserve cancellation without private error text.
        _retain_unknown_control_operator_source_v1(state, key=operator55._SOURCE_KEY)
        if isinstance(error, asyncio.CancelledError):
            raise asyncio.CancelledError() from None
        if isinstance(error, (KeyboardInterrupt, SystemExit)):
            raise type(error)() from None
        _deny("native_control_operator_acquisition_or_commit_unknown")


def _replay_controlled_current_lineage_v1(held, *, key):
    owner55, _, lineage55, _, _, _ = _control_operator_modules_v1()
    if key is not lineage55._SOURCE_KEY:
        _deny("native_control_operator_lineage_purpose_required")
    actual_v6 = _maybe_replay_controlled_current_lineage_v6(held, key=key)
    if actual_v6 is not None:
        return actual_v6
    matches = tuple(
        state
        for state in _CONTROL_OPERATOR_ACTIVE_V1.values()
        if state["current"] is held
    )
    if len(matches) != 1:
        _deny("native_control_operator_original_CURRENT_unavailable")
    _control_operator_check_v1(matches[0])
    old_lineage = owner55.legacy_lineage

    return current._replay_account_revision_lineage_v1(
        held, key=old_lineage._SOURCE_KEY
    )


def _take_owned_control_operator_source_v1(cap, *, key):
    _, operator55, _, _, _, _ = _control_operator_modules_v1()
    if key is not operator55._SOURCE_KEY:
        _deny("native_control_operator_repository_purpose_required")
    matches = tuple(
        state
        for state in _CONTROL_OPERATOR_ACTIVE_V1.values()
        if state.get("source_cap") is cap
    )
    if type(cap) is not _OwnedControlOperatorSourceV1 or len(matches) != 1:
        _deny("native_control_operator_original_source_unavailable")
    state = matches[0]
    if state["phase"] != "READY":
        _deny("native_control_operator_source_already_moved")
    # Burn before the first source check and before returning a coroutine.
    # Failed context/expiry/cancellation cannot leave a retryable owner.
    state["phase"] = "ISSUING"
    state.pop("source_cap")
    try:
        _control_operator_check_v1(state)
        return state
    except BaseException:
        _retain_unknown_control_operator_source_v1(state, key=key)
        raise


def _replay_owned_control_operator_source_v1(state, *, key):
    owner55, operator55, _, _, _, _ = _control_operator_modules_v1()
    if key is not operator55._SOURCE_KEY:
        _deny("native_control_operator_repository_purpose_required")
    _control_operator_check_v1(state)
    if state["phase"] != "ISSUING" or state["lineage"] is None:
        _deny("native_control_operator_source_not_issued_to_repository")
    control_state = state["control"]
    old_lineage = owner55.legacy_lineage

    replay, _ = current._replay_account_revision_lineage_v1(
        state["current"], key=old_lineage._SOURCE_KEY
    )
    return _ControlOperatorReplayV1(
        state["purpose"],
        state["command_id"],
        state["confirmation"],
        control_state["factory"],
        control_state["ledger_scope"],
        control_state["observation"].state,
        control_state["observation"].event_sha256,
        replay,
        state["lineage"],
        control_state["configured_policy"],
        control_state["genesis"].original_measurement,
        state["expires_at"],
        state["monotonic_deadline_ns"],
    )


def _finish_owned_control_operator_source_v1(state, readback, *, key):
    owner55, operator55, _, _, _, _ = _control_operator_modules_v1()
    if (
        key is not operator55._SOURCE_KEY
        or type(readback) is not operator55._OperatorReadbackV1
    ):
        _deny("native_control_operator_actual_readback_required")
    replay = _replay_owned_control_operator_source_v1(state, key=key)
    operator55._verify_operator_readback_v1(readback, replay)
    _control_operator_check_v1(state)
    owner55._finish_operator_control_v1(
        state["control"],
        readback.control_observation,
        purpose=state["purpose"],
        key=_CONTROLLED_SOURCE_KEY_V1,
    )
    _CONTROL_OPERATOR_ACTIVE_V1.pop(id(state))
    state["current"].clear()
    state["phase"] = "RETIRED"
    return readback.value


_CONTROL_OPERATOR_STORAGE_V1 = (
    _CONTROL_OPERATOR_ACTIVE_V1,
    _CONTROL_OPERATOR_UNKNOWN_V1,
)


@dataclass(frozen=True, slots=True, repr=False)
class PostpublicationAccountRootsV1:
    history_raw_root: Path = field(repr=False)
    history_companion_root: Path = field(repr=False)
    account_proof_root: Path = field(repr=False)
    account_native_root: Path = field(repr=False)

    def __post_init__(self):
        roots = (
            self.history_raw_root,
            self.history_companion_root,
            self.account_proof_root,
            self.account_native_root,
        )
        if any(
            type(root) is not type(Path()) or not root.is_absolute() for root in roots
        ) or any(
            a == b or a in b.parents or b in a.parents
            for index, a in enumerate(roots)
            for b in roots[index + 1 :]
        ):
            _deny("native_controlled_financial_distinct_roots_required")


@dataclass(frozen=True, slots=True, repr=False)
class IssuedAccountForG11V1:
    """Inert private replay view. Only its live native registry is authority."""

    scope: object = field(repr=False)
    session_factory: object = field(repr=False)
    read_session: object = field(repr=False)
    private_client: object = field(repr=False)
    plan: object = field(repr=False)
    expires_at: datetime
    expiry_monotonic_ns: int
    policy_sha256: str
    account_revision: int
    ledger_revision: int
    control_expectation: object = field(repr=False)
    issued_row_sha256: str
    issuer_sha256: str
    claims: object = field(repr=False)
    instrument_specs: tuple = field(repr=False)
    execution_mode: object = field(repr=False)
    source_generation: object = field(repr=False)
    execution_authority: bool = field(default=False, init=False)


def _make_controlled_financial_binding_v6():
    class Binding:
        __slots__ = ("functions", "kernel", "module")

        def __init__(self):
            self.module = None
            self.kernel = None
            self.functions = None

    captured = Binding()
    names = [
        "acquire_owned_controlled_account_v6",
        "_synchronize_control_operator_mirror_v6",
        "_check_control_operator_mirror_budget_v6",
        "_replay_controlled_current_mirror_context_v6",
        "_consume_controlled_history_use_v6",
        "_replay_controlled_coverage_context_v6",
        "_take_owned_account_issuer_v1",
        "_replay_owned_account_issuer_v1",
        "_finish_owned_account_issuer_v1",
        "_retain_unknown_owned_account_issuer_v1",
        "_take_issued_account_for_g11_v1",
        "_replay_issued_account_for_g11_v1",
        "_release_issued_account_for_g11_v1",
        "_take_issued_account_for_r7_v2",
        "_replay_issued_account_for_r7_v2",
        "_release_issued_account_for_r7_v2",
        "discard_issued_account_owner_v1",
        "require_issued_original_frame_context_v1",
        "evaluate_issued_g11_v1",
        "_replay_issued_original_source_v2",
        "require_issued_postpublication_barrier_v2",
        "scan_issued_original_public_v1",
        "scan_issued_postpublication_public_v2",
        "scan_issued_metadata_v1",
        "scan_issued_artifact_v1",
        "bind_issued_demo_transport_v2",
        "_replay_issued_demo_transport_v2",
        "_inhibit_issued_demo_transport_v2",
        "require_issued_original_revision_locked_v2",
        "require_issued_account_revision_locked_v1",
        "acquire_postpublication_issued_account_v1",
        "_replay_current_for_native_lineage_v6",
        "_replay_issued_demo_reconciliation_context_v2",
        "_acquire_native_recovery_read_source_v1",
        "_replay_native_recovery_read_source_v1",
        "_inhibit_native_reconciliation_read_source_v1",
        "scan_native_reconciliation_private_data_v1",
        "prepare_native_flat_source_cleanup_v1",
        "finish_native_flat_source_cleanup_v1",
        "_replay_native_flat_source_cleanup_completion_v1",
        "retire_native_flat_source_cleanup_v1",
        "prepare_native_zero_final_source_cleanup_v2",
        "finish_native_zero_final_source_cleanup_v2",
        "_replay_native_zero_final_source_cleanup_completion_v2",
        "retire_native_zero_final_source_cleanup_v2",
        "_replay_native_zero_final_source_retirement_v2",
        "_scan_native_zero_final_retained_data_v2",
        "compact_native_zero_final_source_retirement_v2",
        "acquire_native_close_read_source_v1",
        "_replay_native_close_read_source_v1",
        "_inhibit_native_close_read_source_v1",
        "bind_native_close_transport_v1",
        "_replay_native_close_transport_v1",
        "_inhibit_native_close_transport_v1",
        "acquire_native_close_post_read_source_v1",
        "_replay_native_close_post_read_source_v1",
        "_inhibit_native_close_post_read_source_v1",
        "prepare_native_filled_flat_source_cleanup_v1",
        "finish_native_filled_flat_source_cleanup_v1",
        "_replay_native_filled_flat_source_cleanup_completion_v1",
        "retire_native_filled_flat_source_cleanup_v1",
        "_replay_native_filled_flat_source_retirement_v1",
        "_scan_native_filled_cleanup_retained_data_v1",
        "compact_native_filled_cleanup_retirement_v1",
    ]

    def bind(key):
        from app.trade_qualification import (
            native_controlled_account_source_v6 as source,
        )

        if captured.module is not None or key is not source._SOURCE_KEY_V6:
            _deny("native_controlled_financial_bootstrap_already_bound_or_foreign")
        captured.module = source
        captured.kernel = source._REQUIRE_SOURCE_KERNEL_V6
        captured.functions = tuple((name, getattr(source, name)) for name in names)

    def function(name):
        if captured.module is None:
            _deny("native_controlled_financial_bootstrap_required")
        # Safety retirement never grants positive authority. Those fixed
        # entries authenticate their original registry/key independently and
        # remain callable after expiry/cancel/unknown or a failed trade guard.
        safety = (
            "_retain_unknown_owned_account_issuer_v1",
            "_release_issued_account_for_g11_v1",
            "_release_issued_account_for_r7_v2",
            "discard_issued_account_owner_v1",
            "_inhibit_issued_demo_transport_v2",
            "_inhibit_native_reconciliation_read_source_v1",
        )
        if name not in safety:
            captured.kernel()
        for expected, value in captured.functions:
            if name == expected:
                return value
        _deny("native_controlled_financial_fixed_purpose_required")

    def maybe_current(held, *, key):
        if captured.module is None:
            return None  # Genuine old operator purpose remains separate.
        return function("_replay_current_for_native_lineage_v6")(held, key=key)

    def cleanup_source(*, key):
        from app.trade_qualification import native_postsubmit_reconciliation_v1 as post

        if key is not post._SOURCE_SETTLEMENT_KEY_V1 or captured.module is None:
            _deny("native_flat_cleanup_captured_source_required")
        captured.kernel()
        selected = (
            "prepare_native_zero_final_source_cleanup_v2",
            "finish_native_zero_final_source_cleanup_v2",
            "_replay_native_zero_final_source_cleanup_completion_v2",
            "retire_native_zero_final_source_cleanup_v2",
            "_replay_native_zero_final_source_retirement_v2",
            "_scan_native_zero_final_retained_data_v2",
            "compact_native_zero_final_source_retirement_v2",
        )
        originals = tuple((name, function(name)) for name in selected)
        return captured.module, captured.kernel, originals

    def filled_cleanup_source(*, key):
        from app.trade_qualification import native_postsubmit_reconciliation_v1 as post

        if key is not post._FILLED_SOURCE_SETTLEMENT_KEY_V1 or captured.module is None:
            _deny("native_filled_cleanup_captured_source_required")
        captured.kernel()
        selected = (
            "prepare_native_filled_flat_source_cleanup_v1",
            "finish_native_filled_flat_source_cleanup_v1",
            "_replay_native_filled_flat_source_cleanup_completion_v1",
            "retire_native_filled_flat_source_cleanup_v1",
        )
        return (
            captured.module,
            captured.kernel,
            tuple((name, function(name)) for name in selected),
        )

    def close_read_source(*, key):
        from app.trade_qualification import native_postsubmit_reconciliation_v1 as post

        if key is not post._SOURCE_KEY_V1 or captured.module is None:
            _deny("native_close_read_captured_source_required")
        captured.kernel()
        selected = (
            "acquire_native_close_read_source_v1",
            "_replay_native_close_read_source_v1",
            "_inhibit_native_close_read_source_v1",
        )
        originals = tuple((name, function(name)) for name in selected)
        return captured.module, captured.kernel, originals

    def close_transport_source(*, key):
        from app.exchange.okx import native_demo_write_v2 as write

        if key is not write._CLOSE_INTENT_KEY_V1 or captured.module is None:
            _deny("native_close_transport_captured_source_required")
        captured.kernel()
        selected = (
            "bind_native_close_transport_v1",
            "_replay_native_close_transport_v1",
            "_inhibit_native_close_transport_v1",
        )
        return (
            captured.module,
            captured.kernel,
            tuple((name, function(name)) for name in selected),
        )

    def close_post_read_source(*, key):
        from app.trade_qualification import native_postsubmit_reconciliation_v1 as post

        if key is not post._SOURCE_KEY_V1 or captured.module is None:
            _deny("native_close_post_read_captured_source_required")
        captured.kernel()
        selected = (
            "acquire_native_close_post_read_source_v1",
            "_replay_native_close_post_read_source_v1",
            "_inhibit_native_close_post_read_source_v1",
        )
        return (
            captured.module,
            captured.kernel,
            tuple((name, function(name)) for name in selected),
        )

    wrappers = {}
    for name in names:
        if name == "_replay_current_for_native_lineage_v6":
            continue

        def make_entry(selected):
            def entry(*args, **kwargs):
                return function(selected)(*args, **kwargs)

            return entry

        wrappers[name] = make_entry(name)
    return (
        bind,
        maybe_current,
        wrappers,
        cleanup_source,
        close_read_source,
        close_transport_source,
        close_post_read_source,
        filled_cleanup_source,
    )


(
    _bind_controlled_financial_source_v6,
    _maybe_replay_controlled_current_lineage_v6,
    _CONTROLLED_FINANCIAL_ENTRIES_V6,
    _require_native_flat_cleanup_source_v1,
    _require_native_close_read_source_v1,
    _require_native_close_transport_source_v1,
    _require_native_close_post_read_source_v1,
    _require_native_filled_flat_cleanup_source_v1,
) = _make_controlled_financial_binding_v6()
acquire_owned_controlled_account_v6 = _CONTROLLED_FINANCIAL_ENTRIES_V6[
    "acquire_owned_controlled_account_v6"
]
_synchronize_control_operator_mirror_v6 = _CONTROLLED_FINANCIAL_ENTRIES_V6[
    "_synchronize_control_operator_mirror_v6"
]
_check_control_operator_mirror_budget_v6 = _CONTROLLED_FINANCIAL_ENTRIES_V6[
    "_check_control_operator_mirror_budget_v6"
]
_replay_controlled_current_mirror_context_v6 = _CONTROLLED_FINANCIAL_ENTRIES_V6[
    "_replay_controlled_current_mirror_context_v6"
]
_consume_controlled_history_use_v6 = _CONTROLLED_FINANCIAL_ENTRIES_V6[
    "_consume_controlled_history_use_v6"
]
_replay_controlled_coverage_context_v6 = _CONTROLLED_FINANCIAL_ENTRIES_V6[
    "_replay_controlled_coverage_context_v6"
]
_take_owned_account_issuer_v1 = _CONTROLLED_FINANCIAL_ENTRIES_V6[
    "_take_owned_account_issuer_v1"
]
_replay_owned_account_issuer_v1 = _CONTROLLED_FINANCIAL_ENTRIES_V6[
    "_replay_owned_account_issuer_v1"
]
_finish_owned_account_issuer_v1 = _CONTROLLED_FINANCIAL_ENTRIES_V6[
    "_finish_owned_account_issuer_v1"
]
_retain_unknown_owned_account_issuer_v1 = _CONTROLLED_FINANCIAL_ENTRIES_V6[
    "_retain_unknown_owned_account_issuer_v1"
]
_take_issued_account_for_g11_v1 = _CONTROLLED_FINANCIAL_ENTRIES_V6[
    "_take_issued_account_for_g11_v1"
]
_replay_issued_account_for_g11_v1 = _CONTROLLED_FINANCIAL_ENTRIES_V6[
    "_replay_issued_account_for_g11_v1"
]
_release_issued_account_for_g11_v1 = _CONTROLLED_FINANCIAL_ENTRIES_V6[
    "_release_issued_account_for_g11_v1"
]
_take_issued_account_for_r7_v2 = _CONTROLLED_FINANCIAL_ENTRIES_V6[
    "_take_issued_account_for_r7_v2"
]
_replay_issued_account_for_r7_v2 = _CONTROLLED_FINANCIAL_ENTRIES_V6[
    "_replay_issued_account_for_r7_v2"
]
_release_issued_account_for_r7_v2 = _CONTROLLED_FINANCIAL_ENTRIES_V6[
    "_release_issued_account_for_r7_v2"
]
discard_issued_account_owner_v1 = _CONTROLLED_FINANCIAL_ENTRIES_V6[
    "discard_issued_account_owner_v1"
]
require_issued_original_frame_context_v1 = _CONTROLLED_FINANCIAL_ENTRIES_V6[
    "require_issued_original_frame_context_v1"
]
evaluate_issued_g11_v1 = _CONTROLLED_FINANCIAL_ENTRIES_V6["evaluate_issued_g11_v1"]
_replay_issued_original_source_v2 = _CONTROLLED_FINANCIAL_ENTRIES_V6[
    "_replay_issued_original_source_v2"
]
require_issued_postpublication_barrier_v2 = _CONTROLLED_FINANCIAL_ENTRIES_V6[
    "require_issued_postpublication_barrier_v2"
]
scan_issued_original_public_v1 = _CONTROLLED_FINANCIAL_ENTRIES_V6[
    "scan_issued_original_public_v1"
]
scan_issued_postpublication_public_v2 = _CONTROLLED_FINANCIAL_ENTRIES_V6[
    "scan_issued_postpublication_public_v2"
]
scan_issued_metadata_v1 = _CONTROLLED_FINANCIAL_ENTRIES_V6["scan_issued_metadata_v1"]
scan_issued_artifact_v1 = _CONTROLLED_FINANCIAL_ENTRIES_V6["scan_issued_artifact_v1"]
bind_issued_demo_transport_v2 = _CONTROLLED_FINANCIAL_ENTRIES_V6[
    "bind_issued_demo_transport_v2"
]
_replay_issued_demo_transport_v2 = _CONTROLLED_FINANCIAL_ENTRIES_V6[
    "_replay_issued_demo_transport_v2"
]
_inhibit_issued_demo_transport_v2 = _CONTROLLED_FINANCIAL_ENTRIES_V6[
    "_inhibit_issued_demo_transport_v2"
]
require_issued_original_revision_locked_v2 = _CONTROLLED_FINANCIAL_ENTRIES_V6[
    "require_issued_original_revision_locked_v2"
]
require_issued_account_revision_locked_v1 = _CONTROLLED_FINANCIAL_ENTRIES_V6[
    "require_issued_account_revision_locked_v1"
]
acquire_postpublication_issued_account_v1 = _CONTROLLED_FINANCIAL_ENTRIES_V6[
    "acquire_postpublication_issued_account_v1"
]


# The genuine composite manifest, never historical54's denial pin map, closes
# this explicit current55 purpose. Parent operator-only carriers stay frozen.
_CONTROLLED_SOURCE_PATHS_V1 = (
    *_CONTROLLED_SOURCE_PATHS_V1,
    "app/trade_qualification/native_controlled_account_source_v6.py",
    "app/demo_automation/native_server_selectors_v2.py",
    "app/trade_qualification/controlled_writer_coverage_current55_v2.py",
    "app/trade_qualification/controlled_current_mirror_current55_v1.py",
    "app/trade_qualification/account_controlled_measured_hwm_v2.py",
    "app/trade_qualification/account_history_source_v1.py",
    "app/trade_qualification/account_original_raw_proof_bank_v1.py",
    "app/trade_qualification/account_original_position_path_v1.py",
    "app/trade_qualification/captured_execution_mode_v1.py",
    "app/trade_qualification/native_original_local_current55_v2.py",
    "app/trade_qualification/owned_native_candidate_v1.py",
    "app/trade_qualification/native_postpublication_recheck_v2.py",
    "app/trade_qualification/native_postpublication_support_v2.py",
    "app/trade_qualification/native_ledger_runtime_v2.py",
    "app/trade_qualification/native_continuation_guard_v1.py",
    "app/database/repositories/account_issued_revision_current55_v1.py",
    "app/database/repositories/account_history_original_raw_proof_bank_current55_v1.py",
    "app/exchange/okx/native_demo_source_identity_v2.py",
    "app/exchange/okx/native_demo_write_v2.py",
    "app/exchange/okx/native_demo_transport_v2.py",
    "app/exchange/okx/native_demo_vendor_integrity_v2.py",
)

_replay_issued_demo_reconciliation_context_v2 = _CONTROLLED_FINANCIAL_ENTRIES_V6[
    "_replay_issued_demo_reconciliation_context_v2"
]
_acquire_native_recovery_read_source_v1 = _CONTROLLED_FINANCIAL_ENTRIES_V6[
    "_acquire_native_recovery_read_source_v1"
]
_replay_native_recovery_read_source_v1 = _CONTROLLED_FINANCIAL_ENTRIES_V6[
    "_replay_native_recovery_read_source_v1"
]
_inhibit_native_reconciliation_read_source_v1 = _CONTROLLED_FINANCIAL_ENTRIES_V6[
    "_inhibit_native_reconciliation_read_source_v1"
]
scan_native_reconciliation_private_data_v1 = _CONTROLLED_FINANCIAL_ENTRIES_V6[
    "scan_native_reconciliation_private_data_v1"
]
