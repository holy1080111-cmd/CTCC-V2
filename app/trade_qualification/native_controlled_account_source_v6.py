"""Same-invocation controlled financial source and owned issuer.

The original OFF/bootstrap V5 purpose is unchanged. This new purpose starts
only from the genuine already-Armed runtime owner, uses the original native
CURRENT/history/PG-fold/custody acquisition, and the existing finite risk core
and DB0017 ledger. DATA constructors never supply source eligibility.

Postsubmission and restart reconciliation use distinct genuine read sources,
new clients and fixed read budgets. Expired trading owners never regain Arm,
CURRENT freshness or dispatch authority through these DATA-only contexts.
"""

import asyncio
from collections.abc import Coroutine
from dataclasses import dataclass, field, fields, is_dataclass
from datetime import UTC, datetime, timedelta
from decimal import Context, Decimal, localcontext
from fractions import Fraction
from pathlib import Path

from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings as configured
from app.core import native_frame_reader_0056_v1 as frame56
from app.core import native_locked_demo_control_0056_v1 as control56
from app.core import native_role_witness_0056_v1 as witness56
from app.database.models.account_revision_collector_lineage import (
    DemoCollectorCurrentRevisionLineage as Lineage,
)
from app.database.models.demo_operator_acknowledgements_v55 import (
    DemoPolicyCounterInitializationV1 as CounterRow,
)
from app.database.repositories import (
    account_history_original_raw_proof_bank_current55_v1 as reader55,
)
from app.database.repositories import (
    account_history_partition_current55_v2 as partition_repository,
)
from app.database.repositories import account_issued_revision_current55_v1 as issuer55
from app.database.repositories import (
    account_revision_collector_lineage_current55_v1 as lineage55,
)
from app.database.repositories import (
    collector_current_0055_multirole_catalog_v1 as catalog55,
)
from app.database.repositories import demo_control_current56_v1 as repository56
from app.database.repositories import (
    demo_operator_acknowledgements_current55_v1 as operator55,
)
from app.database.repositories import public_runtime_bundle_custody as custody
from app.database.repositories.demo_control_current55_v2 import DemoControlRepositoryV2
from app.database.repositories.qualification_ledger import QualificationLedgerRepository
from app.demo_automation import native_server_selectors_v2 as selector56
from app.domain import account_history_partition_v1 as partition_policy
from app.domain import native_clock
from app.domain import source_primitives as clock_contract
from app.domain.source_primitives import (
    canonical,
    decode,
    sha,
    utc_from_ns,
    validate_stamps,
)
from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_controlled_measured_hwm_v2 as hwm
from app.trade_qualification import account_current_source_v4 as current
from app.trade_qualification import account_history_source_bundle_v1 as history_bundle
from app.trade_qualification import account_history_source_v1 as history
from app.trade_qualification import account_ledger_claims_v2 as codec
from app.trade_qualification import account_observation_index as observed
from app.trade_qualification import account_original_position_path_v1 as position_path
from app.trade_qualification import account_original_raw_proof_bank_v1 as bank
from app.trade_qualification import account_owned_financial_v1 as financial
from app.trade_qualification import captured_execution_mode_v1 as execution_mode
from app.trade_qualification import control_bound_ledger as bound
from app.trade_qualification import controlled_current_mirror_current55_v1 as mirror55
from app.trade_qualification import (
    controlled_writer_coverage_current55_v2 as coverage55,
)
from app.trade_qualification import demo_control as control
from app.trade_qualification import demo_policy_counter_initialization_v1 as counter
from app.trade_qualification import native_account_risk_assembly_v1 as native
from app.trade_qualification import native_demo_control_owner_v2 as owner55
from app.trade_qualification import portfolio, reservations
from app.trade_qualification.account_runtime import ControlledDemoAccountSession

_SOURCE_KEY_V6 = object()
_SCAN_KEY_V6 = object()
_MAX_SOURCES_V6 = 16
_MAX_RETAINED_V6 = 16 * 1024 * 1024
_ARITHMETIC_V6 = Context(prec=100)


class ControlledAccountSourceError(ValueError):
    pass


def _deny(code):
    raise ControlledAccountSourceError(code) from None


class _OpaqueV6:
    __slots__ = ("__weakref__",)

    def __new__(cls):
        _deny("controlled_account_source_not_constructible")

    def __copy__(self):
        _deny("controlled_account_source_not_transferable")

    def __deepcopy__(self, memo):
        _deny("controlled_account_source_not_transferable")

    def __reduce_ex__(self, protocol):
        _deny("controlled_account_source_not_transferable")


class _AcquisitionV6(_OpaqueV6):
    __slots__ = ()


class _CoverageBindingV6(_OpaqueV6):
    __slots__ = ()


class _HistoryUseV6(_OpaqueV6):
    __slots__ = ()


class _IssuerOwnerV6(_OpaqueV6):
    __slots__ = ()


class OwnedIssuedAccountRevisionV1(_OpaqueV6):
    __slots__ = ()


class _HeldIssuedAccountV1(_OpaqueV6):
    __slots__ = ()


class _DemoTransportBindingV2(_OpaqueV6):
    __slots__ = ()


@dataclass(frozen=True, slots=True, repr=False)
class ControlledAccountRootsV6:
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
            for i, a in enumerate(roots)
            for b in roots[i + 1 :]
        ):
            _deny("controlled_account_distinct_roots_required")


_STATES_V6 = {}
_UNKNOWN_V6 = {}
_CURRENT_V6 = {}
_COVERAGE_V6 = {}
_HISTORY_USES_V6 = {}
_ISSUERS_V6 = {}
_ISSUED_V6 = {}
_HELD_V6 = {}
_TRANSPORT_V6 = {}
_OPERATOR_MIRRORS_V6 = {}


def _stamp_v6():
    stamp = native_clock.native_stamp()
    if (
        type(stamp) is not dict
        or set(stamp) != {"utc_ns", "monotonic_ns"}
        or any(type(value) is not int or value <= 0 for value in stamp.values())
    ):
        _deny("controlled_account_clock_invalid")
    return stamp


def _decimal_exact_v6(value):
    if type(value) is not Fraction:
        _deny("controlled_account_exact_amount_required")
    with localcontext(_ARITHMETIC_V6):
        result = Decimal(value.numerator) / Decimal(value.denominator)
    if Fraction(result) != value or not result.is_finite():
        _deny("controlled_account_decimal_rounding_forbidden")
    return result


def _context_v6():
    context = native._context()
    return (*context[:3], native.ref(context[3]))


def _check_v6(state):
    _REQUIRE_SOURCE_KERNEL_V6()
    if (
        type(state) is not dict
        or _STATES_V6.get(id(state)) is not state
        or state["phase"] == "UNKNOWN"
    ):
        _deny("controlled_account_source_unavailable")
    actual = native._context()
    expected = state["context"]
    if actual[:3] != expected[:3] or actual[3] is not expected[3]():
        _deny("controlled_account_original_context_changed")
    stamp = _FLAT_CONFIGURATION_SOURCE_V1._control_stamp_for_source_v1(
        state, key=_SOURCE_KEY_V6
    )
    if stamp is None:
        stamp = owner55._check_controlled_acquisition_v1(
            state["control"], key=native._CONTROLLED_SOURCE_KEY_V1
        )
    if native._package_inventory_controlled55_v1() != state["inventory"]:
        _deny("controlled_account_original_package_changed")
    if (
        configured.get_settings() is not state["control"]["read_session"]._settings
        or native._v3_data_pin(state["control"]["read_session"]._settings.__dict__)
        != state["settings_pin"]
    ):
        _deny("controlled_account_original_settings_changed")
    for factory, expected_pin, route in state["factories"]:
        if (
            history._factory_pin(factory) != expected_pin
            or native._database_connection_pin_v4(factory) != route
        ):
            _deny("controlled_account_original_factory_changed")
    if state.get("current") is not None:
        record, current_stamp = current._replay_account_revision_lineage_v1(
            state["current"], key=owner55.legacy_lineage._SOURCE_KEY
        )
        if (
            record != state["current_record"]
            or capture.plan_sha256(record.packet.plan) != state["current_plan_sha256"]
            or record.reference != state["current_reference"]
            or capture.plan_sha256(state["current_plan"])
            != state["current_plan_sha256"]
        ):
            _deny("controlled_account_original_CURRENT_changed")
        stamp = current_stamp
        if state.get("execution_mode") is not None:
            execution_mode.verify_captured_execution_mode_v1(
                state["execution_mode"],
                record.packet,
                expected_plan_sha256=state["current_plan_sha256"],
                expected_packet_sha256=record.reference.packet_sha256,
            )
        if state.get("instrument_specs") is not None:
            actual_specs = _instrument_specs_v6(state)
            if (
                type(state["instrument_specs"]) is not tuple
                or len(actual_specs) != len(state["instrument_specs"])
                or any(
                    type(item) is not portfolio.ContractRiskSpec
                    for item in state["instrument_specs"]
                )
                or tuple(
                    canonical(item.model_dump(mode="json")) for item in actual_specs
                )
                != tuple(
                    canonical(item.model_dump(mode="json"))
                    for item in state["instrument_specs"]
                )
            ):
                _deny("controlled_account_original_instrument_specifications_changed")
    if state.get("history") is not None:
        history_state = history._financial_state_v1(
            state["history"], key=native._HOOK_KEY
        )
        if history_state["source_sha256"] != state["history_sha256"]:
            _deny("controlled_account_original_history_changed")
    validate_stamps((state["last"], stamp))
    if (
        utc_from_ns(stamp["utc_ns"]) >= state["expires_at"]
        or stamp["monotonic_ns"] >= state["monotonic_deadline_ns"]
    ):
        _deny("controlled_account_original_expiry")
    _retained_budget_v6(state)
    finished = _stamp_v6()
    validate_stamps((stamp, finished))
    if (
        utc_from_ns(finished["utc_ns"]) >= state["expires_at"]
        or finished["monotonic_ns"] >= state["monotonic_deadline_ns"]
    ):
        _deny("controlled_account_original_expiry")
    state["last"] = finished
    return finished


def _remaining_v6(state):
    stamp = _check_v6(state)
    value = min(
        (state["expires_at"] - utc_from_ns(stamp["utc_ns"])).total_seconds(),
        (state["monotonic_deadline_ns"] - stamp["monotonic_ns"]) / 1_000_000_000,
    )
    if value <= 0:
        _deny("controlled_account_original_expiry")
    return value


def _shorten_v6(state, *, expires_at, monotonic_deadline_ns):
    state["expires_at"] = min(state["expires_at"], expires_at)
    state["monotonic_deadline_ns"] = min(
        state["monotonic_deadline_ns"], monotonic_deadline_ns
    )
    _check_v6(state)


def _make_retirement_v6():
    states, unknown = _STATES_V6, _UNKNOWN_V6
    maps = (
        _CURRENT_V6,
        _COVERAGE_V6,
        _HISTORY_USES_V6,
        _ISSUERS_V6,
        _ISSUED_V6,
        _HELD_V6,
    )
    control_active, control_unknown = owner55._ACTIVE, owner55._UNKNOWN
    revoke = owner55.original.DemoControlOwner._revoke
    owner_type = owner55.original.DemoControlOwner
    clear_current = dict.clear
    history_release = history._release_financial_assembly_v1
    history_key = native._HOOK_KEY

    def retire(state, *, uncertain):
        if (
            type(uncertain) is not bool
            or type(state) is not dict
            or states.get(id(state)) is not state
        ):
            return
        ctrl = state["control"]
        owner = ctrl["owner"]
        if type(owner) is not owner_type:
            _deny("controlled_account_original_safety_owner_required")
        revoke(owner, stop=uncertain)
        state["phase"] = "UNKNOWN" if uncertain else "RETIRED"
        for mapping in maps:
            for identity, record in tuple(mapping.items()):
                if record is state or (type(record) is tuple and record[0] is state):
                    mapping.pop(identity, None)
        control_active.pop(id(ctrl), None)
        ctrl["phase"] = "UNKNOWN" if uncertain else "RETIRED_SOURCE"
        if uncertain:
            unknown.setdefault(id(state), state)
            control_unknown.setdefault(id(ctrl), ctrl)
        else:
            states.pop(id(state), None)
            if state.get("history") is not None:
                history_release(state["history"], key=history_key)
            if state.get("current") is not None:
                clear_current(state["current"])
        # The real façade safety handle/control dictionary is retained. Only
        # explicit disarm/EStop/release may persist; no safety owner is erased.

    return retire


_RETIRE_SOURCE_V6 = _make_retirement_v6()


class _AcquisitionAttemptV6(Coroutine):
    """Retire the already-MOVEd source even if cancelled before first send."""

    def __init__(self, state, body, *, _retire=_RETIRE_SOURCE_V6):
        self._state, self._body, self._retire = state, body, _retire
        self._complete = False
        self._closed = False

    def __await__(self):
        return self

    def __iter__(self):
        return self

    def __next__(self):
        return self.send(None)

    def send(self, value):
        if self._closed:
            _deny("controlled_account_attempt_already_closed")
        try:
            return self._body.send(value)
        except StopIteration:
            self._complete = True
            raise
        except BaseException:
            self._closed = True
            self._retire(self._state, uncertain=True)
            raise

    def throw(self, *arguments):
        if self._closed:
            _deny("controlled_account_attempt_already_closed")
        try:
            return self._body.throw(*arguments)
        except StopIteration:
            self._complete = True
            raise
        except BaseException:
            self._closed = True
            self._retire(self._state, uncertain=True)
            raise

    def close(self):
        if not self._complete and not self._closed:
            self._closed = True
            self._retire(self._state, uncertain=True)
        self._body.close()


def _retire_v6(state, *, uncertain, _original_retire=_RETIRE_SOURCE_V6):
    return _original_retire(state, uncertain=uncertain)


async def _admit_writer_v6(state):
    _check_v6(state)
    database = _owned_database_0056_v6(state)
    if database is not None:
        await _admit_writer_0056_v6(state, *database)
        return
    async with state["control"]["factory"]() as session, session.begin():
        await session.execute(
            text("SET LOCAL search_path TO pg_catalog,public,pg_temp")
        )
        _check_v6(state)
        await session.execute(text("SET LOCAL TIME ZONE 'UTC'"))
        _check_v6(state)
        await catalog55.verify_actual_control_catalog_v2(session)
        _check_v6(state)
        actual, event = await DemoControlRepositoryV2.read_in_transaction(
            session, state["control"]["scope"]
        )
        _check_v6(state)
        expected = state["control"]["observation"]
        if actual != expected.state or event != expected.event_sha256:
            _deny("controlled_account_original_Control_changed")
    _check_v6(state)


def _owned_database_0056_v6(state):
    ctrl = state["control"]
    repository = ctrl["repository"]
    roles, factories = ctrl.get("db0056_roles"), ctrl.get("db0056_factories")
    if roles is factories is None and type(repository) is DemoControlRepositoryV2:
        return None
    if (
        type(repository) is not repository56.DemoControlRepository0056V1
        or type(roles) is not witness56.RestrictedNativeRoles0056WitnessV1
        or type(factories) is not selector56.ConfiguredNativeDemoFactoriesV2
    ):
        _deny("controlled_account_original_0056_owner_required")
    selected_record, selected_factories, selected_roles = (
        selector56._controlled_identity_0056(ctrl["identity_selection"])
    )
    if (
        selected_record is not ctrl["identity_record"]
        or selected_factories is not factories
        or selected_roles is not roles
        or ctrl["factory"] is not factories.writer_factory
        or ctrl["custody_original_factory"] is not factories.custody_factory
        or selected_record.raw_reader_factory is not factories.frame_reader_factory
        or selected_record.read_session is not ctrl["read_session"]
        or selected_record.private_client is not ctrl["private_client"]
        or selected_record.current_plan is not ctrl["plan"]
        or repository.session_factory is not ctrl["factory"]
        or repository._selected is not factories
        or repository._roles is not roles
        or repository._scope != ctrl["scope"]
    ):
        _deny("controlled_account_original_0056_owner_changed")
    return factories, roles


async def _admit_writer_0056_v6(state, factories, roles):
    ctrl = state["control"]
    async with (
        ctrl["factory"]() as session,
        session.begin(),
        control56.held_native_writer_control_0056_v1(
            factories,
            session,
            ctrl["scope"],
            roles=roles,
            purpose=control56.NativeWriterControlPurpose0056.PREPARE_OBSERVATION,
        ) as held,
    ):
        _check_v6(state)
        held.check_held()
        actual = held.observation.control
        expected = ctrl["observation"]
        if (
            actual.state != expected.state
            or actual.event_sha256 != expected.event_sha256
            or not expected.observed_at <= actual.observed_at < state["expires_at"]
            or not actual.state.arm_intent_current(actual.observed_at)
            or actual.state.owner_sha256 != ctrl["owner_sha256"]
            or actual.state.pins != ctrl["pins"]
        ):
            _deny("controlled_account_original_0056_Control_changed")
    _check_v6(state)


def _fresh_current_plan_v6(state):
    stamp = _check_v6(state)
    original = state["control"]["plan"]
    values = original.model_dump(mode="python")
    values.update(
        plan_id=state["invocation_id"], created_at=utc_from_ns(stamp["utc_ns"])
    )
    plan = capture.CurrentDemoAccountCapturePlanV7.model_validate(values)
    if any(
        getattr(plan, name) != getattr(original, name)
        for name in type(plan).model_fields
        if name not in {"plan_id", "created_at"}
    ):
        _deny("controlled_account_original_plan_changed")
    return plan


async def _current_v6(state):
    await _admit_writer_v6(state)
    plan = _fresh_current_plan_v6(state)
    state["current_plan"] = plan
    state["current_plan_sha256"] = capture.plan_sha256(plan)
    session = ControlledDemoAccountSession.from_pinned_read_session(
        read_session=state["control"]["read_session"],
        plan=plan,
        expected_plan_sha256=state["current_plan_sha256"],
    )
    result = await current.acquire_initial_demo_account_source_v4(
        account_session=session,
        private_client=state["control"]["private_client"],
        session_factory=state["control"]["factory"],
        private_proof_root=state["roots"].account_proof_root,
        native_proof_root=state["roots"].account_native_root,
        custody_repository=state["control"]["custody_repository"],
    )
    state["unmoved_current"] = result
    _check_v6(state)
    before_take = current._CURRENT.get(result)
    state["pre_take_current"] = (
        None if type(before_take) is not dict else dict(before_take)
    )
    held = current._take_account_revision_lineage_v1(
        result, key=owner55.legacy_lineage._SOURCE_KEY
    )
    state["unmoved_current"] = None
    replay, _stamp = current._replay_account_revision_lineage_v1(
        held, key=owner55.legacy_lineage._SOURCE_KEY
    )
    state["current"] = held
    state["current_record"] = replay
    state["current_reference"] = replay.reference
    _CURRENT_V6[id(held)] = state
    _shorten_v6(
        state,
        expires_at=replay.expires_at,
        monotonic_deadline_ns=replay.monotonic_deadline_ns,
    )
    await _admit_writer_v6(state)
    state["execution_mode"] = execution_mode.derive_captured_execution_mode_v1(
        replay.packet,
        expected_plan_sha256=state["current_plan_sha256"],
        expected_packet_sha256=replay.reference.packet_sha256,
    )
    _check_v6(state)


def _replay_current_for_native_lineage_v6(held, *, key):
    if key is not lineage55._SOURCE_KEY:
        _deny("controlled_account_lineage_fixed_purpose_required")
    state = _CURRENT_V6.get(id(held))
    if state is None:
        return None  # Only the separate genuine operator registry may try next.
    if state["current"] is not held:
        _deny("controlled_account_CURRENT_owner_changed")
    _check_v6(state)
    return current._replay_account_revision_lineage_v1(
        held, key=owner55.legacy_lineage._SOURCE_KEY
    )


def _coverage_context_v6(binding):
    if type(binding) is not _CoverageBindingV6:
        _deny("controlled_account_coverage_fixed_purpose_required")
    state = _COVERAGE_V6.get(binding)
    if state is None or state["phase"] not in (
        "ACQUIRING",
        "HISTORY",
        "BANK",
        "PREPARING_ISSUER",
    ):
        _deny("controlled_account_coverage_source_unavailable")
    _check_v6(state)
    record = state["current_record"]
    ctrl = state["control"]
    return coverage55.ControlledCoverageSourceV6(
        session_factory=ctrl["factory"],
        scope=ctrl["ledger_scope"],
        control_state=ctrl["observation"].state,
        control_event_sha256=ctrl["observation"].event_sha256,
        current_record=record,
        current_bundle_raw=state["current"]["bundle_raw"],
        current_custody_repository=ctrl["custody_repository"],
        current_custody_id=state["current"]["custody_id"],
        current_bundle_sha256=state["current"]["bundle_sha256"],
        source_generation=state["generation"],
        original_expires_at=state["expires_at"],
        monotonic_deadline_ns=state["monotonic_deadline_ns"],
        writer_inventory_sha256=state["inventory"][0]["writer_sha256"],
        read_session=ctrl["read_session"],
        db0056_factories=ctrl.get("db0056_factories"),
        db0056_roles=ctrl.get("db0056_roles"),
    )


def _replay_controlled_coverage_context_v6(binding, *, key):
    if key is not coverage55._SOURCE_KEY:
        _deny("controlled_account_coverage_fixed_purpose_required")
    return _coverage_context_v6(binding)


def _replay_controlled_current_mirror_context_v6(binding, *, key):
    if key is not mirror55._SOURCE_KEY:
        _deny("controlled_account_mirror_fixed_purpose_required")
    operator_state = (
        _OPERATOR_MIRRORS_V6.get(binding) if type(binding) is object else None
    )
    if operator_state is None:
        return _coverage_context_v6(binding)
    native._control_operator_check_v1(operator_state)
    ctrl = operator_state["control"]
    record, _ = current._replay_account_revision_lineage_v1(
        operator_state["current"], key=owner55.legacy_lineage._SOURCE_KEY
    )
    operator_state["mirror_current_record"] = record
    _check_control_operator_mirror_budget_v6(operator_state)
    return coverage55.ControlledCoverageSourceV6(
        session_factory=ctrl["factory"],
        scope=ctrl["ledger_scope"],
        control_state=ctrl["observation"].state,
        control_event_sha256=ctrl["observation"].event_sha256,
        current_record=record,
        current_bundle_raw=operator_state["current"]["bundle_raw"],
        current_custody_repository=ctrl["custody_repository"],
        current_custody_id=operator_state["current"]["custody_id"],
        current_bundle_sha256=operator_state["current"]["bundle_sha256"],
        source_generation=operator_state["mirror_generation"],
        original_expires_at=operator_state["expires_at"],
        monotonic_deadline_ns=operator_state["monotonic_deadline_ns"],
        writer_inventory_sha256=operator_state["inventory"][0]["writer_sha256"],
        read_session=ctrl["read_session"],
        db0056_factories=ctrl.get("db0056_factories"),
        db0056_roles=ctrl.get("db0056_roles"),
    )


def _check_control_operator_mirror_budget_v6(state):
    if (
        type(state) is not dict
        or (
            native._CONTROL_OPERATOR_ACTIVE_V1.get(id(state)) is not state
            and native._CONTROL_OPERATOR_UNKNOWN_V1.get(id(state)) is not state
        )
        or state.get("mirror_binding") is None
    ):
        _deny("controlled_account_original_operator_mirror_required")
    _retained_budget_v6(
        {
            "control": state["control"],
            "current": state["current"],
            "current_record": state.get("mirror_current_record"),
            "mirror_observation": state.get("mirror_observation"),
            "coverage_binding": state["mirror_binding"],
        }
    )


async def _synchronize_control_operator_mirror_v6(state):
    native._control_operator_check_v1(state)
    if (
        state["purpose"] not in ("clear_stop", "policy_counter_initialize")
        or state.get("mirror_binding") is not None
    ):
        _deny("controlled_account_operator_mirror_once_required")
    if len(_OPERATOR_MIRRORS_V6) >= _MAX_SOURCES_V6:
        _deny("controlled_account_operator_mirror_capacity")
    binding = object()
    state["mirror_binding"], state["mirror_generation"] = binding, object()
    _OPERATOR_MIRRORS_V6[binding] = state
    value = await mirror55.synchronize_owned_current_mirror_v1(binding)
    native._control_operator_check_v1(state)
    if (
        type(value) is not mirror55.ControlledCurrentMirrorObservationV1
        or value.scope != state["control"]["ledger_scope"]
        or value.control_state != state["control"]["observation"].state
        or value.control_event_sha256 != state["control"]["observation"].event_sha256
        or value.source_generation is not state["mirror_generation"]
    ):
        _deny("controlled_account_actual_operator_mirror_changed")
    state["mirror_observation"] = value
    _check_control_operator_mirror_budget_v6(state)
    _OPERATOR_MIRRORS_V6.pop(binding)


async def _synchronize_mirror_v6(state):
    _check_v6(state)
    value = await mirror55.synchronize_owned_current_mirror_v1(
        state["coverage_binding"]
    )
    _check_v6(state)
    if (
        type(value) is not mirror55.ControlledCurrentMirrorObservationV1
        or value.scope != state["control"]["ledger_scope"]
        or value.control_state != state["control"]["observation"].state
        or value.control_event_sha256 != state["control"]["observation"].event_sha256
        or value.source_generation is not state["generation"]
    ):
        _deny("controlled_account_actual_mirror_observation_changed")
    state["mirror_observation"] = value
    _retained_budget_v6(state)


