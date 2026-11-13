"""Configured native Demo Control owner for the explicit current55 path.

Preparation acquires a disarmed durable owner. Arm/clear-stop are separate
operator operations; no source acquisition invokes either operation. A Control
DTO, memory token, caller factory/clock or observed armed flag is never an input.
"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from weakref import ref

from app.config import settings as configured
from app.database.repositories import account_capture_journal as journal_repository
from app.database.repositories import account_observation_index as indexed
from app.database.repositories import (
    account_revision_collector_lineage as legacy_lineage,
)
from app.database.repositories import (
    account_revision_collector_lineage_current55_v1 as lineage_repository,
)
from app.database.repositories import (
    collector_current_0055_multirole_catalog_v1 as catalog55,
)
from app.database.repositories import demo_control as historical_repository
from app.database.repositories import demo_control_current55_v2 as repository_v2
from app.database.repositories import demo_control_current56_v1 as repository56
from app.database.repositories import (
    demo_operator_acknowledgements_current55_v1 as operator_repository,
)
from app.database.repositories import public_runtime_bundle_custody as custody
from app.domain import native_clock
from app.domain.source_primitives import canonical, decode, sha, utc_from_ns
from app.exchange.okx.private_rest import (
    DemoPinnedReadSession,
    OkxDemoPrivateRestClient,
)
from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_current_source_v4 as current
from app.trade_qualification import account_history_source_v1 as history
from app.trade_qualification import account_native_runtime as runtime
from app.trade_qualification import account_observation_index as observed
from app.trade_qualification import account_original_balance_measurement_v1 as measured
from app.trade_qualification import account_owned_financial_v1 as financial
from app.trade_qualification import control_bound_ledger as bound
from app.trade_qualification import (
    controlled_writer_coverage_v1 as coverage,  # noqa: F401 - captured by the source kernel's alias-name inventory.
)
from app.trade_qualification import demo_control as control
from app.trade_qualification import demo_control_runtime as original
from app.trade_qualification import native_account_risk_assembly_v1 as native
from app.trade_qualification.reservations import LedgerScope

MAX_OWNERS = 16
_SOURCE_KEY = object()
_OWNERS = {}
_ACTIVE = {}
_UNKNOWN = {}
_HANDLES = {}


class NativeDemoControlError(ValueError):
    """Closed codes; owner tokens, UID and SQL/credential values stay private."""


def _deny(code):
    raise NativeDemoControlError(code) from None


def _raise_fixed(error, code):
    if isinstance(error, asyncio.CancelledError):
        raise asyncio.CancelledError() from None
    if isinstance(error, (KeyboardInterrupt, SystemExit)):
        raise type(error)() from None
    if type(error) is NativeDemoControlError:
        raise error from None
    _deny(code)


class OwnedDemoControlSessionV2:
    __slots__ = ("__weakref__",)

    def __new__(cls):
        _deny("native_demo_control_not_constructible")

    @property
    def execution_authority(self):
        return False

    async def request_arm(self, *, expires_at):
        """Explicit caller operation, never called by prepare/acquire/clear."""
        return await _operation(self, "request_arm", expires_at=expires_at)

    def acquire_controlled_account(self, *, roots):
        """Explicit already-Armed purpose from this server-selected identity."""
        from app.trade_qualification import native_controlled_account_source_v6

        native_controlled_account_source_v6._REQUIRE_SOURCE_KERNEL_V6()
        state = _OWNERS.get(self) if type(self) is OwnedDemoControlSessionV2 else None
        if state is None:
            _deny("native_demo_control_original_selected_owner_required")
        selected = _selected_record_v3(state)
        if selected is None:
            _deny("native_demo_control_genuine_server_selector_required")
        return native.acquire_owned_controlled_account_v6(
            self,
            scope_config=selected.scope_config,
            frame_session_factory=selected.raw_reader_factory,
            roots=roots,
        )

    async def renew(self):
        """A new operator invocation only; an active source cannot extend."""
        return await _operation(self, "renew")

    def disarm(self):
        return _tightening(self, stop=False)

    def emergency_stop(self):
        return _tightening(self, stop=True)

    async def release(self):
        return await _operation(self, "release")

    def initialize_policy_counter(
        self, *, confirmation, private_proof_root, native_proof_root
    ):
        """One explicit operator command; the source is acquired internally."""
        return native.perform_owned_control_operator_v1(
            self,
            purpose="policy_counter_initialize",
            confirmation=confirmation,
            private_proof_root=private_proof_root,
            native_proof_root=native_proof_root,
        )

    def clear_stop(self, *, confirmation, private_proof_root, native_proof_root):
        """Fresh flat proof plus one acknowledgement; result stays disarmed."""
        return native.perform_owned_control_operator_v1(
            self,
            purpose="clear_stop",
            confirmation=confirmation,
            private_proof_root=private_proof_root,
            native_proof_root=native_proof_root,
        )


@dataclass(frozen=True, slots=True, repr=False)
class _MeasuredGenesisV1:
    scope: LedgerScope
    window_started_at: datetime
    first_batch_sha256: str
    first_batch_document: bytes = field(repr=False)
    original_measurement: bytes = field(repr=False)

    @property
    def source_sha256(self):
        return sha(
            canonical(
                {
                    "schema_version": "ctcc.native_control.original_measured_genesis.v1",
                    "scope": [
                        self.scope.environment,
                        self.scope.account_id,
                        self.scope.settlement_currency,
                    ],
                    "window_started_at": self.window_started_at.isoformat(),
                    "first_batch_sha256": self.first_batch_sha256,
                    "first_batch_document_sha256": sha(self.first_batch_document),
                    "original_measurement_sha256": sha(self.original_measurement),
                }
            )
        )


def _stamp():
    value = native_clock.native_stamp()
    if (
        type(value) is not dict
        or set(value) != {"utc_ns", "monotonic_ns"}
        or any(type(item) is not int or item <= 0 for item in value.values())
    ):
        _deny("native_demo_control_native_clock_invalid")
    return value


def _now():
    return utc_from_ns(_stamp()["utc_ns"])


def _mono():
    return _stamp()["monotonic_ns"] / 1_000_000_000


def _context():
    context = history._context()
    return (*context[:3], ref(context[3]))


def _selected_record_v3(state):
    if state.get("identity_selection") is None:
        return None
    from app.demo_automation import native_server_selectors_v2 as selector
    from app.trade_qualification import (
        native_controlled_account_source_v6 as controlled_source,
    )

    controlled_source._REQUIRE_SOURCE_KERNEL_V6()
    if state.get("db0056_roles") is None:
        value = selector._replay_configured_demo_identity_selection_v2(
            state["identity_selection"], key=_SOURCE_KEY
        )
    else:
        value, factories, roles = selector._controlled_identity_0056(
            state["identity_selection"]
        )
        if (
            factories is not state["db0056_factories"]
            or roles is not state["db0056_roles"]
            or type(state["repository"]) is not repository56.DemoControlRepository0056V1
        ):
            _deny("native_demo_control_original_db0056_selection_changed")
    if (
        type(value) is not selector.ConfiguredDemoIdentityRecordV2
        or value != state["identity_record"]
        or value.read_session is not state["read_session"]
        or value.private_client is not state["private_client"]
        or value.writer_factory is not state["factory"]
        or value.custody_factory is not state["custody_original_factory"]
        or capture.plan_sha256(value.current_plan) != state["plan_sha256"]
    ):
        _deny("native_demo_control_original_server_selection_changed")
    return value


def _require_context(state, *, released=False):
    _REQUIRE_KERNEL()
    context = history._context()
    expected = state["context"]
    if context[:3] != expected[:3] or context[3] is not expected[3]():
        _deny("native_demo_control_original_context_changed")
    state["read_session"].verify_current()
    _selected_record_v3(state)
    if (
        configured.get_settings() is not state["settings"]
        or state["private_client"].settings is not state["settings"]
        or native._v3_data_pin(state["settings"].__dict__) != state["settings_pin"]
    ):
        _deny("native_demo_control_actual_configured_settings_changed")
    if (
        state["read_session"]._owner is not state["private_client"]
        or capture.plan_sha256(state["plan"]) != state["plan_sha256"]
        or not runtime._configured_factory(state["factory"])
        or history._factory_pin(state["factory"]) != state["factory_pin"]
        or native._package_inventory_controlled55_v1() != state["inventory"]
        or history._factory_pin(state["custody_original_factory"])
        != state["custody_original_pin"]
        or history._factory_pin(state["custody_factory"])
        != state["custody_factory_pin"]
        or native._database_connection_pin_v4(state["factory"])
        != state["database_routes"][0]
        or native._database_connection_pin_v4(state["custody_factory"])
        != state["database_routes"][1]
        or type(state["custody_repository"])
        is not custody.RuntimeBundleCustodyRepository
        or state["custody_repository"]._factory is not state["custody_factory"]
    ):
        _deny("native_demo_control_original_source_changed")
    financial._require_same_configured_policy_v1(state["configured_policy"])
    owner = state["owner"]
    if (
        type(owner) is not original.DemoControlOwner
        or owner.repository is not state["repository"]
        or state["repository"].session_factory is not state["factory"]
        or state["repository"].clock is not _NOW
        or owner.scope != state["scope"]
        or owner.pins != state["pins"]
        or owner.clock is not _NOW
        or owner.monotonic is not _MONO
        or owner._owner_token is not state["owner_token"]
        or owner._owner_sha256 != state["owner_sha256"]
        or (owner._poisoned and not released)
    ):
        _deny("native_demo_control_runtime_owner_changed")
    return owner


async def _read_genesis(factory, scope):
    # Read the actual original sequence-one source, not a caller timestamp,
    # the newest folder or the first row of an incomplete history collection.
    repository = indexed.AccountObservationIndexRepository(factory, clock=_NOW)
    first = await repository.read(scope, after=0, limit=1)
    if (
        type(first) is not tuple
        or len(first) != 1
        or type(first[0]) is not indexed.ObservationBatchReadback
        or first[0].sequence != 1
    ):
        _deny("native_demo_control_original_measured_genesis_missing")
    row = first[0]
    document = decode(row.document_json, measured.MAX_MEASUREMENT_BYTES)
    if document.get("sequence") != 1 or document.get("previous_sha256") is not None:
        _deny("native_demo_control_original_measured_genesis_changed")
    reference = observed.CaptureReference(**document["reference"])
    chain = await repository.journal.read_chain(scope, reference.capture_id)
    raw = measured.measure_original_balance_v1(
        chain, reference=reference, scope=scope, checked_at=_now()
    )
    value = measured._read(raw)
    return _MeasuredGenesisV1(
        scope,
        control.utc(datetime.fromisoformat(value["body_completed_at"])),
        row.event_sha256,
        bytes(row.document_json),
        bytes(raw),
    )


async def _actual_observation(state):
    owner = _require_context(state)
    result = await state["repository"].read(state["scope"])
    _require_context(state)
    if type(result) is not historical_repository.ControlObservation:
        _deny("native_demo_control_actual_observation_type")
    result.__post_init__()
    previous = state["observation"]
    if (
        result.state != previous.state
        or result.event_sha256 != previous.event_sha256
        or result.observed_at < previous.observed_at
        or owner._observation is not previous
    ):
        _deny("native_demo_control_actual_revision_or_journal_changed")
    return result


async def _prepare_configured_demo_control_owner_v2(
    *,
    read_session,
    private_client,
    current_account_plan,
    custody_session_factory,
    _identity_selection=None,
    _identity_record=None,
    _db0056=None,
):
    """Create the real configured owner; acquire is disarmed and does not clear."""
    from app.database.session import AsyncSessionFactory

    _REQUIRE_KERNEL()
    if (
        len(_OWNERS) + len(_ACTIVE) + len(_UNKNOWN) >= MAX_OWNERS
        or len(_HANDLES) >= MAX_OWNERS
        or type(read_session) is not DemoPinnedReadSession
        or type(private_client) is not OkxDemoPrivateRestClient
        or read_session._owner is not private_client
        or private_client.settings is not configured.get_settings()
        or read_session._settings is not configured.get_settings()
        or type(current_account_plan) is not capture.CurrentDemoAccountCapturePlanV7
        or not runtime._configured_factory(AsyncSessionFactory)
    ):
        _deny("native_demo_control_original_inputs_required")
    read_session.verify_current()
    plan = capture._checked_plan(
        current_account_plan, capture.plan_sha256(current_account_plan)
    )
    if (
        plan.environment != "demo"
        or plan.settlement_currency != "USDT"
        or plan.session_binding_id != read_session.binding_id
        or plan.origin != read_session._origin
    ):
        _deny("native_demo_control_original_session_or_scope_changed")
    scope = LedgerScope(account_id=plan.expected_uid, settlement_currency="USDT")
    custody_pin, owned_custody_factory, owned_custody_pin = history._clone_factory(
        custody_session_factory
    )
    writer_route = native._database_connection_pin_v4(AsyncSessionFactory)
    custody_route = native._database_connection_pin_v4(owned_custody_factory)
    if writer_route[1] != custody_route[1]:
        _deny("native_demo_control_same_database_route_required")
    custody_repository = custody.RuntimeBundleCustodyRepository(owned_custody_factory)
    db0056_roles = db0056_factories = None
    if _db0056 is None:
        repository = repository_v2.DemoControlRepositoryV2(
            AsyncSessionFactory, clock=_NOW
        )
        await repository.verify_native_deployment_v2()
        read_session.verify_current()
        genesis = await _read_genesis(AsyncSessionFactory, scope)
        read_session.verify_current()
        configured_policy = financial.capture_configured_owned_risk_policy_v1(
            drawdown_window_started_at=genesis.window_started_at
        )
        inventory = native._package_inventory_controlled55_v1()
        pins = control.ControlPins(
            config_sha256=sha(
                canonical(
                    {
                        "schema_version": "ctcc.native_demo_control.config.v2",
                        "plan_sha256": capture.plan_sha256(plan),
                        "scope": [
                            scope.environment,
                            scope.account_id,
                            scope.settlement_currency,
                        ],
                        "measured_genesis_sha256": genesis.source_sha256,
                        "configured_policy_sha256": configured_policy.selected_settings_sha256,
                        "writer_inventory_sha256": inventory[0]["writer_sha256"],
                    }
                )
            ),
            policy_sha256=configured_policy.selected_settings_sha256,
            credential_session_sha256=sha(read_session.binding_id.encode("ascii")),
        )
    else:
        from app.demo_automation import native_server_selectors_v2 as selector
        from app.trade_qualification import (
            native_demo_control_prepare_0056_v1 as prepare56,
        )

        record, db0056_factories, db0056_roles = selector._controlled_identity_0056(
            _identity_selection
        )
        if (
            record is not _identity_record
            or db0056_factories.writer_factory is not AsyncSessionFactory
            or db0056_factories.custody_factory is not custody_session_factory
            or type(_db0056) is not tuple
            or len(_db0056) != 2
            or _db0056[0] is not db0056_factories
            or _db0056[1] is not db0056_roles
        ):
            _deny("native_demo_control_original_db0056_selection_changed")
        control_scope = control.ControlScope("demo", scope.account_id)
        reader = repository56.DemoControlRepository0056V1(
            db0056_factories, roles=db0056_roles, scope=control_scope, clock=_NOW
        )
        before = await reader.verify_native_deployment_v2()
        read_session.verify_current()
        genesis = await _read_genesis(AsyncSessionFactory, scope)
        read_session.verify_current()
        configured_policy = financial.capture_configured_owned_risk_policy_v1(
            drawdown_window_started_at=genesis.window_started_at
        )
        inventory = native._package_inventory_controlled55_v1()
        pins = prepare56._control_pins(
            record, db0056_roles, scope, genesis, configured_policy, inventory
        )
        repository = repository56.DemoControlRepository0056V1(
            db0056_factories,
            roles=db0056_roles,
            scope=control_scope,
            clock=_NOW,
            expected_acquire_event=before.event_sha256,
        )
    owner = original.DemoControlOwner(
        repository,
        control.ControlScope("demo", scope.account_id),
        pins,
        clock=_NOW,
        monotonic=_MONO,
    )
    state = {
        "context": _context(),
        "read_session": read_session,
        "private_client": private_client,
        "plan": plan,
        "identity_selection": _identity_selection,
        "identity_record": _identity_record,
        "db0056_roles": db0056_roles,
        "db0056_factories": db0056_factories,
        "settings": configured.get_settings(),
        "settings_pin": native._v3_data_pin(configured.get_settings().__dict__),
        "plan_sha256": capture.plan_sha256(plan),
        "factory": AsyncSessionFactory,
        "factory_pin": history._factory_pin(AsyncSessionFactory),
        "custody_original_factory": custody_session_factory,
        "custody_original_pin": custody_pin,
        "custody_factory": owned_custody_factory,
        "custody_factory_pin": owned_custody_pin,
        "custody_repository": custody_repository,
        "database_routes": (writer_route, custody_route),
        "configured_policy": configured_policy,
        "genesis": genesis,
        "inventory": inventory,
        "scope": owner.scope,
        "ledger_scope": scope,
        "pins": pins,
        "repository": repository,
        "owner": owner,
        "owner_token": owner._owner_token,
        "owner_sha256": owner.owner_sha256,
        "phase": "PREPARING",
        "observation": None,
    }
    result = object.__new__(OwnedDemoControlSessionV2)
    state["public_owner"] = result
    _OWNERS[result] = state
    _HANDLES[result] = state
    try:
        _require_context(state)
        observation = await owner.start()
        _require_context(state)
        if observation.state.arm_request_id is not None:
            _deny("native_demo_control_preparation_must_be_disarmed")
        state["observation"] = observation
        state["phase"] = "READY_DISARMED"
        await _actual_observation(state)
        return result
    except BaseException as error:  # noqa: BLE001 -- Retain unknown state; preserve cancellation without private error text.
        _retain(state)
        _OWNERS.pop(result, None)
        _raise_fixed(error, "native_demo_control_preparation_failed")


async def prepare_selected_demo_control_owner_v3(selection):
    """Server registry only; no caller UID, plan, factory, routing or truth DTO."""
    from app.demo_automation import native_server_selectors_v2 as selector
    from app.trade_qualification import (
        native_controlled_account_source_v6 as controlled_source,
    )

    controlled_source._REQUIRE_SOURCE_KERNEL_V6()
    selected = selector._replay_configured_demo_identity_selection_v2(
        selection, key=_SOURCE_KEY
    )
    if type(selected) is not selector.ConfiguredDemoIdentityRecordV2:
        _deny("native_demo_control_genuine_server_selector_required")
    try:
        async with asyncio.timeout(30):
            return await _prepare_configured_demo_control_owner_v2(
                read_session=selected.read_session,
                private_client=selected.private_client,
                current_account_plan=selected.current_plan,
                custody_session_factory=selected.custody_factory,
                _identity_selection=selection,
                _identity_record=selected,
            )
    except BaseException as error:  # noqa: BLE001 -- retain original ownership and fail closed on unknown/cancellation.
        _raise_fixed(error, "native_demo_control_preparation_failed")


async def prepare_selected_demo_control_owner_0056(selection):
    """Acquire a disarmed Control owner from the genuine configured selection."""
    from app.demo_automation import native_server_selectors_v2 as selector
    from app.trade_qualification import native_controlled_account_source_v6 as source

    source._REQUIRE_SOURCE_KERNEL_V6()
    selected, factories, roles = selector._controlled_identity_0056(selection)
    if type(selected) is not selector.ConfiguredDemoIdentityRecordV2:
        _deny("native_demo_control_genuine_server_selector_required")
    try:
        async with asyncio.timeout(30):
            return await _prepare_configured_demo_control_owner_v2(
                read_session=selected.read_session,
                private_client=selected.private_client,
                current_account_plan=selected.current_plan,
                custody_session_factory=selected.custody_factory,
                _identity_selection=selection,
                _identity_record=selected,
                _db0056=(factories, roles),
            )
    except BaseException as error:  # noqa: BLE001 -- fixed refusal for the configured owner.
        _raise_fixed(error, "native_demo_control_preparation_failed")


async def prepare_configured_demo_control_owner_v2(
    *, read_session, private_client, current_account_plan, custody_session_factory
):
    # Preparation has no active lease yet. It is bounded independently and
    # returns only after the real new disarmed owner and full readback exist.
    try:
        async with asyncio.timeout(30):
            return await _prepare_configured_demo_control_owner_v2(
                read_session=read_session,
                private_client=private_client,
                current_account_plan=current_account_plan,
                custody_session_factory=custody_session_factory,
            )
    except BaseException as error:  # noqa: BLE001 -- Retain unknown state; preserve cancellation without private error text.
        _raise_fixed(error, "native_demo_control_preparation_failed")


async def _operation_impl(cap, action, *, expires_at=None):
    state = _OWNERS.get(cap) if type(cap) is OwnedDemoControlSessionV2 else None
    if state is None or state["phase"] not in ("READY_DISARMED", "READY_ARMED"):
        _deny("native_demo_control_operator_owner_unavailable")
    owner = _require_context(state)
    if action not in ("request_arm", "renew", "disarm", "emergency_stop", "release"):
        _deny("native_demo_control_operator_action_unknown")
    await _actual_observation(state)
    _require_context(state)
    state["phase"] = "OPERATOR_OPERATION"
    try:
        if action == "request_arm":
            expires_at = control.utc(expires_at)
            now = _now()
            if (
                not now
                < expires_at
                <= min(
                    state["observation"].state.lease_until, now + timedelta(seconds=30)
                )
            ):
                _deny("native_demo_control_original_arm_expiry")
            observation = await _REQUEST_ARM(owner, expires_at=expires_at)
        else:
            observation = await _OPERATIONS[action](owner)
        _require_context(state, released=action == "release")
        state["observation"] = observation
        state["phase"] = (
            "READY_ARMED"
            if observation.state.arm_intent_current(_now())
            else "READY_DISARMED"
        )
        if action != "release":
            await _actual_observation(state)
        else:
            _OWNERS.pop(cap, None)
            _HANDLES.pop(cap, None)
        return observation
    except BaseException as error:  # noqa: BLE001 -- Retain unknown state; preserve cancellation without private error text.
        _OWNERS.pop(cap, None)
        _retain(state)
        _raise_fixed(error, "native_demo_control_operator_operation_failed")


async def _operation(cap, action, *, expires_at=None):
    state = _OWNERS.get(cap) if type(cap) is OwnedDemoControlSessionV2 else None
    if state is None:
        _deny("native_demo_control_operator_owner_unavailable")
    try:
        async with asyncio.timeout(_remaining_control(state)):
            return await _operation_impl(cap, action, expires_at=expires_at)
    except BaseException as error:  # noqa: BLE001 -- Retain unknown state; preserve cancellation without private error text.
        _OWNERS.pop(cap, None)
        _retain(state)
        _raise_fixed(error, "native_demo_control_operator_operation_failed")


def _tightening(cap, *, stop):
    """Revoke synchronously even during an ACTIVE source or an unknown result.

    Safety tightening deliberately does not require an unexpired private GET
    lease or the source task. It cannot mint a source, renew, clear or Arm.
    """
    _REQUIRE_KERNEL()
    state = _HANDLES.get(cap) if type(cap) is OwnedDemoControlSessionV2 else None
    if state is None or type(stop) is not bool:
        _deny("native_demo_control_safety_handle_unavailable")
    owner = state["owner"]
    if (
        type(owner) is not original.DemoControlOwner
        or owner.repository is not state["repository"]
        or owner.scope != state["scope"]
        or owner._owner_token is not state["owner_token"]
        or not runtime._configured_factory(state["factory"])
    ):
        _deny("native_demo_control_safety_owner_changed")
    # The original runtime synchronously increments its local revocation
    # generation before any mutex/database await in this exact method.
    owner._revoke(stop=stop)
    state["phase"] = "REVOKING"
    _OWNERS.pop(cap, None)
    method = _ESTOP if stop else _DISARM
    return _await_tightening(state, method(owner), stop=stop)


async def _await_tightening(state, operation, *, stop):
    try:
        async with asyncio.timeout(5):
            result = await operation
        result.__post_init__()
        if (
            result.state.scope != state["scope"]
            or result.state.arm_request_id is not None
            or (stop and result.state.emergency_stop is not True)
        ):
            _deny("native_demo_control_safety_readback_changed")
        state["observation"] = result
        state["phase"] = "STOPPED" if stop else "DISARMED"
        return result
    except BaseException as error:  # noqa: BLE001 -- Retain unknown state; preserve cancellation without private error text.
        _retain(state)
        _raise_fixed(error, "native_demo_control_source_or_safety_failure")


def _retain(state):
    # Strong tombstones are never replaced by cleanup or a retry. No DB write,
    # Arm, clear-stop or automatic new request occurs during failure handling.
    state["owner"].revoke_now()
    _ACTIVE.pop(id(state), None)
    state["phase"] = "UNKNOWN"
    _UNKNOWN.setdefault(id(state), state)


def _controlled_key(key):
    if key is not native._CONTROLLED_SOURCE_KEY_V1:
        _deny("native_demo_control_fixed_acquisition_purpose_required")


def _take_controlled_acquisition_v1(cap, *, key):
    _controlled_key(key)
    state = _OWNERS.pop(cap, None) if type(cap) is OwnedDemoControlSessionV2 else None
    if state is None:
        _deny("native_demo_control_operator_owner_unavailable")
    try:
        owner = _require_context(state)
        observation = state["observation"]
        stamp = _stamp()
        now = utc_from_ns(stamp["utc_ns"])
        if (
            state["phase"] != "READY_ARMED"
            or _selected_record_v3(state) is None
            or owner._observation is not observation
            or owner._arm_intent is not True
            or owner._local_estop is not False
            or not observation.state.arm_intent_current(now)
            or owner._lease_monotonic_deadline is None
            or owner._arm_monotonic_deadline is None
        ):
            _deny("native_demo_control_genuine_current_arm_required")
        until = min(observation.state.lease_until, observation.state.arm_expires_at)
        deadline_ns = min(
            int(owner._lease_monotonic_deadline * 1_000_000_000),
            int(owner._arm_monotonic_deadline * 1_000_000_000),
            stamp["monotonic_ns"] + int((until - now).total_seconds() * 1_000_000_000),
        )
        if deadline_ns <= stamp["monotonic_ns"]:
            _deny("native_demo_control_original_arm_expired")
        state["generation"] = owner._generation
        state["expires_at"] = until
        state["monotonic_deadline_ns"] = deadline_ns
        state["phase"] = "ACTIVE_ACQUISITION"
        state["expectation"] = bound.ControlExpectation(
            state["scope"],
            state["owner_token"],
            observation.state.owner_epoch,
            observation.state.revision,
            observation.event_sha256,
            state["pins"],
        )
        _ACTIVE[id(state)] = state
        return state
    except BaseException:
        _retain(state)
        raise


def _check_controlled_acquisition_v1(state, *, key):
    _controlled_key(key)
    if _ACTIVE.get(id(state)) is not state:
        _deny("native_demo_control_source_unavailable")
    owner = _require_context(state)
    stamp = _stamp()
    now = utc_from_ns(stamp["utc_ns"])
    if (
        state["phase"] != "ACTIVE_ACQUISITION"
        or owner._generation != state["generation"]
        or owner._arm_intent is not True
        or owner._local_estop is not False
        or owner._observation is not state["observation"]
        or not state["observation"].state.arm_intent_current(now)
        or now >= state["expires_at"]
        or stamp["monotonic_ns"] >= state["monotonic_deadline_ns"]
    ):
        _deny("native_demo_control_original_arm_revoked_or_expired")
    return stamp


async def _replay_controlled_acquisition_v1(state, *, key):
    _check_controlled_acquisition_v1(state, key=key)
    observation = await _actual_observation(state)
    _check_controlled_acquisition_v1(state, key=key)
    genesis = await _read_genesis(state["factory"], state["ledger_scope"])
    _check_controlled_acquisition_v1(state, key=key)
    if genesis != state["genesis"]:
        _deny("native_demo_control_immutable_measured_genesis_changed")
    return observation


def _release_controlled_acquisition_v1(state, *, key, uncertain=False):
    _controlled_key(key)
    if type(uncertain) is not bool:
        _deny("native_demo_control_release_shape_invalid")
    if _ACTIVE.pop(id(state), None) is state:
        if uncertain:
            _retain(state)
        else:
            state["owner"].revoke_now()
            state["phase"] = "RELEASED"
            _HANDLES.pop(state["public_owner"], None)
            state.clear()


def _remaining_control(state):
    owner = _require_context(state)
    stamp = _stamp()
    observation = state["observation"]
    if (
        owner._observation is not observation
        or owner._lease_monotonic_deadline is None
        or observation.state.lease_until is None
    ):
        _deny("native_demo_control_original_lease_missing")
    remaining = min(
        (observation.state.lease_until - utc_from_ns(stamp["utc_ns"])).total_seconds(),
        owner._lease_monotonic_deadline - stamp["monotonic_ns"] / 1_000_000_000,
    )
    if remaining <= 0:
        _deny("native_demo_control_original_lease_expired")
    return remaining


def _take_operator_control_v1(cap, *, key):
    _controlled_key(key)
    state = _OWNERS.pop(cap, None) if type(cap) is OwnedDemoControlSessionV2 else None
    if state is None:
        _deny("native_demo_control_operator_owner_unavailable")
    try:
        owner = _require_context(state)
        observation = state["observation"]
        remaining = _remaining_control(state)
        stamp = _stamp()
        if (
            state["phase"] != "READY_DISARMED"
            or observation.state.emergency_stop is not True
            or observation.state.arm_request_id is not None
            or observation.state.arm_expires_at is not None
            or owner._arm_intent is not False
            or owner._local_estop is not True
        ):
            _deny("native_demo_control_stopped_disarmed_owner_required")
        state["operator_generation"] = owner._generation
        state["operator_expires_at"] = observation.state.lease_until
        state["operator_monotonic_deadline_ns"] = min(
            int(owner._lease_monotonic_deadline * 1_000_000_000),
            stamp["monotonic_ns"] + int(remaining * 1_000_000_000),
        )
        state["phase"] = "OPERATOR_ACQUISITION"
        _ACTIVE[id(state)] = state
        return state
    except BaseException:
        _retain(state)
        raise


def _check_operator_control_v1(state, *, key):
    _controlled_key(key)
    if _ACTIVE.get(id(state)) is not state:
        _deny("native_demo_control_operator_source_unavailable")
    owner = _require_context(state)
    stamp = _stamp()
    if (
        state["phase"] != "OPERATOR_ACQUISITION"
        or owner._generation != state["operator_generation"]
        or owner._observation is not state["observation"]
        or owner._arm_intent is not False
        or owner._local_estop is not True
        or state["observation"].state.emergency_stop is not True
        or state["observation"].state.arm_request_id is not None
        or utc_from_ns(stamp["utc_ns"]) >= state["operator_expires_at"]
        or stamp["monotonic_ns"] >= state["operator_monotonic_deadline_ns"]
    ):
        _deny("native_demo_control_original_operator_source_changed")
    return stamp


def _finish_operator_control_v1(state, observation, *, purpose, key):
    _check_operator_control_v1(state, key=key)
    if type(observation) is not historical_repository.ControlObservation:
        _deny("native_demo_control_operator_actual_readback_required")
    observation.__post_init__()
    before = state["observation"]
    owner = state["owner"]
    expected_revision = before.state.revision + (1 if purpose == "clear_stop" else 0)
    if (
        purpose not in ("clear_stop", "policy_counter_initialize")
        or observation.state.scope != before.state.scope
        or observation.state.revision != expected_revision
        or observation.state.owner_sha256 != before.state.owner_sha256
        or observation.state.owner_epoch != before.state.owner_epoch
        or observation.state.lease_until != before.state.lease_until
        or observation.state.pins != before.state.pins
        or observation.state.arm_request_id is not None
        or observation.state.arm_expires_at is not None
        or observation.state.emergency_stop is not (purpose != "clear_stop")
        or (
            purpose == "policy_counter_initialize"
            and (
                observation.state != before.state
                or observation.event_sha256 != before.event_sha256
            )
        )
    ):
        _deny("native_demo_control_operator_readback_changed")
    with owner._local:
        _check_operator_control_v1(state, key=key)
        now, _ = owner._time()
        if (
            observation.observed_at > now
            or observation.observed_at < before.observed_at
        ):
            _deny("native_demo_control_operator_readback_clock_changed")
        owner._observation = observation
        owner._arm_intent = False
        owner._arm_monotonic_deadline = None
        # Only the genuine, separately read-back clear operation clears this
        # local latch. Its original lease deadline is deliberately unchanged.
        owner._local_estop = purpose != "clear_stop"
    state["observation"] = observation
    state["phase"] = "READY_DISARMED"
    _ACTIVE.pop(id(state), None)
    _OWNERS[state["public_owner"]] = state


def _retain_operator_control_v1(state, *, key):
    _controlled_key(key)
    _OWNERS.pop(state["public_owner"], None)
    _retain(state)


_NOW = _now
_MONO = _mono
_REQUEST_ARM = original.DemoControlOwner.request_arm
_DISARM = original.DemoControlOwner.disarm
_ESTOP = original.DemoControlOwner.emergency_stop
_OPERATIONS = {
    "renew": original.DemoControlOwner.renew,
    "disarm": original.DemoControlOwner.disarm,
    "emergency_stop": original.DemoControlOwner.emergency_stop,
    "release": original.DemoControlOwner.release,
}


def _make_kernel():
    namespace = globals()
    callable_pin = native._v3_callable_pin
    module_pin = native._api_asof_module_pin_v3
    aliases = tuple(
        (name, namespace[name])
        for name in (
            "configured",
            "indexed",
            "repository_v2",
            "repository56",
            "historical_repository",
            "operator_repository",
            "lineage_repository",
            "legacy_lineage",
            "journal_repository",
            "catalog55",
            "current",
            "custody",
            "native_clock",
            "capture",
            "history",
            "runtime",
            "observed",
            "measured",
            "financial",
            "bound",
            "coverage",
            "control",
            "original",
            "native",
        )
    )
    functions = tuple(
        (owner, name, callable_pin(getattr(owner, name)))
        for owner, names in (
            (
                original.DemoControlOwner,
                (
                    "__init__",
                    "_perform",
                    "_time",
                    "start",
                    "request_arm",
                    "renew",
                    "disarm",
                    "emergency_stop",
                    "release",
                    "_tighten",
                    "revoke_now",
                    "_revoke",
                ),
            ),
            (
                financial,
                (
                    "capture_configured_owned_risk_policy_v1",
                    "_require_same_configured_policy_v1",
                ),
            ),
            (indexed.AccountObservationIndexRepository, ("__init__", "read")),
            (history, ("_context", "_factory_pin", "_clone_factory")),
            (capture, ("_checked_plan", "plan_sha256")),
            (runtime, ("_configured_factory",)),
            (
                measured,
                ("measure_original_balance_v1", "_measure_verified_packet_v1", "_read"),
            ),
            (native.ControlOperatorRootsV1, ("__init__", "__post_init__")),
            (native._ControlOperatorReplayV1, ("__init__",)),
            (
                native,
                (
                    "perform_owned_control_operator_v1",
                    "_replay_controlled_current_lineage_v1",
                    "_take_owned_control_operator_source_v1",
                    "_replay_owned_control_operator_source_v1",
                    "_finish_owned_control_operator_source_v1",
                    "_retain_unknown_control_operator_source_v1",
                    "_database_connection_pin_v4",
                    "_control_operator_modules_v1",
                    "_control_operator_registry_v1",
                    "_control_operator_check_v1",
                    "_control_operator_remaining_v1",
                    "_acquire_control_operator_v1",
                    "_actual_control_operator_deployment_v1",
                    "_package_inventory_controlled55_v1",
                ),
            ),
        )
        for name in names
    )
    static_modules = tuple(
        (module, module_pin(module))
        for module in (
            configured,
            indexed,
            measured,
            financial,
            repository_v2,
            repository56,
            historical_repository,
            operator_repository,
            lineage_repository,
            legacy_lineage,
            journal_repository,
            lineage_repository.policy,
            lineage_repository.journal,
            catalog55,
            catalog55.policy,
            catalog55.policy.old54,
            catalog55.policy.old53,
            catalog55.policy.issued,
            catalog55.policy.lineage,
            catalog55.policy.operator,
            catalog55.frame_policy,
            catalog55.parent_policy,
            current,
            custody,
            native_clock,
            control,
            original,
            bound,
        )
    )
    facade_functions = tuple(
        (name, callable_pin(value))
        for name, value in OwnedDemoControlSessionV2.__dict__.items()
        if hasattr(value, "__code__") or type(value) is property
    )
    storage = (_OWNERS, _ACTIVE, _UNKNOWN, _HANDLES)
    native_aliases = tuple(
        (name, getattr(native, name))
        for name in (
            "current",
            "coverage",
            "capture",
            "ControlledDemoAccountSession",
            "asyncio",
            "native_clock",
            "canonical",
            "sha",
            "utc_from_ns",
            "ref",
            "Path",
            "_v3_callable_pin",
            "_api_asof_module_pin_v3",
            "_v3_data_pin",
            "_V3FunctionType",
            "_V3MethodType",
            "_deny",
            "_context",
            "os",
            "sys",
            "get_ident",
        )
    )
    native_storage = (
        native._CONTROL_OPERATOR_ACTIVE_V1,
        native._CONTROL_OPERATOR_UNKNOWN_V1,
        native._CONTROL_OPERATOR_STORAGE_V1,
        native._CONTROLLED_SOURCE_KEY_V1,
        native._CONTROLLED_SOURCE_PATHS_V1,
        native.ControlOperatorRootsV1,
        native._ControlOperatorReplayV1,
        native._OwnedControlOperatorSourceV1,
    )
    operation_items = tuple(_OPERATIONS.items())
    clock_and_safety = (_NOW, _MONO, _REQUEST_ARM, _DISARM, _ESTOP)
    own_functions = tuple(
        (name, callable_pin(value))
        for name, value in tuple(namespace.items())
        if callable(value)
        and getattr(value, "__module__", None) == __name__
        and hasattr(value, "__code__")
        and name != "_make_kernel"
    )

    def require():
        if (
            globals() is not namespace
            or namespace["_OWNERS"] is not storage[0]
            or namespace["_ACTIVE"] is not storage[1]
            or namespace["_UNKNOWN"] is not storage[2]
            or namespace["_HANDLES"] is not storage[3]
            or tuple(namespace["_OPERATIONS"].items()) != operation_items
            or (
                namespace["_NOW"],
                namespace["_MONO"],
                namespace["_REQUEST_ARM"],
                namespace["_DISARM"],
                namespace["_ESTOP"],
            )
            != clock_and_safety
            or native._CONTROL_OPERATOR_ACTIVE_V1 is not native_storage[0]
            or native._CONTROL_OPERATOR_UNKNOWN_V1 is not native_storage[1]
            or native._CONTROL_OPERATOR_STORAGE_V1 is not native_storage[2]
            or native._CONTROLLED_SOURCE_KEY_V1 is not native_storage[3]
            or native._CONTROLLED_SOURCE_PATHS_V1 is not native_storage[4]
            or native.ControlOperatorRootsV1 is not native_storage[5]
            or native._ControlOperatorReplayV1 is not native_storage[6]
            or native._OwnedControlOperatorSourceV1 is not native_storage[7]
            or namespace["MAX_OWNERS"] != 16
            or any(namespace[name] is not owner for name, owner in aliases)
            or any(getattr(native, name) is not owner for name, owner in native_aliases)
            or native._v3_callable_pin is not callable_pin
            or native._api_asof_module_pin_v3 is not module_pin
        ):
            _deny("native_demo_control_namespace_changed")
        for module, expected in static_modules:
            if module_pin(module) != expected:
                _deny("native_demo_control_static_dependency_changed")
        for name, expected in facade_functions:
            if callable_pin(OwnedDemoControlSessionV2.__dict__[name]) != expected:
                _deny("native_demo_control_facade_changed")
        for owner, name, expected in functions:
            if callable_pin(getattr(owner, name)) != expected:
                _deny("native_demo_control_callee_changed")
        for name, expected in own_functions:
            if callable_pin(namespace[name]) != expected:
                _deny("native_demo_control_boundary_changed")

    return require


_REQUIRE_KERNEL = _make_kernel()
native._bind_control_operator_modules_v1(_SOURCE_KEY)


# Distinct real disarmed configuration purpose. No old Armed/operator owner
# is relabelled and no supplied Control DTO/token supplies this registry.
def _flat_leverage_key_v1(key):
    from app.trade_qualification import native_flat_leverage_account_source_v1 as flat

    if key is not flat._CONTROL_KEY_V1:
        _deny("native_flat_leverage_fixed_source_required")


def _take_flat_leverage_control_v1(cap, *, key):
    _flat_leverage_key_v1(key)
    _REQUIRE_KERNEL()
    state = _OWNERS.pop(cap, None) if type(cap) is OwnedDemoControlSessionV2 else None
    if state is None:
        _deny("native_flat_leverage_disarmed_owner_unavailable")
    try:
        owner = _require_context(state)
        selected = _selected_record_v3(state)
        stamp = _stamp()
        remaining = _remaining_control(state)
        observed = state["observation"]
        if (
            selected is None
            or state["phase"] != "READY_DISARMED"
            or observed.state.emergency_stop is not False
            or observed.state.arm_request_id is not None
            or observed.state.arm_expires_at is not None
            or owner._arm_intent is not False
            or owner._local_estop is not False
            or selected.writer_factory is not state["factory"]
            or selected.custody_factory is not state["custody_original_factory"]
        ):
            _deny("native_flat_leverage_exact_disarmed_source_required")
        state["configuration_generation"] = owner._generation
        state["configuration_expires_at"] = observed.state.lease_until
        state["configuration_monotonic_deadline_ns"] = min(
            int(owner._lease_monotonic_deadline * 1_000_000_000),
            stamp["monotonic_ns"] + int(remaining * 1_000_000_000),
        )
        state["phase"] = "FLAT_LEVERAGE_CONFIGURATION"
        _ACTIVE[id(state)] = state
        return state
    except BaseException:
        _retain(state)
        raise


def _check_flat_leverage_control_v1(state, *, key):
    _flat_leverage_key_v1(key)
    _REQUIRE_KERNEL()
    if type(state) is not dict or _ACTIVE.get(id(state)) is not state:
        _deny("native_flat_leverage_original_owner_required")
    owner = _require_context(state)
    stamp = _stamp()
    observed = state["observation"]
    if (
        state["phase"] != "FLAT_LEVERAGE_CONFIGURATION"
        or owner._generation != state["configuration_generation"]
        or owner._observation is not observed
        or owner._arm_intent is not False
        or owner._local_estop is not False
        or observed.state.emergency_stop is not False
        or observed.state.arm_request_id is not None
        or observed.state.arm_expires_at is not None
        or utc_from_ns(stamp["utc_ns"]) >= state["configuration_expires_at"]
        or stamp["monotonic_ns"] >= state["configuration_monotonic_deadline_ns"]
    ):
        _deny("native_flat_leverage_original_control_changed")
    return stamp


def _retain_flat_leverage_control_v1(state, *, key):
    _flat_leverage_key_v1(key)
    if type(state) is not dict or _HANDLES.get(state.get("public_owner")) is not state:
        _deny("native_flat_leverage_original_safety_handle_required")
    _OWNERS.pop(state["public_owner"], None)
    _retain(state)


def _finish_flat_leverage_control_v1(state, completion, *, key):
    _flat_leverage_key_v1(key)
    _REQUIRE_KERNEL()
    from app.trade_qualification import native_flat_leverage_account_source_v1 as flat

    proof = flat._take_flat_leverage_control_completion_v1(completion, key=_SOURCE_KEY)
    if (
        type(proof) is not tuple
        or len(proof) != 4
        or proof[0] is not state
        or proof[1] is not state["public_owner"]
        or proof[2] is not state["owner"]
        or type(proof[3]) is not bytes
        or not 0 < len(proof[3]) <= 32768
        or _HANDLES.get(state["public_owner"]) is not state
        or state["owner"]._arm_intent is not False
    ):
        _deny("native_flat_leverage_actual_cleanup_required")
    # The Source completion proves actual negative lease/resource settlement
    # and final committed cleanup readback. It never revives this old owner.
    for mapping, identity in (
        (_OWNERS, state["public_owner"]),
        (_ACTIVE, id(state)),
        (_UNKNOWN, id(state)),
        (_HANDLES, state["public_owner"]),
    ):
        actual = mapping.get(identity)
        if actual is not None and actual is not state:
            _deny("native_flat_leverage_original_registry_changed")
    for mapping, identity in (
        (_OWNERS, state["public_owner"]),
        (_ACTIVE, id(state)),
        (_UNKNOWN, id(state)),
        (_HANDLES, state["public_owner"]),
    ):
        if mapping.get(identity) is state:
            mapping.pop(identity)
    state["phase"] = "FLAT_LEVERAGE_CONFIGURATION_COMPLETE"
    return proof[3]