def _consume_controlled_history_use_v6(
    token,
    *,
    read_session,
    private_client,
    scope_config,
    session_factory,
    custody_session_factory,
    key,
):
    if (
        key is not history._CONTROLLED_CURRENT_KEY_V4
        or type(token) is not _HistoryUseV6
    ):
        _deny("controlled_account_history_fixed_purpose_required")
    state = _HISTORY_USES_V6.pop(token, None)
    if state is None:
        _deny("controlled_account_history_use_already_moved")
    _check_v6(state)
    ctrl, record = state["control"], state["current_record"]
    if (
        read_session is not ctrl["read_session"]
        or private_client is not ctrl["private_client"]
        or scope_config != state["scope_config"]
        or session_factory is not ctrl["factory"]
        or custody_session_factory is not ctrl["custody_factory"]
    ):
        _deny("controlled_account_history_original_context_changed")
    original = record.packet.barrier_completed_at - timedelta(days=7, seconds=60)
    requested = original.replace(microsecond=(original.microsecond // 1000) * 1000)
    return (
        requested,
        sha(
            canonical(
                {
                    "schema_version": "ctcc.native_controlled_history_once.v6",
                    "credential_session_binding": read_session.binding_id,
                    "invocation_id": state["invocation_id"],
                }
            )
        ),
        _owned_database_0056_v6(state),
    )


async def _history_v6(state):
    _check_v6(state)
    state["phase"] = "HISTORY"
    token = object.__new__(_HistoryUseV6)
    _HISTORY_USES_V6[token] = state
    ctrl = state["control"]
    owner = await history._acquire_native_history_controlled_asof_v4(
        read_session=ctrl["read_session"],
        private_client=ctrl["private_client"],
        scope_config=state["scope_config"],
        session_factory=ctrl["factory"],
        custody_session_factory=ctrl["custody_factory"],
        raw_root=state["roots"].history_raw_root,
        companion_root=state["roots"].history_companion_root,
        controlled_source_token=token,
    )
    state["unmoved_history"] = owner
    _check_v6(state)
    before_take = history._OWNERS.get(owner)
    state["pre_take_history"] = (
        None if type(before_take) is not dict else dict(before_take)
    )
    binding = history._take_financial_assembly_v2(owner, key=native._HOOK_KEY)
    state["unmoved_history"] = None
    state["history"] = binding
    history_state = history._financial_state_v1(binding, key=native._HOOK_KEY)
    state["history_sha256"] = history_state["source_sha256"]
    state["facts"], history_stamp = await history._replay_financial_assembly_v1(
        binding, key=native._HOOK_KEY
    )
    _check_v6(state)
    first = min(page.request_started_at for page in state["facts"].packet.observations)
    record = state["current_record"]
    if (
        not record.packet.completed_at < first
        or state["facts"].packet.plan.history_end < record.packet.completed_at
    ):
        _deny("controlled_account_history_not_after_original_CURRENT")
    original = history_state["original"]
    _shorten_v6(
        state,
        expires_at=utc_from_ns(original["utc_ns"]) + timedelta(seconds=60),
        monotonic_deadline_ns=original["monotonic_ns"] + 60_000_000_000,
    )
    state["history_tokens"] = history_state["controlled_private_tokens"]
    state["last"] = history_stamp


def _latest_native_linkage_v6(state, facts):
    from app.trade_qualification import account_history_current_bundle_v2 as bounded

    _check_v6(state)
    held_history = history._financial_state_v1(state["history"], key=native._HOOK_KEY)
    original = history_bundle._json(facts.bundle_raw)
    temporal = original["temporal"]
    document = history_bundle._json(
        facts.batches[-1].document_json, observed.MAX_SOURCE_BYTES
    )
    if (
        type(facts) is not bounded.CurrentHistorySourceFactsV2
        or held_history.get("partition_current_key")
        is not history._PARTITION_CURRENT_V2
        or held_history["facts"].bundle_raw != facts.bundle_raw
        or held_history["source_sha256"] != facts.bundle_sha256
        or sha(facts.bundle_raw) != facts.bundle_sha256
        or original["policy_sha256"] != bounded.POLICY_SHA256
        or held_history["original"] != temporal["original_started"]
        or held_history["custody_proof_sha256"] != facts.proof_sha256
        or facts.proof_sha256 != sha(history.journal.canonical(temporal))
        or held_history["reference"] != facts.reference
        or document["reference"] != observed.reference_document(facts.reference)
    ):
        _deny("native_financial_v4_original_linkage_changed")
    linkage = {
        "schema_version": partition_policy.LINKAGE_SCHEMA,
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
        "custody_bundle_id": held_history["custody_id"],
        "bundle_sha256": facts.bundle_sha256,
        "proof_sha256": facts.proof_sha256,
        "native_policy_sha256": bounded.POLICY_SHA256,
        "source_manifest_sha256": state["inventory"][1],
        "raw_inventory": {
            name: list(pin) for name, pin in held_history["raw_inventory"].items()
        },
        "companion_inventory": {
            name: list(pin) for name, pin in held_history["companion_inventory"].items()
        },
        "raw_root": {
            "path": str(held_history["raw_root"]),
            "device": held_history["root_identity"][0],
            "inode": held_history["root_identity"][1],
        },
        "companion_root": {
            "path": str(held_history["companion_root"]),
            "device": held_history["companion_identity"][0],
            "inode": held_history["companion_identity"][1],
        },
        "original_started": dict(held_history["original"]),
        "native_started": dict(temporal["stage"]["started"]),
        "source_readback_complete": dict(temporal["source_readback_complete"]),
    }
    raw = partition_policy.canonical(linkage)
    registration = partition_policy.derive_native_member_registration_v1(
        scope=_partition_scope_v6(facts),
        b3_document_raw=facts.batches[-1].document_json,
        native_linkage_raw=raw,
        expected_b3_policy_sha256=document["policy_sha256"],
        expected_b3_event_sha256=facts.batches[-1].event_sha256,
    )
    return raw, registration, document["policy_sha256"]


def _partition_scope_v6(facts):
    return partition_policy.HistoryPartitionScopeV1(
        facts.scope.environment, facts.scope.account_id, facts.scope.settlement_currency
    )


def _partition_fences_v6(state, observation):
    _check_v6(state)
    point = observation.ledger_state
    if (
        point.scope != state["control"]["ledger_scope"]
        or observation.control_state != state["control"]["observation"].state
        or observation.control_event_sha256
        != state["control"]["observation"].event_sha256
        or observation.source_generation is not state["generation"]
    ):
        _deny("controlled_account_actual_coverage_fences_changed")
    return (
        observation.control_state.revision,
        point.account_revision,
        point.ledger_revision,
    )


def _partition_readback_v6(state, facts, observation, value):
    _check_v6(state)
    if (
        type(value) is not partition_repository.HistoryPartitionReadbackV1
        or value.scope != _partition_scope_v6(facts)
        or value.b3_documents != tuple(batch.document_json for batch in facts.batches)
        or value.head_sequence != facts.batches[-1].sequence
        or value.head_sha256 != facts.batches[-1].event_sha256
        or (value.control_revision, value.account_revision, value.ledger_revision)
        != _partition_fences_v6(state, observation)
        or value.account_complete is not False
        or value.execution_authority is not False
    ):
        _deny("controlled_account_actual_partition_changed")
    if tuple(row.b3_sequence for row in value.members) != tuple(
        sorted({row.b3_sequence for row in value.members})
    ):
        _deny("controlled_account_partition_membership_conflict")


def _same_coverage_v6(state, value):
    if type(value) is not coverage55.ControlledSourceCoverageObservationV2:
        _deny("controlled_account_actual_coverage_observation_required")
    _partition_fences_v6(state, value)
    original = state.get("coverage_observation")
    if original is None:
        state["coverage_observation"] = value
    elif (
        value.control_state != original.control_state
        or value.control_event_sha256 != original.control_event_sha256
        or value.ledger_state != original.ledger_state
        or value.local_checkpoint.document_json
        != original.local_checkpoint.document_json
        or value.received_at < original.received_at
    ):
        _deny("controlled_account_original_coverage_changed")
    receipt = decode(
        state["current_record"].current_receipt_json, capture.MAX_PACKET_BYTES
    )
    if (
        receipt.get("observed_flat") is not True
        or value.local_checkpoint.known_flat is not True
    ):
        _deny("controlled_account_initial_purpose_requires_actual_complete_flat")
    state["latest_coverage_observation"] = value
    return value


async def _coverage_v6(state):
    _check_v6(state)
    value = await coverage55.observe_controlled_source_coverage_v2(
        state["coverage_binding"]
    )
    _check_v6(state)
    return _same_coverage_v6(state, value)


async def _register_prefix_v6(state):
    facts = state["facts"]
    observation = await _coverage_v6(state)
    history_state = history._financial_state_v1(state["history"], key=native._HOOK_KEY)

    def clock():
        return utc_from_ns(_check_v6(state)["utc_ns"])

    repository = partition_repository.AccountHistoryPartitionRepository(
        history_state["factory"], clock=clock
    )
    state["partition_repository"] = repository
    await repository.verify_deployment()
    _check_v6(state)
    before = await repository.read(_partition_scope_v6(facts))
    _partition_readback_v6(state, facts, observation, before)
    linkage, registration, source_policy = _latest_native_linkage_v6(state, facts)
    checkpoint = native._partition_checkpoint_v4(
        facts, before, registration, source_policy
    )
    await _coverage_v6(state)
    committed = await repository.append_member_and_checkpoint(
        _partition_scope_v6(facts),
        b3_document_raw=facts.batches[-1].document_json,
        native_linkage_raw=linkage,
        expected_b3_policy_sha256=source_policy,
        expected_b3_event_sha256=facts.batches[-1].event_sha256,
        expected_previous_checkpoint_sha256=before.checkpoints[-1].record_sha256
        if before.checkpoints
        else None,
        expected_control_revision=observation.control_state.revision,
        expected_account_revision=observation.ledger_state.account_revision,
        expected_ledger_revision=observation.ledger_state.ledger_revision,
        expires_at=state["expires_at"],
        checkpoint_raw=checkpoint,
    )
    _partition_readback_v6(state, facts, observation, committed)
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
        _deny("controlled_account_registration_commit_changed")
    await _coverage_v6(state)
    final = await repository.read(_partition_scope_v6(facts))
    _partition_readback_v6(state, facts, observation, final)
    if (
        native._v3_readback_identity(final) != native._v3_readback_identity(committed)
        or final.transaction_id == committed.transaction_id
    ):
        _deny("controlled_account_registration_independent_readback_changed")
    _check_v6(state)
    state["partition"] = final
    state["partition_pin"] = native._native_hwm_prefix_pin_v1(final)
    state["registration"] = registration


async def _bank_v6(state):
    _check_v6(state)
    state["phase"] = "BANK"
    await _register_prefix_v6(state)
    facts, original, final = (
        state["facts"],
        history_bundle._json(state["facts"].bundle_raw),
        state["partition"],
    )
    position_plan = native.financial_v2.position_pages.PositionHistoryPlanV1(
        **{
            **original["position_plan"],
            "created_at": datetime.fromisoformat(
                original["position_plan"]["created_at"]
            ),
        }
    )
    identity_doc = original["position_identity"]
    if identity_doc["declared_scope_currency"] != "USDT":
        _deny("controlled_account_position_archive_scope_changed")
    position_identity = native.financial_v2.positions._NativePositionIdentityV1(
        **{
            name: value
            for name, value in identity_doc.items()
            if name != "declared_scope_currency"
        }
    )
    positions = native.financial_v2.NativePositionSourceV2(
        position_plan,
        position_identity,
        facts.positions.raw_archive,
        facts.positions.archive_sha256,
    )
    state["position_source"] = positions
    request = native._v4_wire.OriginalFrameRequestV1(
        _partition_scope_v6(facts),
        final.head_sequence,
        final.head_sha256,
        0,
        None,
        final.control_revision,
        final.account_revision,
        final.ledger_revision,
        state["expires_at"],
    )
    result = await reader55.replay_original_history_api_population_from_pg_v2(
        state["reader_factory"],
        genesis_request=request,
        expected_checkpoint_sha256=final.checkpoints[-1].record_sha256
        if final.checkpoints
        else None,
        approved_collector_role_oid=state["reader_role_oid"],
        lineage_session_factory=state["control"]["factory"],
        expected_financial_policy_sha256=sha(
            native._v4_stream._financial_stream_policy_bytes_v3()
        ),
        expected_raw_bank_policy_sha256=bank.POLICY_SHA256,
        expected_api_population_policy_sha256=bank.API_POPULATION_POLICY_SHA256_V2,
        position_source=positions,
    )
    _check_v6(state)
    if type(result) is not reader55.OriginalAccountApiPopulationPgReadObservationV2:
        _deny("controlled_account_actual_bank_read_required")
    await _coverage_v6(state)
    after = await state["partition_repository"].read(_partition_scope_v6(facts))
    _partition_readback_v6(state, facts, state["coverage_observation"], after)
    if (
        native._native_hwm_prefix_pin_v1(after) != state["partition_pin"]
        or after.transaction_id == final.transaction_id
    ):
        _deny("controlled_account_original_prefix_changed")
    _check_v6(state)
    # Retain the SAME actual typed bank and original validation time, not a
    # digest-only continuation or another cold-start/packet reconstruction.
    state["bank_result"] = result
    state["population"] = result.api_population
    state["original_validated_at"] = result.measured_validation_at
    state["prefix_sha256"] = state["partition_pin"].hex()
    if (
        sum(
            map(
                len,
                (
                    result.raw_proof_bank.raw_source_json,
                    result.raw_proof_bank.financial_facts.receipt_json,
                    result.raw_proof_bank.receipt_json,
                    result.api_population.receipt_json,
                    result.acquisition_receipt_json,
                    result.replay.original_replay.receipt_json,
                ),
            )
        )
        > _MAX_RETAINED_V6
    ):
        _deny("controlled_account_retained_bank_byte_bound")


async def _reader_role_v6(state):
    _check_v6(state)
    database = _owned_database_0056_v6(state)
    async with state["reader_factory"]() as session, session.begin():
        for query in (
            "SET TRANSACTION ISOLATION LEVEL READ COMMITTED",
            "SET LOCAL search_path TO pg_catalog,public,pg_temp",
            "SET LOCAL TIME ZONE 'UTC'",
            "SET LOCAL lock_timeout='1000ms'",
            "SET LOCAL statement_timeout='1000ms'",
        ):
            await session.execute(text(query))
            _check_v6(state)

        class CatalogFence:
            def check(self):
                return utc_from_ns(_check_v6(state)["utc_ns"])

        fence = CatalogFence()
        if database is None:
            actual = await lineage55._metadata(session)
            _check_v6(state)
            await catalog55.verify_actual_raw_history_catalog_v1(
                session,
                fence,
                actual[3],
                state["control"]["factory"],
                state["reader_factory"],
            )
            reader_oid = actual[3]
        else:
            reader_oid = await _reader_role_0056_v6(state, session, fence, *database)
        _check_v6(state)
    _check_v6(state)
    state["reader_role_oid"] = reader_oid


async def _reader_role_0056_v6(state, session, fence, factories, roles):
    reader = state["reader_factory"]
    routes, names = frame56._checked_factories(
        session,
        factories.writer_factory,
        factories.custody_factory,
        reader,
    )
    raw_pin = history._factory_pin(factories.frame_reader_factory)
    if (
        state["factories"][1][:2] != (factories.frame_reader_factory, raw_pin)
        or history._factory_pin(reader)[1:] != raw_pin[1:]
    ):
        _deny("controlled_account_original_0056_frame_factory_changed")
    route = selector56._database_route(reader.kw["bind"].url)
    raw_before = await frame56._identity(session, fence, names[2])
    proof_bytes = await frame56.verify_held_frame_catalog_0056_v1(
        session,
        fence,
        factories.writer_factory,
        factories.custody_factory,
        reader,
    )
    fence.check()
    actual = decode(proof_bytes, 4 * 1024 * 1024)
    graphs = actual["current0056"]
    profile = graphs["profile"]
    writer_name, writer_oid = (
        graphs["writer"]["role"]["name"],
        graphs["writer"]["role"]["oid"],
    )
    writer_identity = (writer_name, writer_name, "none", writer_oid, *raw_before[4:])
    if (
        writer_name != names[0]
        or actual["schema"] != "ctcc.native_only.held_frame_catalog_0056.v1"
        or actual["database_head"] != "0056"
        or actual["granted_inherited_acl_shape_sha256"]
        != roles.observation.granted_inherited_acl_shape_sha256
        or profile["native_definitions_sha256"]
        != roles.observation.inherited_native_definitions_sha256
        or witness56.runtime_witness_0056(writer_identity, names, (route,) * 3)
        != (roles.server_incarnation_sha256, roles.role_route_sha256)
        or graphs["raw"]["role"]["oid"] != raw_before[3]
        or actual["account_complete"] is not False
        or actual["execution_authority"] is not False
        or actual["admission"] != "DENY"
    ):
        _deny("controlled_account_original_0056_frame_role_changed")
    if await frame56._identity(session, fence, names[2]) != raw_before:
        _deny("controlled_account_original_0056_frame_transaction_changed")
    fence.check()
    if (
        frame56._checked_factories(
            session, factories.writer_factory, factories.custody_factory, reader
        )[0]
        != routes
    ):
        _deny("controlled_account_original_0056_frame_factory_changed")
    return raw_before[3]


def _source_stamp_v6(state, *, source_sha256, observed_at, received_at):
    return portfolio.EvidenceStamp(
        source_sha256=source_sha256,
        observed_at=observed_at,
        received_at=received_at,
        environment=state["control"]["ledger_scope"].environment,
        account_id=state["control"]["ledger_scope"].account_id,
        complete=True,
    )


def _instrument_specs_v6(state):
    packet = state["current_record"].packet
    rows = {}
    for page in packet.observations:
        if page.request.stream == "account_instruments":
            for member in page.rows:
                value = decode(member.canonical_json.encode("utf-8"), 65536)
                rows.setdefault(value.get("instId"), []).append((page, value))
    result = []
    requested = state["control"]["plan"].leverage_instrument_ids
    allowed = state["control"]["read_session"]._settings.okx_demo_allowed_symbol_list
    if any(name not in allowed for name in requested):
        _deny("controlled_account_instrument_outside_configured_scope")
    for name in requested:
        choices = rows.get(name)
        if type(choices) is not list or len(choices) == 0:
            _deny("controlled_account_actual_instrument_metadata_missing")
        page, item = choices[0]
        if any(canonical(value) != canonical(item) for _, value in choices):
            _deny("controlled_account_actual_instrument_metadata_changed")
        if (
            item.get("instId") != name
            or item.get("instType") != "SWAP"
            or item.get("ctType") != "linear"
            or item.get("settleCcy") != "USDT"
            or item.get("ctValCcy") != name.split("-")[0]
            or item.get("ctMult") != "1"
            or item.get("state") != "live"
        ):
            _deny("controlled_account_instrument_contract_unsupported")
        amounts = {
            field: _decimal_exact_v6(
                position_path._number(item.get(field), positive=True)
            )
            for field in ("ctVal", "lotSz", "minSz", "maxLmtSz", "lever")
        }
        leverage = amounts["lever"]
        if (
            leverage != leverage.to_integral_value()
            or not 1 <= leverage <= 125
            or amounts["minSz"] > amounts["maxLmtSz"]
        ):
            _deny("controlled_account_actual_instrument_limits_invalid")
        result.append(
            portfolio.ContractRiskSpec(
                instrument_id=name,
                base_currency=name.split("-")[0],
                settlement_currency="USDT",
                contract_kind="linear_base",
                contract_value_currency=name.split("-")[0],
                contract_value=amounts["ctVal"],
                lot_size=amounts["lotSz"],
                min_contracts=amounts["minSz"],
                max_contracts=amounts["maxLmtSz"],
                max_leverage=int(leverage),
                correlation_group="all_reviewed_linear_USDT_SWAPS_shared_conservative_group",
                source_sha256=sha(canonical(item)),
                observed_at=page.body_completed_at,
                received_at=packet.completed_at,
            )
        )
    if len(result) != len(requested) or len(
        {item.instrument_id for item in result}
    ) != len(result):
        _deny("controlled_account_actual_instrument_population_changed")
    return tuple(result)


def _financial_v6(state, *, at):
    _check_v6(state)
    result = financial.derive_owned_finite_financial_values_v1(
        state["population"],
        scope=state["control"]["ledger_scope"],
        original_validated_at=state["original_validated_at"],
        evaluated_at=at,
        held_current_record=state["current_record"],
        original_position_source=state["position_source"],
        configured_policy=state["control"]["configured_policy"],
        original_expires_at=state["expires_at"],
        original_prefix_sha256=state["prefix_sha256"],
        policy_counter_initialization=state.get("counter"),
    )
    _check_v6(state)
    return result


def _claims_v6(state, values):
    _check_v6(state)
    record, ctrl = state["current_record"], state["control"]
    scope = ctrl["ledger_scope"]
    point = state["latest_coverage_observation"].local_checkpoint
    if point.known_flat is not True:
        _deny("controlled_account_initial_local_flat_unknown")
    anchor = position_path._coupled_anchor_from_held_current_record_v3(
        record, scope=scope, checked_at=state["original_validated_at"]
    )
    if any(item.contracts != 0 for item in anchor.positions):
        _deny("controlled_account_initial_position_inventory_not_flat")
    receipt = decode(record.current_receipt_json, capture.MAX_PACKET_BYTES)
    if receipt.get("observed_flat") is not True:
        _deny("controlled_account_initial_exchange_inventory_not_flat")
    separate = decode(anchor.separate_balance_measurement_json, 1024 * 1024)
    balance_at = datetime.fromisoformat(separate["body_completed_at"])
    balance_stamp = _source_stamp_v6(
        state,
        source_sha256=sha(anchor.separate_balance_measurement_json),
        observed_at=balance_at,
        received_at=record.packet.completed_at,
    )
    positions_stamp = _source_stamp_v6(
        state,
        source_sha256=sha(anchor.receipt_json),
        observed_at=anchor.risk_body_completed_at,
        received_at=record.packet.completed_at,
    )
    history_stamp = _source_stamp_v6(
        state,
        source_sha256=state["bank_result"].acquisition_receipt_sha256,
        observed_at=values.financial.source_as_of_at,
        received_at=state["bank_result"].session_closed_at,
    )
    reservations_stamp = _source_stamp_v6(
        state,
        source_sha256=point.state_sha256,
        observed_at=point.observed_at,
        received_at=point.received_at,
    )
    cfg = ctrl["read_session"]._settings
    observed_control = state["latest_coverage_observation"]
    authority_stamp = _source_stamp_v6(
        state,
        source_sha256=sha(observed_control.receipt_json),
        observed_at=observed_control.observed_at,
        received_at=observed_control.received_at,
    )
    authority = portfolio.DemoRiskAuthority(
        stamp=authority_stamp,
        environment="demo",
        demo_enabled=cfg.okx_demo_enabled,
        armed=ctrl["observation"].state.arm_intent_current(
            values.financial.evaluated_at
        )
        and ctrl["owner"].observed_arm_intent,
        order_writes_allowed=cfg.okx_demo_allow_order_writes,
        simulated_trading_header="1",
        emergency_stop=ctrl["observation"].state.emergency_stop,
        live_trading=cfg.live_trading,
        live_order_writes=cfg.okx_live_allow_order_writes,
        live_auto_execution=cfg.okx_live_auto_execution,
    )
    # These empty arrays are an explicit initial-purpose decision AFTER actual
    # complete exchange/current and all-currency local flatness, not defaults.
    snapshot = codec.FinitePortfolioSnapshotV1(
        account_id=scope.account_id,
        settlement_currency="USDT",
        balance_stamp=balance_stamp,
        positions_stamp=positions_stamp,
        history_stamp=history_stamp,
        reservations_stamp=reservations_stamp,
        equity=_decimal_exact_v6(anchor.account_equity_usdt),
        available_margin=_decimal_exact_v6(
            anchor.separately_observed_available_margin_usdt
        ),
        positions=(),
        pending_reservations=(),
        position_count=0,
        pending_reservation_count=0,
        history_start=values.financial.weekly_window_started_at,
        history_end=values.financial.source_as_of_at,
        original_start_seed=None,
    )
    claims = codec.FiniteAccountLedgerClaimsV1(
        payload_schema=codec.FINITE_PAYLOAD_SCHEMA_V1,
        scope=scope,
        account=snapshot,
        financial=values.financial,
        risk_policy=ctrl["configured_policy"].risk_policy,
        authority=authority,
        reconciliation_id=sha(
            canonical(
                {
                    "schema_version": "ctcc.controlled55.actual_current_portfolio_join.v6",
                    "source_reference": observed.reference_document(record.reference),
                    "local_checkpoint_sha256": point.state_sha256,
                    "control_event_sha256": ctrl["observation"].event_sha256,
                    "configured_policy_sha256": ctrl[
                        "configured_policy"
                    ].selected_settings_sha256,
                }
            )
        ),
    )
    raw = codec.encode_finite_account_ledger_claims_v1(claims)
    if (
        codec.encode_finite_account_ledger_claims_v1(
            codec.decode_finite_account_ledger_claims_v1(raw)
        )
        != raw
    ):
        _deny("controlled_account_canonical_claims_changed")
    return raw


@dataclass(frozen=True, slots=True, repr=False)
class _CounterOriginV6:
    configured_policy: object = field(repr=False)
    original_measurement: bytes = field(repr=False)


async def _read_counter_v6(state):
    ctrl = state["control"]
    scope = ctrl["ledger_scope"]
    snapshots = []
    rows = []
    metadata = []
    reference = _CounterOriginV6(
        ctrl["configured_policy"], ctrl["genesis"].original_measurement
    )
    for _ in range(2):
        _check_v6(state)
        async with ctrl["factory"]() as session, session.begin():
            for statement in (
                "SET LOCAL search_path TO pg_catalog,public,pg_temp",
                "SET LOCAL TIME ZONE 'UTC'",
                "SET LOCAL lock_timeout='1000ms'",
                "SET LOCAL statement_timeout='1000ms'",
            ):
                await session.execute(text(statement))
                _check_v6(state)
            await catalog55.verify_actual_control_catalog_v2(session)
            _check_v6(state)
            actual, event = await DemoControlRepositoryV2.read_in_transaction(
                session, ctrl["scope"]
            )
            _check_v6(state)
            if (
                actual != ctrl["observation"].state
                or event != ctrl["observation"].event_sha256
            ):
                _deny("controlled_account_counter_Control_changed")
            values = tuple(
                (
                    await session.scalars(
                        select(CounterRow)
                        .where(
                            CounterRow.environment == scope.environment,
                            CounterRow.account_id == scope.account_id,
                            CounterRow.settlement_currency == scope.settlement_currency,
                        )
                        .limit(2)
                    )
                ).all()
            )
            _check_v6(state)
            if len(values) > 1:
                _deny("controlled_account_counter_population_conflict")
            meta = await lineage55._metadata(session)
            _check_v6(state)
            metadata.append(meta)
            if values:
                ack = operator55._validate_counter_row(values[0], reference)
                snapshots.append(
                    operator55._row_raw(values[0], operator55._COUNTER_COLUMNS)
                )
                rows.append((ack, values[0].initialized_at, values[0].db_recorded_at))
            else:
                snapshots.append(None)
                rows.append(None)
    if (
        snapshots[0] != snapshots[1]
        or metadata[0][8] == metadata[1][8]
        or metadata[0][9] > metadata[1][9]
    ):
        _deny("controlled_account_counter_independent_readback_changed")
    if rows[1] is None:
        state["counter"] = None
        state["counter_native_receipt"] = canonical(
            {
                "schema_version": "ctcc.controlled55.counter.actual_absence.v6",
                "scope": [
                    scope.environment,
                    scope.account_id,
                    scope.settlement_currency,
                ],
                "first_transaction_id": metadata[0][8],
                "readback_transaction_id": metadata[1][8],
                "observed_at": metadata[0][9].isoformat(),
                "received_at": metadata[1][9].isoformat(),
                "source_authenticity_verified": False,
                "execution_authority": False,
            }
        )
        return
    ack, initialized_at, recorded_at = rows[1]
    # These are the original facts ACTUALLY persisted in the immutable ack.
    # No missing historical username/write-response timestamp is fabricated.
    original_metadata = (
        ("origin", "immutable_ack_columns"),
        ("db_role_oid", ack.db_role_oid),
        ("db_backend_pid", ack.db_backend_pid),
        ("db_transaction_id", ack.db_transaction_id),
        ("owner_sampled_at", ack.owner_sampled_at.isoformat()),
    )
    state["counter"] = counter.PolicyCounterInitializationReadbackV1(
        ack,
        initialized_at,
        recorded_at,
        snapshots[1],
        original_metadata,
        (("first_actual_read", metadata[0]), ("independent_actual_read", metadata[1])),
    )
    state["counter_native_receipt"] = canonical(
        {
            "schema_version": "ctcc.controlled55.counter.actual_retained_row.v6",
            "scope": [scope.environment, scope.account_id, scope.settlement_currency],
            "initialization_sha256": ack.initialization_sha256,
            "original_row_sha256": sha(snapshots[1]),
            "first_actual_read_transaction_id": metadata[0][8],
            "independent_actual_read_transaction_id": metadata[1][8],
            "source_authenticity_verified": False,
            "execution_authority": False,
        }
    )


def _native_financial_v6(state, *, at):
    # Prefer a genuinely complete observed positive-reset suffix. Otherwise the
    # immutable one-time operator epoch may establish a finite current bound.
    # The durable init row/epoch is never changed or reset by this selection.
    prior = state.get("counter")
    state["counter"] = None
    try:
        first = _financial_v6(state, at=at)
    finally:
        state["counter"] = prior
    if first.financial.current_loss_streak_upper_bound is not None:
        return first
    if prior is None:
        return first
    second = _financial_v6(state, at=at)
    extra = sum(
        map(
            len,
            (
                first.source_receipt_json,
                first.measured_hwm_receipt_json,
                first.original_window_receipt_json,
            ),
        )
    )
    charged = decode(
        second.original_window_receipt_json, bank.financial.MAX_COMPACT_BYTES
    )["derivation_retained_original_bytes_before_receipt"]
    if (
        charged
        + extra
        + sum(
            map(
                len,
                (
                    second.source_receipt_json,
                    second.measured_hwm_receipt_json,
                    second.original_window_receipt_json,
                ),
            )
        )
        > _MAX_RETAINED_V6
    ):
        _deny("controlled_account_temporary_financial_byte_bound")
    return second


async def _prepare_issuer_v6(state):
    state["phase"] = "PREPARING_ISSUER"
    await _coverage_v6(state)
    state[
        "lineage_readback"
    ] = await lineage55._record_held_current_revision_lineage_v2(
        state["current"], key=native._CONTROLLED_SOURCE_KEY_V1
    )
    _check_v6(state)
    await _read_counter_v6(state)
    await _coverage_v6(state)
    state["instrument_specs"] = _instrument_specs_v6(state)
    at = utc_from_ns(_check_v6(state)["utc_ns"])
    values = _native_financial_v6(state, at=at)
    state["financial_values"] = values
    state["claims_json"] = _claims_v6(state, values)
    state["hwm_raw"] = hwm.build_controlled_hwm_record_v2(
        values,
        state["current_record"],
        owner_sampled_at=utc_from_ns(_check_v6(state)["utc_ns"]),
    )
    state["hwm_proof_sha256"] = sha(
        canonical(
            {
                "schema_version": "ctcc.controlled55.measured_hwm.native_envelope.v2",
                "current_reference": observed.reference_document(
                    state["current_reference"]
                ),
                "current_proof_envelope_sha256": state[
                    "current_record"
                ].proof_envelope_sha256,
                "source_lineage_sha256": state["lineage_readback"].lineage_sha256,
                "hwm_bundle_sha256": sha(state["hwm_raw"]),
                "policy_sha256": hwm.POLICY_SHA256,
            }
        )
    )
    receipt = await custody.RuntimeBundleCustodyRepository.seal_bundle(
        state["control"]["custody_repository"],
        bundle_raw=state["hwm_raw"],
        expected_sha256=sha(state["hwm_raw"]),
        proof_envelope_sha256=state["hwm_proof_sha256"],
    )
    _check_v6(state)
    retained = custody._consume_runtime_bundle_custody_receipt(
        receipt,
        expected_bundle_sha256=sha(state["hwm_raw"]),
        expected_proof_envelope_sha256=state["hwm_proof_sha256"],
    )
    if retained.bundle_raw != state["hwm_raw"]:
        _deny("controlled_account_actual_HWM_custody_changed")
    state["hwm_retained"] = retained
    _check_v6(state)
    ctrl = state["control"]
    async with ctrl["factory"]() as session, session.begin():
        await catalog55.verify_actual_issuer_catalog_v2(session)
        _check_v6(state)
        actual, event = await DemoControlRepositoryV2.read_in_transaction(
            session, ctrl["scope"]
        )
        _check_v6(state)
        if (
            actual != ctrl["observation"].state
            or event != ctrl["observation"].event_sha256
        ):
            _deny("controlled_account_original_Control_changed")
        ledger = QualificationLedgerRepository(
            ctrl["factory"], clock=lambda: utc_from_ns(_check_v6(state)["utc_ns"])
        )
        scope_row = await ledger._locked(session, ctrl["ledger_scope"])
        _check_v6(state)
        row = await session.get(Lineage, state["lineage_readback"].lineage_sha256)
        _check_v6(state)
        lineage55._validate_row(
            row,
            scope=ctrl["ledger_scope"],
            reference=state["current_reference"],
            scope_row=scope_row,
            control_state=actual,
            control_event=event,
        )
        state["lineage_row_json"] = issuer55._lineage_snapshot_v1(row)
        state["base_revisions"] = (
            scope_row.account_revision,
            scope_row.ledger_revision,
            scope_row.claims_sha256,
        )
    _check_v6(state)
    state["proof_operands"] = _proof_operands_v6(state)
    if (
        sum(
            map(
                len,
                (
                    state["claims_json"],
                    state["lineage_row_json"],
                    state["hwm_raw"],
                    *(raw for _, raw in state["proof_operands"]),
                ),
            )
        )
        > issuer55._MAX_SOURCE_BYTES
    ):
        _deny("controlled_account_issuer_source_byte_bound")
    state["phase"] = "READY_ISSUER"
    source = object.__new__(_IssuerOwnerV6)
    _ISSUERS_V6[source] = state
    return await issuer55.issue_owned_account_revision_v1(source)


def _proof_operands_v6(state):
    record, values, _ctrl = (
        state["current_record"],
        state["financial_values"],
        state["control"],
    )
    bundle = decode(state["current"]["bundle_raw"], custody.MAX_BUNDLE_BYTES)
    point = state["latest_coverage_observation"].local_checkpoint
    receipt = decode(record.current_receipt_json, capture.MAX_PACKET_BYTES)
    fields = {
        "registration": canonical(bundle["route_proof"]),
        "current_identity": canonical(
            {
                "source_reference": observed.reference_document(record.reference),
                "identity": receipt["identity"],
            }
        ),
        "history_prefix": state["bank_result"].acquisition_receipt_json,
        "product_scope": canonical(
            {
                "current_plan": capture._json_value(record.packet.plan),
                "history_scope": {
                    "expected_uid": state["scope_config"].expected_uid,
                    "expected_main_uid": state["scope_config"].expected_main_uid,
                    "registration_evidence_sha256": state[
                        "scope_config"
                    ].registration_evidence_sha256,
                    "leverage_instrument_ids": list(
                        state["scope_config"].leverage_instrument_ids
                    ),
                },
            }
        ),
        "history_coverage": state[
            "population"
        ].original_raw_bank.financial_facts.receipt_json,
        "cashflow_and_funding_finality": values.original_window_receipt_json,
        "loss_streak_seed": canonical(
            {
                "original_start_seed": None,
                "financial": values.financial.model_dump(mode="json"),
                "counter_native_receipt_sha256": sha(state["counter_native_receipt"]),
            }
        ),
        "unchanged_window_drawdown": values.measured_hwm_receipt_json,
        "inventory_metadata": canonical(
            {
                "specifications": [
                    item.model_dump(mode="json") for item in state["instrument_specs"]
                ],
                "execution_mode_receipt_sha256": sha(
                    state["execution_mode"].receipt_json
                ),
            }
        ),
        "protection": canonical(
            {
                "initial_purpose": "actual_complete_flat_no_position_or_pending_protection_required",
                "source_reference": observed.reference_document(record.reference),
                "current_receipt_sha256": sha(record.current_receipt_json),
            }
        ),
        "local_ledger": point.document_json,
        "current_portfolio": codec.decode_finite_account_ledger_claims_v1(
            state["claims_json"]
        )
        .account.model_dump_json()
        .encode("utf-8"),
    }
    if set(fields) != set(codec.ISSUER_PROOF_KEYS) or any(
        type(raw) is not bytes or not raw for raw in fields.values()
    ):
        _deny("controlled_account_actual_issuer_proof_membership_changed")
    return tuple((name, fields[name]) for name in codec.ISSUER_PROOF_KEYS)


def acquire_owned_controlled_account_v6(
    owner, *, scope_config, frame_session_factory, roots
):
    """MOVE an actual Armed runtime owner before returning the acquisition.

    Caller parameters select only the already-reviewed scope and fresh private
    roots/native reader factory. No financial facts, clocks, claims, source
    packet, Arm boolean, owner token or policy approval is accepted.
    """
    _REQUIRE_SOURCE_KERNEL_V6()
    if (
        len(_STATES_V6) >= _MAX_SOURCES_V6
        or type(roots) is not ControlledAccountRootsV6
        or type(scope_config) is not history.NativeHistoryScopeV1
    ):
        _deny("controlled_account_original_inputs_required")
    roots.__post_init__()
    scope_config.__post_init__()
    ctrl = owner55._take_controlled_acquisition_v1(
        owner, key=native._CONTROLLED_SOURCE_KEY_V1
    )
    state = None
    try:
        import secrets

        original_plan = ctrl["plan"]
        selected = owner55._selected_record_v3(ctrl)
        if (
            selected is None
            or scope_config != selected.scope_config
            or frame_session_factory is not selected.raw_reader_factory
            or ctrl["factory"] is not selected.writer_factory
            or ctrl["custody_original_factory"] is not selected.custody_factory
            or scope_config.expected_uid != original_plan.expected_uid
            or scope_config.expected_main_uid != original_plan.expected_main_uid
            or scope_config.registration_evidence_sha256
            != original_plan.registration_evidence_sha256
            or scope_config.leverage_instrument_ids
            != original_plan.leverage_instrument_ids
            or configured.get_settings() is not ctrl["read_session"]._settings
        ):
            _deny("controlled_account_configured_original_scope_required")
        reader_original_pin, reader_factory, reader_pin = history._clone_factory(
            frame_session_factory
        )
        writer_route = native._database_connection_pin_v4(ctrl["factory"])
        reader_route = native._database_connection_pin_v4(reader_factory)
        if writer_route[1] != reader_route[1]:
            _deny("controlled_account_reader_database_route_changed")
        stamp = owner55._check_controlled_acquisition_v1(
            ctrl, key=native._CONTROLLED_SOURCE_KEY_V1
        )
        state = {
            "context": _context_v6(),
            "control": ctrl,
            "roots": roots,
            "scope_config": scope_config,
            "phase": "ACQUIRING",
            "generation": object(),
            "invocation_id": secrets.token_hex(32),
            "expires_at": ctrl["expires_at"],
            "monotonic_deadline_ns": ctrl["monotonic_deadline_ns"],
            "invocation_started": stamp,
            "inventory": native._package_inventory_controlled55_v1(),
            "settings_pin": native._v3_data_pin(
                ctrl["read_session"]._settings.__dict__
            ),
            "factories": (
                (ctrl["factory"], history._factory_pin(ctrl["factory"]), writer_route),
                (
                    frame_session_factory,
                    reader_original_pin,
                    native._database_connection_pin_v4(frame_session_factory),
                ),
                (reader_factory, reader_pin, reader_route),
                (
                    ctrl["custody_factory"],
                    history._factory_pin(ctrl["custody_factory"]),
                    native._database_connection_pin_v4(ctrl["custody_factory"]),
                ),
            ),
            "reader_factory": reader_factory,
            "current": None,
            "history": None,
            "last": stamp,
            "commit_binding": None,
            "issued_readback": None,
            "barrier": None,
            "original_state": None,
        }
        _STATES_V6[id(state)] = state
        _check_v6(state)
        return _AcquisitionAttemptV6(state, _acquire_v6(state))
    except BaseException:
        if state is not None:
            _retire_v6(state, uncertain=True)
        else:
            ctrl["owner"].revoke_now()
        raise


async def _acquire_v6(state):
    try:
        async with asyncio.timeout(_remaining_v6(state)) as budget:
            await _admit_writer_v6(state)
            await _reader_role_v6(state)
            await _current_v6(state)
            shorter = asyncio.get_running_loop().time() + _remaining_v6(state)
            if shorter < budget.when():
                budget.reschedule(shorter)
            binding = object.__new__(_CoverageBindingV6)
            _COVERAGE_V6[binding] = state
            state["coverage_binding"] = binding
            await _synchronize_mirror_v6(state)
            await _coverage_v6(state)
            await _history_v6(state)
            shorter = asyncio.get_running_loop().time() + _remaining_v6(state)
            if shorter < budget.when():
                budget.reschedule(shorter)
            await _coverage_v6(state)
            await _bank_v6(state)
            _check_v6(state)
            return await _prepare_issuer_v6(state)
    except BaseException as error:  # noqa: BLE001 -- retire unknown; preserve cancel/system exits and fixed redaction.
        _retire_v6(state, uncertain=True)
        if isinstance(error, asyncio.CancelledError):
            raise asyncio.CancelledError() from None
        if isinstance(error, (KeyboardInterrupt, SystemExit)):
            raise type(error)() from None
        _deny("controlled_account_source_or_issuance_unknown")


def _issuer_key_v6(key):
    if key is not issuer55._SOURCE_KEY:
        _deny("controlled_account_issuer55_fixed_purpose_required")


def _take_owned_account_issuer_v1(owner, *, key):
    _issuer_key_v6(key)
    state = _ISSUERS_V6.pop(owner, None) if type(owner) is _IssuerOwnerV6 else None
    if state is None:
        _deny("controlled_account_issuer_source_unavailable")
    state["phase"] = "ISSUING"
    try:
        _check_v6(state)
        return state
    except BaseException:
        _retire_v6(state, uncertain=True)
        raise


def _commit_binding_v6(state, transition):
    if transition is None:
        return
    if type(transition) is not issuer55._IssuerCommitBindingV1:
        _deny("controlled_account_exact_private_commit_required")
    previous = state["base_revisions"]
    if (
        (transition.previous_account_revision, transition.previous_ledger_revision)
        != previous[:2]
        or (transition.account_revision, transition.ledger_revision)
        != (previous[0] + 1, previous[1] + 1)
        or transition.claims_sha256 != sha(state["claims_json"])
        or type(transition.issuer_sha256) is not str
        or len(transition.issuer_sha256) != 64
        or any(c not in "0123456789abcdef" for c in transition.issuer_sha256)
        or type(transition.write_role_oid) is not int
        or transition.write_role_oid <= 0
        or type(transition.write_backend_pid) is not int
        or transition.write_backend_pid <= 0
        or type(transition.write_transaction_id) is not str
        or not transition.write_transaction_id.isascii()
        or not transition.write_transaction_id.isdigit()
    ):
        _deny("controlled_account_own_CAS_binding_changed")
    existing = state["commit_binding"]
    if existing is None:
        state["commit_binding"] = transition
        state["commit_pin"] = tuple(
            getattr(transition, item.name)
            for item in fields(issuer55._IssuerCommitBindingV1)
        )
    elif (
        transition is not existing
        or tuple(
            getattr(existing, item.name)
            for item in fields(issuer55._IssuerCommitBindingV1)
        )
        != state["commit_pin"]
    ):
        _deny("controlled_account_own_CAS_binding_replaced")


def _replay_owned_account_issuer_v1(state, *, key, transition=None):
    _issuer_key_v6(key)
    _check_v6(state)
    if state["phase"] != "ISSUING":
        _deny("controlled_account_issuer_source_already_retired")
    _commit_binding_v6(state, transition)
    if (
        state["commit_binding"] is not None
        and transition is not state["commit_binding"]
    ):
        _deny("controlled_account_own_CAS_allowance_required")
    retained = state["hwm_retained"]
    base = state["base_revisions"]
    return issuer55._IssuerSourceV1(
        session_factory=state["control"]["factory"],
        scope=state["control"]["ledger_scope"],
        control_state=state["control"]["observation"].state,
        control_event_sha256=state["control"]["observation"].event_sha256,
        previous_account_revision=base[0],
        previous_ledger_revision=base[1],
        previous_claims_sha256=base[2],
        expires_at=state["expires_at"],
        monotonic_deadline_ns=state["monotonic_deadline_ns"],
        claims_json=state["claims_json"],
        lineage_row_json=state["lineage_row_json"],
        hwm_custody_id=retained.bundle_id,
        hwm_bundle_json=state["hwm_raw"],
        hwm_db_recorded_at=retained.recorded_at,
        hwm_proof_envelope_sha256=state["hwm_proof_sha256"],
        proof_operands=state["proof_operands"],
    )


def _finish_owned_account_issuer_v1(state, readback, *, key):
    _issuer_key_v6(key)
    _check_v6(state)
    if (
        type(readback) is not issuer55._IssuedReadbackV1
        or readback.transition is not state["commit_binding"]
    ):
        _deny("controlled_account_actual_issued_readback_required")
    source = _replay_owned_account_issuer_v1(
        state, key=key, transition=readback.transition
    )
    issuer55._checked_source(source)
    if (
        readback.source != source
        or readback.claims_json != state["claims_json"]
        or readback.database_head not in {"0055", "0056"}
        or sha(readback.proof_json) != readback.transition.issuer_sha256
        or readback.write_metadata[3] != readback.readback_metadata[3]
        or readback.write_metadata[8] == readback.readback_metadata[8]
        or readback.write_metadata[9] > readback.readback_metadata[9]
        or readback.readback_observed_at >= state["expires_at"]
    ):
        _deny("controlled_account_issued_independent_readback_changed")
    document = decode(readback.row_json, issuer55._MAX_SOURCE_BYTES)
    expected = {
        "issuer_sha256": readback.transition.issuer_sha256,
        "environment": source.scope.environment,
        "account_id": source.scope.account_id,
        "settlement_currency": source.scope.settlement_currency,
        "previous_account_revision": source.previous_account_revision,
        "previous_ledger_revision": source.previous_ledger_revision,
        "previous_claims_sha256": source.previous_claims_sha256,
        "account_revision": readback.transition.account_revision,
        "ledger_revision": readback.transition.ledger_revision,
        "claims_sha256": sha(source.claims_json),
        "claims_json": source.claims_json.decode("utf-8"),
        "control_revision": source.control_state.revision,
        "control_event_sha256": source.control_event_sha256,
        "control_state_sha256": issuer55._control_sha(source.control_state),
        "source_lineage_sha256": decode(source.lineage_row_json)["lineage_sha256"],
        "hwm_custody_id": source.hwm_custody_id,
        "hwm_bundle_sha256": sha(source.hwm_bundle_json),
        "proof_json": readback.proof_json.decode("utf-8"),
        "original_expires_at": source.expires_at.isoformat(),
    }
    if set(document) != set(issuer55._ISSUED_COLUMNS) or any(
        document[name] != value for name, value in expected.items()
    ):
        _deny("controlled_account_issued_all_columns_changed")
    recorded = datetime.fromisoformat(document["db_recorded_at"])
    proof = decode(readback.proof_json, issuer55._MAX_PROOF_BYTES)
    sampled = datetime.fromisoformat(proof["owner_sampled_at"])
    if (
        canonical(document) != readback.row_json
        or not sampled <= recorded <= readback.readback_metadata[9] < source.expires_at
    ):
        _deny("controlled_account_issued_recorded_time_changed")
    state["issued_readback"] = readback
    state["issued_row_sha256"] = sha(readback.row_json)
    state["phase"] = "ISSUED"
    owner = object.__new__(OwnedIssuedAccountRevisionV1)
    _ISSUED_V6[owner] = state
    _check_v6(state)
    return owner


def _retain_unknown_owned_account_issuer_v1(state, *, key):
    _issuer_key_v6(key)
    _retire_v6(state, uncertain=True)


def _issued_purpose_v6(key, *, original=False, ledger=False, transport=False):
    from app.database.repositories import qualification_ledger as ledger_module
    from app.trade_qualification import (
        native_original_local_current55_v2 as original_module,
    )
    from app.trade_qualification import native_postpublication_recheck_v2 as r7
    from app.trade_qualification import owned_native_candidate_v1 as candidate

    if key is candidate._G11_KEY_V1:
        return "G11"
    if key is r7._ACCOUNT_KEY_V2:
        return "R7"
    if original and key is original_module._SOURCE_KEY_V2:
        return "ORIGINAL"
    if ledger and key is ledger_module._NATIVE_V2_KEY:
        return "LEDGER"
    if transport:
        from app.exchange.okx import native_demo_write_v2 as write

        if key is write._INTENT_KEY:
            return "TRANSPORT"
    _deny("controlled_account_fixed_issued_purpose_required")


def _issued_state_v6(
    binding, *, key, allow_owner=False, original=False, ledger=False, transport=False
):
    purpose = _issued_purpose_v6(
        key, original=original, ledger=ledger, transport=transport
    )
    state = None
    if allow_owner and type(binding) is OwnedIssuedAccountRevisionV1:
        state = _ISSUED_V6.get(binding)
    elif type(binding) is _HeldIssuedAccountV1:
        saved = _HELD_V6.get(binding)
        if type(saved) is tuple and len(saved) == 2:
            state, held_purpose = saved
            if purpose not in (held_purpose, "ORIGINAL", "LEDGER", "TRANSPORT"):
                _deny("controlled_account_issued_wrong_held_purpose")
    if state is None or state["phase"] != "ISSUED" or state["issued_readback"] is None:
        _deny("controlled_account_native_issued_owner_required")
    _check_v6(state)
    readback = state["issued_readback"]
    _commit_binding_v6(state, readback.transition)
    if (
        sha(readback.row_json) != state["issued_row_sha256"]
        or readback.claims_json != state["claims_json"]
    ):
        _deny("controlled_account_issued_original_readback_changed")
    return state


def _issued_view_v6(state):
    _check_v6(state)
    readback, ctrl = state["issued_readback"], state["control"]
    return native.IssuedAccountForG11V1(
        scope=ctrl["ledger_scope"],
        session_factory=ctrl["factory"],
        read_session=ctrl["read_session"],
        private_client=ctrl["private_client"],
        plan=state["current_plan"],
        expires_at=state["expires_at"],
        expiry_monotonic_ns=state["monotonic_deadline_ns"],
        policy_sha256=ctrl["configured_policy"].selected_settings_sha256,
        account_revision=readback.transition.account_revision,
        ledger_revision=readback.transition.ledger_revision,
        control_expectation=ctrl["expectation"],
        issued_row_sha256=state["issued_row_sha256"],
        issuer_sha256=readback.transition.issuer_sha256,
        claims=codec.decode_finite_account_ledger_claims_v1(state["claims_json"]),
        instrument_specs=state["instrument_specs"],
        execution_mode=state["execution_mode"],
        source_generation=state["generation"],
        database_head=readback.database_head,
    )


def _take_issued_v6(owner, *, key, purpose):
    if _issued_purpose_v6(key) != purpose:
        _deny("controlled_account_fixed_issued_move_required")
    state = (
        _ISSUED_V6.pop(owner, None)
        if type(owner) is OwnedIssuedAccountRevisionV1
        else None
    )
    if state is None:
        _deny("controlled_account_issued_owner_already_moved")
    try:
        _check_v6(state)
        if (purpose == "R7") != (state["barrier"] is not None):
            _deny("controlled_account_original_or_fresh_purpose_changed")
        binding = object.__new__(_HeldIssuedAccountV1)
        _HELD_V6[binding] = (state, purpose)
        return binding
    except BaseException:
        _retire_v6(state, uncertain=True)
        raise


def _take_issued_account_for_g11_v1(owner, *, key):
    return _take_issued_v6(owner, key=key, purpose="G11")


def _take_issued_account_for_r7_v2(owner, *, key):
    return _take_issued_v6(owner, key=key, purpose="R7")


def _replay_issued_account_for_g11_v1(binding, *, key):
    if _issued_purpose_v6(key) != "G11":
        _deny("controlled_account_G11_purpose_required")
    return _issued_view_v6(_issued_state_v6(binding, key=key))


def _replay_issued_account_for_r7_v2(binding, *, key):
    if _issued_purpose_v6(key) != "R7":
        _deny("controlled_account_R7_purpose_required")
    return _issued_view_v6(_issued_state_v6(binding, key=key))


def _release_issued_v6(binding, *, key, purpose):
    if _issued_purpose_v6(key) != purpose:
        _deny("controlled_account_fixed_release_purpose_required")
    saved = (
        _HELD_V6.pop(binding, None) if type(binding) is _HeldIssuedAccountV1 else None
    )
    if saved is None:
        return
    state, actual = saved
    if actual != purpose:
        _deny("controlled_account_fixed_release_owner_required")
    # A native close grants no trade permission and must still retire after
    # original expiry or after a failed source/callee check.
    _retire_v6(state, uncertain=False)


def _release_issued_account_for_g11_v1(binding, *, key):
    return _release_issued_v6(binding, key=key, purpose="G11")


def _release_issued_account_for_r7_v2(binding, *, key):
    return _release_issued_v6(binding, key=key, purpose="R7")


def _replay_issued_original_source_v2(binding, *, key):
    from app.trade_qualification import native_original_local_current55_v2 as original

    if key is not original._SOURCE_KEY_V2:
        _deny("controlled_account_original_source_purpose_required")
    state = _issued_state_v6(binding, key=key, allow_owner=True, original=True)
    if state["barrier"] is not None:
        _deny("controlled_account_original_source_cannot_use_fresh_R7_owner")
    ctrl = state["control"]
    return original.IssuedOriginalSourceV2(
        read_session=ctrl["read_session"],
        private_client=ctrl["private_client"],
        session_factory=ctrl["factory"],
        custody_session_factory=ctrl["custody_factory"],
        custody_repository=ctrl["custody_repository"],
        current_plan=state["current_plan"],
        current_record=state["current_record"],
        private_raw_bundle=state["current"]["bundle_raw"],
        control_expectation=ctrl["expectation"],
        configured_policy=ctrl["configured_policy"],
        source_generation=state["generation"],
        original_expires_at=state["expires_at"],
        monotonic_deadline_ns=state["monotonic_deadline_ns"],
        execution_mode=state["execution_mode"],
    )


def require_issued_original_frame_context_v1(binding, local_binding, *, key):
    from app.trade_qualification import native_original_local_current55_v2 as original

    if _issued_purpose_v6(key) != "G11":
        _deny("controlled_account_actual_original_frame_required")
    state = _issued_state_v6(binding, key=key)
    record, source = original._journal_context_v2(
        local_binding, key=original._SOURCE_KEY_V2
    )
    _check_v6(state)
    ctrl = state["control"]
    if (
        source.read_session is not ctrl["read_session"]
        or source.private_client is not ctrl["private_client"]
        or source.session_factory is not ctrl["factory"]
        or source.custody_session_factory is not ctrl["custody_factory"]
        or source.custody_repository is not ctrl["custody_repository"]
        or source.source_generation is not state["generation"]
        or record["source_generation"] is not state["generation"]
        or source.control_expectation != ctrl["expectation"]
        or record["control_expectation"] != ctrl["expectation"]
        or source.current_record != state["current_record"]
        or source.private_raw_bundle != state["current"]["bundle_raw"]
    ):
        _deny("controlled_account_original_frame_source_relation_changed")
    _check_v6(state)


def _pending_from_actual_ledger_v6(state, active):
    if type(active) is not tuple or len(active) > 2048:
        _deny("controlled_account_actual_ledger_holds_required")
    scope = state["control"]["ledger_scope"]
    specifications = {item.instrument_id: item for item in state["instrument_specs"]}
    merged = {}
    for receipt in active:
        if type(receipt) is not reservations.ReservationReceipt:
            _deny("controlled_account_exact_durable_reservation_required")
        receipt = reservations.ReservationReceipt.model_validate_json(
            receipt.model_dump_json(), strict=True
        )
        spec = specifications.get(receipt.instrument_id)
        if (
            receipt.scope != scope
            or spec is None
            or receipt.correlation_group != spec.correlation_group
            or receipt.state not in ("reserved", "consumed", "uncertain")
        ):
            _deny("controlled_account_actual_ledger_hold_unusable")
        value = portfolio.PendingReservation(
            reservation_id=receipt.reservation_id,
            instrument_id=receipt.instrument_id,
            direction=receipt.direction,
            settlement_currency=scope.settlement_currency,
            notional=receipt.coverage.notional_amount,
            margin=receipt.coverage.margin_amount,
            risk_amount=receipt.coverage.risk_amount,
            correlation_group=receipt.correlation_group,
        )
        previous = merged.setdefault(value.reservation_id, value)
        if previous != value:
            _deny("controlled_account_pending_reservation_conflict")
    return tuple(merged[name] for name in sorted(merged))


def evaluate_issued_g11_v1(
    binding,
    *,
    report_id,
    instrument_id,
    direction,
    entry,
    stop,
    cost,
    contracts,
    leverage,
    evaluated_at,
    key,
    active_reservations=(),
):
    purpose = _issued_purpose_v6(key)
    state = _issued_state_v6(binding, key=key)
    stamp = _check_v6(state)
    now = utc_from_ns(stamp["utc_ns"])
    if (
        type(evaluated_at) is not datetime
        or evaluated_at.tzinfo is not UTC
        or not state["current_record"].packet.completed_at <= evaluated_at <= now
        or now >= state["expires_at"]
    ):
        _deny("controlled_account_actual_G11_clock_required")
    if purpose == "G11" and active_reservations != ():
        _deny("controlled_account_original_G11_cannot_supply_new_holds")
    # Every later evaluation derives from the SAME authenticated retained
    # raw-bank facts at its actual clock. The issued DB row/claims/asof remain
    # historical immutable bytes; they are never relabelled as a fresh capture.
    values = _native_financial_v6(state, at=now)
    raw = _claims_v6(state, values)
    claims = codec.decode_finite_account_ledger_claims_v1(raw)
    risk_context = None
    if purpose == "R7":
        from app.database.repositories import qualification_ledger as ledger_module
        from app.trade_qualification import native_ledger_runtime_v2 as ledger_runtime

        risk_context = ledger_runtime.native_reservation_risk_context_v2(
            binding, key=ledger_module._NATIVE_V2_KEY
        )
        if risk_context is None:
            if active_reservations != ():
                _deny("controlled_account_unbound_ledger_active_tuple")
        elif risk_context.active is not active_reservations:
            _deny("controlled_account_actual_locked_active_tuple_changed")
    pending = _pending_from_actual_ledger_v6(state, active_reservations)
    original = {
        item.reservation_id: item for item in claims.account.pending_reservations
    }
    if risk_context is not None and risk_context.own_receipt is not None:
        own = risk_context.own_receipt
        if type(own) is not reservations.ReservationReceipt:
            _deny("controlled_account_native_own_receipt_changed")
        existing = original.get(own.reservation_id)
        if existing is not None and own.state == "consumed":
            exact = _pending_from_actual_ledger_v6(state, (own,))[0]
            if existing != exact:
                _deny("controlled_account_own_pending_coverage_conflict")
            original.pop(own.reservation_id)
    for item in pending:
        previous = original.setdefault(item.reservation_id, item)
        if previous != item:
            _deny("controlled_account_exchange_local_pending_conflict")
    claims = claims.model_copy(
        update={
            "account": claims.account.model_copy(
                update={
                    "pending_reservations": tuple(
                        original[name] for name in sorted(original)
                    ),
                    "pending_reservation_count": len(original),
                }
            )
        }
    )
    # Encoding and decoding revalidates the updated finite model; model_copy is
    # not used to skip Pydantic or exact ratio/window consistency checks.
    claims = codec.decode_finite_account_ledger_claims_v1(
        codec.encode_finite_account_ledger_claims_v1(claims)
    )
    choices = tuple(
        item
        for item in state["instrument_specs"]
        if item.instrument_id == instrument_id
    )
    if len(choices) != 1:
        _deny("controlled_account_selected_source_instrument_missing")
    execution_mode.select_captured_isolated_execution_v1(
        state["execution_mode"],
        instrument_id=instrument_id,
        direction=direction,
        requested_leverage=leverage,
    )
    result = portfolio.evaluate_finite_account_portfolio_v1(
        claims=claims,
        report_id=report_id,
        instrument_id=instrument_id,
        direction=direction,
        candidate_entry=entry,
        stop_loss=stop,
        round_trip_cost_per_base=cost,
        requested_contracts=contracts,
        requested_leverage=leverage,
        instrument=choices[0],
        current_time=now,
    )
    _check_v6(state)
    _retained_budget_v6(
        state,
        extra=(
            raw,
            values.source_receipt_json,
            values.measured_hwm_receipt_json,
            values.original_window_receipt_json,
        ),
    )
    return result


async def _require_locked_issued_v6(
    binding,
    *,
    session,
    scope_row,
    control_state,
    control_event_sha256,
    expected,
    key,
    original,
):
    state = _issued_state_v6(
        binding, key=key, allow_owner=original, original=original, ledger=not original
    )
    ctrl = state["control"]
    if (
        type(session) is not AsyncSession
        or session.bind is not ctrl["factory"].kw["bind"]
        or control_state != ctrl["observation"].state
        or control_event_sha256 != ctrl["observation"].event_sha256
        or (scope_row.account_revision, scope_row.ledger_revision) != expected
    ):
        _deny("controlled_account_actual_locked_issued_context_changed")
    readback = state["issued_readback"]
    claims = await issuer55.read_issued_revision_locked_v1(
        readback,
        session=session,
        scope_row=scope_row,
        control_state=control_state,
        control_event_sha256=control_event_sha256,
        expected_ledger_revision=expected[1],
    )
    _check_v6(state)
    if codec.encode_finite_account_ledger_claims_v1(claims) != state["claims_json"]:
        _deny("controlled_account_locked_all_column_claims_changed")
    return claims


async def require_issued_original_revision_locked_v2(
    binding, *, session, scope_row, control_state, control_event_sha256, key
):
    from app.trade_qualification import native_original_local_current55_v2 as original

    if key is not original._SOURCE_KEY_V2:
        _deny("controlled_account_original_locked_purpose_required")
    state = _issued_state_v6(binding, key=key, allow_owner=True, original=True)
    transition = state["issued_readback"].transition
    return await _require_locked_issued_v6(
        binding,
        session=session,
        scope_row=scope_row,
        control_state=control_state,
        control_event_sha256=control_event_sha256,
        expected=(transition.account_revision, transition.ledger_revision),
        key=key,
        original=True,
    )


async def require_issued_account_revision_locked_v1(
    binding, *, session, scope_row, control_state, control_event_sha256, key
):
    from app.database.repositories import qualification_ledger as ledger_module
    from app.trade_qualification import native_ledger_runtime_v2 as ledger_runtime

    if key is not ledger_module._NATIVE_V2_KEY:
        _deny("controlled_account_native_ledger_locked_purpose_required")
    state = _issued_state_v6(binding, key=key, ledger=True)
    transition = state["issued_readback"].transition
    expected = ledger_runtime.issued_revision_expectation_v2(binding, key=key)
    if (
        type(expected) is not tuple
        or len(expected) != 2
        or expected[0] != transition.account_revision
        or type(expected[1]) is not int
        or expected[1]
        not in (
            transition.ledger_revision,
            transition.ledger_revision + 1,
            transition.ledger_revision + 2,
        )
    ):
        _deny("controlled_account_exact_native_own_ledger_chain_required")
    return await _require_locked_issued_v6(
        binding,
        session=session,
        scope_row=scope_row,
        control_state=control_state,
        control_event_sha256=control_event_sha256,
        expected=expected,
        key=key,
        original=False,
    )


def _retained_budget_v6(state, *, extra=()):
    # Traverse only retained DATA roots, never a task, engine, source registry
    # or arbitrary object __dict__. Exact loaded mirror ORM buffers are read
    # separately through its fixed pure accounting helper below.
    roots = [
        state.get(name)
        for name in (
            "claims_json",
            "lineage_row_json",
            "hwm_raw",
            "counter_native_receipt",
            "current_record",
            "facts",
            "bank_result",
            "population",
            "position_source",
            "partition",
            "registration",
            "coverage_observation",
            "latest_coverage_observation",
            "mirror_observation",
            "counter",
            "proof_operands",
            "issued_readback",
            "financial_values",
        )
    ]
    current_state = state.get("current")
    if type(current_state) is dict:
        roots.extend(
            (
                current_state.get("bundle_raw"),
                current_state.get("private_secret_tokens"),
            )
        )
    roots.append(state.get("history_tokens"))
    entry_transport = state.get("transport_binding")
    if entry_transport is not None:
        from app.exchange.okx import native_demo_write_v2 as write

        original = (
            _TRANSPORT_V6.get(entry_transport)
            if type(entry_transport) is _DemoTransportBindingV2
            else None
        )
        if original is None or original["state"] is not state:
            _deny("controlled_account_actual_root_entry_buffers_required")
        root_buffers = write._native_source_retained_buffers_v1(
            entry_transport, key=_SCAN_KEY_V6
        )
        if type(root_buffers) is not tuple or any(
            type(value) not in (bytes, str) for value in root_buffers
        ):
            _deny("controlled_account_actual_root_buffer_types_changed")
        roots.append(root_buffers)
    controlled = state.get("control")
    if type(controlled) is dict and controlled.get("identity_record") is not None:
        roots.append(controlled["identity_record"].route_evidence)
    binding = state.get("coverage_binding")
    if binding is not None:
        retained_mirror = mirror55._retained_mirror_buffers_v1(
            binding, key=_SOURCE_KEY_V6
        )
        if type(retained_mirror) is not tuple or any(
            type(raw) not in (bytes, str) for raw in retained_mirror
        ):
            _deny("controlled_account_actual_mirror_retention_invalid")
        roots.append(retained_mirror)
    generation = state.get("generation")
    if generation is not None:
        from app.trade_qualification import (
            native_original_local_current55_v2 as original55,
        )

        family_buffers = original55._retained_original_family_buffers_v2(
            generation, key=_SOURCE_KEY_V6
        )
        if type(family_buffers) is not tuple or any(
            type(raw) not in (bytes, str) for raw in family_buffers
        ):
            _deny("controlled_account_actual_original_family_retention_invalid")
        roots.append(family_buffers)
    if type(extra) is not tuple or any(type(raw) is not bytes for raw in extra):
        _deny("controlled_account_retained_bytes_invalid")
    roots.append(extra)
    seen = set()
    total = 0
    visited = 0
    pending = [(value, 0) for value in roots]
    while pending:
        value, depth = pending.pop()
        if id(value) in seen:
            continue
        seen.add(id(value))
        visited += 1
        if depth > 64 or visited > 1000000:
            _deny("controlled_account_retained_data_capacity")
        if type(value) is bytes:
            total += len(value)
        elif type(value) is str:
            total += len(value.encode("utf-8"))
        elif type(value) in (tuple, list):
            pending.extend((item, depth + 1) for item in value)
        elif type(value) is dict:
            pending.extend((item, depth + 1) for item in value.values())
        elif isinstance(value, BaseModel):
            pending.extend((item, depth + 1) for item in value.__dict__.values())
        elif is_dataclass(value) and not isinstance(value, type):
            pending.extend(
                (getattr(value, item.name), depth + 1) for item in fields(value)
            )
        if total > _MAX_RETAINED_V6:
            _deny("controlled_account_retained_byte_capacity")


def _strictly_after_v6(barrier, stamp):
    validate_stamps((barrier, stamp))
    if any(stamp[name] <= barrier[name] for name in ("utc_ns", "monotonic_ns")):
        _deny("controlled_account_fresh_request_not_after_publication")


def _request_witnesses_after_v6(barrier, witnesses):
    if type(witnesses) is not list or not witnesses or len(witnesses) > 100000:
        _deny("controlled_account_fresh_request_witnesses_required")
    requests = 0
    for witness in witnesses:
        if type(witness) is not dict or type(witness.get("phase")) is not str:
            _deny("controlled_account_fresh_request_witness_invalid")
        if witness["phase"] in ("request_start", "request_dispatch"):
            _strictly_after_v6(barrier, witness["stamp"])
            requests += 1
    if requests == 0:
        _deny("controlled_account_no_fresh_request_witness")


def require_issued_postpublication_barrier_v2(binding, barrier, *, key):
    from app.trade_qualification import native_postpublication_support_v2 as measured

    if _issued_purpose_v6(key) != "R7":
        _deny("controlled_account_postpublication_purpose_required")
    state = _issued_state_v6(binding, key=key)
    if (
        type(barrier) is not dict
        or state["barrier"] is None
        or canonical(barrier) != state["barrier_json"]
    ):
        _deny("controlled_account_original_publication_barrier_changed")
    _strictly_after_v6(barrier, state["invocation_started"])
    document = decode(state["current"]["bundle_raw"], custody.MAX_BUNDLE_BYTES)
    _strictly_after_v6(barrier, document["route_proof"]["request_start"])
    current_proof = current.proof._canonical_document(
        current._unhex(document["native_proof_hex"], current.proof.MAX_PROOF)
    )
    _strictly_after_v6(barrier, current_proof["stage"]["started"])
    _request_witnesses_after_v6(barrier, current_proof["witnesses"])
    temporal = decode(state["facts"].bundle_raw, history_bundle.MAX_BUNDLE_BYTES)[
        "temporal"
    ]
    _strictly_after_v6(barrier, temporal["original_started"])
    _strictly_after_v6(barrier, temporal["stage"]["started"])
    _request_witnesses_after_v6(barrier, temporal["witnesses"])
    _request_witnesses_after_v6(barrier, temporal["positions_trace"]["events"])
    # Only this invocation's request witnesses and clock files are compared.
    # Historical transaction times, retained prefix/genesis and policy windows
    # remain old historical facts, never newly dispatched HTTP requests.
    for files in (document["clock_files"], temporal["clock_files_hex"]):
        for phase in ("before", "after"):
            measured._clock_after(barrier, bytes.fromhex(files[f"host-{phase}.json"]))
            trace = decode(bytes.fromhex(files[f"exchange-{phase}-trace.json"]))
            if type(trace) is not list:
                _deny("controlled_account_fresh_exchange_clock_trace_invalid")
            matched = [item for item in trace if item["kind"] == "request_start"]
            if len(matched) != 1:
                _deny("controlled_account_fresh_exchange_clock_request_missing")
            _strictly_after_v6(barrier, matched[0]["stamp"])
    for page in (
        *state["current_record"].packet.observations,
        *state["facts"].packet.observations,
    ):
        if page.request_started_at <= utc_from_ns(barrier["utc_ns"]):
            _deny("controlled_account_fresh_page_not_after_publication")
    _check_v6(state)


def acquire_postpublication_issued_account_v1(binding, *, barrier, roots, key):
    if _issued_purpose_v6(key) != "R7":
        _deny("controlled_account_fresh_acquisition_purpose_required")
    from app.trade_qualification import owned_native_candidate_v1 as candidate

    # The original G11 owner is historical, still live and source checked.
    original = _issued_state_v6(binding, key=candidate._G11_KEY_V1)
    if original["barrier"] is not None or original.get(
        "postpublication_started", False
    ):
        _deny("controlled_account_fresh_acquisition_already_started")
    if type(roots) is not native.PostpublicationAccountRootsV1:
        _deny("controlled_account_exact_fresh_roots_required")
    roots.__post_init__()
    root_values = tuple(
        getattr(roots, item.name)
        for item in fields(native.PostpublicationAccountRootsV1)
    )
    original_values = tuple(
        getattr(original["roots"], item.name)
        for item in fields(ControlledAccountRootsV6)
    )
    if any(
        a == b or a in b.parents or b in a.parents
        for a in root_values
        for b in original_values
    ):
        _deny("controlled_account_fresh_roots_overlap_original")
    stamp = _check_v6(original)
    _strictly_after_v6(barrier, stamp)
    if len(_STATES_V6) >= _MAX_SOURCES_V6:
        _deny("controlled_account_source_capacity")
    # Burn before returning the awaitable. No owner re-take, Arm or lease
    # renewal occurs; every NEW HTTP/clock/plan begins after this barrier.
    original["postpublication_started"] = True
    import secrets

    state = {
        "context": original["context"],
        "control": original["control"],
        "roots": ControlledAccountRootsV6(*root_values),
        "scope_config": original["scope_config"],
        "phase": "ACQUIRING",
        "generation": object(),
        "invocation_id": secrets.token_hex(32),
        "invocation_started": stamp,
        "expires_at": original["expires_at"],
        "monotonic_deadline_ns": original["monotonic_deadline_ns"],
        "inventory": original["inventory"],
        "settings_pin": original["settings_pin"],
        "factories": original["factories"],
        "reader_factory": original["reader_factory"],
        "current": None,
        "history": None,
        "last": stamp,
        "commit_binding": None,
        "issued_readback": None,
        "barrier": dict(barrier),
        "barrier_json": canonical(barrier),
        "original_state": original,
    }
    _STATES_V6[id(state)] = state
    try:
        _check_v6(state)
        return _AcquisitionAttemptV6(state, _acquire_fresh_v6(state))
    except BaseException:
        _retire_v6(state, uncertain=True)
        raise


async def _acquire_fresh_v6(state):
    owner = await _acquire_v6(state)
    try:
        from app.trade_qualification import native_postpublication_recheck_v2 as r7

        # Barrier replay requires a held fresh owner. The temporary registry
        # binding is private, removed before the original owner is returned.
        temporary = object.__new__(_HeldIssuedAccountV1)
        _HELD_V6[temporary] = (state, "R7")
        try:
            require_issued_postpublication_barrier_v2(
                temporary, state["barrier"], key=r7._ACCOUNT_KEY_V2
            )
        finally:
            _HELD_V6.pop(temporary, None)
        return owner
    except BaseException:
        _retire_v6(state, uncertain=True)
        raise


def _scan_tokens_v6(state):
    from app.demo_automation import native_server_selectors_v2 as selector
    from app.trade_qualification import native_original_local_current55_v2 as original
    from app.trade_qualification import native_postpublication_recheck_v2 as r7

    _check_v6(state)
    contexts = (
        (state,)
        if state["original_state"] is None
        else (state["original_state"], state)
    )
    values = []
    for held in contexts:
        _check_v6(held)
        tokens = held["current"]["private_secret_tokens"]
        if type(tokens) is not tuple or len(tokens) < 4:
            _deny("controlled_account_original_current_secret_custody_missing")
        values.extend(tokens)
        history_tokens = held["history_tokens"]
        if type(history_tokens) is not tuple or len(history_tokens) < 4:
            _deny("controlled_account_original_history_secret_custody_missing")
        values.extend(history_tokens)
    # The real new original public probe learns a later signature. It is read
    # only inside the native scanner through the fixed same-task registry key.
    source = contexts[0]
    values.extend(
        original._original_public_scan_tokens_v2(source["generation"], key=_SCAN_KEY_V6)
    )
    values.extend(
        selector._configured_demo_selector_scan_tokens_v2(
            source["control"]["identity_selection"], key=_SCAN_KEY_V6
        )
    )
    if state["original_state"] is not None:
        values.extend(
            r7._postpublication_public_scan_tokens_v2(
                state["generation"], key=_SCAN_KEY_V6
            )
        )
    if any(
        type(value) is not str or not value.isascii() or not value for value in values
    ):
        _deny("controlled_account_terminal_secret_tokens_invalid")
    return tuple(dict.fromkeys(values))


def scan_issued_original_public_v1(binding, directory, events, *, key):
    from app.trade_qualification import native_postpublication_support_v2 as measured

    state = _issued_state_v6(binding, key=key, allow_owner=True, original=True)
    measured._secret_public_journal(directory, events, _scan_tokens_v6(state))
    _check_v6(state)


def scan_issued_postpublication_public_v2(binding, directory, events, *, key):
    if _issued_purpose_v6(key) != "R7":
        _deny("controlled_account_fresh_public_scanner_purpose_required")
    return scan_issued_original_public_v1(binding, directory, events, key=key)


def scan_issued_metadata_v1(binding, raw, *, key):
    state = _issued_state_v6(binding, key=key, allow_owner=True, original=True)
    if type(raw) is not bytes or not 0 < len(raw) <= 8 * 1024 * 1024:
        _deny("controlled_account_bounded_metadata_required")
    current.runtime._secret_checked(raw, _scan_tokens_v6(state))
    _check_v6(state)


def scan_issued_artifact_v1(binding, filename, raw, *, key):
    from app.trade_evidence import storage

    state = _issued_state_v6(binding, key=key, allow_owner=True, original=True)
    if (
        type(filename) is not str
        or filename not in storage.FILE_NAMES
        or type(raw) is not bytes
    ):
        _deny("controlled_account_fixed_public_artifact_required")
    tokens = _scan_tokens_v6(state)
    if filename == "report.json":
        if not 0 < len(raw) <= storage.MAX_REPORT_BYTES:
            _deny("controlled_account_bounded_report_required")
        current.runtime._secret_checked(raw, tokens)
    else:
        storage._png_dimensions(raw)
        current.runtime.collector._no_secrets(raw, tokens)
    _check_v6(state)


def _transport_key_v6(key):
    from app.exchange.okx import native_demo_write_v2 as write

    if key is not write._INTENT_KEY:
        _deny("controlled_account_final_transport_purpose_required")
    return write


def bind_issued_demo_transport_v2(binding, *, key):
    from app.exchange.okx import private_rest

    write = _transport_key_v6(key)
    state = _issued_state_v6(binding, key=key, transport=True)
    if state["barrier"] is None or state.get("transport_binding") is not None:
        _deny("controlled_account_fresh_final_transport_once_required")
    settings = state["control"]["read_session"]._settings
    if settings is not configured.get_settings():
        _deny("controlled_account_actual_configured_transport_settings_required")
    # This is a distinct real client constructed from the ORIGINAL Settings
    # object. It is not the GET client/lease, an injected client, or a copied
    # Settings DTO. Only Root's final native intent registry can activate it.
    client = private_rest.OkxDemoPrivateRestClient(settings=settings)
    if (
        client is state["control"]["private_client"]
        or client.settings is not settings
        or client._external_client is not None
        or client._clock_injected is not False
        or state["control"]["read_session"]._origin != state["current_plan"].origin
    ):
        _deny("controlled_account_dedicated_native_transport_client_required")
    token = object.__new__(_DemoTransportBindingV2)
    record = {
        "state": state,
        "held": binding,
        "client": client,
        "key": key,
        "write_module": write,
        "context_type": write._DemoTransportContextV2,
        "inhibited": False,
        "reconciliation_tokens": _scan_tokens_v6(state),
        "client_pin": native._v3_data_pin(client.__dict__),
        "repository": state["control"]["repository"],
        "factory": state["control"]["factory"],
        "owner": state["control"]["owner"],
        "scope": state["control"]["scope"],
    }
    _TRANSPORT_V6[token] = record
    state["transport_binding"] = token
    _retained_budget_v6(
        state,
        extra=tuple(value.encode("ascii") for value in record["reconciliation_tokens"]),
    )
    _replay_issued_demo_transport_v2(token, key=key)
    return token


def _replay_issued_demo_transport_v2(binding, *, key):
    write = _transport_key_v6(key)
    record = (
        _TRANSPORT_V6.get(binding) if type(binding) is _DemoTransportBindingV2 else None
    )
    if (
        record is None
        or record["key"] is not key
        or record["write_module"] is not write
        or record["inhibited"]
    ):
        _deny("controlled_account_native_transport_unavailable")
    state = _issued_state_v6(record["held"], key=key, transport=True)
    if state is not record["state"]:
        _deny("controlled_account_native_transport_source_changed")
    client = record["client"]
    settings = state["control"]["read_session"]._settings
    if (
        client.settings is not settings
        or settings is not configured.get_settings()
        or native._v3_data_pin(client.__dict__) != record["client_pin"]
        or client._external_client is not None
        or client._clock_injected is not False
        or state["control"]["read_session"]._origin != state["current_plan"].origin
        or write._DemoTransportContextV2 is not record["context_type"]
    ):
        _deny("controlled_account_native_transport_configuration_changed")
    return record["context_type"](
        client=client,
        scope=state["control"]["ledger_scope"],
        session_factory=state["control"]["factory"],
        origin=state["current_plan"].origin,
        credential_session_sha256=state["control"]["pins"].credential_session_sha256,
        original_expires_at=state["expires_at"],
        monotonic_deadline_ns=state["monotonic_deadline_ns"],
        read_session=state["control"]["read_session"],
    )


def _inhibit_issued_demo_transport_v2(binding, *, key):
    # No healthy/young trading source is required for conservative stop. The
    # actual spent transport registry supplies its original owner/factory/UID.
    record = (
        _TRANSPORT_V6.get(binding) if type(binding) is _DemoTransportBindingV2 else None
    )
    if record is None or record["key"] is not key or record["inhibited"]:
        _deny("controlled_account_original_transport_stop_once_required")
    record["inhibited"] = True
    return _INHIBIT_TRANSPORT_V6(record)


def _make_transport_inhibitor_v6():
    revoke = owner55.original.DemoControlOwner._revoke
    tighten = owner55.original.DemoControlOwner._tighten
    owner_type = owner55.original.DemoControlOwner
    repository_type = DemoControlRepositoryV2
    owner_kernel = owner55._REQUIRE_KERNEL
    timeout = asyncio.timeout

    async def persist(record):
        try:
            # Independent integrity check, without source lease/Arm age.
            owner_kernel()
            async with timeout(5):
                result = await tighten(record["owner"], stop=True)
            if (
                result.state.scope != record["scope"]
                or result.state.emergency_stop is not True
                or result.state.arm_request_id is not None
            ):
                _deny("controlled_account_conservative_stop_readback_unknown")
            return result
        except asyncio.CancelledError:
            raise asyncio.CancelledError() from None
        except Exception:  # noqa: BLE001 -- retain unknown and expose only fixed code, never DB details.
            _deny("controlled_account_conservative_stop_persistence_unknown")

    def inhibit(record):
        state = record["state"]
        ctrl, owner = state["control"], record["owner"]
        if (
            type(owner) is not owner_type
            or owner is not ctrl["owner"]
            or type(record["repository"]) is not repository_type
            or owner.repository is not record["repository"]
            or record["factory"] is not ctrl["factory"]
            or record["scope"] != ctrl["scope"]
        ):
            _deny("controlled_account_original_transport_safety_owner_changed")
        revoke(owner, stop=True)
        _RETIRE_SOURCE_V6(state, uncertain=True)
        return persist(record)

    return inhibit


_INHIBIT_TRANSPORT_V6 = _make_transport_inhibitor_v6()


def _install_safety_entries_v6():
    from app.exchange.okx import native_demo_write_v2 as write
    from app.trade_qualification import native_original_local_current55_v2 as original
    from app.trade_qualification import native_postpublication_recheck_v2 as r7
    from app.trade_qualification import owned_native_candidate_v1 as candidate

    held, issued, states, transports = _HELD_V6, _ISSUED_V6, _STATES_V6, _TRANSPORT_V6
    held_type, owner_type, transport_type = (
        _HeldIssuedAccountV1,
        OwnedIssuedAccountRevisionV1,
        _DemoTransportBindingV2,
    )
    retire, inhibit = _RETIRE_SOURCE_V6, _INHIBIT_TRANSPORT_V6
    issuer_key, g11_key, r7_key, original_key, transport_key = (
        issuer55._SOURCE_KEY,
        candidate._G11_KEY_V1,
        r7._ACCOUNT_KEY_V2,
        original._SOURCE_KEY_V2,
        write._INTENT_KEY,
    )

    def unknown(state, *, key):
        if (
            key is not issuer_key
            or type(state) is not dict
            or states.get(id(state)) is not state
        ):
            _deny("controlled_account_original_unknown_source_required")
        retire(state, uncertain=True)

    def release_g11(binding, *, key):
        if key is not g11_key:
            _deny("controlled_account_fixed_release_purpose_required")
        saved = held.pop(binding, None) if type(binding) is held_type else None
        if saved is not None:
            if saved[1] != "G11":
                _deny("controlled_account_fixed_release_owner_required")
            retire(saved[0], uncertain=False)

    def release_r7(binding, *, key):
        if key is not r7_key:
            _deny("controlled_account_fixed_release_purpose_required")
        saved = held.pop(binding, None) if type(binding) is held_type else None
        if saved is not None:
            if saved[1] != "R7":
                _deny("controlled_account_fixed_release_owner_required")
            retire(saved[0], uncertain=False)

    def discard(owner, *, key):
        if key is not r7_key and key is not original_key:
            _deny("controlled_account_fixed_discard_purpose_required")
        state = issued.pop(owner, None) if type(owner) is owner_type else None
        if state is not None:
            retire(state, uncertain=False)

    def inhibit_transport(binding, *, key):
        if key is not transport_key:
            _deny("controlled_account_final_transport_purpose_required")
        record = transports.get(binding) if type(binding) is transport_type else None
        if record is None or record["key"] is not transport_key or record["inhibited"]:
            _deny("controlled_account_original_transport_stop_once_required")
        record["inhibited"] = True
        return inhibit(record)

    return unknown, release_g11, release_r7, discard, inhibit_transport


(
    _retain_unknown_owned_account_issuer_v1,
    _release_issued_account_for_g11_v1,
    _release_issued_account_for_r7_v2,
    discard_issued_account_owner_v1,
    _inhibit_issued_demo_transport_v2,
) = _install_safety_entries_v6()


# Independent DATA-only postsubmission/restart read contexts. A context owns
# a NEW client/credential-session identity and a single fresh 60-second read
# deadline. It never refreshes an old CURRENT, Arm, source or submit window.
_RECONCILIATION_KEY_V1 = object()
_RECONCILIATION_READ_V6 = {}
_RECONCILIATION_TRANSPORT_V6 = {}
_RECOVERY_SELECTION_USES_V6 = {}
_RECONCILIATION_READ_SECONDS_V6 = 60


def _reconciliation_key_v6(key):
    from app.trade_qualification import (
        native_postsubmit_reconciliation_v1 as postsubmit,
    )

    if key is not postsubmit._SOURCE_KEY_V1:
        _deny("controlled_account_reconciliation_fixed_read_purpose")
    return postsubmit


def _reconciliation_read_window_v6(
    started, last, current, utc_deadline_ns, mono_deadline_ns, *, enforce
):
    if (
        type(enforce) is not bool
        or type(utc_deadline_ns) is not int
        or type(mono_deadline_ns) is not int
        or any(
            type(value) is not dict
            or set(value) != {"utc_ns", "monotonic_ns"}
            or any(type(item) is not int or item <= 0 for item in value.values())
            for value in (started, last, current)
        )
        or utc_deadline_ns != started["utc_ns"] + 60_000_000_000
        or mono_deadline_ns != started["monotonic_ns"] + 60_000_000_000
    ):
        _deny("controlled_account_reconciliation_read_window_changed")
    if enforce:
        # Positive source/DB admission retains the original fixed lease check.
        validate_stamps((started, last, current))
    else:
        # Retained DATA/negative cleanup is not a new read lease. Validate the
        # same typed bounds, causal order and original 5ms clock relation only.
        values = tuple(
            clock_contract.ClockStamp.model_validate(value)
            for value in (started, last, current)
        )
        first = previous = values[0]
        for observed_stamp in values[1:]:
            if (
                observed_stamp.utc_ns < previous.utc_ns
                or observed_stamp.monotonic_ns < previous.monotonic_ns
            ):
                _deny("controlled_account_reconciliation_clock_reversed")
            if (
                abs(
                    (observed_stamp.utc_ns - first.utc_ns)
                    - (observed_stamp.monotonic_ns - first.monotonic_ns)
                )
                > clock_contract.MAX_CLOCK_DEVIATION_NS
            ):
                _deny("controlled_account_reconciliation_clock_jump")
            previous = observed_stamp
    if enforce and (
        current["utc_ns"] >= utc_deadline_ns
        or current["monotonic_ns"] >= mono_deadline_ns
    ):
        _deny("controlled_account_reconciliation_read_deadline")
    return current


def _reconciliation_selector_pin_v6(value):
    from app.demo_automation import native_server_selectors_v2 as selector

    if type(value) is not selector.ConfiguredDemoIdentityRecordV2:
        _deny("controlled_account_reconciliation_original_selector_record_required")
    evidence = value.route_evidence
    return (
        value.read_session,
        value.private_client,
        value.writer_factory,
        value.raw_reader_factory,
        value.custody_factory,
        capture.plan_sha256(value.current_plan),
        value.scope_config.expected_uid,
        value.scope_config.expected_main_uid,
        value.scope_config.registration_evidence_sha256,
        value.scope_config.leverage_instrument_ids,
        evidence.selector_json,
        evidence.selector_raw_body,
        evidence.route_artifact_json,
        evidence.route_artifact_sha256,
        evidence.configured_origin,
        evidence.binding_sha256,
        evidence.session_binding_id,
        evidence.registered_site,
        evidence.registered_site_proven,
    )


def _reconciliation_identity_v6(record, *, deadline):
    _REQUIRE_SOURCE_KERNEL_V6()
    if (
        type(record) is not dict
        or _RECONCILIATION_READ_V6.get(record["generation"]) is not record
        or type(deadline) is not bool
    ):
        _deny("controlled_account_reconciliation_source_unavailable")
    actual = native._context()
    expected = record["context"]
    if actual[:3] != expected[:3] or actual[3] is not expected[3]():
        _deny("controlled_account_reconciliation_original_task_changed")
    settings = record["settings"]
    if (
        configured.get_settings() is not settings
        or native._v3_data_pin(settings.__dict__) != record["settings_pin"]
        or record["client"].settings is not settings
        or type(record["client"]) is not record["client_type"]
        or record["client"]._external_client is not None
        or record["client"]._clock_injected is not False
        or record["client"]._clock is not record["client_clock"]
        or native._v3_callable_pin(record["client"]._clock)
        != record["client_clock_pin"]
        or canonical(record["scope"].model_dump(mode="json")) != record["scope_json"]
        or _reconciliation_selector_pin_v6(record["selector_record"])
        != record["selector_pin"]
        or record["scope"].account_id
        != record["selector_record"].scope_config.expected_uid
        or record["origin"]
        != record["selector_record"].route_evidence.configured_origin
    ):
        _deny("controlled_account_reconciliation_configured_source_changed")
    # No private client getter is called: its old/new read ContextVar may be
    # transitioning. Inspect actual original Settings and frozen credentials.
    credentials = (
        settings.okx_demo_api_key.get_secret_value(),
        settings.okx_demo_api_secret.get_secret_value(),
        settings.okx_demo_api_passphrase.get_secret_value(),
    )
    if (
        credentials != record["credentials"]
        or settings.okx_demo_rest_base_url.rstrip("/") != record["origin"]
        or settings.trading_mode != "okx_demo"
        or settings.live_trading is not False
    ):
        _deny("controlled_account_reconciliation_credentials_or_route_changed")
    for factory, pin, route in record["factories"]:
        if (
            history._factory_pin(factory) != pin
            or native._database_connection_pin_v4(factory) != route
        ):
            _deny("controlled_account_reconciliation_actual_factory_changed")
    from app.exchange.okx import private_rest

    active = private_rest._DEMO_READ_SESSION.get()
    if (
        type(active) is private_rest.DemoPinnedReadSession
        and active._owner is record["client"]
    ):
        if (
            type(active) is not private_rest.DemoPinnedReadSession
            or active._settings is not settings
            or active._credentials != record["credentials"]
            or active._origin != record["origin"]
        ):
            _deny("controlled_account_reconciliation_new_read_lease_changed")
        if record["read_lease"] is None:
            record["read_lease"] = active
            record["read_lease_binding_id"] = active.binding_id
        elif (
            active is not record["read_lease"]
            or active.binding_id != record["read_lease_binding_id"]
        ):
            _deny("controlled_account_reconciliation_read_lease_replaced")
        active.verify_current()  # Only the NEW lease, never the expired trade.
    elif active is not None and active is not record["previous_read_context"]:
        _deny("controlled_account_reconciliation_foreign_read_context")
    elif record["read_lease"] is not None:
        # After the one NEW GET lease closes, DB retention/readback may still
        # use this DATA source within its SAME read budget. It cannot open a
        # second lease or revive the original trade lease.
        if record["read_lease"]._lease.active is not False:
            _deny("controlled_account_reconciliation_read_lease_not_active")
    _reconciliation_budget_v6(record)
    stamp = _stamp_v6()
    _reconciliation_read_window_v6(
        record["original"],
        record["last"],
        stamp,
        record["original"]["utc_ns"] + _RECONCILIATION_READ_SECONDS_V6 * 1_000_000_000,
        record["read_deadline_ns"],
        enforce=deadline,
    )
    if record["read_expires_at"] != utc_from_ns(
        record["original"]["utc_ns"]
    ) + timedelta(seconds=60):
        _deny("controlled_account_reconciliation_read_expiry_changed")
    record["last"] = stamp
    return record


def _reconciliation_view_v6(record, postsubmit):
    if postsubmit.NativeReconciliationSourceContextV1 is not record["context_type"]:
        _deny("controlled_account_reconciliation_context_type_changed")
    return record["view"]


def _new_reconciliation_source_v6(
    *,
    authority,
    settings,
    credentials,
    scope,
    origin,
    writer_factory,
    raw_factory,
    control_owner,
    source_state,
    transport,
    selector_record,
    private_tokens,
    close_post_read_cap=None,
):
    from app.exchange.okx import private_rest
    from app.trade_qualification import (
        native_postsubmit_reconciliation_v1 as postsubmit,
    )

    _REQUIRE_SOURCE_KERNEL_V6()
    from app.demo_automation import native_server_selectors_v2 as selector
    from app.exchange.okx import native_demo_write_v2 as write

    if (
        source_state is None
        and type(authority) is not selector.ConfiguredDemoIdentitySelectionV2
    ):
        _deny("controlled_account_genuine_moved_recovery_selection_required")
    if close_post_read_cap is not None:
        if source_state is None or type(authority) is not _DemoCloseTransportBindingV1:
            _deny("controlled_account_genuine_moved_close_response_required")
    elif source_state is not None and type(authority) is not _DemoTransportBindingV2:
        _deny("controlled_account_genuine_submitted_transport_required")
    if (
        settings is not configured.get_settings()
        or type(credentials) is not tuple
        or len(credentials) != 3
        or any(type(value) is not str or not value for value in credentials)
        or type(scope) is not reservations.LedgerScope
        or scope.environment != "demo"
        or type(origin) is not str
        or type(private_tokens) is not tuple
        or any(
            type(value) is not str or not value or not value.isascii()
            for value in private_tokens
        )
    ):
        _deny("controlled_account_reconciliation_original_source_required")

    post_lineage = None
    if source_state is None:
        if (
            close_post_read_cap is not None
            or type(authority) is not selector.ConfiguredDemoIdentitySelectionV2
            or authority not in _RECOVERY_SELECTION_USES_V6
            or _RECOVERY_SELECTION_USES_V6[authority] is not None
        ):
            _deny("controlled_account_genuine_moved_recovery_selection_required")
        actual = selector._replay_configured_demo_identity_selection_v2(
            authority, key=owner55._SOURCE_KEY
        )
        if (
            actual is not selector_record
            or settings is not actual.read_session._settings
            or credentials != actual.read_session._credentials
            or scope.account_id != actual.scope_config.expected_uid
            or origin != actual.route_evidence.configured_origin
            or writer_factory is not actual.writer_factory
            or raw_factory is not actual.raw_reader_factory
            or control_owner is not None
            or transport is not None
        ):
            _deny("controlled_account_recovery_constructor_data_is_not_authority")
    elif close_post_read_cap is not None:
        if (
            close_post_read_cap not in _CLOSE_POST_READ_USES_V6
            or _CLOSE_POST_READ_USES_V6[close_post_read_cap] is not None
        ):
            _deny("controlled_account_genuine_moved_close_response_required")
        actual, first, prior, close_write = _close_post_lineage_v6(close_post_read_cap)
        if (
            close_write["token"] is not authority
            or transport is not prior["transport"]
            or source_state is not prior["source_state"]
            or settings is not prior["settings"]
            or credentials != prior["credentials"]
            or scope is not prior["scope"]
            or origin != prior["origin"]
            or writer_factory is not prior["writer_factory"]
            or raw_factory is not prior["raw_factory"]
            or control_owner is not prior["control_owner"]
            or selector_record is not prior["selector_record"]
            or private_tokens
            != _close_post_private_tokens_v6(first, prior, close_write)
        ):
            _deny("controlled_account_close_response_constructor_data_is_not_authority")
        post_lineage = actual, first, prior, close_write
    else:
        original = (
            _TRANSPORT_V6.get(authority)
            if type(authority) is _DemoTransportBindingV2
            else None
        )
        if (
            original is None
            or authority is not transport
            or original["state"] is not source_state
        ):
            _deny("controlled_account_genuine_submitted_transport_required")
        ctrl = source_state["control"]
        actual_post = write._native_postsubmit_scan_tokens_v1(
            transport, key=_SCAN_KEY_V6
        )
        if (
            settings is not ctrl["read_session"]._settings
            or credentials != ctrl["read_session"]._credentials
            or scope is not ctrl["ledger_scope"]
            or origin != source_state["current_plan"].origin
            or writer_factory is not ctrl["factory"]
            or raw_factory is not source_state["reader_factory"]
            or control_owner is not original["owner"]
            or selector_record is not ctrl["identity_record"]
            or private_tokens != (*original["reconciliation_tokens"], *actual_post)
        ):
            _deny("controlled_account_current_constructor_data_is_not_authority")
    if len(_RECONCILIATION_READ_V6) >= _MAX_SOURCES_V6:
        _deny("controlled_account_reconciliation_source_capacity")
    client = private_rest.OkxDemoPrivateRestClient(settings=settings)
    if (
        client._external_client is not None
        or client._clock_injected is not False
        or (
            post_lineage is not None
            and any(client is prior["client"] for prior in post_lineage[1:])
        )
    ):
        _deny("controlled_account_reconciliation_dedicated_client_required")
    stamp = _stamp_v6()
    generation = object()
    # This is a genuinely minted native read credential-session record, tied
    # to this new client/registry. The later actual SDK GET lease binding is
    # retained separately; neither is the old trading credential-session.
    session_id = native.secrets.token_hex(32)
    credential_document = canonical(
        {
            "schema_version": "ctcc.native_reconciliation_credential_session.v1",
            "session_id": session_id,
            "scope": scope.model_dump(mode="json"),
            "configured_origin": origin,
            "configured_credentials_sha256": sha(canonical(list(credentials))),
            "created": stamp,
        }
    )
    factories = tuple(
        (
            factory,
            history._factory_pin(factory),
            native._database_connection_pin_v4(factory),
        )
        for factory in (writer_factory, raw_factory)
    )
    record = {
        "generation": generation,
        "context": _context_v6(),
        "settings": settings,
        "settings_pin": native._v3_data_pin(settings.__dict__),
        "credentials": credentials,
        "scope": scope,
        "scope_json": canonical(scope.model_dump(mode="json")),
        "origin": origin,
        "factories": factories,
        "writer_factory": writer_factory,
        "raw_factory": raw_factory,
        "client": client,
        "client_type": private_rest.OkxDemoPrivateRestClient,
        "client_clock": client._clock,
        "client_clock_pin": native._v3_callable_pin(client._clock),
        "client_keys": frozenset(client.__dict__),
        "control_owner": control_owner,
        "source_state": source_state,
        "transport": transport,
        "selector_record": selector_record,
        "selector_pin": _reconciliation_selector_pin_v6(selector_record),
        "private_tokens": private_tokens,
        "credential_document": credential_document,
        "credential_session_sha256": sha(credential_document),
        "read_lease": None,
        "read_lease_binding_id": None,
        "previous_read_context": private_rest._DEMO_READ_SESSION.get(),
        "original": stamp,
        "last": stamp,
        "read_expires_at": utc_from_ns(stamp["utc_ns"])
        + timedelta(seconds=_RECONCILIATION_READ_SECONDS_V6),
        "read_deadline_ns": stamp["monotonic_ns"]
        + _RECONCILIATION_READ_SECONDS_V6 * 1_000_000_000,
        "inhibit_started": False,
        "inhibit_operation": None,
        "context_type": postsubmit.NativeReconciliationSourceContextV1,
    }
    if post_lineage is not None:
        actual, first, prior, close_write = post_lineage
        record.update(
            {
                "close_post_read_cap": close_post_read_cap,
                "close_post_read_view": actual,
                "close_preflight_read": prior,
                "close_post_write_record": close_write,
                "close_preflight_journals": tuple(
                    prior["client"]._native_reconciliation_read_journals_v1
                ),
            }
        )
    record["view"] = postsubmit.NativeReconciliationSourceContextV1(
        client=client,
        scope=scope,
        session_factory=writer_factory,
        origin=origin,
        credential_session_sha256=record["credential_session_sha256"],
        source_generation=generation,
        control_owner=control_owner,
    )
    # Retain before any subsequent check; failed/unknown acquisition remains
    # a bounded one-use tombstone, never an automatic retry.
    _RECONCILIATION_READ_V6[generation] = record
    if source_state is None:
        _RECOVERY_SELECTION_USES_V6[authority] = generation
    if close_post_read_cap is not None:
        _CLOSE_POST_READ_USES_V6[close_post_read_cap] = generation
    return _reconciliation_identity_v6(record, deadline=True)


def _replay_issued_demo_reconciliation_context_v2(transport_binding, *, key):
    from app.exchange.okx import native_demo_write_v2 as write

    _REQUIRE_SOURCE_KERNEL_V6()
    postsubmit = _reconciliation_key_v6(key)
    original = (
        _TRANSPORT_V6.get(transport_binding)
        if type(transport_binding) is _DemoTransportBindingV2
        else None
    )
    if (
        original is None
        or original["write_module"] is not write
        or original["key"] is not write._INTENT_KEY
    ):
        _deny("controlled_account_genuine_submitted_transport_required")
    # Root authenticates the actual submitted/spent/unlocked request and its
    # captured HMAC. This DATA purpose never invokes Root's healthy write gate.
    root_tokens = write._native_postsubmit_scan_tokens_v1(
        transport_binding, key=_SCAN_KEY_V6
    )
    if (
        type(root_tokens) is not tuple
        or len(root_tokens) != 1
        or type(root_tokens[0]) is not str
    ):
        _deny("controlled_account_actual_post_signature_required")
    if transport_binding not in _RECONCILIATION_TRANSPORT_V6:
        if len(_RECONCILIATION_TRANSPORT_V6) >= _MAX_SOURCES_V6:
            _deny("controlled_account_reconciliation_transport_capacity")
        # Burn before any mint/factory/check failure; UNKNOWN never remints.
        _RECONCILIATION_TRANSPORT_V6[transport_binding] = None
        state = original["state"]
        actual = native._context()
        context = state["context"]
        if actual[:3] != context[:3] or actual[3] is not context[3]():
            _deny("controlled_account_reconciliation_original_transport_task_required")
        ctrl = state["control"]
        if (
            configured.get_settings() is not ctrl["read_session"]._settings
            or native._v3_data_pin(ctrl["read_session"]._settings.__dict__)
            != state["settings_pin"]
        ):
            _deny("controlled_account_reconciliation_original_config_changed")
        for factory, pin, route in state["factories"]:
            if (
                history._factory_pin(factory) != pin
                or native._database_connection_pin_v4(factory) != route
            ):
                _deny("controlled_account_reconciliation_original_factory_changed")
        record = _new_reconciliation_source_v6(
            authority=transport_binding,
            settings=ctrl["read_session"]._settings,
            credentials=ctrl["read_session"]._credentials,
            scope=ctrl["ledger_scope"],
            origin=state["current_plan"].origin,
            writer_factory=ctrl["factory"],
            raw_factory=state["reader_factory"],
            control_owner=original["owner"],
            source_state=state,
            transport=transport_binding,
            selector_record=ctrl["identity_record"],
            private_tokens=(*original["reconciliation_tokens"], *root_tokens),
        )
        _RECONCILIATION_TRANSPORT_V6[transport_binding] = record["generation"]
    else:
        generation = _RECONCILIATION_TRANSPORT_V6[transport_binding]
        record = _RECONCILIATION_READ_V6.get(generation)
        if record is None or record["transport"] is not transport_binding:
            _deny("controlled_account_reconciliation_transport_generation_changed")
        _reconciliation_identity_v6(record, deadline=True)
    return _reconciliation_view_v6(record, postsubmit)


def _acquire_native_recovery_read_source_v1(selection, *, key):
    from app.demo_automation import native_server_selectors_v2 as selector

    _REQUIRE_SOURCE_KERNEL_V6()
    postsubmit = _reconciliation_key_v6(key)
    if type(selection) is not selector.ConfiguredDemoIdentitySelectionV2:
        _deny("controlled_account_genuine_fresh_recovery_selection_required")
    if selection in _RECOVERY_SELECTION_USES_V6:
        _deny("controlled_account_recovery_selection_already_moved")
    if (
        len(_RECOVERY_SELECTION_USES_V6) >= _MAX_SOURCES_V6
        or len(_RECONCILIATION_READ_V6) >= _MAX_SOURCES_V6
    ):
        _deny("controlled_account_recovery_selection_capacity")
    selected = selector._replay_configured_demo_identity_selection_v2(
        selection, key=owner55._SOURCE_KEY
    )
    if type(selected) is not selector.ConfiguredDemoIdentityRecordV2:
        _deny("controlled_account_genuine_fresh_recovery_selection_required")
    _RECOVERY_SELECTION_USES_V6[selection] = (
        None  # Burn synchronously, even on failure.
    )
    lease = selected.read_session
    lease.verify_current()
    scope = reservations.LedgerScope(
        environment="demo",
        account_id=selected.scope_config.expected_uid,
        settlement_currency="USDT",
    )
    tokens = selector._configured_demo_selector_scan_tokens_v2(
        selection, key=_SCAN_KEY_V6
    )
    record = _new_reconciliation_source_v6(
        authority=selection,
        settings=lease._settings,
        credentials=lease._credentials,
        scope=scope,
        origin=lease._origin,
        writer_factory=selected.writer_factory,
        raw_factory=selected.raw_reader_factory,
        control_owner=None,
        source_state=None,
        transport=None,
        selector_record=selected,
        private_tokens=(*lease._credentials, *tokens),
    )
    _RECOVERY_SELECTION_USES_V6[selection] = record["generation"]
    return _reconciliation_view_v6(record, postsubmit)


def _replay_native_recovery_read_source_v1(source_generation, *, key):
    postsubmit = _reconciliation_key_v6(key)
    record = _RECONCILIATION_READ_V6.get(source_generation)
    if (
        record is None
        or record["transport"] is not None
        or record["control_owner"] is not None
    ):
        _deny("controlled_account_genuine_recovery_read_source_required")
    # No positive selector replay here: its separate original GET context has
    # been replaced by this new DATA read context.
    _reconciliation_identity_v6(record, deadline=True)
    return _reconciliation_view_v6(record, postsubmit)


def _reconciliation_retained_buffers_v6(record):
    from app.exchange.okx import native_reconciliation_observation_v1 as read

    client = record["client"]
    allowed = record["client_keys"] | {"_native_reconciliation_read_journals_v1"}
    if set(client.__dict__) - allowed:
        _deny("controlled_account_reconciliation_client_namespace_changed")
    journals = getattr(client, "_native_reconciliation_read_journals_v1", None)
    extra = [record["credential_document"]]
    cleanup_receipt = record.get("flat_cleanup_receipt")
    if cleanup_receipt is not None:
        if type(cleanup_receipt) is not bytes:
            _deny("controlled_account_flat_cleanup_receipt_type_changed")
        extra.append(cleanup_receipt)
    extra.extend(value.encode("ascii") for value in record["private_tokens"])
    if journals is not None:
        if type(journals) is not list or len(journals) > read.MAX_REQUESTS:
            _deny("controlled_account_reconciliation_journal_bound")
        for journal in journals:
            if type(journal) is not read.ReadJournalV1:
                _deny("controlled_account_reconciliation_journal_type_changed")
            actual_httpx = read.retained_httpx_journal_data_v1(journal)
            if type(actual_httpx) is not tuple or any(
                type(value) not in (bytes, str) for value in actual_httpx
            ):
                _deny("controlled_account_actual_get_request_response_buffers_required")
            extra.extend(
                value if type(value) is bytes else value.encode("utf-8")
                for value in actual_httpx
            )
            if (
                record["read_lease_binding_id"] is not None
                and journal.read_session_id != record["read_lease_binding_id"]
            ):
                _deny("controlled_account_reconciliation_journal_lease_changed")
            if type(journal.chunks) is not list or any(
                type(raw) is not bytes for raw in journal.chunks
            ):
                _deny("controlled_account_reconciliation_chunk_type_changed")
            extra.extend(journal.chunks)
            for pair in journal.raw_headers:
                if (
                    type(pair) is not tuple
                    or len(pair) != 2
                    or any(type(raw) is not bytes for raw in pair)
                ):
                    _deny("controlled_account_reconciliation_header_type_changed")
                extra.extend(pair)
            if journal.signature is not None:
                if (
                    type(journal.signature) is not str
                    or not journal.signature.isascii()
                ):
                    _deny("controlled_account_reconciliation_signature_type_changed")
                extra.append(journal.signature.encode("ascii"))
            if journal.observation is not None:
                if (
                    type(journal.observation)
                    is not read.DemoReconciliationObservationV1
                ):
                    _deny("controlled_account_reconciliation_observation_type_changed")
                extra.append(journal.observation.raw_body)
    if record["source_state"] is None:
        from app.trade_qualification import (
            native_postsubmit_reconciliation_v1 as postsubmit,
        )

        historical = postsubmit._retained_native_recovery_history_v1(
            record["generation"], key=_RECONCILIATION_KEY_V1
        )
        if type(historical) is not tuple or any(
            type(raw) is not bytes for raw in historical
        ):
            _deny("controlled_account_recovery_history_buffer_type_changed")
        extra.extend(historical)
    return tuple(extra)


def _reconciliation_budget_v6(record):
    extra = list(_reconciliation_retained_buffers_v6(record))
    if record.get("close_post_read_cap") is not None:
        first, prior = _close_post_prior_reads_v6(record)
        extra.extend(_reconciliation_retained_buffers_v6(first))
        extra.extend(_reconciliation_retained_buffers_v6(prior))
        extra.extend(_close_write_retained_buffers_v6(prior))
    elif record.get("close_entry_cap") is not None:
        prior = _close_prior_read_v6(record)
        extra.extend(_reconciliation_retained_buffers_v6(prior))
    if record.get("close_write_token") is not None:
        extra.extend(_close_write_retained_buffers_v6(record))
    base = record["source_state"]
    if base is None:
        base = {"current_record": record["selector_record"].route_evidence}
    _retained_budget_v6(base, extra=tuple(extra))


def _scan_reconciliation_received_buffer_v6(raw, tokens):
    # Actual fragments/headers are raw DATA, not declared complete JSON.
    current.runtime.collector._no_secrets(raw, tokens)


def _scan_reconciliation_complete_document_v6(raw, tokens):
    current.runtime.collector._no_secrets(raw, tokens)
    try:
        current.runtime.collector._no_secret_json(raw, tokens)
    except (ValueError, UnicodeError, TypeError):
        _deny("controlled_account_reconciliation_incomplete_or_secret_json")


def scan_native_reconciliation_private_data_v1(source_generation, raw, *, key):
    from app.exchange.okx import native_reconciliation_observation_v1 as read

    _reconciliation_key_v6(key)
    record = _RECONCILIATION_READ_V6.get(source_generation)
    _reconciliation_identity_v6(record, deadline=False)
    if type(raw) is not bytes or not 0 <= len(raw) <= read.MAX_BODY:
        _deny("controlled_account_reconciliation_bounded_private_data_required")
    tokens = list(record["private_tokens"])
    records = (record,)
    if record.get("close_post_read_cap") is not None:
        records = (*_close_post_prior_reads_v6(record), record)
    elif record.get("close_entry_cap") is not None:
        records = (_close_prior_read_v6(record), record)
    journals = tuple(
        journal
        for owned in records
        for journal in getattr(
            owned["client"], "_native_reconciliation_read_journals_v1", ()
        )
    )
    transports = () if record["transport"] is None else (record["transport"],)
    close_write_record = None
    close_write_read = (
        record["close_preflight_read"]
        if record.get("close_post_read_cap") is not None
        else record
    )
    close_write_token = close_write_read.get("close_write_token")
    if close_write_token is not None:
        from app.exchange.okx import native_demo_write_v2 as close_write

        close_write_record = _close_write_read_record_v6(close_write_read)
        signature = close_write._native_postsubmit_scan_tokens_v1(
            close_write_token, key=_SCAN_KEY_V6
        )
        if type(signature) is not tuple or len(signature) not in (0, 1):
            _deny("controlled_account_actual_negative_post_signature_required")
        close_write_record["post_signatures"] = signature
        tokens.extend(signature)
        transports = (*transports, close_write_token)
    for journal in journals:
        if type(journal) is not read.ReadJournalV1:
            _deny("controlled_account_reconciliation_journal_type_changed")
        if journal.signature is not None:
            tokens.append(journal.signature)
    if any(
        type(value) is not str or not value or not value.isascii() for value in tokens
    ):
        _deny("controlled_account_reconciliation_actual_secret_tokens_required")
    tokens = tuple(dict.fromkeys(tokens))
    if record["source_state"] is None:
        from app.trade_qualification import (
            native_postsubmit_reconciliation_v1 as postsubmit,
        )

        # Later actual GET HMACs rescan earlier durable complete documents
        # and raw header bytes, rather than only the newest input.
        historical = postsubmit._retained_native_recovery_history_v1(
            record["generation"], key=_RECONCILIATION_KEY_V1
        )
        if type(historical) is not tuple or any(
            type(part) is not bytes for part in historical
        ):
            _deny("controlled_account_recovery_history_buffer_type_changed")
        for document in historical:
            if document.lstrip().startswith((b"{", b"[")):
                _scan_reconciliation_complete_document_v6(document, tokens)
            else:
                _scan_reconciliation_received_buffer_v6(document, tokens)
    for transport_token in transports:
        from app.exchange.okx import native_demo_write_v2 as write
        from app.trade_evidence import storage
        from app.trade_qualification import (
            native_postpublication_support_v2 as measured,
        )

        earlier = write._native_postsubmit_received_buffers_v1(
            transport_token, key=_SCAN_KEY_V6
        )
        if type(earlier) is not tuple or any(
            type(part) is not bytes for part in earlier
        ):
            _deny("controlled_account_actual_earlier_response_buffers_required")
        if close_write_record is not None and transport_token is close_write_token:
            close_write_record["received_buffers"] = earlier
            _reconciliation_budget_v6(record)
        for part in earlier:
            _scan_reconciliation_received_buffer_v6(part, tokens)
        complete_documents = write._native_postsubmit_complete_documents_v1(
            transport_token, key=_SCAN_KEY_V6
        )
        if type(complete_documents) is not tuple or any(
            type(part) is not bytes for part in complete_documents
        ):
            _deny("controlled_account_actual_complete_documents_required")
        if close_write_record is not None and transport_token is close_write_token:
            close_write_record["received_buffers"] = (*earlier, *complete_documents)
            _reconciliation_budget_v6(record)
        for document in complete_documents:
            _scan_reconciliation_complete_document_v6(document, tokens)
        public_journals, artifacts = write._native_postsubmit_r7_scannables_v1(
            transport_token, key=_SCAN_KEY_V6
        )
        if type(public_journals) is not tuple or len(public_journals) != 2:
            _deny("controlled_account_actual_original_and_fresh_journals_required")
        for pair in public_journals:
            if type(pair) is not tuple or len(pair) != 2:
                _deny("controlled_account_actual_public_journal_shape_changed")
            measured._secret_public_journal(pair[0], pair[1], tokens)
        if type(artifacts) is not tuple or len(artifacts) != len(storage.FILE_NAMES):
            _deny("controlled_account_actual_fixed_artifacts_required")
        names = set()
        retained_artifacts = []
        for pair in artifacts:
            if type(pair) is not tuple or len(pair) != 2:
                _deny("controlled_account_actual_artifact_shape_changed")
            name, artifact = pair
            if (
                name not in storage.FILE_NAMES
                or name in names
                or type(artifact) is not bytes
            ):
                _deny("controlled_account_actual_artifact_membership_changed")
            names.add(name)
            retained_artifacts.append(artifact)
            if close_write_record is not None and transport_token is close_write_token:
                close_write_record["received_buffers"] = (
                    *earlier,
                    *complete_documents,
                    *retained_artifacts,
                )
                _reconciliation_budget_v6(record)
            if name == "report.json":
                current.runtime._secret_checked(artifact, tokens)
            else:
                storage._png_dimensions(artifact)
                current.runtime.collector._no_secrets(artifact, tokens)
    # Scan received/partial private DATA before persistence, without assuming
    # JSON completion or granting its contents any source/account authority.
    if raw.lstrip().startswith((b"{", b"[")):
        _scan_reconciliation_complete_document_v6(raw, tokens)
    else:
        _scan_reconciliation_received_buffer_v6(raw, tokens)
    for journal in journals:
        for chunk in journal.chunks:
            current.runtime.collector._no_secrets(chunk, tokens)
        if journal.chunks:
            joined = b"".join(journal.chunks)
            _scan_reconciliation_received_buffer_v6(joined, tokens)
            if journal.eof_stamp is not None:
                _scan_reconciliation_complete_document_v6(joined, tokens)
    _reconciliation_budget_v6(record)


def _make_reconciliation_inhibitor_v6():
    from app.trade_qualification import (
        native_postsubmit_reconciliation_v1 as postsubmit,
    )

    records = _RECONCILIATION_READ_V6
    source_key = postsubmit._SOURCE_KEY_V1
    target_reader = postsubmit._replay_durable_target_for_stop_v1
    saved_guard = {}
    postsubmit.guard.save_native_guard_v1(saved_guard, postsubmit._NATIVE_GUARD)
    saved_check = postsubmit.guard.check_saved_native_guard_v1
    saved_check_code = saved_check.__code__
    target_key = _RECONCILIATION_KEY_V1
    revoke = owner55.original.DemoControlOwner._revoke
    original_inhibit = _INHIBIT_TRANSPORT_V6
    repository_type = DemoControlRepositoryV2
    latch = DemoControlRepositoryV2.latch_stop
    owner_kernel = owner55._REQUIRE_KERNEL
    clock = _stamp_v6
    context = native._context
    timeout = asyncio.timeout

    async def persist(record):
        try:
            owner_kernel()  # Integrity only, not old trading lease/Arm age.
            async with timeout(5):
                if (
                    history._factory_pin(record["writer_factory"])
                    != record["factories"][0][1]
                ):
                    _deny("controlled_account_reconciliation_stop_factory_changed")
                repository = repository_type(
                    record["writer_factory"],
                    clock=lambda: utc_from_ns(clock()["utc_ns"]),
                )
                result = await latch(
                    repository,
                    control.ControlScope(
                        record["scope"].environment, record["scope"].account_id
                    ),
                    command_id=native.secrets.token_hex(32),
                )
            if (
                result.state.scope.environment != record["scope"].environment
                or result.state.scope.account_id != record["scope"].account_id
                or result.state.emergency_stop is not True
                or result.state.arm_request_id is not None
            ):
                _deny("controlled_account_reconciliation_stop_readback_unknown")
            return result
        except asyncio.CancelledError:
            raise asyncio.CancelledError() from None
        except Exception:  # noqa: BLE001 -- fixed redaction, unknown never retries.
            _deny("controlled_account_reconciliation_stop_persistence_unknown")

    def inhibit(source_generation, *, key):
        if key is not source_key:
            _deny("controlled_account_reconciliation_stop_fixed_purpose")
        record = records.get(source_generation)
        if type(record) is not dict or record["inhibit_started"]:
            _deny("controlled_account_reconciliation_stop_once_required")
        actual = context()
        expected = record["context"]
        if actual[:3] != expected[:3] or actual[3] is not expected[3]():
            _deny("controlled_account_reconciliation_stop_original_task_required")
        record["inhibit_started"] = True
        if record["control_owner"] is not None:
            # Tighten locally NOW, before target read/DB waits/expiry checks.
            revoke(record["control_owner"], stop=True)
        if record["transport"] is not None:
            # The genuine Root transport already authenticated this original
            # UID/factory/owner before physical send. A failed NEW target read
            # cannot suppress its existing conservative persistent EStop.
            transport = _TRANSPORT_V6.get(record["transport"])
            if (
                transport is None
                or transport["owner"] is not record["control_owner"]
                or transport["factory"] is not record["writer_factory"]
                or transport["state"] is not record["source_state"]
                or transport["scope"].environment != record["scope"].environment
                or transport["scope"].account_id != record["scope"].account_id
            ):
                _deny("controlled_account_reconciliation_original_stop_owner_changed")
            operation = original_inhibit(transport)
        else:
            # A restart source has no old owner/intent capability. Its actual
            # durable target must be read under the genuine new scope first.
            if saved_check.__code__ is not saved_check_code:
                _deny("controlled_account_reconciliation_saved_guard_changed")
            saved_check(saved_guard)
            target = target_reader(source_generation, key=target_key)
            if (
                target.scope != record["scope"]
                or target.session_factory is not record["writer_factory"]
                or type(target.reservation_id) is not str
            ):
                _deny(
                    "controlled_account_reconciliation_actual_durable_target_required"
                )
            operation = persist(record)
        record["inhibit_operation"] = operation
        return operation

    return inhibit


_inhibit_native_reconciliation_read_source_v1 = _make_reconciliation_inhibitor_v6()


# Genuine current-invocation flat cleanup. These registries are separate from
# trading/read authority and remain strong until Root's actual finalizer.
_FLAT_CLEANUP_KEY_V1 = object()
_FLAT_CLEANUP_RECORDS_V6 = {}
_FLAT_CLEANUP_PREPARED_V6 = {}
_FLAT_CLEANUP_COMPLETED_V6 = {}


class _PreparedFlatCleanupV6(_OpaqueV6):
    __slots__ = ()


class _CompletedFlatCleanupV6(_OpaqueV6):
    __slots__ = ()


@dataclass(frozen=True, slots=True, repr=False)
class NativeFlatSourceCleanupCompletionViewV1:
    settlement_cap: object = field(repr=False)
    source_generation: object = field(repr=False)
    transport_binding: object = field(repr=False)
    flat_result_view: object = field(repr=False)
    closed_transport_handle: object = field(repr=False)
    disarm_observation: object = field(repr=False)
    new_get_observations: tuple = field(repr=False)
    receipt_json: bytes = field(repr=False)
    execution_authority: bool = field(default=False, init=False)
    order_retry_authority: bool = field(default=False, init=False)


def _flat_cleanup_key_v6(key):
    from app.trade_qualification import native_postsubmit_reconciliation_v1 as post

    if key is not post._SOURCE_SETTLEMENT_KEY_V1:
        _deny("controlled_account_flat_cleanup_fixed_purpose_required")
    return post


def _flat_original_close_contract_v6(state):
    # These are pure historical replays of ACTUAL native-owned source bytes.
    # Their original SDK/source_closed predicates remain unchanged. No absent
    # original socket is reported as a physically closed raw descriptor.
    if (
        type(state) is not dict
        or (
            _STATES_V6.get(id(state)) is not state
            and _UNKNOWN_V6.get(id(state)) is not state
        )
        or state.get("issued_readback") is None
        or type(state.get("current")) is not dict
        or type(state["current"].get("bundle_raw")) is not bytes
        or state.get("facts") is None
    ):
        _deny("controlled_account_flat_cleanup_original_source_required")
    raw = state["current"]["bundle_raw"]
    replay = current.replay_current_account_bundle_v4(
        raw, expected_sha256=state["current"]["bundle_sha256"]
    )
    historical = history.current_bundle.replay_current_history_source_bundle_v2(
        state["facts"].bundle_raw, expected_sha256=state["history_sha256"]
    )
    if (
        replay != state["current_record"]
        or historical.bundle_raw != state["facts"].bundle_raw
    ):
        _deny("controlled_account_flat_cleanup_original_close_changed")
    return (
        state["generation"],
        sha(raw),
        state["history_sha256"],
        sha(state["issued_readback"].row_json),
        state["current_plan_sha256"],
    )


def _closed_reconciliation_get_v6(item, lease_id):
    from app.exchange.okx import native_demo_settlement_v1 as settlement
    from app.exchange.okx import native_reconciliation_network_custody_v1 as network
    from app.exchange.okx import native_reconciliation_observation_v1 as read

    if (
        type(item) is not read.ReadJournalV1
        or item.read_session_id != lease_id
        or item.eof_stamp is None
        or item.observation is None
        or item.faults
        or item.transport_client is None
        or item.tcp_stream is None
        or item.tls_stream is None
        or item.tcp_transport is None
        or item.raw_socket is None
        or type(item.network_backend) is not network._RetainedBackend
        or item.network_backend.journal is not item
        or item.network_backend.connected is not True
        or item.transport_client._transport._pool._network_backend
        is not item.network_backend
        or type(item.raw_transport_proxy) is not network._RawTransport
        or item.raw_transport_proxy._journal is not item
        or item.raw_transport_proxy._native is not item.tcp_transport
        or type(item.cleanup_hold) is not dict
        or type(item.cleanup_hold.get("cleanup_unknown_sockets")) is not list
        or item.cleanup_hold["cleanup_unknown_sockets"]
        or item.cleanup_hold.get("cleanup_corrupt_socket") is not None
        or item.cleanup_abort is None
        or item.cleanup_abort.__code__ is not item.cleanup_abort_code
    ):
        _deny("controlled_account_flat_cleanup_actual_get_custody_changed")
    network._stream(item, item.tcp_stream)
    network._stream(item, item.tls_stream)
    return settlement.observe_native_closed_httpx_tcp_data_v1(
        item.transport_client,
        item.transport_client._transport,
        item.tcp_transport,
        item.raw_socket,
    )


def _flat_cleanup_new_gets_v6(read_record):
    journals = vars(read_record["client"]).get(
        "_native_reconciliation_read_journals_v1"
    )
    # Only the fixed nineteen genuine committed zero-FOK GETs are accepted here.
    if type(journals) is not list or len(journals) != 19:
        _deny("controlled_account_flat_cleanup_complete_get_owners_required")
    observations = []
    for item in journals:
        observed = _closed_reconciliation_get_v6(
            item, read_record["read_lease_binding_id"]
        )
        if observed is None:
            return None
        observations.append(observed)
    return tuple(observations)


def _flat_cleanup_check_v6(record, *, after_finalizer=False):
    _REQUIRE_SOURCE_KERNEL_V6()
    if (
        type(record) is not dict
        or _FLAT_CLEANUP_RECORDS_V6.get(record["token"]) is not record
        or record["phase"] not in ("PREPARING", "PREPARED", "COMPLETED")
    ):
        _deny("controlled_account_flat_cleanup_original_operation_required")
    actual = native._context()
    expected = record["context"]
    if actual[:3] != expected[:3] or actual[3] is not expected[3]():
        _deny("controlled_account_flat_cleanup_original_task_required")
    post = _flat_cleanup_key_v6(record["key"])
    replay = (
        post._replay_native_flat_source_after_finalization_v1
        if after_finalizer
        else post._replay_native_flat_source_settlement_v1
    )
    view = replay(record["settlement_cap"], key=_FLAT_CLEANUP_KEY_V1)
    if view is not record["settlement_view"]:
        _deny("controlled_account_flat_cleanup_settlement_replaced")
    read_record = _reconciliation_identity_v6(record["read_record"], deadline=False)
    transport = _TRANSPORT_V6.get(view.transport_binding)
    if (
        transport is not record["transport"]
        or transport["state"] is not read_record["source_state"]
        or read_record["transport"] is not view.transport_binding
        or _RECONCILIATION_TRANSPORT_V6.get(view.transport_binding)
        is not view.source_generation
        or read_record["generation"] is not view.source_generation
        or read_record["control_owner"] is not transport["owner"]
        or read_record["writer_factory"] is not transport["factory"]
        or transport["scope"] != record["scope"]
        or record["fresh"] is not transport["state"]
        or record["original"] is not record["fresh"]["original_state"]
        or record["original"] is record["fresh"]
        or record["original"]["barrier"] is not None
        or record["fresh"]["barrier"] is None
        or record["original"]["control"]["owner"] is not transport["owner"]
        or record["original"]["control"]["factory"] is not transport["factory"]
        or record["original"]["control"]["ledger_scope"] != read_record["scope"]
        or view.closed_transport_view.flat_result_view is not view.flat_result_view
        or view.closed_transport_view.closed_transport_handle
        is not record["closed_handle"]
    ):
        _deny("controlled_account_flat_cleanup_source_relation_changed")
    if not after_finalizer:
        contracts = tuple(
            _flat_original_close_contract_v6(state)
            for state in (record["original"], record["fresh"])
        )
        if contracts != record["close_contracts"]:
            _deny("controlled_account_flat_cleanup_original_contract_changed")
    return view


def _retain_flat_cleanup_unknown_v6(record, *, uncertain):
    if (
        type(uncertain) is not bool
        or _FLAT_CLEANUP_RECORDS_V6.get(record["token"]) is not record
    ):
        _deny("controlled_account_flat_cleanup_unknown_operation_required")
    record["phase"] = "UNKNOWN"


def prepare_native_flat_source_cleanup_v1(cap, *, key):
    from app.trade_qualification import native_postsubmit_reconciliation_v1 as post

    _REQUIRE_SOURCE_KERNEL_V6()
    _flat_cleanup_key_v6(key)
    if len(_FLAT_CLEANUP_RECORDS_V6) >= _MAX_SOURCES_V6:
        _deny("controlled_account_flat_cleanup_capacity")
    # The genuine R7 cap is spent synchronously before any returned awaitable.
    view = post._take_native_flat_source_settlement_v1(cap, key=_FLAT_CLEANUP_KEY_V1)
    if type(view) is not post.NativeFlatSourceSettlementForAccountV1:
        _deny("controlled_account_flat_cleanup_genuine_settlement_required")
    read_record = _RECONCILIATION_READ_V6.get(view.source_generation)
    transport = _TRANSPORT_V6.get(view.transport_binding)
    if (
        type(read_record) is not dict
        or type(transport) is not dict
        or read_record["transport"] is not view.transport_binding
        or read_record["source_state"] is not transport["state"]
        or read_record["control_owner"] is not transport["owner"]
    ):
        _deny("controlled_account_flat_cleanup_current_owned_source_required")
    owner = transport["owner"]
    if type(owner) is not owner55.original.DemoControlOwner:
        _deny("controlled_account_flat_cleanup_actual_control_owner_required")
    owner55.original.DemoControlOwner._revoke(owner, stop=False)
    fresh = transport["state"]
    original = fresh["original_state"]
    token = object()
    record = {
        "token": token,
        "context": _context_v6(),
        "key": key,
        "settlement_cap": cap,
        "settlement_view": view,
        "read_record": read_record,
        "transport": transport,
        "fresh": fresh,
        "original": original,
        "scope": transport["scope"],
        "closed_handle": view.closed_transport_view.closed_transport_handle,
        "phase": "PREPARING",
        "operation": None,
        "close_contracts": tuple(
            _flat_original_close_contract_v6(state) for state in (original, fresh)
        ),
        "disarm": None,
        "observations": None,
        "prepared": None,
        "completed": None,
    }
    _FLAT_CLEANUP_RECORDS_V6[token] = record
    try:
        _flat_cleanup_check_v6(record)
        operation = _AcquisitionAttemptV6(
            record,
            _prepare_flat_cleanup_body_v6(record),
            _retire=_retain_flat_cleanup_unknown_v6,
        )
        record["operation"] = operation
        return operation
    except BaseException:
        record["phase"] = "UNKNOWN"
        raise


async def _prepare_flat_cleanup_body_v6(record):
    try:
        started = _stamp_v6()
        last = started
        async with asyncio.timeout(5):
            _flat_cleanup_check_v6(record)
            owner = record["transport"]["owner"]
            observed = await owner55.original.DemoControlOwner._tighten(
                owner, stop=False
            )
            _flat_cleanup_check_v6(record)
            baseline = record["fresh"]["control"]["observation"].state
            if (
                type(observed.state) is not control.ControlState
                or observed.state.scope != record["scope"]
                or observed.state.owner_sha256 != baseline.owner_sha256
                or observed.state.owner_epoch != baseline.owner_epoch
                or observed.state.arm_request_id is not None
                or observed.state.arm_expires_at is not None
            ):
                _deny("controlled_account_flat_cleanup_disarm_readback_unknown")
            record["disarm"] = observed
            # Charge the actual retained receipt in the SAME combined source
            # budget, including all original/new read and family buffers.
            raw = canonical(
                {
                    "schema_version": "ctcc.native_flat_source_cleanup_disarm.v1",
                    "state": control.document(observed.state),
                    "event_sha256": observed.event_sha256,
                    "execution_authority": False,
                }
            )
            record["read_record"]["flat_cleanup_receipt"] = raw
            _reconciliation_budget_v6(record["read_record"])
            while True:
                _flat_cleanup_check_v6(record)
                stamp = _stamp_v6()
                validate_stamps((last, stamp))
                if any(
                    stamp[name] - started[name] >= 5_000_000_000
                    for name in ("utc_ns", "monotonic_ns")
                ):
                    _deny("controlled_account_flat_cleanup_budget_exhausted")
                last = stamp
                observations = _flat_cleanup_new_gets_v6(record["read_record"])
                if observations is not None:
                    record["observations"] = observations
                    prepared = object.__new__(_PreparedFlatCleanupV6)
                    record["prepared"] = prepared
                    record["phase"] = "PREPARED"
                    _FLAT_CLEANUP_PREPARED_V6[prepared] = record
                    return prepared
                await asyncio.sleep(0.01)
    except BaseException as error:  # noqa: BLE001 -- retain all owners; preserve cancellation/system exits and redact DB failures.
        record["phase"] = "UNKNOWN"
        if isinstance(error, asyncio.CancelledError):
            raise asyncio.CancelledError() from None
        if isinstance(error, (KeyboardInterrupt, SystemExit)):
            raise type(error)() from None
        _deny("controlled_account_flat_cleanup_unknown")


def finish_native_flat_source_cleanup_v1(prepared, *, key):
    _flat_cleanup_key_v6(key)
    record = (
        _FLAT_CLEANUP_PREPARED_V6.get(prepared)
        if type(prepared) is _PreparedFlatCleanupV6
        else None
    )
    if record is None or record["phase"] != "PREPARED":
        _deny("controlled_account_flat_cleanup_prepared_once_required")
    view = _flat_cleanup_check_v6(record)
    if (
        _flat_cleanup_new_gets_v6(record["read_record"]) is None
        or record["disarm"] is None
    ):
        _deny("controlled_account_flat_cleanup_not_confirmed")
    completed = object.__new__(_CompletedFlatCleanupV6)
    result = NativeFlatSourceCleanupCompletionViewV1(
        record["settlement_cap"],
        view.source_generation,
        view.transport_binding,
        view.flat_result_view,
        record["closed_handle"],
        record["disarm"],
        record["observations"],
        record["read_record"]["flat_cleanup_receipt"],
    )
    record["completed"], record["completion_view"] = completed, result
    record["phase"] = "COMPLETED"
    _FLAT_CLEANUP_COMPLETED_V6[completed] = record
    _FLAT_CLEANUP_PREPARED_V6.pop(prepared)
    return completed


def _replay_native_flat_source_cleanup_completion_v1(completed, *, key):
    _flat_cleanup_key_v6(key)
    record = (
        _FLAT_CLEANUP_COMPLETED_V6.get(completed)
        if type(completed) is _CompletedFlatCleanupV6
        else None
    )
    if (
        record is None
        or record["phase"] != "COMPLETED"
        or record["completed"] is not completed
    ):
        _deny("controlled_account_flat_cleanup_genuine_completion_required")
    _flat_cleanup_check_v6(record)
    if _flat_cleanup_new_gets_v6(record["read_record"]) is None:
        _deny("controlled_account_flat_cleanup_closed_owners_changed")
    return record["completion_view"]


def retire_native_flat_source_cleanup_v1(completed, *, key):
    _flat_cleanup_key_v6(key)
    record = (
        _FLAT_CLEANUP_COMPLETED_V6.get(completed)
        if type(completed) is _CompletedFlatCleanupV6
        else None
    )
    if (
        record is None
        or record["phase"] != "COMPLETED"
        or record["completed"] is not completed
    ):
        _deny("controlled_account_flat_cleanup_retirement_once_required")
    # This genuine R7 replay requires the actual Root finalizer to have removed
    # its exact closed/UNKNOWN/gate/client records; a returned DATA tuple cannot
    # satisfy this purpose.
    view = _flat_cleanup_check_v6(record, after_finalizer=True)
    if _flat_cleanup_new_gets_v6(record["read_record"]) is None:
        _deny("controlled_account_flat_cleanup_closed_owners_changed")
    # Spend this negative retirement phase before its first mutation. Any
    # partial failure remains strongly retained and is never retried as success.
    record["phase"] = "RETIRING"
    for state in (record["original"], record["fresh"]):
        _RETIRE_SOURCE_V6(state, uncertain=False)
        if any(
            value is state or (type(value) is tuple and value and value[0] is state)
            for mapping in (
                _CURRENT_V6,
                _COVERAGE_V6,
                _HISTORY_USES_V6,
                _ISSUERS_V6,
                _ISSUED_V6,
                _HELD_V6,
            )
            for value in mapping.values()
        ):
            _deny("controlled_account_flat_cleanup_source_still_active")
        if _STATES_V6.get(id(state)) is state:
            _deny("controlled_account_flat_cleanup_source_retirement_unknown")
        _UNKNOWN_V6.pop(id(state), None)
        ctrl = state["control"]
        if not any(value.get("control") is ctrl for value in _UNKNOWN_V6.values()):
            owner55._UNKNOWN.pop(id(ctrl), None)
    _TRANSPORT_V6.pop(view.transport_binding)
    _RECONCILIATION_READ_V6.pop(view.source_generation)
    _RECONCILIATION_TRANSPORT_V6.pop(view.transport_binding)
    record["phase"] = "RETIRED"
    _FLAT_CLEANUP_COMPLETED_V6.pop(completed)
    _FLAT_CLEANUP_RECORDS_V6.pop(record["token"])
    # Durable rows/events/claims/receipts and accepted artifacts are untouched.


# A later controlled-close read is a distinct one-use negative purpose. The
# first reconciliation generation/lease and its complete owners stay retained.
_CLOSE_READ_KEY_V1 = object()
_CLOSE_READ_USES_V6 = {}


def _close_prior_read_v6(record):
    from app.exchange.okx import private_rest
    from app.trade_qualification import native_controlled_close_v1 as close

    _REQUIRE_SOURCE_KERNEL_V6()
    cap = record.get("close_entry_cap")
    if (
        type(cap) is not close.OwnedNativeCloseEntryV1
        or _CLOSE_READ_USES_V6.get(cap) is not record["generation"]
        or _RECONCILIATION_READ_V6.get(record["generation"]) is not record
    ):
        _deny("controlled_account_genuine_close_read_generation_required")
    view = close._replay_native_close_entry_for_source_v1(cap, key=_CLOSE_READ_KEY_V1)
    prior = record["close_prior_read"]
    if (
        type(view) is not close.NativeCloseEntryForSourceV1
        or view is not record["close_entry_view"]
        or _RECONCILIATION_READ_V6.get(prior["generation"]) is not prior
        or _RECONCILIATION_TRANSPORT_V6.get(view.transport_binding)
        is not prior["generation"]
        or prior is record
        or prior.get("close_entry_cap") is not None
        or prior["transport"] is not record["transport"]
        or view.transport_binding is not record["transport"]
        or view.response_handle is not record["close_response_handle"]
        or view.filled_result_view.response_data.transport_facts.transport_binding
        is not record["transport"]
        or prior["source_state"] is not record["source_state"]
        or prior["settings"] is not record["settings"]
        or prior["scope"] is not record["scope"]
        or prior["control_owner"] is not record["control_owner"]
        or prior["writer_factory"] is not record["writer_factory"]
        or prior["raw_factory"] is not record["raw_factory"]
        or prior["credentials"] != record["credentials"]
        or prior["origin"] != record["origin"]
        or prior["selector_record"] is not record["selector_record"]
        or prior["client"] is record["client"]
    ):
        _deny("controlled_account_original_and_close_read_relation_changed")
    actual = native._context()
    expected = prior["context"]
    if actual[:3] != expected[:3] or actual[3] is not expected[3]():
        _deny("controlled_account_close_read_original_task_changed")
    old_client = prior["client"]
    old_lease = prior["read_lease"]
    if (
        type(old_client) is not private_rest.OkxDemoPrivateRestClient
        or old_client.settings is not record["settings"]
        or old_client._external_client is not None
        or old_client._clock_injected is not False
        or old_client._clock is not prior["client_clock"]
        or native._v3_callable_pin(old_client._clock) != prior["client_clock_pin"]
        or type(old_lease) is not private_rest.DemoPinnedReadSession
        or old_lease._owner is not old_client
        or old_lease._settings is not record["settings"]
        or old_lease._credentials != record["credentials"]
        or old_lease._origin != record["origin"]
        or old_lease.binding_id != prior["read_lease_binding_id"]
        or old_lease._lease.active is not False
    ):
        _deny("controlled_account_prior_read_must_remain_original_and_closed")
    old_journals = getattr(old_client, "_native_reconciliation_read_journals_v1", None)
    if (
        type(old_journals) is not list
        or not old_journals
        or len(old_journals) != len(record["close_prior_journals"])
        or any(
            actual is not expected
            for actual, expected in zip(
                old_journals, record["close_prior_journals"], strict=True
            )
        )
    ):
        _deny("controlled_account_prior_read_journal_owners_changed")
    for factory, pin, route in prior["factories"]:
        if (
            history._factory_pin(factory) != pin
            or native._database_connection_pin_v4(factory) != route
        ):
            _deny("controlled_account_prior_read_factory_changed")
    stamp = _stamp_v6()
    _reconciliation_read_window_v6(
        prior["original"],
        prior["last"],
        stamp,
        prior["original"]["utc_ns"] + _RECONCILIATION_READ_SECONDS_V6 * 1_000_000_000,
        prior["read_deadline_ns"],
        enforce=False,
    )
    prior["last"] = stamp
    return prior


def acquire_native_close_read_source_v1(cap, *, key):
    from app.exchange.okx import native_demo_write_v2 as write
    from app.trade_qualification import native_controlled_close_v1 as close
    from app.trade_qualification import (
        native_postsubmit_reconciliation_v1 as postsubmit,
    )

    _REQUIRE_SOURCE_KERNEL_V6()
    if key is not close._SOURCE_KEY_V1:
        _deny("controlled_account_close_read_fixed_purpose_required")
    if type(cap) is not close.OwnedNativeCloseEntryV1:
        _deny("controlled_account_genuine_close_entry_required")
    if cap in _CLOSE_READ_USES_V6 or len(_RECONCILIATION_READ_V6) >= _MAX_SOURCES_V6:
        _deny("controlled_account_close_read_once_or_capacity")
    view = close._take_native_close_entry_for_source_v1(cap, key=_CLOSE_READ_KEY_V1)
    if type(view) is not close.NativeCloseEntryForSourceV1:
        _deny("controlled_account_genuine_close_entry_required")
    transport = _TRANSPORT_V6.get(view.transport_binding)
    first_generation = _RECONCILIATION_TRANSPORT_V6.get(view.transport_binding)
    first = _RECONCILIATION_READ_V6.get(first_generation)
    if (
        transport is None
        or first is None
        or first["transport"] is not view.transport_binding
        or first["source_state"] is not transport["state"]
        or first.get("close_entry_cap") is not None
        or first["control_owner"] is not transport["owner"]
        or first["writer_factory"] is not transport["factory"]
        or view.filled_result_view.response_data.transport_facts.transport_binding
        is not view.transport_binding
        or view.filled_result_view.response_handle is not view.response_handle
    ):
        _deny("controlled_account_close_requires_original_submitted_read")
    # Historical integrity only; neither the spent entry nor first read is
    # made healthy. The NEW client below receives its own fixed read budget.
    _reconciliation_identity_v6(first, deadline=False)
    if first["read_lease"] is None or first["read_lease"]._lease.active is not False:
        _deny("controlled_account_first_read_not_closed")
    journals = getattr(first["client"], "_native_reconciliation_read_journals_v1", None)
    if type(journals) is not list or not journals:
        _deny("controlled_account_first_read_journals_required")
    state = transport["state"]
    ctrl = state["control"]
    root_tokens = write._native_postsubmit_scan_tokens_v1(
        view.transport_binding, key=_SCAN_KEY_V6
    )
    record = _new_reconciliation_source_v6(
        authority=view.transport_binding,
        settings=first["settings"],
        credentials=first["credentials"],
        scope=first["scope"],
        origin=first["origin"],
        writer_factory=first["writer_factory"],
        raw_factory=first["raw_factory"],
        control_owner=first["control_owner"],
        source_state=state,
        transport=view.transport_binding,
        selector_record=ctrl["identity_record"],
        private_tokens=(*transport["reconciliation_tokens"], *root_tokens),
    )
    record["close_entry_cap"] = cap
    record["close_entry_view"] = view
    record["close_response_handle"] = view.response_handle
    record["close_prior_read"] = first
    record["close_prior_journals"] = tuple(journals)
    _CLOSE_READ_USES_V6[cap] = record["generation"]
    _close_prior_read_v6(record)
    _reconciliation_identity_v6(record, deadline=True)
    return _reconciliation_view_v6(record, postsubmit)


def _replay_native_close_read_source_v1(source_generation, *, key):
    postsubmit = _reconciliation_key_v6(key)
    record = _RECONCILIATION_READ_V6.get(source_generation)
    if type(record) is not dict or record.get("close_entry_cap") is None:
        _deny("controlled_account_genuine_close_read_generation_required")
    _REQUIRE_SOURCE_KERNEL_V6()
    _close_prior_read_v6(record)
    _reconciliation_identity_v6(record, deadline=True)
    return _reconciliation_view_v6(record, postsubmit)


def _make_close_read_inhibitor_v6():
    from app.trade_qualification import (
        native_postsubmit_reconciliation_v1 as postsubmit,
    )

    source_key = postsubmit._SOURCE_KEY_V1
    records = _RECONCILIATION_READ_V6
    uses = _CLOSE_READ_USES_V6
    original_inhibit = _inhibit_native_reconciliation_read_source_v1

    def inhibit(source_generation, *, key):
        if key is not source_key:
            _deny("controlled_account_close_stop_fixed_purpose_required")
        record = records.get(source_generation)
        if (
            type(record) is not dict
            or record.get("close_entry_cap") is None
            or uses.get(record["close_entry_cap"]) is not source_generation
        ):
            _deny("controlled_account_close_stop_original_generation_required")
        # Captured original negative closure performs local revoke immediately,
        # then the one real bounded persistence operation. No positive read or
        # old Arm/lease freshness check precedes that safety tightening.
        return original_inhibit(source_generation, key=source_key)

    return inhibit


_inhibit_native_close_read_source_v1 = _make_close_read_inhibitor_v6()


# A committed controlled close gets one distinct negative WRITE client. The
# original entry/first-read/close-read owners and spent maps stay strong.
_CLOSE_TRANSPORT_KEY_V1 = object()
_CLOSE_TRANSPORT_V6 = {}
_CLOSE_TRANSPORT_USES_V6 = {}


class _DemoCloseTransportBindingV1(_OpaqueV6):
    __slots__ = ()


def _close_transport_key_v6(key):
    from app.exchange.okx import native_demo_write_v2 as write

    if type(key) is not object or vars(write).get("_CLOSE_INTENT_KEY_V1") is not key:
        _deny("controlled_account_negative_transport_fixed_purpose_required")
    return write


def _close_write_window_v6(
    budget, last, current_stamp, read_utc_deadline_ns, read_mono_deadline_ns
):
    keys = {
        "started_utc_ns",
        "started_monotonic_ns",
        "expires_utc_ns",
        "expires_monotonic_ns",
        "maximum_seconds",
        "entry_lease_renewed",
    }
    if (
        type(budget) is not dict
        or set(budget) != keys
        or type(budget["maximum_seconds"]) is not int
        or budget["maximum_seconds"] != 10
        or budget["entry_lease_renewed"] is not False
        or any(
            type(budget[name]) is not int or budget[name] <= 0
            for name in keys - {"maximum_seconds", "entry_lease_renewed"}
        )
        or type(read_utc_deadline_ns) is not int
        or type(read_mono_deadline_ns) is not int
        or not budget["started_utc_ns"]
        < budget["expires_utc_ns"]
        <= min(read_utc_deadline_ns, budget["started_utc_ns"] + 10_000_000_000)
        or not budget["started_monotonic_ns"]
        < budget["expires_monotonic_ns"]
        <= min(read_mono_deadline_ns, budget["started_monotonic_ns"] + 10_000_000_000)
    ):
        _deny("controlled_account_negative_transport_budget_changed")
    started = {
        "utc_ns": budget["started_utc_ns"],
        "monotonic_ns": budget["started_monotonic_ns"],
    }
    validate_stamps((started, last, current_stamp))
    if (
        current_stamp["utc_ns"] >= budget["expires_utc_ns"]
        or current_stamp["monotonic_ns"] >= budget["expires_monotonic_ns"]
    ):
        _deny("controlled_account_negative_transport_deadline")
    return current_stamp


def _close_write_read_record_v6(read_record):
    token = read_record.get("close_write_token")
    record = (
        _CLOSE_TRANSPORT_V6.get(token)
        if type(token) is _DemoCloseTransportBindingV1
        else None
    )
    if (
        type(record) is not dict
        or record["token"] is not token
        or record["read_record"] is not read_record
        or record["read_generation"] is not read_record["generation"]
        or _RECONCILIATION_READ_V6.get(record["read_generation"]) is not read_record
        or _CLOSE_TRANSPORT_USES_V6.get(id(record["intent_state"])) is not token
    ):
        _deny("controlled_account_negative_transport_owned_read_required")
    return record


def _close_write_buffer_bytes_v6(roots, *, preserve_text=False):
    # Read only retained DATA roots. Never serialize a row again or traverse
    # an SDK client, source registry, task, engine or arbitrary __dict__.
    if type(roots) is not tuple or type(preserve_text) is not bool:
        _deny("controlled_account_negative_transport_buffer_roots_invalid")
    pending, seen, values = [(value, 0) for value in roots], set(), []
    total = 0
    while pending:
        value, depth = pending.pop()
        if id(value) in seen:
            continue
        seen.add(id(value))
        if depth > 64 or len(seen) > 100000:
            _deny("controlled_account_negative_transport_buffer_capacity")
        if type(value) is bytes:
            total += len(value)
            values.append(value)
        elif type(value) is str:
            raw = value.encode("utf-8")
            total += len(raw)
            values.append(value if preserve_text else raw)
        elif type(value) in (tuple, list):
            pending.extend((item, depth + 1) for item in value)
        elif type(value) is dict:
            pending.extend((item, depth + 1) for item in value.values())
        elif isinstance(value, BaseModel):
            pending.extend((item, depth + 1) for item in value.__dict__.values())
        elif is_dataclass(value) and not isinstance(value, type):
            pending.extend(
                (getattr(value, item.name), depth + 1) for item in fields(value)
            )
        if total > _MAX_RETAINED_V6:
            _deny("controlled_account_negative_transport_buffer_capacity")
    return tuple(values)


def _close_write_retained_buffers_v6(read_record):
    from app.exchange.okx import native_demo_write_v2 as write

    record = _close_write_read_record_v6(read_record)
    live_root = write._native_source_retained_buffers_v1(
        record["token"], key=_SCAN_KEY_V6
    )
    if type(live_root) is not tuple or any(
        type(value) not in (bytes, str) for value in live_root
    ):
        _deny("controlled_account_actual_negative_root_buffers_required")
    signatures = record["post_signatures"]
    received = record["received_buffers"]
    if (
        type(signatures) is not tuple
        or any(
            type(value) is not str or not value or not value.isascii()
            for value in signatures
        )
        or type(received) is not tuple
        or any(type(value) is not bytes for value in received)
    ):
        _deny("controlled_account_negative_transport_buffer_type_changed")
    return _close_write_buffer_bytes_v6(
        (
            record["credential_document"],
            record["budget_json"],
            record["intent_readback"],
            record["intent_view"].target,
            record["intent_view"].filled_result_view.readback,
            live_root,
            received,
            signatures,
        )
    )


def _close_transport_identity_v6(record, *, key):
    from app.exchange.okx import private_rest
    from app.trade_qualification import native_controlled_close_v1 as close

    _REQUIRE_SOURCE_KERNEL_V6()
    write = _close_transport_key_v6(key)
    if (
        type(record) is not dict
        or _CLOSE_TRANSPORT_V6.get(record["token"]) is not record
        or record["key"] is not key
        or record["write_module"] is not write
        or record["inhibited"]
        or _CLOSE_TRANSPORT_USES_V6.get(id(record["intent_state"]))
        is not record["token"]
    ):
        _deny("controlled_account_negative_transport_unavailable")
    actual, expected = native._context(), record["context"]
    if actual[:3] != expected[:3] or actual[3] is not expected[3]():
        _deny("controlled_account_negative_transport_original_task_required")
    view = close._replay_native_close_source_for_transport_v1(
        record["intent_state"], key=_CLOSE_TRANSPORT_KEY_V1
    )
    read_record = record["read_record"]
    if (
        type(view) is not close.NativeCloseSourceForTransportV1
        or view.entry_cap is not read_record["close_entry_cap"]
        or view.filled_result_view
        is not read_record["close_entry_view"].filled_result_view
        or view.read_source_generation is not record["read_generation"]
        or view.scope is not read_record["scope"]
        or view.session_factory is not read_record["writer_factory"]
        or view.close_intent_view is not record["intent_view"]
        or view.close_intent_view.source_context is not read_record["view"]
        or view.close_intent_view.intent_readback is not record["intent_readback"]
        or view.expires_at != record["view"].negative_expires_at
        or view.expiry_monotonic_ns != record["view"].monotonic_deadline_ns
        or write._DemoCloseTransportContextV1 is not record["context_type"]
    ):
        _deny("controlled_account_negative_transport_intent_relation_changed")
    adopted = record["intent_state"].get("transport_binding")
    if adopted is not record["token"] and not (
        adopted is None and record["phase"] == "BINDING"
    ):
        _deny("controlled_account_negative_transport_adoption_changed")
    _reconciliation_identity_v6(read_record, deadline=True)
    _close_prior_read_v6(read_record)
    original = _TRANSPORT_V6.get(read_record["transport"])
    client, settings = record["client"], read_record["settings"]
    if (
        original is not record["original_transport"]
        or original["state"] is not read_record["source_state"]
        or original["factory"] is not read_record["writer_factory"]
        or original["owner"] is not read_record["control_owner"]
        or record["view"].read_session
        is not original["state"]["control"]["read_session"]
        or type(client) is not private_rest.OkxDemoPrivateRestClient
        or client is read_record["client"]
        or client is original["client"]
        or client.settings is not settings
        or configured.get_settings() is not settings
        or client._external_client is not None
        or client._clock_injected is not False
        or native._v3_data_pin(client.__dict__) != record["client_pin"]
        or record["intent_readback"][4] != decode(record["budget_json"])
    ):
        _deny("controlled_account_negative_transport_actual_client_changed")
    # The previous GET lease is only an identity anchor, never renewed here.
    lease = read_record["read_lease"]
    if (
        type(lease) is not private_rest.DemoPinnedReadSession
        or lease._owner is not read_record["client"]
        or lease._lease.active is not False
    ):
        _deny("controlled_account_negative_transport_close_read_must_be_closed")
    stamp = _stamp_v6()
    _close_write_window_v6(
        record["intent_readback"][4],
        record["last"],
        stamp,
        read_record["original"]["utc_ns"] + 60_000_000_000,
        read_record["read_deadline_ns"],
    )
    record["last"] = stamp
    _reconciliation_budget_v6(read_record)
    return record["view"]


def bind_native_close_transport_v1(intent_state, *, key):
    from app.exchange.okx import private_rest
    from app.trade_qualification import native_controlled_close_v1 as close

    _REQUIRE_SOURCE_KERNEL_V6()
    write = _close_transport_key_v6(key)
    view = close._replay_native_close_source_for_transport_v1(
        intent_state, key=_CLOSE_TRANSPORT_KEY_V1
    )
    if type(view) is not close.NativeCloseSourceForTransportV1:
        _deny("controlled_account_genuine_negative_transport_intent_required")
    read_record = _RECONCILIATION_READ_V6.get(view.read_source_generation)
    _reconciliation_identity_v6(read_record, deadline=True)
    _close_prior_read_v6(read_record)
    if (
        id(intent_state) in _CLOSE_TRANSPORT_USES_V6
        or read_record.get("close_write_token") is not None
        or len(_CLOSE_TRANSPORT_USES_V6) >= _MAX_SOURCES_V6
        or view.entry_cap is not read_record["close_entry_cap"]
        or view.close_intent_view.source_context is not read_record["view"]
        or view.scope is not read_record["scope"]
        or view.session_factory is not read_record["writer_factory"]
    ):
        _deny("controlled_account_negative_transport_once_required")
    # Burn the genuine intent's Source purpose before constructing any client.
    # A failed construction remains spent; no replacement client/submit retry.
    _CLOSE_TRANSPORT_USES_V6[id(intent_state)] = intent_state
    proof = view.close_intent_view.intent_readback
    if (
        type(proof) is not tuple
        or len(proof) != 5
        or type(proof[0]) is not bytes
        or type(proof[1]) is not bytes
        or type(proof[4]) is not dict
    ):
        _deny("controlled_account_negative_transport_committed_readback_required")
    document = decode(proof[1])
    if (
        document["negative_budget"] != proof[4]
        or view.expires_at != utc_from_ns(proof[4]["expires_utc_ns"])
        or view.expiry_monotonic_ns != proof[4]["expires_monotonic_ns"]
    ):
        _deny("controlled_account_negative_transport_committed_budget_changed")
    stamp = _stamp_v6()
    _close_write_window_v6(
        proof[4],
        {
            "utc_ns": proof[4]["started_utc_ns"],
            "monotonic_ns": proof[4]["started_monotonic_ns"],
        },
        stamp,
        read_record["original"]["utc_ns"] + 60_000_000_000,
        read_record["read_deadline_ns"],
    )
    # Charge the actual independently read row/intent/target/entry DATA before
    # client construction, then charge the new credential document on retention.
    retained = _close_write_buffer_bytes_v6(
        (proof, view.close_intent_view.target, view.filled_result_view.readback)
    )
    prior = read_record["close_prior_read"]
    _retained_budget_v6(
        read_record["source_state"],
        extra=(
            *_reconciliation_retained_buffers_v6(read_record),
            *_reconciliation_retained_buffers_v6(prior),
            *retained,
        ),
    )
    settings = read_record["settings"]
    client = private_rest.OkxDemoPrivateRestClient(settings=settings)
    if (
        type(client) is not private_rest.OkxDemoPrivateRestClient
        or client.settings is not settings
        or client._external_client is not None
        or client._clock_injected is not False
    ):
        _deny("controlled_account_dedicated_negative_transport_client_required")
    original = _TRANSPORT_V6.get(read_record["transport"])
    if original is None or original["state"] is not read_record["source_state"]:
        _deny("controlled_account_negative_transport_original_owner_required")
    credential_document = canonical(
        {
            "schema_version": "ctcc.native_controlled_close_write_credential_session.v1",
            "session_id": native.secrets.token_hex(32),
            "created": stamp,
            "scope": read_record["scope"].model_dump(mode="json"),
            "configured_origin": read_record["origin"],
            "configured_credentials_sha256": sha(
                canonical(list(read_record["credentials"]))
            ),
            "read_credential_session_sha256": read_record["credential_session_sha256"],
            "close_intent_sha256": sha(proof[1]),
            "negative_budget": proof[4],
        }
    )
    token = object.__new__(_DemoCloseTransportBindingV1)
    context_type = write._DemoCloseTransportContextV1
    record = {
        "token": token,
        "intent_state": intent_state,
        "intent_view": view.close_intent_view,
        "intent_readback": proof,
        "budget_json": canonical(proof[4]),
        "read_record": read_record,
        "read_generation": read_record["generation"],
        "original_transport": original,
        "context": _context_v6(),
        "key": key,
        "write_module": write,
        "context_type": context_type,
        "client": client,
        "client_pin": native._v3_data_pin(client.__dict__),
        "credential_document": credential_document,
        "received_buffers": (),
        "post_signatures": (),
        "last": stamp,
        "phase": "BINDING",
        "inhibited": False,
        "inhibit_operation": None,
    }
    record["view"] = context_type(
        client=client,
        read_session=original["state"]["control"]["read_session"],
        scope=read_record["scope"],
        session_factory=read_record["writer_factory"],
        origin=read_record["origin"],
        credential_session_sha256=sha(credential_document),
        read_source_generation=read_record["generation"],
        original_entry_transport_binding=read_record["transport"],
        negative_expires_at=view.expires_at,
        monotonic_deadline_ns=view.expiry_monotonic_ns,
    )
    _CLOSE_TRANSPORT_V6[token] = record
    _CLOSE_TRANSPORT_USES_V6[id(intent_state)] = token
    read_record["close_write_token"] = token
    _close_transport_identity_v6(record, key=key)
    record["phase"] = "BOUND"
    return token


def _replay_native_close_transport_v1(token, *, key):
    record = (
        _CLOSE_TRANSPORT_V6.get(token)
        if type(token) is _DemoCloseTransportBindingV1
        else None
    )
    return _close_transport_identity_v6(record, key=key)


def _make_close_transport_inhibitor_v6():
    records, uses = _CLOSE_TRANSPORT_V6, _CLOSE_TRANSPORT_USES_V6
    record_type, context = _DemoCloseTransportBindingV1, native._context
    original_inhibit = _INHIBIT_TRANSPORT_V6

    def inhibit(token, *, key):
        record = records.get(token) if type(token) is record_type else None
        if (
            type(record) is not dict
            or record["key"] is not key
            or record["inhibited"]
            or uses.get(id(record["intent_state"])) is not token
        ):
            _deny("controlled_account_negative_transport_stop_once_required")
        actual, expected = context(), record["context"]
        if actual[:3] != expected[:3] or actual[3] is not expected[3]():
            _deny("controlled_account_negative_transport_stop_original_task_required")
        original = record["original_transport"]
        read_record = record["read_record"]
        if (
            original["state"] is not read_record["source_state"]
            or original["owner"] is not read_record["control_owner"]
            or original["factory"] is not read_record["writer_factory"]
            or original["scope"].account_id != read_record["scope"].account_id
            or original["scope"].environment != read_record["scope"].environment
        ):
            _deny("controlled_account_negative_transport_stop_owner_changed")
        record["inhibited"] = True
        record["phase"] = "UNKNOWN"
        # Captured original synchronous revoke precedes the sole bounded
        # persistence awaitable. Neither old Arm nor any read/write age gates it.
        operation = original_inhibit(original)
        record["inhibit_operation"] = operation
        return operation

    return inhibit


_inhibit_native_close_transport_v1 = _make_close_transport_inhibitor_v6()


# The response to the negative close mints a THIRD independent read purpose.
# No earlier entry/preflight/write generation or closed lease is renewed.
_CLOSE_POST_READ_KEY_V1 = object()
_CLOSE_POST_READ_USES_V6 = {}


def _close_post_lineage_v6(cap):
    from app.exchange.okx import private_rest
    from app.trade_qualification import native_controlled_close_v1 as close

    _REQUIRE_SOURCE_KERNEL_V6()
    view = close._replay_native_close_post_read_for_source_v1(
        cap, key=_CLOSE_POST_READ_KEY_V1
    )
    if (
        type(view) is not close.NativeClosePostReadForSourceV1
        or type(view.response_data) is not close.NativeCloseSubmissionResponseDataV1
    ):
        _deny("controlled_account_genuine_close_response_source_required")
    data = view.response_data
    token = data.transport_facts.transport_binding
    write_record = (
        _CLOSE_TRANSPORT_V6.get(token)
        if type(token) is _DemoCloseTransportBindingV1
        else None
    )
    if (
        type(write_record) is not dict
        or write_record["token"] is not token
        or _CLOSE_TRANSPORT_USES_V6.get(id(write_record["intent_state"])) is not token
        or data.transport_state is not write_record["intent_state"]
        or view.close_intent_view is not write_record["intent_view"]
        or data.filled_result_view is not view.close_intent_view.filled_result_view
        or data.transport_facts.transport_context is not write_record["view"]
    ):
        _deny("controlled_account_close_response_write_lineage_changed")
    preflight = write_record["read_record"]
    if (
        _RECONCILIATION_READ_V6.get(preflight["generation"]) is not preflight
        or _close_write_read_record_v6(preflight) is not write_record
        or view.close_intent_view.source_generation is not preflight["generation"]
        or view.close_intent_view.source_context is not preflight["view"]
        or view.close_intent_view.entry_cap is not preflight["close_entry_cap"]
        or data.entry_response_handle is not preflight["close_response_handle"]
        or data.filled_result_view.response_data.transport_facts.transport_binding
        is not preflight["transport"]
    ):
        _deny("controlled_account_close_response_preflight_lineage_changed")
    actual, expected = native._context(), preflight["context"]
    if actual[:3] != expected[:3] or actual[3] is not expected[3]():
        _deny("controlled_account_close_response_original_task_required")
    first = _close_prior_read_v6(preflight)
    lease, client = preflight["read_lease"], preflight["client"]
    if (
        type(client) is not private_rest.OkxDemoPrivateRestClient
        or client.settings is not preflight["settings"]
        or client._external_client is not None
        or client._clock_injected is not False
        or client._clock is not preflight["client_clock"]
        or native._v3_callable_pin(client._clock) != preflight["client_clock_pin"]
        or type(lease) is not private_rest.DemoPinnedReadSession
        or lease._owner is not client
        or lease._settings is not preflight["settings"]
        or lease._credentials != preflight["credentials"]
        or lease._origin != preflight["origin"]
        or lease.binding_id != preflight["read_lease_binding_id"]
        or lease._lease.active is not False
    ):
        _deny("controlled_account_close_preflight_lease_must_remain_closed")
    for factory, pin, route in preflight["factories"]:
        if (
            history._factory_pin(factory) != pin
            or native._database_connection_pin_v4(factory) != route
        ):
            _deny("controlled_account_close_preflight_factory_changed")
    stamp = _stamp_v6()
    _reconciliation_read_window_v6(
        preflight["original"],
        preflight["last"],
        stamp,
        preflight["original"]["utc_ns"] + 60_000_000_000,
        preflight["read_deadline_ns"],
        enforce=False,
    )
    preflight["last"] = stamp
    return view, first, preflight, write_record


def _close_post_private_tokens_v6(first, preflight, write_record):
    from app.exchange.okx import native_demo_write_v2 as write
    from app.exchange.okx import native_reconciliation_observation_v1 as read

    tokens = list(preflight["private_tokens"])
    for transport in (preflight["transport"], write_record["token"]):
        actual = write._native_postsubmit_scan_tokens_v1(transport, key=_SCAN_KEY_V6)
        expected_lengths = (0, 1) if transport is write_record["token"] else (1,)
        if type(actual) is not tuple or len(actual) not in expected_lengths:
            _deny("controlled_account_close_post_actual_post_signature_required")
        # Only Root's genuine spent/unprepared CLOSE record can return empty.
        # Missing/ambiguous prepared signatures raise in that fixed accessor.
        tokens.extend(actual)
    for prior in (first, preflight):
        journals = getattr(
            prior["client"], "_native_reconciliation_read_journals_v1", None
        )
        if (
            type(journals) is not list
            or not journals
            or len(journals) > read.MAX_REQUESTS
        ):
            _deny("controlled_account_close_post_actual_read_journals_required")
        for journal in journals:
            if (
                type(journal) is not read.ReadJournalV1
                or type(journal.signature) is not str
            ):
                _deny("controlled_account_close_post_actual_read_signature_required")
            tokens.append(journal.signature)
    if any(
        type(value) is not str or not value or not value.isascii() for value in tokens
    ):
        _deny("controlled_account_close_post_actual_secret_tokens_required")
    return tuple(dict.fromkeys(tokens))


def _close_post_prior_reads_v6(record):
    cap = record.get("close_post_read_cap")
    if (
        _RECONCILIATION_READ_V6.get(record["generation"]) is not record
        or _CLOSE_POST_READ_USES_V6.get(cap) is not record["generation"]
    ):
        _deny("controlled_account_genuine_third_read_generation_required")
    view, first, preflight, write_record = _close_post_lineage_v6(cap)
    if (
        view is not record["close_post_read_view"]
        or preflight is not record["close_preflight_read"]
        or write_record is not record["close_post_write_record"]
        or record["transport"] is not preflight["transport"]
        or record["source_state"] is not preflight["source_state"]
        or record["settings"] is not preflight["settings"]
        or record["scope"] is not preflight["scope"]
        or record["writer_factory"] is not preflight["writer_factory"]
        or record["raw_factory"] is not preflight["raw_factory"]
        or record["control_owner"] is not preflight["control_owner"]
        or record["selector_record"] is not preflight["selector_record"]
        or record["credentials"] != preflight["credentials"]
        or record["origin"] != preflight["origin"]
        or record["client"] is first["client"]
        or record["client"] is preflight["client"]
        or record["client"] is write_record["client"]
    ):
        _deny("controlled_account_original_preflight_third_read_relation_changed")
    actual_journals = getattr(
        preflight["client"], "_native_reconciliation_read_journals_v1", None
    )
    expected_journals = record["close_preflight_journals"]
    if (
        type(actual_journals) is not list
        or len(actual_journals) != len(expected_journals)
        or any(
            actual is not expected
            for actual, expected in zip(actual_journals, expected_journals, strict=True)
        )
    ):
        _deny("controlled_account_close_preflight_journal_owners_changed")
    return first, preflight


def acquire_native_close_post_read_source_v1(cap, *, key):
    from app.trade_qualification import native_controlled_close_v1 as close
    from app.trade_qualification import (
        native_postsubmit_reconciliation_v1 as postsubmit,
    )

    _REQUIRE_SOURCE_KERNEL_V6()
    if (
        key is not close._SOURCE_KEY_V1
        or type(cap) is not close.OwnedNativeClosePostReadV1
    ):
        _deny("controlled_account_fixed_close_post_read_purpose_required")
    if (
        cap in _CLOSE_POST_READ_USES_V6
        or len(_CLOSE_POST_READ_USES_V6) >= _MAX_SOURCES_V6
    ):
        _deny("controlled_account_close_post_read_once_required")
    # Post has already MOVEd this real received-response cap. Burn Source's
    # purpose before construction; any failure remains one-use and held.
    view = close._take_native_close_post_read_for_source_v1(
        cap, key=_CLOSE_POST_READ_KEY_V1
    )
    _CLOSE_POST_READ_USES_V6[cap] = None
    actual, first, preflight, write_record = _close_post_lineage_v6(cap)
    if actual is not view:
        _deny("controlled_account_close_post_source_view_changed")
    tokens = _close_post_private_tokens_v6(first, preflight, write_record)
    record = _new_reconciliation_source_v6(
        authority=write_record["token"],
        settings=preflight["settings"],
        credentials=preflight["credentials"],
        scope=preflight["scope"],
        origin=preflight["origin"],
        writer_factory=preflight["writer_factory"],
        raw_factory=preflight["raw_factory"],
        control_owner=preflight["control_owner"],
        source_state=preflight["source_state"],
        transport=preflight["transport"],
        selector_record=preflight["selector_record"],
        private_tokens=tokens,
        close_post_read_cap=cap,
    )
    # Scanner identity replays remain passive; this does not grant DB retention
    # past the new fixed60s or make either POST/old read healthy again.
    scan_native_reconciliation_private_data_v1(
        record["generation"], b"", key=postsubmit._SOURCE_KEY_V1
    )
    return _reconciliation_view_v6(record, postsubmit)


def _replay_native_close_post_read_source_v1(source_generation, *, key):
    postsubmit = _reconciliation_key_v6(key)
    record = _RECONCILIATION_READ_V6.get(source_generation)
    if type(record) is not dict or record.get("close_post_read_cap") is None:
        _deny("controlled_account_genuine_third_read_generation_required")
    _reconciliation_identity_v6(record, deadline=True)
    _close_post_prior_reads_v6(record)
    return _reconciliation_view_v6(record, postsubmit)


def _make_close_post_read_inhibitor_v6():
    from app.trade_qualification import (
        native_postsubmit_reconciliation_v1 as postsubmit,
    )

    records, uses = _RECONCILIATION_READ_V6, _CLOSE_POST_READ_USES_V6
    purpose, context = postsubmit._SOURCE_KEY_V1, native._context
    original_inhibit = _inhibit_native_reconciliation_read_source_v1

    def inhibit(source_generation, *, key):
        record = records.get(source_generation)
        if (
            key is not purpose
            or type(record) is not dict
            or record.get("close_post_read_cap") is None
            or uses.get(record["close_post_read_cap"]) is not source_generation
        ):
            _deny("controlled_account_close_post_stop_genuine_generation_required")
        actual, expected = context(), record["context"]
        if actual[:3] != expected[:3] or actual[3] is not expected[3]():
            _deny("controlled_account_close_post_stop_original_task_required")
        # The captured original immediately revokes the genuine ORIGINAL scoped
        # safety owner and returns one bounded persistence operation. No old or
        # new read/write budget, Arm or caller-provided target gates tightening.
        return original_inhibit(source_generation, key=purpose)

    return inhibit


_inhibit_native_close_post_read_source_v1 = _make_close_post_read_inhibitor_v6()

# Filled-flat completion is a distinct negative capability. The historical
# zero-FOK nineteen-role cleanup above remains byte-exact.
_FILLED_FLAT_CLEANUP_KEY_V1 = object()
_FILLED_FLAT_CLEANUP_RECORDS_V6 = {}
_FILLED_FLAT_CLEANUP_PREPARED_V6 = {}
_FILLED_FLAT_CLEANUP_COMPLETED_V6 = {}
_FILLED_FLAT_CLEANUP_RETIRED_V6 = {}
_FILLED_FLAT_CLEANUP_TERMINAL_V6 = {}


class _PreparedFilledFlatCleanupV6(_OpaqueV6):
    __slots__ = ()


class _CompletedFilledFlatCleanupV6(_OpaqueV6):
    __slots__ = ()


class _RetiredFilledFlatCleanupV6(_OpaqueV6):
    __slots__ = ()


@dataclass(frozen=True, slots=True, repr=False)
class NativeFilledFlatSourceCleanupCompletionViewV1:
    settlement_cap: object = field(repr=False)
    source_generations: tuple = field(repr=False)
    entry_transport_binding: object = field(repr=False)
    close_transport_binding: object = field(repr=False)
    filled_result_view: object = field(repr=False)
    closed_transports_view: object = field(repr=False)
    closed_transports_handle: object = field(repr=False)
    disarm_observation: object = field(repr=False)
    new_get_observations: tuple = field(repr=False)
    receipt_json: bytes = field(repr=False)
    execution_authority: bool = field(default=False, init=False)
    order_retry_authority: bool = field(default=False, init=False)


def _filled_cleanup_generation_tuple_v6(value):
    # Pure DATA shape only. A tuple passing this helper is never an owner.
    if (
        type(value) is not tuple
        or len(value) != 3
        or any(type(item) is not object for item in value)
        or len({id(item) for item in value}) != 3
    ):
        _deny("controlled_account_filled_cleanup_three_generations_required")
    return value


def _filled_cleanup_readsets_shape_v6(generations, readsets):
    from app.exchange.okx import native_reconciliation_observation_v1 as read

    _filled_cleanup_generation_tuple_v6(generations)
    if type(readsets) is not tuple or len(readsets) != 3:
        _deny("controlled_account_filled_cleanup_three_manifests_required")
    manifests = set()
    for generation, row in zip(generations, readsets, strict=True):
        if (
            type(row) is not tuple
            or len(row) != 3
            or row[0] is not generation
            or type(row[1]) is not str
            or len(row[1]) != 64
            or any(char not in "0123456789abcdef" for char in row[1])
            or row[1] in manifests
            or type(row[2]) is not tuple
            or not 0 < len(row[2]) <= read.MAX_REQUESTS
        ):
            _deny("controlled_account_filled_cleanup_actual_manifest_required")
        manifests.add(row[1])
        roles = set()
        for item in row[2]:
            if (
                type(item) is not tuple
                or len(item) != 2
                or type(item[0]) is not str
                or not 0 < len(item[0]) <= 96
                or not item[0].isascii()
                or any(not (char.isalnum() or char == "_") for char in item[0])
                or item[0] in roles
                or not _filled_cleanup_observation_data_v6(item[1])
            ):
                _deny("controlled_account_filled_cleanup_actual_manifest_row_required")
            roles.add(item[0])
    return readsets


def _filled_cleanup_key_v6(key):
    from app.trade_qualification import native_postsubmit_reconciliation_v1 as post

    if key is not post._FILLED_SOURCE_SETTLEMENT_KEY_V1:
        _deny("controlled_account_filled_cleanup_fixed_purpose_required")
    return post


def _filled_cleanup_window_v6(started, last, stamp):
    # One five-second negative cleanup phase, not an entry/read lease renewal.
    validate_stamps((started, last, stamp))
    if any(
        stamp[name] - started[name] >= 5_000_000_000
        for name in ("utc_ns", "monotonic_ns")
    ):
        _deny("controlled_account_filled_cleanup_budget_exhausted")
    return stamp


def _filled_cleanup_original_buffers_v6(state):
    # Same finite DATA roots as the existing source accountant. No registry,
    # task/engine/client namespace or serialization is traversed.
    roots = [
        state.get(name)
        for name in (
            "claims_json",
            "lineage_row_json",
            "hwm_raw",
            "counter_native_receipt",
            "current_record",
            "facts",
            "bank_result",
            "population",
            "position_source",
            "partition",
            "registration",
            "coverage_observation",
            "latest_coverage_observation",
            "mirror_observation",
            "counter",
            "proof_operands",
            "issued_readback",
            "financial_values",
        )
    ]
    current_state = state.get("current")
    if type(current_state) is dict:
        roots.extend(
            (
                current_state.get("bundle_raw"),
                current_state.get("private_secret_tokens"),
            )
        )
    roots.append(state.get("history_tokens"))
    ctrl = state.get("control")
    if type(ctrl) is dict and ctrl.get("identity_record") is not None:
        roots.append(ctrl["identity_record"].route_evidence)
    binding = state.get("coverage_binding")
    if binding is not None:
        values = mirror55._retained_mirror_buffers_v1(binding, key=_SOURCE_KEY_V6)
        if type(values) is not tuple or any(
            type(raw) not in (bytes, str) for raw in values
        ):
            _deny("controlled_account_filled_cleanup_original_mirror_buffers_changed")
        roots.append(values)
    generation = state.get("generation")
    if generation is not None:
        from app.trade_qualification import (
            native_original_local_current55_v2 as original55,
        )

        values = original55._retained_original_family_buffers_v2(
            generation, key=_SOURCE_KEY_V6
        )
        if type(values) is not tuple or any(
            type(raw) not in (bytes, str) for raw in values
        ):
            _deny("controlled_account_filled_cleanup_original_family_buffers_changed")
        roots.append(values)
    return _close_write_buffer_bytes_v6(tuple(roots), preserve_text=True)


def _filled_cleanup_own_r7_retirement_v6(record):
    post = _filled_cleanup_key_v6(record["key"])
    proof = post._replay_native_filled_flat_r7_retirement_for_source_v1(
        record["settlement_cap"], key=_FILLED_FLAT_CLEANUP_KEY_V1
    )
    if (
        type(proof) is not tuple
        or len(proof) != 2
        or type(proof[0]) is not tuple
        or len(proof[0]) != 4
        or proof[0][0] != "ctcc.native_filled_flat_r7_source_chain_retired.v1"
        or any(
            type(value) is not str or not 0 < len(value) <= 128 for value in proof[0]
        )
        or proof[1] is not record["settlement_view"].source_generations
        or proof[0][1]
        != record["settlement_view"].filled_result_view.result.reservation_id
    ):
        _deny("controlled_account_filled_cleanup_actual_own_R7_retirement_required")
    previous = record.get("r7_retirement")
    if previous is not None and (
        proof[0] is not previous[0] or proof[1] is not previous[1]
    ):
        _deny("controlled_account_filled_cleanup_R7_retirement_replaced")
    for state in (record["original"], record["fresh"]):
        if (
            _STATES_V6.get(id(state)) is not None
            or state["phase"] != "RETIRED"
            or type(state.get("current")) is not dict
            or state["current"]
            or any(
                value is state or (type(value) is tuple and value and value[0] is state)
                for mapping in (
                    _CURRENT_V6,
                    _COVERAGE_V6,
                    _HISTORY_USES_V6,
                    _ISSUERS_V6,
                    _ISSUED_V6,
                    _HELD_V6,
                )
                for value in mapping.values()
            )
        ):
            _deny("controlled_account_filled_cleanup_own_R7_effects_changed")
    record["r7_retirement"] = proof
    return proof


def _filled_cleanup_historical_reads_v6(record):
    from app.exchange.okx import private_rest

    for read_record, saved in zip(
        record["reads"], record["read_bindings"], strict=True
    ):
        (
            generation,
            client,
            settings,
            scope,
            lease,
            binding_id,
            clock,
            clock_pin,
            context,
        ) = saved
        if (
            _RECONCILIATION_READ_V6.get(generation) is not read_record
            or read_record["generation"] is not generation
            or read_record["client"] is not client
            or type(client) is not private_rest.OkxDemoPrivateRestClient
            or client.settings is not settings
            or read_record["settings"] is not settings
            or configured.get_settings() is not settings
            or native._v3_data_pin(settings.__dict__) != read_record["settings_pin"]
            or read_record["scope"] is not scope
            or read_record["read_lease"] is not lease
            or canonical(scope.model_dump(mode="json")) != read_record["scope_json"]
            or _reconciliation_selector_pin_v6(read_record["selector_record"])
            != read_record["selector_pin"]
            or type(lease) is not private_rest.DemoPinnedReadSession
            or lease._owner is not client
            or lease._settings is not settings
            or lease._credentials != read_record["credentials"]
            or lease._origin != read_record["origin"]
            or lease.binding_id != binding_id
            or read_record["read_lease_binding_id"] != binding_id
            or lease._lease.active is not False
            or client._external_client is not None
            or client._clock_injected is not False
            or client._clock is not clock
            or native._v3_callable_pin(clock) != clock_pin
        ):
            _deny("controlled_account_filled_cleanup_historical_read_owner_changed")
        credentials = (
            settings.okx_demo_api_key.get_secret_value(),
            settings.okx_demo_api_secret.get_secret_value(),
            settings.okx_demo_api_passphrase.get_secret_value(),
        )
        if (
            credentials != read_record["credentials"]
            or settings.trading_mode != "okx_demo"
            or settings.live_trading is not False
            or settings.okx_demo_rest_base_url.rstrip("/") != read_record["origin"]
        ):
            _deny("controlled_account_filled_cleanup_historical_credentials_changed")
        for factory, pin, route in read_record["factories"]:
            if (
                history._factory_pin(factory) != pin
                or native._database_connection_pin_v4(factory) != route
            ):
                _deny("controlled_account_filled_cleanup_historical_factory_changed")
        actual = native._context()
        if actual[:3] != context[:3] or actual[3] is not context[3]():
            _deny("controlled_account_filled_cleanup_historical_read_task_changed")


def _filled_cleanup_budget_v6(record):
    from app.exchange.okx import native_demo_write_v2 as write

    first, preflight, third = record["reads"]
    if record.get("r7_retirement") is None:
        # Live pre-release path retains the original complete relation checks.
        _reconciliation_budget_v6(third)
        live_source = (
            *_filled_cleanup_original_buffers_v6(record["original"]),
            *_filled_cleanup_original_buffers_v6(record["fresh"]),
        )
    else:
        # This is reachable only through the genuine post/ledger receipt.
        # It is not a caller-selected allow-retired flag or an empty fallback.
        _filled_cleanup_own_r7_retirement_v6(record)
        _filled_cleanup_historical_reads_v6(record)
        live_source = record["captured_source_buffers"]
    roots = [record["captured_source_buffers"], live_source]
    for read_record in (first, preflight, third):
        roots.append(_reconciliation_retained_buffers_v6(read_record))
    for binding in (
        record["settlement_view"].entry_transport_binding,
        record["settlement_view"].close_transport_binding,
    ):
        values = write._native_source_retained_buffers_v1(binding, key=_SCAN_KEY_V6)
        if type(values) is not tuple or any(
            type(value) not in (bytes, str) for value in values
        ):
            _deny("controlled_account_filled_cleanup_actual_both_Root_buffers_required")
        roots.append(values)
    closing = record["close_transport"]
    roots.extend(
        (
            closing["credential_document"],
            closing["budget_json"],
            closing["intent_readback"],
            closing["intent_view"],
            closing["received_buffers"],
            closing["post_signatures"],
            record["settlement_view"].filled_result_view,
            record["settlement_view"].closed_transports_view,
            record["readsets"],
            record.get("completion_view"),
            record.get("disarm_receipt"),
            record.get("retirement_receipt"),
            record.get("root_finalized"),
            record.get("terminal_tokens"),
            record.get("r7_retirement"),
            record.get("control_resolution"),
            record.get("control_readbacks"),
            record.get("control_read_pending"),
            record.get("negative_release_observation"),
            record.get("terminal_owner_observation"),
        )
    )
    # Preserve the original immutable bytes/strings and identity-deduplicate,
    # rather than retaining a second encoded copy of the same strings.
    snapshot = _close_write_buffer_bytes_v6(tuple(roots), preserve_text=True)
    _retained_budget_v6({"history_tokens": snapshot})
    return snapshot


def _filled_cleanup_observation_data_v6(value):
    from app.exchange.okx import native_reconciliation_observation_v1 as read

    if type(value) is not read.DemoReconciliationObservationV1:
        return False
    # Check finite native field types BEFORE dataclass equality. A replaced
    # DATA object must not invoke a foreign __eq__/tzinfo callback on denial.
    for name in (
        "path",
        "request_url",
        "request_signature_sha256",
        "read_session_id",
        "certificate_sha256",
        "tls_hostname",
    ):
        item = getattr(value, name)
        if type(item) is not str or not item:
            return False
    for name in (
        "started_monotonic_ns",
        "headers_monotonic_ns",
        "eof_monotonic_ns",
        "http_status",
    ):
        item = getattr(value, name)
        if type(item) is not int or item <= 0:
            return False
    for name in ("started_at", "headers_at", "eof_at"):
        item = getattr(value, name)
        if type(item) is not datetime or item.tzinfo is not UTC:
            return False
    return not (
        type(value.parameters) is not tuple
        or len(value.parameters) > 8
        or any(
            type(pair) is not tuple
            or len(pair) != 2
            or any(type(item) is not str for item in pair)
            for pair in value.parameters
        )
        or type(value.raw_headers) is not tuple
        or len(value.raw_headers) > 256
        or any(
            type(pair) is not tuple
            or len(pair) != 2
            or any(type(item) is not bytes for item in pair)
            for pair in value.raw_headers
        )
        or type(value.raw_body) is not bytes
        or not 0 < len(value.raw_body) <= read.MAX_BODY
        or value.account_complete is not False
        or value.execution_authority is not False
        or value.order_retry_authority is not False
    )


def _filled_cleanup_journals_v6(record, *, capture=False, observe=False):
    from app.exchange.okx import native_demo_settlement_v1 as passive
    from app.exchange.okx import native_reconciliation_network_custody_v1 as network
    from app.exchange.okx import native_reconciliation_observation_v1 as read

    all_journals, all_observations = [], []
    for index, (read_record, readset) in enumerate(
        zip(record["reads"], record["readsets"], strict=True)
    ):
        journals = vars(read_record["client"]).get(
            "_native_reconciliation_read_journals_v1"
        )
        expected_rows = readset[2]
        if type(journals) is not list or len(journals) != len(expected_rows):
            _deny("controlled_account_filled_cleanup_complete_get_owners_required")
        if not capture:
            saved = record["journals"][index]
            if len(saved) != len(journals) or any(
                left is not right for left, right in zip(saved, journals, strict=True)
            ):
                _deny("controlled_account_filled_cleanup_get_owner_replaced")
        measured = []
        for position, (item, (_role, expected)) in enumerate(
            zip(journals, expected_rows, strict=True)
        ):
            if (
                type(item) is not read.ReadJournalV1
                or item.read_session_id != read_record["read_lease_binding_id"]
                or item.eof_stamp is None
                or type(item.faults) is not list
                or item.faults
                or (
                    not capture
                    and item.observation
                    is not record["journal_observations"][index][position]
                )
                or not _filled_cleanup_observation_data_v6(item.observation)
                or not _filled_cleanup_observation_data_v6(expected)
                or item.observation != expected
                or item.transport_client is None
                or item.tcp_stream is None
                or item.tls_stream is None
                or item.tcp_transport is None
                or item.raw_socket is None
                or type(item.network_backend) is not network._RetainedBackend
                or item.network_backend.journal is not item
                or item.network_backend.connected is not True
                or type(item.raw_transport_proxy) is not network._RawTransport
                or item.raw_transport_proxy._journal is not item
                or item.raw_transport_proxy._native is not item.tcp_transport
                or type(item.cleanup_hold) is not dict
                or type(item.cleanup_hold.get("cleanup_unknown_sockets")) is not list
                or item.cleanup_hold["cleanup_unknown_sockets"]
                or item.cleanup_hold.get("cleanup_corrupt_socket") is not None
                or type(item.cleanup_abort) is not type(_stamp_v6)
                or item.cleanup_abort.__code__ is not item.cleanup_abort_code
            ):
                _deny("controlled_account_filled_cleanup_actual_get_custody_changed")
            client = item.transport_client
            if type(client) is not network.httpx.AsyncClient:
                _deny("controlled_account_filled_cleanup_actual_httpx_client_required")
            transport = vars(client).get("_transport")
            if type(transport) is not network.httpx.AsyncHTTPTransport:
                _deny(
                    "controlled_account_filled_cleanup_actual_httpx_transport_required"
                )
            pool = vars(transport).get("_pool")
            if (
                type(pool) is not network.httpcore.AsyncConnectionPool
                or vars(pool).get("_network_backend") is not item.network_backend
            ):
                _deny("controlled_account_filled_cleanup_actual_httpcore_pool_required")
            network._stream(item, item.tcp_stream)
            network._stream(item, item.tls_stream)
            if observe:
                result = passive.observe_native_closed_httpx_tcp_data_v1(
                    client, transport, item.tcp_transport, item.raw_socket
                )
                if result is None:
                    return None
                measured.append(result)
        all_journals.append(tuple(journals))
        all_observations.append(tuple(measured))
    if capture:
        record["journals"] = tuple(all_journals)
        record["journal_observations"] = tuple(
            tuple(item.observation for item in group) for group in all_journals
        )
    return tuple(all_observations)


def _filled_cleanup_check_v6(record, *, after_finalizer=False):
    from app.exchange.okx import native_demo_filled_settlement_v1 as filled

    _REQUIRE_SOURCE_KERNEL_V6()
    if (
        type(record) is not dict
        or _FILLED_FLAT_CLEANUP_RECORDS_V6.get(record["token"]) is not record
        or record["phase"] not in ("PREPARING", "PREPARED", "COMPLETED")
    ):
        _deny("controlled_account_filled_cleanup_original_operation_required")
    actual, expected = native._context(), record["context"]
    if actual[:3] != expected[:3] or actual[3] is not expected[3]():
        _deny("controlled_account_filled_cleanup_original_task_required")
    post = _filled_cleanup_key_v6(record["key"])
    replay = (
        post._replay_native_filled_flat_source_after_finalization_v1
        if after_finalizer
        else post._replay_native_filled_flat_source_settlement_v1
    )
    view = replay(record["settlement_cap"], key=_FILLED_FLAT_CLEANUP_KEY_V1)
    if view is not record["settlement_view"]:
        _deny("controlled_account_filled_cleanup_settlement_replaced")
    sets = post._replay_native_filled_flat_source_readsets_v1(
        record["settlement_cap"], key=_FILLED_FLAT_CLEANUP_KEY_V1
    )
    _filled_cleanup_readsets_shape_v6(view.source_generations, sets)
    if sets != record["readsets"]:
        _deny("controlled_account_filled_cleanup_committed_readsets_changed")
    first, preflight, third = record["reads"]
    if after_finalizer:
        _filled_cleanup_own_r7_retirement_v6(record)
        _filled_cleanup_historical_reads_v6(record)
    for read_record in record["reads"]:
        if not after_finalizer:
            _reconciliation_identity_v6(read_record, deadline=False)
        if (
            read_record["read_lease"] is None
            or read_record["read_lease"]._lease.active is not False
        ):
            _deny("controlled_account_filled_cleanup_read_lease_not_closed")
    old_first, old_preflight = (
        (first, preflight) if after_finalizer else _close_post_prior_reads_v6(third)
    )
    if (
        old_first is not first
        or old_preflight is not preflight
        or preflight["close_prior_read"] is not first
        or third["close_preflight_read"] is not preflight
        or third["close_post_write_record"] is not record["close_transport"]
    ):
        _deny("controlled_account_filled_cleanup_generation_order_changed")
    transport = _TRANSPORT_V6.get(view.entry_transport_binding)
    closing = _CLOSE_TRANSPORT_V6.get(view.close_transport_binding)
    if (
        transport is not record["transport"]
        or closing is not record["close_transport"]
        or closing["read_record"] is not preflight
        or _CLOSE_TRANSPORT_USES_V6.get(id(closing["intent_state"]))
        is not view.close_transport_binding
        or (
            not after_finalizer
            and _close_write_read_record_v6(preflight) is not closing
        )
        or transport["state"] is not record["fresh"]
        or record["original"] is not record["fresh"]["original_state"]
        or record["original"] is record["fresh"]
        or (
            not after_finalizer
            and any(
                _STATES_V6.get(id(state)) is not state
                for state in (record["original"], record["fresh"])
            )
        )
        or record["original"]["barrier"] is not None
        or record["fresh"]["barrier"] is None
        or record["original"]["control"]["owner"] is not transport["owner"]
        or any(
            read_record["transport"] is not view.entry_transport_binding
            or read_record["source_state"] is not record["fresh"]
            or read_record["control_owner"] is not transport["owner"]
            or read_record["writer_factory"] is not transport["factory"]
            or read_record["scope"].environment != record["scope"].environment
            or read_record["scope"].account_id != record["scope"].account_id
            or read_record["scope"] != first["scope"]
            for read_record in record["reads"]
        )
        or _RECONCILIATION_TRANSPORT_V6.get(view.entry_transport_binding)
        is not first["generation"]
        or view.closed_transports_handle is not record["closed_handle"]
        or type(view.closed_transports_view) is not filled.NativeClosedTransportsViewV1
        or view.closed_transports_view.closed_transports_handle
        is not record["closed_handle"]
        or view.closed_transports_view.filled_result_view is not view.filled_result_view
    ):
        _deny("controlled_account_filled_cleanup_source_relation_changed")
    if not after_finalizer:
        contracts = tuple(
            _flat_original_close_contract_v6(state)
            for state in (record["original"], record["fresh"])
        )
        if contracts != record["close_contracts"]:
            _deny("controlled_account_filled_cleanup_original_contract_changed")
    if record.get("control_resolution") is not None:
        resolution_raw = _filled_cleanup_control_resolution_v12(
            record["control_resolution"], record["controllers"]
        )
        if resolution_raw is not record["disarm_receipt"]:
            _deny("controlled_account_cleanup_control_resolution_replaced")
    _filled_cleanup_journals_v6(record)
    _filled_cleanup_budget_v6(record)
    return view


def _retain_filled_cleanup_unknown_v6(record, *, uncertain):
    if (
        type(uncertain) is not bool
        or _FILLED_FLAT_CLEANUP_RECORDS_V6.get(record["token"]) is not record
    ):
        _deny("controlled_account_filled_cleanup_unknown_operation_required")
    record["phase"] = "UNKNOWN"


def prepare_native_filled_flat_source_cleanup_v1(cap, *, key):
    from app.trade_qualification import native_postsubmit_reconciliation_v1 as post

    _REQUIRE_SOURCE_KERNEL_V6()
    _filled_cleanup_key_v6(key)
    if (
        len(_FILLED_FLAT_CLEANUP_RECORDS_V6) + len(_FILLED_FLAT_CLEANUP_RETIRED_V6)
        >= _MAX_SOURCES_V6
    ):
        _deny("controlled_account_filled_cleanup_capacity")
    # Genuine postsubmit purpose MOVE happens synchronously before an awaitable.
    started = _stamp_v6()
    view = post._take_native_filled_flat_source_settlement_v1(
        cap, key=_FILLED_FLAT_CLEANUP_KEY_V1
    )
    if type(view) is not post.NativeFilledFlatSourceSettlementV1:
        _deny("controlled_account_filled_cleanup_genuine_settlement_required")
    generations = _filled_cleanup_generation_tuple_v6(view.source_generations)
    reads = tuple(_RECONCILIATION_READ_V6.get(generation) for generation in generations)
    transport = _TRANSPORT_V6.get(view.entry_transport_binding)
    closing = _CLOSE_TRANSPORT_V6.get(view.close_transport_binding)
    if (
        any(type(item) is not dict for item in reads)
        or type(transport) is not dict
        or type(closing) is not dict
        or transport["state"] is not reads[0]["source_state"]
    ):
        _deny("controlled_account_filled_cleanup_current_owned_source_required")
    owner = transport["owner"]
    if type(owner) is not owner55.original.DemoControlOwner:
        _deny("controlled_account_filled_cleanup_actual_control_owner_required")
    owner55.original.DemoControlOwner._revoke(owner, stop=False)
    fresh = transport["state"]
    record = {
        "token": object(),
        "context": _context_v6(),
        "key": key,
        "settlement_cap": cap,
        "settlement_view": view,
        "reads": reads,
        "transport": transport,
        "close_transport": closing,
        "fresh": fresh,
        "original": fresh["original_state"],
        "scope": transport["scope"],
        "closed_handle": view.closed_transports_handle,
        "phase": "PREPARING",
        "close_contracts": tuple(
            _flat_original_close_contract_v6(state)
            for state in (fresh["original_state"], fresh)
        ),
        "cleanup_started": started,
        "disarm": None,
        "disarm_receipt": None,
        "observations": None,
        "prepared": None,
        "completed": None,
        "operation": None,
    }
    _FILLED_FLAT_CLEANUP_RECORDS_V6[record["token"]] = record
    try:
        controllers = []
        for state in (record["original"], record["fresh"]):
            ctrl = state["control"]
            public_owner, safety = ctrl["public_owner"], ctrl["owner"]
            if (
                type(public_owner) is not owner55.OwnedDemoControlSessionV2
                or type(safety) is not owner55.original.DemoControlOwner
                or owner55._HANDLES.get(public_owner) is not ctrl
                or safety is not owner
                or ctrl["scope"] != record["scope"]
            ):
                _deny("controlled_account_filled_cleanup_original_facade_required")
            if not any(saved[0] is ctrl for saved in controllers):
                controllers.append(
                    (
                        ctrl,
                        public_owner,
                        safety,
                        ctrl["context"],
                        ctrl["observation"].state.owner_sha256,
                        ctrl["observation"].state.owner_epoch,
                    )
                )
        record["controllers"] = tuple(controllers)
        record["negative_factory_pin"] = history._factory_pin(
            record["original"]["control"]["factory"]
        )
        record["control_readbacks"] = []
        record["control_read_pending"] = ()
        record["captured_source_buffers"] = (
            *_filled_cleanup_original_buffers_v6(record["original"]),
            *_filled_cleanup_original_buffers_v6(record["fresh"]),
        )
        sets = post._replay_native_filled_flat_source_readsets_v1(
            cap, key=_FILLED_FLAT_CLEANUP_KEY_V1
        )
        record["readsets"] = _filled_cleanup_readsets_shape_v6(generations, sets)
        record["read_bindings"] = tuple(
            (
                row["generation"],
                row["client"],
                row["settings"],
                row["scope"],
                row["read_lease"],
                row["read_lease_binding_id"],
                row["client_clock"],
                row["client_clock_pin"],
                row["context"],
            )
            for row in reads
        )
        _filled_cleanup_journals_v6(record, capture=True)
        _filled_cleanup_check_v6(record)
        operation = _AcquisitionAttemptV6(
            record,
            _prepare_filled_cleanup_body_v6(record),
            _retire=_retain_filled_cleanup_unknown_v6,
        )
        record["operation"] = operation
        return operation
    except BaseException:
        record["phase"] = "UNKNOWN"
        raise


def _filled_cleanup_control_kind_v12(state, owner_sha256, owner_epoch, db_clock):
    # Pure DATA classification only; no tuple/label is an owned completion.
    control.checked(state, control.ControlState)
    if (
        type(owner_sha256) is not str
        or len(owner_sha256) != 64
        or any(char not in "0123456789abcdef" for char in owner_sha256)
        or type(owner_epoch) is not int
        or owner_epoch <= 0
        or type(db_clock) is not datetime
        or db_clock.tzinfo is not UTC
        or db_clock < state.updated_at
    ):
        _deny("controlled_account_cleanup_control_identity_invalid")
    if state.owner_sha256 == owner_sha256 and state.owner_epoch == owner_epoch:
        return "release_required" if state.lease_until > db_clock else "own_expired"
    if (
        state.owner_sha256 is not None
        and state.owner_sha256 != owner_sha256
        and state.owner_epoch > owner_epoch
    ):
        return "owner_superseded"
    _deny("controlled_account_cleanup_control_ownership_ambiguous")


def _negative_cleanup_control_check_v13(record):
    # Only these two genuine registries select the fixed negative algorithm.
    # No caller-supplied mode, callback or observation acquires a purpose.
    if (
        type(record) is dict
        and _FLAT_CLEANUP_RECORDS_V6.get(record.get("token")) is record
        and record.get("zero_final_marker") is _ZERO_FINAL_MARKER_V2
    ):
        return _zero_final_check_v2(record)
    return _filled_cleanup_check_v6(record)


async def _read_filled_cleanup_control_v12(record):
    # Actual independent transactions, each UID -> Control -> exact scope.
    # No old GET/entry lease, caller clock or DTO proves current ownership.
    _negative_cleanup_control_check_v13(record)
    ctrl = record["original"]["control"]
    factory = ctrl["factory"]
    if (
        history._factory_pin(factory) != record["negative_factory_pin"]
        or record["transport"]["factory"] is not factory
        or type(ctrl["repository"]) is not DemoControlRepositoryV2
        or ctrl["repository"].session_factory is not factory
        or record["transport"]["owner"].repository is not ctrl["repository"]
    ):
        _deny("controlled_account_cleanup_control_factory_changed")
    async with factory() as session, session.begin():
        _negative_cleanup_control_check_v13(record)
        if type(session) is not AsyncSession:
            _deny("controlled_account_cleanup_control_native_session_required")
        for statement in (
            "SET TRANSACTION ISOLATION LEVEL READ COMMITTED",
            "SET LOCAL search_path TO pg_catalog,public,pg_temp",
            "SET LOCAL TIME ZONE 'UTC'",
            "SET LOCAL lock_timeout='1000ms'",
            "SET LOCAL statement_timeout='1000ms'",
        ):
            await session.execute(text(statement))
            _negative_cleanup_control_check_v13(record)
        await catalog55.verify_actual_control_catalog_v2(session)
        _negative_cleanup_control_check_v13(record)
        state, event = await DemoControlRepositoryV2.read_in_transaction(
            session, ctrl["scope"]
        )
        record["control_read_pending"] = (state, event)
        _negative_cleanup_control_check_v13(record)
        ledger = QualificationLedgerRepository(
            factory, clock=lambda: utc_from_ns(_stamp_v6()["utc_ns"])
        )
        row = await ledger._locked(session, ctrl["ledger_scope"])
        _negative_cleanup_control_check_v13(record)
        scope = ctrl["ledger_scope"]
        if (
            row.environment != scope.environment
            or row.account_id != scope.account_id
            or row.settlement_currency != scope.settlement_currency
            or type(row.account_revision) is not int
            or not 0 <= row.account_revision < 2**63
            or type(row.ledger_revision) is not int
            or not 0 <= row.ledger_revision < 2**63
            or (
                row.claims_sha256 is not None
                and (
                    type(row.claims_sha256) is not str
                    or len(row.claims_sha256) != 64
                    or any(char not in "0123456789abcdef" for char in row.claims_sha256)
                )
            )
        ):
            _deny("controlled_account_cleanup_control_scope_snapshot_invalid")
        scope_raw = canonical(
            {
                "scope": scope.model_dump(mode="json"),
                "account_revision": row.account_revision,
                "ledger_revision": row.ledger_revision,
                "claims_sha256": row.claims_sha256,
            }
        )
        record["control_read_pending"] = (state, event, scope_raw)
        meta = await lineage55._metadata(session)
        _negative_cleanup_control_check_v13(record)
        observed = owner55.original.ControlObservation(state, event, meta[9])
        observed.__post_init__()
        result = (observed, meta, scope_raw, session)
        record["control_readbacks"].append(result)
        record["control_read_pending"] = ()
        _negative_cleanup_control_check_v13(record)
    _negative_cleanup_control_check_v13(record)
    if history._factory_pin(factory) != record["negative_factory_pin"]:
        _deny("controlled_account_cleanup_control_factory_changed")
    return result


def _filled_cleanup_control_document_v12(readback):
    observed, meta, scope_raw, _session = readback
    return {
        "state": control.document(observed.state),
        "event_sha256": observed.event_sha256,
        "observed_at": observed.observed_at.isoformat(),
        "database_metadata": (*meta[:9], meta[9].isoformat()),
        "ledger_scope_json": scope_raw.decode("utf-8"),
        "ledger_scope_sha256": sha(scope_raw),
    }


def _filled_cleanup_control_observation_v12(observed):
    if observed is None:
        return None
    return {
        "state": control.document(observed.state),
        "event_sha256": observed.event_sha256,
        "observed_at": observed.observed_at.isoformat(),
    }


async def _resolve_filled_cleanup_control_v12(record):
    owner = record["transport"]["owner"]
    old_sha, old_epoch = record["controllers"][0][4:6]
    before = await _read_filled_cleanup_control_v12(record)
    kind = _filled_cleanup_control_kind_v12(
        before[0].state, old_sha, old_epoch, before[1][9]
    )
    disarm = released = None
    if kind == "release_required":
        # Unlike scoped _tighten(), this original owner method uses the real
        # owner token + epoch + revision CAS. Never rebase a stale observation.
        previous = owner._observation
        if (
            type(previous) is not owner55.original.ControlObservation
            or control.canonical(control.document(previous.state))
            != control.canonical(control.document(before[0].state))
            or previous.event_sha256 != before[0].event_sha256
        ):
            _deny("controlled_account_cleanup_control_runtime_observation_conflict")
        disarm = await owner55.original.DemoControlOwner._perform(owner, "disarm")
        record["disarm"] = disarm
        _negative_cleanup_control_check_v13(record)
        released = await owner55.original.DemoControlOwner.release(owner)
        record["negative_release_observation"] = released
        _negative_cleanup_control_check_v13(record)
        if (
            type(disarm) is not owner55.original.ControlObservation
            or type(released) is not owner55.original.ControlObservation
            or disarm.state.owner_sha256 != old_sha
            or disarm.state.owner_epoch != old_epoch
            or released.state.owner_sha256 != old_sha
            or released.state.owner_epoch != old_epoch
            or disarm.state.revision != before[0].state.revision + 1
            or released.state.revision != disarm.state.revision + 1
            or disarm.state.lease_until != before[0].state.lease_until
            or released.state.lease_until != released.state.updated_at
            or disarm.state.arm_request_id is not None
            or disarm.state.arm_expires_at is not None
            or released.state.arm_request_id is not None
            or released.state.arm_expires_at is not None
            or disarm.state.emergency_stop is not before[0].state.emergency_stop
            or released.state.emergency_stop is not disarm.state.emergency_stop
            or released.state.pins != before[0].state.pins
        ):
            _deny("controlled_account_cleanup_control_release_readback_conflict")
        kind = "own_released"
    after = await _read_filled_cleanup_control_v12(record)
    if (
        before[3] is after[3]
        or before[1][8] == after[1][8]
        or before[1][9] > after[1][9]
        or before[1][:7] != after[1][:7]
        or before[2] != after[2]
    ):
        _deny("controlled_account_cleanup_control_independent_readback_conflict")
    if released is not None:
        if (
            control.canonical(control.document(after[0].state))
            != control.canonical(control.document(released.state))
            or after[0].event_sha256 != released.event_sha256
            or after[0].state.lease_until > after[1][9]
        ):
            _deny("controlled_account_cleanup_control_release_not_current_readback")
    elif (
        control.canonical(control.document(after[0].state))
        != control.canonical(control.document(before[0].state))
        or after[0].event_sha256 != before[0].event_sha256
        or _filled_cleanup_control_kind_v12(
            after[0].state, old_sha, old_epoch, after[1][9]
        )
        != kind
    ):
        _deny("controlled_account_cleanup_control_readonly_resolution_changed")
    raw = canonical(
        {
            "schema_version": "ctcc.native_filled_flat_source_cleanup_control_resolution.v2",
            "classification": kind,
            "original_owner_sha256": old_sha,
            "original_owner_epoch": old_epoch,
            "before": _filled_cleanup_control_document_v12(before),
            "after": _filled_cleanup_control_document_v12(after),
            "disarm": _filled_cleanup_control_observation_v12(disarm),
            "release": _filled_cleanup_control_observation_v12(released),
            "account_complete": False,
            "execution_authority": False,
            "order_retry_authority": False,
        }
    )
    if not 0 < len(raw) <= 16384:
        _deny("controlled_account_cleanup_control_resolution_capacity")
    record["terminal_owner_observation"] = owner._observation
    record["disarm"] = disarm
    resolution = (kind, before, after, disarm, released, raw)
    record["control_resolution"] = resolution
    return resolution


def _filled_cleanup_control_metadata_v12(meta):
    if (
        type(meta) is not tuple
        or len(meta) != 10
        or type(meta[0]) is not str
        or not meta[0]
        or type(meta[1]) is not str
        or meta[0] != meta[1]
        or type(meta[2]) is not str
        or meta[2] != "none"
        or type(meta[3]) is not int
        or meta[3] <= 0
        or any(value is not False for value in meta[4:7])
        or type(meta[7]) is not int
        or not 0 < meta[7] < 2**31
        or type(meta[8]) is not str
        or not meta[8].isascii()
        or not meta[8].isdigit()
        or not 0 < len(meta[8]) <= 20
        or int(meta[8]) <= 0
        or type(meta[9]) is not datetime
        or meta[9].tzinfo is not UTC
    ):
        _deny("controlled_account_cleanup_control_metadata_changed")
    return meta


def _filled_cleanup_control_resolution_v12(resolution, controllers):
    # Revalidate the retained historical contract, without new DB IO, an old
    # lease check, a public flag or a callback into a retired source.
    if (
        type(resolution) is not tuple
        or len(resolution) != 6
        or type(resolution[0]) is not str
        or resolution[0] not in ("own_released", "own_expired", "owner_superseded")
        or type(controllers) is not tuple
        or not controllers
        or any(type(saved) is not tuple or len(saved) != 6 for saved in controllers)
        or type(resolution[5]) is not bytes
        or not 0 < len(resolution[5]) <= 16384
    ):
        _deny("controlled_account_cleanup_control_resolution_required")
    kind, before, after, disarm, released, raw = resolution
    old_sha, old_epoch = controllers[0][4:6]
    if (
        type(before) is not tuple
        or len(before) != 4
        or type(after) is not tuple
        or len(after) != 4
        or type(before[0]) is not owner55.original.ControlObservation
        or type(after[0]) is not owner55.original.ControlObservation
        or type(before[1]) is not tuple
        or len(before[1]) != 10
        or type(after[1]) is not tuple
        or len(after[1]) != 10
        or type(before[2]) is not bytes
        or not 0 < len(before[2]) <= 4096
        or type(after[2]) is not bytes
        or not 0 < len(after[2]) <= 4096
        or type(before[3]) is not AsyncSession
        or type(after[3]) is not AsyncSession
        or before[3] is after[3]
    ):
        _deny("controlled_account_cleanup_control_resolution_readbacks_changed")
    _filled_cleanup_control_metadata_v12(before[1])
    _filled_cleanup_control_metadata_v12(after[1])
    if (
        before[1][8] == after[1][8]
        or before[1][:7] != after[1][:7]
        or before[1][9] > after[1][9]
        or before[2] != after[2]
    ):
        _deny("controlled_account_cleanup_control_resolution_readbacks_changed")
    if kind == "own_released":
        if (
            type(disarm) is not owner55.original.ControlObservation
            or type(released) is not owner55.original.ControlObservation
            or _filled_cleanup_control_kind_v12(
                before[0].state, old_sha, old_epoch, before[1][9]
            )
            != "release_required"
            or _filled_cleanup_control_kind_v12(
                after[0].state, old_sha, old_epoch, after[1][9]
            )
            != "own_expired"
            or disarm.state.owner_sha256 != old_sha
            or disarm.state.owner_epoch != old_epoch
            or disarm.state.revision != before[0].state.revision + 1
            or released.state.revision != disarm.state.revision + 1
            or disarm.state.lease_until != before[0].state.lease_until
            or released.state.lease_until != released.state.updated_at
            or disarm.state.arm_request_id is not None
            or disarm.state.arm_expires_at is not None
            or released.state.arm_request_id is not None
            or released.state.arm_expires_at is not None
            or disarm.state.emergency_stop is not before[0].state.emergency_stop
            or released.state.emergency_stop is not disarm.state.emergency_stop
            or released.state.pins != before[0].state.pins
            or control.canonical(control.document(after[0].state))
            != control.canonical(control.document(released.state))
            or after[0].event_sha256 != released.event_sha256
        ):
            _deny("controlled_account_cleanup_control_release_proof_changed")
    elif (
        disarm is not None
        or released is not None
        or _filled_cleanup_control_kind_v12(
            before[0].state, old_sha, old_epoch, before[1][9]
        )
        != kind
        or _filled_cleanup_control_kind_v12(
            after[0].state, old_sha, old_epoch, after[1][9]
        )
        != kind
        or control.canonical(control.document(before[0].state))
        != control.canonical(control.document(after[0].state))
        or before[0].event_sha256 != after[0].event_sha256
    ):
        _deny("controlled_account_cleanup_control_readonly_proof_changed")
    expected = canonical(
        {
            "schema_version": "ctcc.native_filled_flat_source_cleanup_control_resolution.v2",
            "classification": kind,
            "original_owner_sha256": old_sha,
            "original_owner_epoch": old_epoch,
            "before": _filled_cleanup_control_document_v12(before),
            "after": _filled_cleanup_control_document_v12(after),
            "disarm": _filled_cleanup_control_observation_v12(disarm),
            "release": _filled_cleanup_control_observation_v12(released),
            "account_complete": False,
            "execution_authority": False,
            "order_retry_authority": False,
        }
    )
    if expected != raw:
        _deny("controlled_account_cleanup_control_resolution_receipt_changed")
    return raw


async def _prepare_filled_cleanup_body_v6(record):
    try:
        started = record["cleanup_started"]
        last = _filled_cleanup_window_v6(started, started, _stamp_v6())
        remaining = (
            min(
                5_000_000_000 - (last[name] - started[name])
                for name in ("utc_ns", "monotonic_ns")
            )
            / 1_000_000_000
        )
        deadline = asyncio.get_running_loop().time() + remaining
        async with asyncio.timeout_at(deadline):
            _filled_cleanup_check_v6(record)
            resolution = await _resolve_filled_cleanup_control_v12(record)
            _filled_cleanup_control_resolution_v12(resolution, record["controllers"])
            raw = resolution[5]
            record["disarm_receipt"] = raw
            record["reads"][2]["flat_cleanup_receipt"] = raw
            _filled_cleanup_budget_v6(record)
            while True:
                _filled_cleanup_check_v6(record)
                stamp = _filled_cleanup_window_v6(started, last, _stamp_v6())
                last = stamp
                observations = _filled_cleanup_journals_v6(record, observe=True)
                last = _filled_cleanup_window_v6(started, last, _stamp_v6())
                if observations is not None:
                    record["observations"] = observations
                    prepared = object.__new__(_PreparedFilledFlatCleanupV6)
                    record["prepared"], record["phase"] = prepared, "PREPARED"
                    _FILLED_FLAT_CLEANUP_PREPARED_V6[prepared] = record
                    return prepared
                await asyncio.sleep(0.01)
    except BaseException as error:  # noqa: BLE001 -- retain every original owner; preserve cancellation/system exits without private details.
        record["phase"] = "UNKNOWN"
        if isinstance(error, asyncio.CancelledError):
            raise asyncio.CancelledError() from None
        if isinstance(error, (GeneratorExit, KeyboardInterrupt, SystemExit)):
            raise type(error)() from None
        _deny("controlled_account_filled_cleanup_unknown")


def finish_native_filled_flat_source_cleanup_v1(prepared, *, key):
    _filled_cleanup_key_v6(key)
    record = (
        _FILLED_FLAT_CLEANUP_PREPARED_V6.get(prepared)
        if type(prepared) is _PreparedFilledFlatCleanupV6
        else None
    )
    if (
        record is None
        or record["phase"] != "PREPARED"
        or record["prepared"] is not prepared
    ):
        _deny("controlled_account_filled_cleanup_prepared_once_required")
    view = _filled_cleanup_check_v6(record)
    observations = _filled_cleanup_journals_v6(record, observe=True)
    if observations is None or record.get("control_resolution") is None:
        _deny("controlled_account_filled_cleanup_not_confirmed")
    record["observations"] = observations
    completed = object.__new__(_CompletedFilledFlatCleanupV6)
    result = NativeFilledFlatSourceCleanupCompletionViewV1(
        record["settlement_cap"],
        view.source_generations,
        view.entry_transport_binding,
        view.close_transport_binding,
        view.filled_result_view,
        view.closed_transports_view,
        record["closed_handle"],
        record["disarm"],
        record["observations"],
        record["disarm_receipt"],
    )
    record["completed"], record["completion_view"] = completed, result
    record["phase"] = "COMPLETED"
    _FILLED_FLAT_CLEANUP_COMPLETED_V6[completed] = record
    _FILLED_FLAT_CLEANUP_PREPARED_V6.pop(prepared)
    _filled_cleanup_budget_v6(record)
    return completed


def _replay_native_filled_flat_source_cleanup_completion_v1(completed, *, key):
    _filled_cleanup_key_v6(key)
    record = (
        _FILLED_FLAT_CLEANUP_COMPLETED_V6.get(completed)
        if type(completed) is _CompletedFilledFlatCleanupV6
        else None
    )
    if (
        record is None
        or record["phase"] != "COMPLETED"
        or record["completed"] is not completed
    ):
        _deny("controlled_account_filled_cleanup_genuine_completion_required")
    _filled_cleanup_check_v6(record)
    if _filled_cleanup_journals_v6(record, observe=True) is None:
        _deny("controlled_account_filled_cleanup_closed_owners_changed")
    value = record["completion_view"]
    view = record["settlement_view"]
    if (
        type(value) is not NativeFilledFlatSourceCleanupCompletionViewV1
        or value.settlement_cap is not record["settlement_cap"]
        or value.source_generations is not view.source_generations
        or value.entry_transport_binding is not view.entry_transport_binding
        or value.close_transport_binding is not view.close_transport_binding
        or value.filled_result_view is not view.filled_result_view
        or value.closed_transports_view is not view.closed_transports_view
        or value.closed_transports_handle is not record["closed_handle"]
        or value.disarm_observation is not record["disarm"]
        or value.new_get_observations is not record["observations"]
        or value.receipt_json is not record["disarm_receipt"]
        or value.execution_authority is not False
        or value.order_retry_authority is not False
    ):
        _deny("controlled_account_filled_cleanup_completion_changed")
    return value


def retire_native_filled_flat_source_cleanup_v1(completed, *, key):
    from app.exchange.okx import native_demo_filled_settlement_v1 as filled

    post = _filled_cleanup_key_v6(key)
    record = (
        _FILLED_FLAT_CLEANUP_COMPLETED_V6.get(completed)
        if type(completed) is _CompletedFilledFlatCleanupV6
        else None
    )
    if (
        record is None
        or record["phase"] != "COMPLETED"
        or record["completed"] is not completed
    ):
        _deny("controlled_account_filled_cleanup_retirement_once_required")
    view = _filled_cleanup_check_v6(record, after_finalizer=True)
    actual = filled._replay_native_finalized_closed_transports_for_source_v1(
        record["closed_handle"], key=post._ROOT_FILLED_TRANSPORT_SETTLEMENT_KEY_V1
    )
    if (
        type(actual) is not tuple
        or len(actual) != 4
        or actual[0] is not view.closed_transports_view
        or type(actual[1]) is not post.OwnedNativeFilledFlatCleanupCompletionV1
        or type(actual[2]) is not post.NativeFilledFlatCleanupCompletionViewV1
        or actual[2].source_completion is not completed
        or actual[2].filled_result_view is not view.filled_result_view
        or actual[2].closed_transports_handle is not record["closed_handle"]
    ):
        _deny("controlled_account_filled_cleanup_actual_Root_finalizer_required")
    measured = _filled_cleanup_journals_v6(record, observe=True)
    if measured is None:
        _deny("controlled_account_filled_cleanup_closed_owners_changed")
    record["observations"] = measured
    _filled_cleanup_controllers_v6(
        record["controllers"],
        record["terminal_owner_observation"],
        record["control_resolution"],
    )
    groups = tuple(
        {
            "manifest_outcome_id": readset[1],
            "credential_session_sha256": read_record["view"].credential_session_sha256,
            "read_lease_binding_sha256": sha(
                read_record["read_lease_binding_id"].encode("utf-8")
            ),
            "requests": tuple(
                {
                    "role": role,
                    "body_sha256": sha(observation.raw_body),
                    "request_signature_sha256": observation.request_signature_sha256,
                }
                for role, observation in readset[2]
            ),
            "closed_observations": closed,
        }
        for read_record, readset, closed in zip(
            record["reads"], record["readsets"], measured, strict=True
        )
    )
    raw = canonical(
        {
            "schema_version": "ctcc.native_filled_flat_source_cleanup_retirement.v2",
            "control_resolution_receipt_json": record["disarm_receipt"].decode("utf-8"),
            "control_resolution_receipt_sha256": sha(record["disarm_receipt"]),
            "manifest_outcome_ids": tuple(row[1] for row in record["readsets"]),
            "read_groups": groups,
            "original_sdk_closed_contracts": tuple(
                contract[1:] for contract in record["close_contracts"]
            ),
            "Root_finalized_receipt": actual[3],
            "R7_retirement_receipt": record["r7_retirement"][0],
            "account_complete": False,
            "execution_authority": False,
            "order_retry_authority": False,
        }
    )
    if not 0 < len(raw) <= 32768:
        _deny("controlled_account_filled_cleanup_complete_receipt_capacity")
    record["retirement_receipt"] = raw
    record["root_finalized"] = actual
    record["terminal_tokens"] = _filled_cleanup_terminal_tokens_v6(record)
    final_buffers = _filled_cleanup_budget_v6(record)
    # Spend before mutation. Partial failure retains the complete record under
    # RETIRING and never retries retirement or claims a successful close.
    record["phase"] = "RETIRING"
    for state in (record["original"], record["fresh"]):
        # Genuine R7 release already performed this exact retirement. Never
        # repeat a release or require the old healthy source lease here.
        if (
            any(
                value is state or (type(value) is tuple and value and value[0] is state)
                for mapping in (
                    _CURRENT_V6,
                    _COVERAGE_V6,
                    _HISTORY_USES_V6,
                    _ISSUERS_V6,
                    _ISSUED_V6,
                    _HELD_V6,
                )
                for value in mapping.values()
            )
            or _STATES_V6.get(id(state)) is state
        ):
            _deny("controlled_account_filled_cleanup_source_retirement_unknown")
        _UNKNOWN_V6.pop(id(state), None)
        ctrl = state["control"]
        if not any(
            value.get("control") is ctrl
            for mapping in (_STATES_V6, _UNKNOWN_V6)
            for value in mapping.values()
        ):
            owner55._UNKNOWN.pop(id(ctrl), None)
    closing = record["close_transport"]
    _CLOSE_TRANSPORT_USES_V6.pop(id(closing["intent_state"]))
    _CLOSE_TRANSPORT_V6.pop(view.close_transport_binding)
    for read_record in record["reads"]:
        if read_record.get("close_entry_cap") is not None:
            _CLOSE_READ_USES_V6.pop(read_record["close_entry_cap"])
        if read_record.get("close_post_read_cap") is not None:
            _CLOSE_POST_READ_USES_V6.pop(read_record["close_post_read_cap"])
        _RECONCILIATION_READ_V6.pop(read_record["generation"])
    _RECONCILIATION_TRANSPORT_V6.pop(view.entry_transport_binding)
    _TRANSPORT_V6.pop(view.entry_transport_binding)
    retired = object.__new__(_RetiredFilledFlatCleanupV6)
    _FILLED_FLAT_CLEANUP_RETIRED_V6[retired] = (
        record["context"],
        record["key"],
        raw,
        record["terminal_tokens"],
        final_buffers,
        (
            record["controllers"],
            record["terminal_owner_observation"],
            record["control_resolution"],
        ),
    )
    record["phase"] = "RETIRED"
    _FILLED_FLAT_CLEANUP_COMPLETED_V6.pop(completed)
    _FILLED_FLAT_CLEANUP_RECORDS_V6.pop(record["token"])
    # No durable row, accepted journal, claim, outcome, reservation or intent
    # is removed. The exact opaque return is retained only by the genuine post
    # caller; no DATA flag or caller-supplied retirement is accepted.
    return retired


def _filled_cleanup_controllers_v6(controllers, owner_observation, resolution=None):
    _filled_cleanup_control_resolution_v12(resolution, controllers)
    # Historical safety custody only. Never call a healthy-lease check or
    # release callback after the genuine R7 source retirement.
    if type(controllers) is not tuple or not 0 < len(controllers) <= 2:
        _deny("controlled_account_filled_cleanup_retained_facade_required")
    for saved in controllers:
        if type(saved) is not tuple or len(saved) != 6:
            _deny("controlled_account_filled_cleanup_retained_facade_shape")
        ctrl, public_owner, safety, context, owner_sha, epoch = saved
        if (
            type(ctrl) is not dict
            or type(public_owner) is not owner55.OwnedDemoControlSessionV2
            or type(safety) is not owner55.original.DemoControlOwner
            or owner55._HANDLES.get(public_owner) is not ctrl
            or ctrl.get("public_owner") is not public_owner
            or ctrl.get("owner") is not safety
            or ctrl.get("context") is not context
            or ctrl.get("phase") != "RETIRED_SOURCE"
            or owner55._ACTIVE.get(id(ctrl)) is not None
            or safety._arm_intent is not False
            or safety._observation is not owner_observation
            or type(owner_observation) is not owner55.original.ControlObservation
            or type(owner_observation.state) is not control.ControlState
            or owner_observation.state.owner_sha256 != owner_sha
            or owner_observation.state.owner_epoch != epoch
            or owner_observation.state.scope != ctrl["scope"]
            or any(
                state.get("control") is ctrl and state.get("phase") != "RETIRED"
                for mapping in (_STATES_V6, _UNKNOWN_V6)
                for state in mapping.values()
            )
        ):
            _deny("controlled_account_filled_cleanup_retired_facade_changed")


def _filled_cleanup_terminal_tokens_v6(record):
    from app.exchange.okx import native_demo_write_v2 as write

    tokens = []
    for read_record, journals in zip(record["reads"], record["journals"], strict=True):
        private = read_record["private_tokens"]
        if type(private) is not tuple:
            _deny("controlled_account_filled_cleanup_actual_terminal_tokens_required")
        tokens.extend(private)
        for journal in journals:
            if type(journal.signature) is not str:
                _deny("controlled_account_filled_cleanup_actual_get_signature_required")
            tokens.append(journal.signature)
    for binding in (
        record["settlement_view"].entry_transport_binding,
        record["settlement_view"].close_transport_binding,
    ):
        values = write._native_postsubmit_scan_tokens_v1(binding, key=_SCAN_KEY_V6)
        # Both POSTs actually filled in this genuine closed-trade purpose.
        # No never-prepared/absent-signature empty branch is applicable.
        if type(values) is not tuple or len(values) != 1:
            _deny(
                "controlled_account_filled_cleanup_both_actual_post_signatures_required"
            )
        tokens.extend(values)
    if any(
        type(value) is not str or not value or not value.isascii() for value in tokens
    ):
        _deny("controlled_account_filled_cleanup_actual_terminal_tokens_required")
    return tuple(dict.fromkeys(tokens))


def _filled_cleanup_retired_record_v6(retirement, *, key):
    _REQUIRE_SOURCE_KERNEL_V6()
    _filled_cleanup_key_v6(key)
    record = (
        _FILLED_FLAT_CLEANUP_RETIRED_V6.get(retirement)
        if type(retirement) is _RetiredFilledFlatCleanupV6
        else None
    )
    if type(record) is not tuple or len(record) != 6 or record[1] is not key:
        _deny("controlled_account_filled_cleanup_genuine_retirement_required")
    actual, expected = native._context(), record[0]
    if actual[:3] != expected[:3] or actual[3] is not expected[3]():
        _deny("controlled_account_filled_cleanup_retirement_original_task_required")
    if (
        type(record[2]) is not bytes
        or not 0 < len(record[2]) <= 32768
        or type(record[3]) is not tuple
        or not record[3]
        or any(
            type(value) is not str or not value or not value.isascii()
            for value in record[3]
        )
    ):
        _deny("controlled_account_filled_cleanup_retained_terminal_data_changed")
    if type(record[4]) is not tuple or any(
        type(value) not in (bytes, str) for value in record[4]
    ):
        _deny("controlled_account_filled_cleanup_retained_buffer_snapshot_changed")
    if type(record[5]) is not tuple or len(record[5]) != 3:
        _deny("controlled_account_filled_cleanup_retained_facade_missing")
    _filled_cleanup_controllers_v6(record[5][0], record[5][1], record[5][2])
    _retained_budget_v6({"history_tokens": (record[3], record[4])}, extra=(record[2],))
    return record


def _replay_native_filled_flat_source_retirement_v1(retirement, *, key):
    # DATA only from the exact genuine sameTask retirement, never a caller row.
    return _filled_cleanup_retired_record_v6(retirement, key=key)[2]


def _scan_native_filled_cleanup_retained_data_v1(retirement, raw, *, key):
    record = _filled_cleanup_retired_record_v6(retirement, key=key)
    # DB0018 full canonical cleanup row has its own fixed size contract;
    # the existing network response/body policy is unchanged.
    if type(raw) is not bytes or not 0 < len(raw) <= 320000:
        _deny("controlled_account_filled_cleanup_bounded_complete_document_required")
    _retained_budget_v6(
        {"history_tokens": (record[3], record[4])}, extra=(record[2], raw)
    )
    _scan_reconciliation_complete_document_v6(raw, record[3])


def compact_native_filled_cleanup_retirement_v1(retirement, completion, *, key):
    record = _filled_cleanup_retired_record_v6(retirement, key=key)
    post = _filled_cleanup_key_v6(key)
    view = post._take_native_filled_cleanup_committed_for_source_v1(
        completion, key=_FILLED_FLAT_CLEANUP_KEY_V1
    )
    if (
        type(view) is not post.NativeFilledCleanupCommittedForSourceV1
        or view.source_retirement is not retirement
        or type(view.cleanup_outcome_id) is not str
        or len(view.cleanup_outcome_id) != 64
        or any(char not in "0123456789abcdef" for char in view.cleanup_outcome_id)
        or type(view.cleanup_row_json) is not bytes
        or not 0 < len(view.cleanup_row_json) <= 320000
        or type(view.read_metadata) is not tuple
        or len(view.read_metadata) != 10
    ):
        _deny("controlled_account_filled_cleanup_actual_committed_readback_required")
    user, session, role, oid, superuser, createrole, bypass, pid, xid, clock = (
        view.read_metadata
    )
    if (
        type(user) is not str
        or type(session) is not str
        or type(role) is not str
        or not user
        or user != session
        or role != "none"
        or type(oid) is not int
        or oid <= 0
        or type(pid) is not int
        or pid <= 0
        or superuser is not False
        or createrole is not False
        or bypass is not False
        or type(xid) is not str
        or not xid.isascii()
        or not xid.isdigit()
        or not 0 < len(xid) <= 20
        or int(xid) <= 0
        or type(clock) is not datetime
        or clock.tzinfo is not UTC
    ):
        _deny("controlled_account_filled_cleanup_actual_readback_metadata_required")
    # The genuine post producer already checked SAME full Outcome row/capture
    # and separate write/read XIDs. Scan those actual readback bytes once more
    # against the full terminal union before removing any private token DATA.
    _scan_native_filled_cleanup_retained_data_v1(
        retirement, view.cleanup_row_json, key=key
    )
    tombstone = (
        "ctcc.native_filled_cleanup_source_compacted.v1",
        sha(record[2]),
        view.cleanup_outcome_id,
        sha(view.cleanup_row_json),
    )
    _filled_cleanup_controllers_v6(record[5][0], record[5][1], record[5][2])
    for ctrl, public_owner, _safety, _context, _sha, _epoch in record[5][0]:
        for mapping, identity in (
            (owner55._HANDLES, public_owner),
            (owner55._OWNERS, public_owner),
            (owner55._ACTIVE, id(ctrl)),
            (owner55._UNKNOWN, id(ctrl)),
        ):
            actual = mapping.get(identity)
            if actual is not None and actual is not ctrl:
                _deny("controlled_account_filled_cleanup_facade_registry_changed")
    # All validation precedes this synchronous exact-identity compaction.
    # Failed persistence never reaches here and retains the complete handle.
    for ctrl, public_owner, _safety, _context, _sha, _epoch in record[5][0]:
        for mapping, identity in (
            (owner55._HANDLES, public_owner),
            (owner55._OWNERS, public_owner),
            (owner55._ACTIVE, id(ctrl)),
            (owner55._UNKNOWN, id(ctrl)),
        ):
            if mapping.get(identity) is ctrl:
                mapping.pop(identity)
        ctrl["phase"] = "FILLED_FLAT_COMPACTED"
    # Only already compacted, fully retired DATA tombstones may leave this
    # bounded ring. Unknown/full records are never evicted or treated empty.
    if len(_FILLED_FLAT_CLEANUP_TERMINAL_V6) >= _MAX_SOURCES_V6:
        oldest = next(iter(_FILLED_FLAT_CLEANUP_TERMINAL_V6))
        _FILLED_FLAT_CLEANUP_TERMINAL_V6.pop(oldest)
    _FILLED_FLAT_CLEANUP_TERMINAL_V6[retirement] = tombstone
    _FILLED_FLAT_CLEANUP_RETIRED_V6.pop(retirement)
    return tombstone


_ZERO_FINAL_MARKER_V2 = object()
_ZERO_FINAL_CLEANUP_KEY_V2 = object()
_ZERO_FINAL_PREPARED_V2 = {}
_ZERO_FINAL_RETIRED_V2 = {}
_ZERO_FINAL_TERMINAL_V2 = {}


class _PreparedZeroFinalCleanupV2(_OpaqueV6):
    __slots__ = ()


class _RetiredZeroFinalCleanupV2(_OpaqueV6):
    __slots__ = ()


def _zero_final_budget_v2(record):
    from app.exchange.okx import native_demo_write_v2 as write

    roots = [
        record["captured_source_buffers"],
        _reconciliation_retained_buffers_v6(record["read_record"]),
    ]
    roots.append(
        write._native_source_retained_buffers_v1(
            record["settlement_view"].transport_binding, key=_SCAN_KEY_V6
        )
    )
    roots.extend(
        record.get(name)
        for name in ("disarm_receipt", "retirement_receipt", "terminal_tokens")
    )
    roots.extend(
        canonical(_filled_cleanup_control_document_v12(row))
        for row in record["control_readbacks"]
    )
    pending = record["control_read_pending"]
    if pending:
        roots.append(canonical(control.document(pending[0])))
        if len(pending) == 3:
            roots.append(pending[2])
    return _close_write_buffer_bytes_v6(tuple(roots), preserve_text=True)


def _zero_final_check_v2(record, *, after_finalizer=False):
    from app.exchange.okx import native_reconciliation_observation_v1 as read

    _REQUIRE_SOURCE_KERNEL_V6()
    if (
        type(record) is not dict
        or _FLAT_CLEANUP_RECORDS_V6.get(record.get("token")) is not record
        or record.get("zero_final_marker") is not _ZERO_FINAL_MARKER_V2
        or record["phase"] not in ("PREPARING", "PREPARED", "COMPLETED")
    ):
        _deny("controlled_account_zero_final_genuine_operation_required")
    actual, expected = native._context(), record["context"]
    if actual[:3] != expected[:3] or actual[3] is not expected[3]():
        _deny("controlled_account_zero_final_original_task_required")
    post = _flat_cleanup_key_v6(record["key"])
    if after_finalizer:
        view = post._replay_native_flat_source_after_finalization_v1(
            record["settlement_cap"], key=_FLAT_CLEANUP_KEY_V1
        )
        proof = post._replay_native_zero_r7_retirement_for_source_v2(
            record["settlement_cap"], key=_ZERO_FINAL_CLEANUP_KEY_V2
        )
        if proof is not record.get("r7_retirement"):
            if record.get("r7_retirement") is not None:
                _deny("controlled_account_zero_final_own_retirement_changed")
            record["r7_retirement"] = proof
        for state in (record["original"], record["fresh"]):
            if (
                _STATES_V6.get(id(state)) is not None
                or state.get("phase") != "RETIRED"
                or type(state.get("current")) is not dict
                or state["current"]
                or any(
                    value is state
                    or (type(value) is tuple and value and value[0] is state)
                    for mapping in (
                        _CURRENT_V6,
                        _COVERAGE_V6,
                        _HISTORY_USES_V6,
                        _ISSUERS_V6,
                        _ISSUED_V6,
                        _HELD_V6,
                    )
                    for value in mapping.values()
                )
            ):
                _deny("controlled_account_zero_final_own_R7_effects_changed")
    else:
        view = _flat_cleanup_check_v6(record)
    read_record, transport = record["read_record"], record["transport"]
    if (
        view is not record["settlement_view"]
        or _RECONCILIATION_READ_V6.get(view.source_generation) is not read_record
        or _TRANSPORT_V6.get(view.transport_binding) is not transport
        or _RECONCILIATION_TRANSPORT_V6.get(view.transport_binding)
        is not view.source_generation
        or read_record["source_state"] is not record["fresh"]
        or read_record["transport"] is not view.transport_binding
        or read_record["control_owner"] is not transport["owner"]
        or transport["factory"] is not record["original"]["control"]["factory"]
        or read_record["writer_factory"] is not transport["factory"]
        or read_record["read_lease"] is None
        or read_record["read_lease"]._lease.active is not False
    ):
        _deny("controlled_account_zero_final_held_source_relation_changed")
    sets = post._replay_native_zero_source_readset_v2(
        record["settlement_cap"], key=_ZERO_FINAL_CLEANUP_KEY_V2
    )
    if sets != record["readset"] or sets[0] is not view.source_generation:
        _deny("controlled_account_zero_final_committed_readset_changed")
    journals = vars(read_record["client"]).get(
        "_native_reconciliation_read_journals_v1"
    )
    if (
        type(journals) is not list
        or len(journals) != len(sets[2])
        or any(type(journal) is not read.ReadJournalV1 for journal in journals)
        or any(
            actual is not saved
            for actual, saved in zip(journals, record["journals"], strict=True)
        )
        or any(
            type(journal.observation) is not read.DemoReconciliationObservationV1
            or journal.observation != row[1]
            or journal.observation is not original
            for journal, row, original in zip(
                journals, sets[2], record["journal_observations"], strict=True
            )
        )
    ):
        _deny("controlled_account_zero_final_actual_journals_changed")
    if record.get("control_resolution") is not None:
        raw = _filled_cleanup_control_resolution_v12(
            record["control_resolution"], record["controllers"]
        )
        if (
            record.get("disarm_receipt") is not None
            and raw is not record["disarm_receipt"]
        ):
            _deny("controlled_account_zero_final_control_resolution_changed")
    _zero_final_budget_v2(record)
    return view


def prepare_native_zero_final_source_cleanup_v2(cap, *, key):
    from app.exchange.okx import native_reconciliation_observation_v1 as read

    post = _flat_cleanup_key_v6(key)
    _REQUIRE_SOURCE_KERNEL_V6()
    if len(_FLAT_CLEANUP_RECORDS_V6) + len(_ZERO_FINAL_RETIRED_V2) >= _MAX_SOURCES_V6:
        _deny("controlled_account_zero_final_capacity")
    started = _stamp_v6()
    view = post._take_native_flat_source_settlement_v1(cap, key=_FLAT_CLEANUP_KEY_V1)
    if type(view) is not post.NativeFlatSourceSettlementForAccountV1:
        _deny("controlled_account_zero_final_genuine_cap_required")
    read_record, transport = (
        _RECONCILIATION_READ_V6.get(view.source_generation),
        _TRANSPORT_V6.get(view.transport_binding),
    )
    if (
        type(read_record) is not dict
        or type(transport) is not dict
        or read_record["source_state"] is not transport["state"]
        or read_record["transport"] is not view.transport_binding
    ):
        _deny("controlled_account_zero_final_original_source_required")
    fresh, owner = transport["state"], transport["owner"]
    if type(owner) is not owner55.original.DemoControlOwner:
        _deny("controlled_account_zero_final_actual_owner_required")
    # Local revoke alone never changes the current database owner's epoch.
    owner55.original.DemoControlOwner._revoke(owner, stop=False)
    original, controllers = fresh["original_state"], []
    for state in (original, fresh):
        ctrl = state["control"]
        public_owner = ctrl["public_owner"]
        if (
            type(public_owner) is not owner55.OwnedDemoControlSessionV2
            or owner55._HANDLES.get(public_owner) is not ctrl
            or ctrl["owner"] is not owner
            or ctrl["scope"] != transport["scope"]
        ):
            _deny("controlled_account_zero_final_actual_facade_required")
        if not any(saved[0] is ctrl for saved in controllers):
            controllers.append(
                (
                    ctrl,
                    public_owner,
                    owner,
                    ctrl["context"],
                    ctrl["observation"].state.owner_sha256,
                    ctrl["observation"].state.owner_epoch,
                )
            )
    sets = post._replay_native_zero_source_readset_v2(
        cap, key=_ZERO_FINAL_CLEANUP_KEY_V2
    )
    journals = vars(read_record["client"]).get(
        "_native_reconciliation_read_journals_v1"
    )
    if (
        type(journals) is not list
        or len(journals) != 19
        or any(type(journal) is not read.ReadJournalV1 for journal in journals)
    ):
        _deny("controlled_account_zero_final_complete_actual_journals_required")
    if any(
        type(journal.signature) is not str
        or not journal.signature
        or not journal.signature.isascii()
        for journal in journals
    ):
        _deny("controlled_account_zero_final_actual_signatures_required")
    record = {
        "token": object(),
        "context": _context_v6(),
        "key": key,
        "settlement_cap": cap,
        "settlement_view": view,
        "read_record": read_record,
        "transport": transport,
        "fresh": fresh,
        "original": original,
        "scope": transport["scope"],
        "closed_handle": view.closed_transport_view.closed_transport_handle,
        "phase": "PREPARING",
        "zero_final_marker": _ZERO_FINAL_MARKER_V2,
        "close_contracts": tuple(
            _flat_original_close_contract_v6(state) for state in (original, fresh)
        ),
        "cleanup_started": started,
        "controllers": tuple(controllers),
        "readset": sets,
        "journals": tuple(journals),
        "journal_observations": tuple(journal.observation for journal in journals),
        "negative_factory_pin": history._factory_pin(original["control"]["factory"]),
        "control_readbacks": [],
        "control_read_pending": (),
        "disarm_receipt": None,
        "disarm": None,
        "observations": None,
        "prepared": None,
        "completed": None,
        "captured_source_buffers": (
            *_filled_cleanup_original_buffers_v6(original),
            *_filled_cleanup_original_buffers_v6(fresh),
        ),
        "terminal_tokens": tuple(
            dict.fromkeys(
                (
                    *read_record["private_tokens"],
                    *(journal.signature for journal in journals),
                )
            )
        ),
    }
    _FLAT_CLEANUP_RECORDS_V6[record["token"]] = record
    try:
        _zero_final_check_v2(record)
        operation = _AcquisitionAttemptV6(
            record,
            _prepare_zero_final_body_v2(record),
            _retire=_retain_flat_cleanup_unknown_v6,
        )
        record["operation"] = operation
        return operation
    except BaseException:
        record["phase"] = "UNKNOWN"
        raise


async def _prepare_zero_final_body_v2(record):
    try:
        started, last = record["cleanup_started"], _stamp_v6()
        _filled_cleanup_window_v6(started, started, last)
        remaining = (
            min(
                5_000_000_000 - (last[name] - started[name])
                for name in ("utc_ns", "monotonic_ns")
            )
            / 1e9
        )
        async with asyncio.timeout_at(asyncio.get_running_loop().time() + remaining):
            resolution = await _resolve_filled_cleanup_control_v12(record)
            record["disarm_receipt"] = _filled_cleanup_control_resolution_v12(
                resolution, record["controllers"]
            )
            record["read_record"]["flat_cleanup_receipt"] = record["disarm_receipt"]
            while True:
                _zero_final_check_v2(record)
                last = _filled_cleanup_window_v6(started, last, _stamp_v6())
                observations = _flat_cleanup_new_gets_v6(record["read_record"])
                if observations is not None:
                    record["observations"] = observations
                    prepared = object.__new__(_PreparedZeroFinalCleanupV2)
                    record["prepared"], record["phase"] = prepared, "PREPARED"
                    _ZERO_FINAL_PREPARED_V2[prepared] = record
                    return prepared
                await asyncio.sleep(0.01)
    except BaseException:
        record["phase"] = "UNKNOWN"
        raise


def finish_native_zero_final_source_cleanup_v2(prepared, *, key):
    _flat_cleanup_key_v6(key)
    record = (
        _ZERO_FINAL_PREPARED_V2.get(prepared)
        if type(prepared) is _PreparedZeroFinalCleanupV2
        else None
    )
    if (
        record is None
        or record["phase"] != "PREPARED"
        or record["prepared"] is not prepared
    ):
        _deny("controlled_account_zero_final_genuine_prepared_required")
    view = _zero_final_check_v2(record)
    observed = _flat_cleanup_new_gets_v6(record["read_record"])
    if observed is None or observed != record["observations"]:
        _deny("controlled_account_zero_final_actual_closed_gets_changed")
    completed = object.__new__(_CompletedFlatCleanupV6)
    record["completed"], record["phase"] = completed, "COMPLETED"
    record["completion_view"] = NativeFlatSourceCleanupCompletionViewV1(
        record["settlement_cap"],
        view.source_generation,
        view.transport_binding,
        view.flat_result_view,
        record["closed_handle"],
        record["disarm"],
        observed,
        record["disarm_receipt"],
    )
    _FLAT_CLEANUP_COMPLETED_V6[completed] = record
    _ZERO_FINAL_PREPARED_V2.pop(prepared)
    return completed


def _replay_native_zero_final_source_cleanup_completion_v2(completed, *, key):
    _flat_cleanup_key_v6(key)
    record = (
        _FLAT_CLEANUP_COMPLETED_V6.get(completed)
        if type(completed) is _CompletedFlatCleanupV6
        else None
    )
    if (
        record is None
        or record["completed"] is not completed
        or record["phase"] != "COMPLETED"
    ):
        _deny("controlled_account_zero_final_genuine_completion_required")
    _zero_final_check_v2(record)
    if _flat_cleanup_new_gets_v6(record["read_record"]) != record["observations"]:
        _deny("controlled_account_zero_final_closed_gets_changed")
    return record["completion_view"]


def retire_native_zero_final_source_cleanup_v2(completed, *, key):
    post = _flat_cleanup_key_v6(key)
    record = (
        _FLAT_CLEANUP_COMPLETED_V6.get(completed)
        if type(completed) is _CompletedFlatCleanupV6
        else None
    )
    if (
        record is None
        or record["completed"] is not completed
        or record["phase"] != "COMPLETED"
    ):
        _deny("controlled_account_zero_final_retirement_once_required")
    view = _zero_final_check_v2(record, after_finalizer=True)
    measured = _flat_cleanup_new_gets_v6(record["read_record"])
    if measured is None or measured != record["observations"]:
        _deny("controlled_account_zero_final_closed_gets_changed")
    _filled_cleanup_controllers_v6(
        record["controllers"],
        record["terminal_owner_observation"],
        record["control_resolution"],
    )
    raw = canonical(
        {
            "schema_version": "ctcc.native_zero_final_source_cleanup_retirement.v2",
            "closure_outcome_id": record["readset"][1],
            "control_resolution_receipt_json": record["disarm_receipt"].decode(),
            "control_resolution_receipt_sha256": sha(record["disarm_receipt"]),
            "original_sdk_closed_contracts": tuple(
                contract[1:] for contract in record["close_contracts"]
            ),
            "credential_session_sha256": record["read_record"][
                "credential_session_sha256"
            ],
            "read_lease_binding_sha256": sha(
                record["read_record"]["read_lease_binding_id"].encode()
            ),
            "requests": tuple(
                {
                    "role": role,
                    "body_sha256": sha(page.raw_body),
                    "request_signature_sha256": page.request_signature_sha256,
                }
                for role, page in record["readset"][2]
            ),
            "closed_observations": measured,
            "Root_finalized_receipt": post._replay_native_zero_root_finalization_v2(
                record["settlement_cap"], key=_ZERO_FINAL_CLEANUP_KEY_V2
            ),
            "R7_retirement_receipt": record["r7_retirement"],
            "execution_authority": False,
            "order_retry_authority": False,
        }
    )
    if not 0 < len(raw) <= 32768:
        _deny("controlled_account_zero_final_receipt_bound")
    record["retirement_receipt"] = raw
    buffers = _zero_final_budget_v2(record)
    record["phase"] = "RETIRING"
    for state in (record["original"], record["fresh"]):
        actual = _UNKNOWN_V6.get(id(state))
        if actual is not None and actual is not state:
            _deny("controlled_account_zero_final_unknown_identity_changed")
    for state in (record["original"], record["fresh"]):
        _UNKNOWN_V6.pop(id(state), None)
    _TRANSPORT_V6.pop(view.transport_binding)
    _RECONCILIATION_READ_V6.pop(view.source_generation)
    _RECONCILIATION_TRANSPORT_V6.pop(view.transport_binding)
    retired = object.__new__(_RetiredZeroFinalCleanupV2)
    _ZERO_FINAL_RETIRED_V2[retired] = (
        record["context"],
        raw,
        record["terminal_tokens"],
        buffers,
        record["controllers"],
        record["terminal_owner_observation"],
        record["control_resolution"],
        (
            record["transport"]["factory"],
            record["negative_factory_pin"],
            native._database_connection_pin_v4(record["transport"]["factory"]),
        ),
        [],
    )
    record["phase"] = "RETIRED"
    _FLAT_CLEANUP_COMPLETED_V6.pop(completed)
    _FLAT_CLEANUP_RECORDS_V6.pop(record["token"])
    return retired


def _zero_final_retired_v2(retirement, key):
    _flat_cleanup_key_v6(key)
    _REQUIRE_SOURCE_KERNEL_V6()
    record = (
        _ZERO_FINAL_RETIRED_V2.get(retirement)
        if type(retirement) is _RetiredZeroFinalCleanupV2
        else None
    )
    if type(record) is not tuple or len(record) != 9:
        _deny("controlled_account_zero_final_genuine_retirement_required")
    actual, expected = native._context(), record[0]
    if actual[:3] != expected[:3] or actual[3] is not expected[3]():
        _deny("controlled_account_zero_final_original_task_required")
    if (
        type(record[8]) is not list
        or len(record[8]) > 256
        or any(type(raw) is not bytes for raw in record[8])
    ):
        _deny("controlled_account_zero_final_retained_documents_changed")
    _close_write_buffer_bytes_v6(
        (record[1], record[2], record[3], record[8]), preserve_text=True
    )
    _filled_cleanup_controllers_v6(record[4], record[5], record[6])
    factory, pin, route = record[7]
    if (
        any(ctrl["factory"] is not factory for ctrl, *_ in record[4])
        or history._factory_pin(factory) != pin
        or native._database_connection_pin_v4(factory) != route
    ):
        _deny("controlled_account_zero_final_retired_factory_changed")
    return record


def _replay_native_zero_final_source_retirement_v2(retirement, *, key):
    return _zero_final_retired_v2(retirement, key)[1]


def _scan_native_zero_final_retained_data_v2(retirement, raw, *, key):
    record = _zero_final_retired_v2(retirement, key)
    if type(raw) is not bytes or not 0 < len(raw) <= 320000:
        _deny("controlled_account_zero_final_complete_data_bound")
    _close_write_buffer_bytes_v6(
        (record[1], record[2], record[3], record[8], raw), preserve_text=True
    )
    _scan_reconciliation_complete_document_v6(raw, record[2])
    if not any(value is raw for value in record[8]):
        if len(record[8]) >= 256:
            _deny("controlled_account_zero_final_retained_documents_bound")
        record[8].append(raw)


def compact_native_zero_final_source_retirement_v2(retirement, committed, *, key):
    record = _zero_final_retired_v2(retirement, key)
    post = _flat_cleanup_key_v6(key)
    proof = post._take_native_zero_cleanup_committed_for_source_v2(
        committed, key=_ZERO_FINAL_CLEANUP_KEY_V2
    )
    if (
        type(proof) is not post.NativeZeroCleanupCommittedForSourceV2
        or proof.source_retirement is not retirement
        or type(proof.cleanup_row_json) is not bytes
        or not 0 < len(proof.cleanup_row_json) <= 320000
        or type(proof.cleanup_outcome_id) is not str
        or len(proof.cleanup_outcome_id) != 64
        or any(char not in "0123456789abcdef" for char in proof.cleanup_outcome_id)
    ):
        _deny("controlled_account_zero_final_actual_db_readback_required")
    _filled_cleanup_control_metadata_v12(proof.read_metadata)
    _scan_native_zero_final_retained_data_v2(
        retirement, proof.cleanup_row_json, key=key
    )
    for ctrl, public, _owner, _context, _sha, _epoch in record[4]:
        for mapping, identity in (
            (owner55._HANDLES, public),
            (owner55._OWNERS, public),
            (owner55._ACTIVE, id(ctrl)),
            (owner55._UNKNOWN, id(ctrl)),
        ):
            actual = mapping.get(identity)
            if actual is not None and actual is not ctrl:
                _deny("controlled_account_zero_final_facade_identity_changed")
    for ctrl, public, _owner, _context, _sha, _epoch in record[4]:
        for mapping, identity in (
            (owner55._HANDLES, public),
            (owner55._OWNERS, public),
            (owner55._ACTIVE, id(ctrl)),
            (owner55._UNKNOWN, id(ctrl)),
        ):
            if mapping.get(identity) is ctrl:
                mapping.pop(identity)
        ctrl["phase"] = "ZERO_FLAT_COMPACTED"
    result = (
        "ctcc.native_zero_final_source_compacted.v2",
        sha(record[1]),
        proof.cleanup_outcome_id,
        sha(proof.cleanup_row_json),
    )
    if len(_ZERO_FINAL_TERMINAL_V2) >= _MAX_SOURCES_V6:
        _ZERO_FINAL_TERMINAL_V2.pop(next(iter(_ZERO_FINAL_TERMINAL_V2)))
    _ZERO_FINAL_TERMINAL_V2[retirement] = result
    _ZERO_FINAL_RETIRED_V2.pop(retirement)
    return result


def _REQUIRE_SOURCE_KERNEL_V6():
    return _NATIVE_GUARD()


def _source_bootstrap_marker_v6():
    return None


def _install_source_kernel_v6():
    import sys

    global _FLAT_CONFIGURATION_SOURCE_V1
    from app.trade_qualification import (
        native_flat_leverage_account_source_v1 as flat_configuration,
    )

    flat_configuration._SOURCE_MODULE_V1 = sys.modules[__name__]
    _FLAT_CONFIGURATION_SOURCE_V1 = flat_configuration
    from app.database.repositories import (
        native_zero_fok_event_schema_v55 as zero_fok_event55,
    )
    from app.demo_automation import native_server_selectors_v2 as selector
    from app.exchange.okx import native_demo_filled_settlement_v1 as filled_settlement55
    from app.exchange.okx import native_demo_settlement_v1 as settlement55
    from app.exchange.okx import native_demo_source_identity_v2 as descriptor
    from app.exchange.okx import native_demo_write_v2 as write
    from app.exchange.okx import (
        native_reconciliation_network_custody_v1 as network_custody55,
    )
    from app.exchange.okx import (
        native_reconciliation_observation_v1 as reconciliation_read,
    )
    from app.exchange.okx import private_rest
    from app.trade_evidence import storage as evidence_storage
    from app.trade_qualification import native_close_final_facts_v1 as close_final_facts
    from app.trade_qualification import native_continuation_guard_v1 as source_guard
    from app.trade_qualification import native_controlled_close_v1 as controlled_close
    from app.trade_qualification import native_ledger_runtime_v2 as ledger_runtime
    from app.trade_qualification import (
        native_leverage_configuration_v1 as configuration_actor,
    )
    from app.trade_qualification import native_original_local_current55_v2 as original
    from app.trade_qualification import native_postpublication_recheck_v2 as r7
    from app.trade_qualification import native_postpublication_support_v2 as measured
    from app.trade_qualification import (
        native_postsubmit_reconciliation_v1 as postsubmit,
    )
    from app.trade_qualification import owned_native_candidate_v1 as candidate

    positive = (
        "acquire_owned_controlled_account_v6",
        "_synchronize_control_operator_mirror_v6",
        "_check_control_operator_mirror_budget_v6",
        "_consume_controlled_history_use_v6",
        "_replay_controlled_coverage_context_v6",
        "_replay_controlled_current_mirror_context_v6",
        "_take_owned_account_issuer_v1",
        "_replay_owned_account_issuer_v1",
        "_finish_owned_account_issuer_v1",
        "_take_issued_account_for_g11_v1",
        "_replay_issued_account_for_g11_v1",
        "_take_issued_account_for_r7_v2",
        "_replay_issued_account_for_r7_v2",
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
        "bind_native_close_transport_v1",
        "_replay_native_close_transport_v1",
        "_replay_issued_demo_reconciliation_context_v2",
        "_acquire_native_recovery_read_source_v1",
        "_replay_native_recovery_read_source_v1",
        "acquire_native_close_read_source_v1",
        "_replay_native_close_read_source_v1",
        "acquire_native_close_post_read_source_v1",
        "_replay_native_close_post_read_source_v1",
        "scan_native_reconciliation_private_data_v1",
        "require_issued_original_revision_locked_v2",
        "require_issued_account_revision_locked_v1",
        "acquire_postpublication_issued_account_v1",
        "_replay_current_for_native_lineage_v6",
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
        "prepare_native_filled_flat_source_cleanup_v1",
        "finish_native_filled_flat_source_cleanup_v1",
        "_replay_native_filled_flat_source_cleanup_completion_v1",
        "retire_native_filled_flat_source_cleanup_v1",
        "_replay_native_filled_flat_source_retirement_v1",
        "_scan_native_filled_cleanup_retained_data_v1",
        "compact_native_filled_cleanup_retirement_v1",
        "_REQUIRE_SOURCE_KERNEL_V6",
        "_source_bootstrap_marker_v6",
    )
    # The existing compiled-source guard checks original defining namespaces,
    # code/defaults/closures, generated model methods, registry identities and
    # actual source bytes. It guards each coroutine resume, not merely entry.
    # No source hash, callback table or guard function is accepted by this API.
    guard = source_guard.install_native_guards_v1(
        sys.modules[__name__],
        positive,
        dependencies=(
            flat_configuration,
            flat_configuration.planner,
            configuration_actor,
            configuration_actor.repository,
            configuration_actor.repository.schema,
            configuration_actor.contracts,
            native,
            owner55,
            owner55.original,
            owner55.runtime,
            reader55,
            reader55.native,
            reader55.stream,
            reader55.records,
            partition_repository,
            partition_repository.original,
            partition_repository.policy,
            partition_repository.controlled,
            partition_policy,
            issuer55,
            issuer55.issuer_transaction,
            issuer55.issuer_transaction.locked,
            issuer55.issuer_transaction.writer56,
            issuer55.issuer_transaction.writer56.inherited,
            repository56,
            control56,
            frame56,
            frame56.roles56,
            frame56.roles55,
            frame56.pair56,
            frame56.partition_policy,
            frame56.frame_policy,
            witness56,
            lineage55,
            catalog55,
            catalog55.frame_policy,
            catalog55.parent_policy,
            catalog55.policy,
            catalog55.policy.old54,
            catalog55.policy.old53,
            catalog55.policy.operator,
            catalog55.policy.native_submission,
            catalog55.policy.native_closure,
            catalog55.policy.native_close_intent,
            catalog55.policy.native_close_index,
            zero_fok_event55,
            zero_fok_event55.schema,
            zero_fok_event55.live_schema,
            catalog55.policy.native_terminal.filled,
            catalog55.policy.native_closure.zero,
            catalog55.policy.native_terminal,
            catalog55.policy.native_report,
            catalog55.policy.native_report.schema,
            catalog55.policy.native_report.helpers,
            operator55,
            current,
            current.native,
            current.operational,
            current.proof,
            current.storage,
            current.runtime,
            current.runtime.collector,
            capture,
            history,
            history.bootstrap,
            history.current_bundle,
            history.stage_api,
            history.collector,
            history.journal,
            history.lifecycle,
            history.positions,
            history.position_pages,
            history._json_lexical_guard,
            history_bundle,
            codec,
            observed,
            position_path,
            bank,
            bank.financial,
            financial,
            hwm,
            execution_mode,
            bound,
            coverage55,
            mirror55,
            mirror55.checkpoint,
            mirror55.checkpoint.locked,
            mirror55.checkpoint.locked.writer56,
            custody,
            control,
            counter,
            portfolio,
            reservations,
            configured,
            native_clock,
            clock_contract,
            selector,
            descriptor,
            write,
            write._native_vendors,
            private_rest,
            reconciliation_read,
            network_custody55,
            settlement55,
            filled_settlement55,
            postsubmit,
            controlled_close,
            close_final_facts,
            close_final_facts.preflight,
            close_final_facts.facts,
            close_final_facts.facts.wire,
            evidence_storage,
            ledger_runtime,
            original,
            r7,
            measured,
            candidate,
            candidate._dynamic_risk,
            candidate._dynamic_risk.risk_profile,
            candidate._dynamic_risk.structural_risk,
            candidate._dynamic_risk.mathematics,
            candidate._dynamic_risk.derivatives,
            candidate._dynamic_risk.risk_config,
            candidate._dynamic_risk.financial,
            candidate._dynamic_risk.financial.configured,
            candidate._dynamic_risk.financial.portfolio,
            candidate._dynamic_risk.hard,
            candidate._dynamic_risk.claims_codec,
            candidate._dynamic_risk.protection,
            candidate._dynamic_risk.candidate_models,
            r7._dynamic_risk,
            r7._dynamic_risk.risk_profile,
            r7._dynamic_risk.structural_risk,
            r7._dynamic_risk.mathematics,
            r7._dynamic_risk.derivatives,
            r7._dynamic_risk.risk_config,
            r7._dynamic_risk.financial,
            r7._dynamic_risk.financial.configured,
            r7._dynamic_risk.financial.portfolio,
            r7._dynamic_risk.hard,
            r7._dynamic_risk.claims_codec,
            r7._dynamic_risk.protection,
            r7._dynamic_risk.candidate_models,
        ),
        bindings=(
            (native.sys, "modules", native.sys.modules),
            *write._VENDOR_ADMISSION_V2.bindings,
        ),
    )
    # Capture the genuine installed functions once into the parent private
    # purpose bridge. Later sys.modules aliases cannot select another callee.
    native._bind_controlled_financial_source_v6(_SOURCE_KEY_V6)
    return guard


_NATIVE_GUARD = _install_source_kernel_v6()
