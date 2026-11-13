"""Native current55 operator acknowledgements; no DTO clear/init authority.

Only the captured original native source can reach these transactions. The
writer holds UID -> Control -> scope, records once, and independently reopens
all committed columns in another session/XID. Unknown commit is never retried.
"""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.account_revision_collector_lineage import (
    DemoCollectorCurrentRevisionLineage as Lineage,
)
from app.database.models.demo_control import DemoAccountControl, DemoControlJournal
from app.database.models.demo_operator_acknowledgements_v55 import (
    DemoControlClearAcknowledgementV2 as ClearRow,
)
from app.database.models.demo_operator_acknowledgements_v55 import (
    DemoPolicyCounterInitializationV1 as CounterRow,
)
from app.database.repositories import (
    account_revision_collector_lineage_current55_v1 as lineage55,
)
from app.database.repositories import (
    collector_current_0055_multirole_catalog_v1 as catalog55,
)
from app.database.repositories import qualification_native_checkpoint_v2 as checkpoint
from app.database.repositories.demo_control import (
    ControlObservation,
    DemoControlRepository,
)
from app.database.repositories.demo_control_current55_v2 import (
    DemoControlRepositoryV2,
    _validated_clear_row_v2,
)
from app.database.repositories.qualification_ledger import (
    PortfolioLocalCheckpoint,
    QualificationLedgerRepository,
)
from app.domain.source_primitives import decode, utc_from_ns
from app.trade_qualification import account_capture_journal as journal
from app.trade_qualification import demo_control as control
from app.trade_qualification import demo_control_clear_v2 as clear
from app.trade_qualification import demo_policy_counter_initialization_v1 as counter
from app.trade_qualification import native_account_risk_assembly_v1 as native

_SOURCE_KEY = object()
_META_SQL = text("""
SELECT current_user,session_user,current_setting('role'),r.oid::bigint,r.rolsuper,
       r.rolcreaterole,r.rolbypassrls,pg_backend_pid(),pg_current_xact_id()::text,
       clock_timestamp()
FROM pg_roles r WHERE r.rolname=current_user
""")
_OPERATOR_FENCE = text(
    "LOCK TABLE public.demo_account_controls, public.demo_control_journal, "
    "public.demo_control_clear_acknowledgements_v2, "
    "public.demo_policy_counter_initializations_v1, "
    "public.demo_collector_current_revision_lineages "
    "IN ROW EXCLUSIVE MODE NOWAIT"
)
_CLEAR_COLUMNS = (
    "ack_sha256",
    "environment",
    "account_id",
    "settlement_currency",
    "command_id",
    "stopped_control_revision",
    "stopped_event_sha256",
    "stopped_state_sha256",
    "clear_control_revision",
    "clear_state_sha256",
    "clear_event_sha256",
    "source_lineage_sha256",
    "account_revision",
    "ledger_revision",
    "claims_sha256",
    "owner_sha256",
    "owner_epoch",
    "original_lease_until",
    "owner_sampled_at",
    "original_expires_at",
    "ack_json",
    "state_json",
    "event_json",
    "db_recorded_at",
)
_COUNTER_COLUMNS = (
    "initialization_sha256",
    "environment",
    "account_id",
    "settlement_currency",
    "command_id",
    "control_revision",
    "control_event_sha256",
    "control_state_sha256",
    "source_lineage_sha256",
    "account_revision",
    "ledger_revision",
    "claims_sha256",
    "configured_policy_sha256",
    "configured_policy_json",
    "measured_genesis_sha256",
    "measured_genesis_json",
    "owner_sampled_at",
    "original_expires_at",
    "ack_json",
    "initialized_at",
    "db_recorded_at",
)


def _deny(code):
    raise control.DemoControlError(code) from None


def _time():
    return utc_from_ns(native.native_clock.native_stamp()["utc_ns"])


def _row_raw(row, columns):
    expected = (
        ClearRow
        if columns is _CLEAR_COLUMNS
        else CounterRow
        if columns is _COUNTER_COLUMNS
        else None
    )
    if expected is None or type(row) is not expected:
        _deny("control55_exact_operator_row_required")
    values = {}
    for name in columns:
        value = getattr(row, name)
        if type(value) is datetime:
            if value.tzinfo is not UTC:
                _deny("control55_operator_row_time_invalid")
            value = value.isoformat()
        elif value is not None and type(value) not in (str, int):
            _deny("control55_operator_row_scalar_invalid")
        values[name] = value
    raw = control.canonical(values).encode("ascii")
    if len(raw) > 2 * 1024 * 1024:
        _deny("control55_operator_row_bound")
    return raw


async def _metadata(session):
    if (
        type(session) is not AsyncSession
        or session.get_bind().dialect.name != "postgresql"
    ):
        _deny("control55_operator_postgresql_required")
    row = tuple((await session.execute(_META_SQL)).one())
    if (
        len(row) != 10
        or type(row[0]) is not str
        or row[0] != row[1]
        or row[2] != "none"
        or type(row[3]) is not int
        or not 0 < row[3] < 2**32
        or any(value is not False for value in row[4:7])
        or type(row[7]) is not int
        or not 0 < row[7] < 2**31
        or type(row[8]) is not str
        or not row[8].isascii()
        or not row[8].isdigit()
        or not 1 <= len(row[8]) <= 20
        or row[8].startswith("0")
        or int(row[8]) >= 2**64
        or type(row[9]) is not datetime
        or row[9].tzinfo is not UTC
    ):
        _deny("control55_operator_actual_metadata_invalid")
    return row


async def _admit(session):
    for statement in (
        "SET TRANSACTION ISOLATION LEVEL READ COMMITTED",
        "SET LOCAL search_path TO pg_catalog, public, pg_temp",
        "SET LOCAL TIME ZONE 'UTC'",
        "SET LOCAL synchronous_commit='on'",
        "SET LOCAL lock_timeout='1000ms'",
        "SET LOCAL statement_timeout='1000ms'",
    ):
        await session.execute(text(statement))


@asynccontextmanager
async def _transaction(session, source):
    # The original native operator capability is replayed after the transaction
    # settings await. Only that replay can select the actual DB0056 branch.
    await _admit(session)
    replay = source()
    selected_database = replay._owned_database_0056
    if selected_database is None:
        await catalog55.verify_actual_control_catalog_v2(session)
        source()
        yield None
        await catalog55.verify_actual_control_catalog_v2(session)
        source()
        return
    selected, roles = selected_database
    if replay.session_factory is not selected.writer_factory:
        _deny("control56_operator_original_writer_required")
    async with checkpoint.held_qualification_checkpoint_0056_v1(
        selected, session, replay.previous_control.scope, roles=roles
    ) as held:
        source()
        held.check()
        connection = selected.writer_factory.kw["bind"].sync_engine
        if session.get_bind(clause=_OPERATOR_FENCE) is not connection or any(
            session.get_bind(mapper=model) is not connection
            for model in (
                DemoAccountControl,
                DemoControlJournal,
                ClearRow,
                CounterRow,
                Lineage,
            )
        ):
            _deny("control56_operator_original_writer_required")
        await session.execute(_OPERATOR_FENCE)
        await held.recheck_catalog()
        source()
        yield held
    source()


def _same_control(actual, event, replay, *, target=None, target_event=None):
    expected = replay.previous_control if target is None else target
    expected_event = (
        replay.previous_control_event_sha256 if target_event is None else target_event
    )
    if actual != expected or event != expected_event:
        _deny("control55_operator_control_revision_changed")


async def _locked(
    session, replay, ledger, *, target=None, target_event=None, held=None
):
    if held is None:
        state, event = await DemoControlRepositoryV2.read_in_transaction(
            session, replay.previous_control.scope
        )
    else:
        state, event = await held.recheck_control()
    _same_control(state, event, replay, target=target, target_event=target_event)
    scope_row = await ledger._locked(session, replay.scope)
    saved = await session.scalar(
        select(Lineage)
        .where(Lineage.lineage_sha256 == replay.lineage.lineage_sha256)
        .execution_options(populate_existing=True)
    )
    lineage55._validate_row(
        saved,
        scope=replay.scope,
        reference=replay.current.reference,
        scope_row=scope_row,
        control_state=replay.previous_control,
        control_event=replay.previous_control_event_sha256,
    )
    if saved.lineage_json.encode("utf-8") != replay.lineage.lineage_json:
        _deny("control55_operator_original_lineage_changed")
    point = await ledger._read_portfolio_checkpoint_locked(
        session, replay.scope, scope_row, _held0056=held
    )
    _flat(replay, point)
    return scope_row, state, event, saved, point


def _flat(replay, point):
    if type(point) is not PortfolioLocalCheckpoint or point.known_flat is not True:
        _deny("control55_operator_local_unknown_or_exposure")
    local = decode(point.document_json, 262144)
    receipt = decode(replay.current.current_receipt_json, 65536)
    if (
        receipt.get("observed_flat") is not True
        or receipt.get("blocking_reasons") != []
        or receipt.get("observed_inventory_state") != "observed_empty_supported_queries"
        or receipt.get("identity", {}).get("environment") != replay.scope.environment
        or receipt.get("identity", {}).get("uid") != replay.scope.account_id
        or receipt.get("identity", {}).get("settlement_currency")
        != replay.scope.settlement_currency
        or local.get("schema_version") != "ctcc.portfolio_local_checkpoint.v2"
        or local.get("blocking_reasons") != []
        or type(local.get("currency_states")) is not list
        or not 1 <= len(local["currency_states"]) <= 128
        or any(
            type(row) is not dict or type(row.get("active")) is not list
            for row in local["currency_states"]
        )
        or local.get("unresolved_intents") != []
        or local.get("environment") != replay.scope.environment
        or local.get("account_id") != replay.scope.account_id
        or local.get("requested_settlement_currency")
        != replay.scope.settlement_currency
        or local.get("legacy", {})
        .get("sync_checkpoint", {})
        .get("credential_session_binding_id")
        != replay.current.packet.plan.session_binding_id
        or any(row["active"] != [] for row in local["currency_states"])
        or not point.observed_at <= point.received_at <= _time() < replay.expires_at
    ):
        _deny("control55_operator_complete_fresh_flat_required")
    return receipt


def _proofs(replay, point, operator_json):
    receipt = _flat(replay, point)
    # Purpose labels bind the actual full same-CURRENT receipt/native lineage,
    # never caller counts, summaries or a supplied true flag.
    prefix = (
        ("current_identity", {"identity": receipt["identity"]}),
        (
            "flat_positions",
            {
                "inventory_row_counts": receipt["inventory_row_counts"],
                "current_rows": receipt["current_rows"],
            },
        ),
        (
            "ordinary_pending",
            {
                "current_pages": receipt["current_pages"],
                "current_rows": receipt["current_rows"],
            },
        ),
        (
            "algo_pending",
            {
                "current_pages": receipt["current_pages"],
                "current_rows": receipt["current_rows"],
            },
        ),
    )
    result = tuple(
        (
            label,
            control.digest(
                control.canonical(
                    {
                        "schema_version": "ctcc.native_control_operator.actual_CURRENT_proof.v1",
                        "purpose": label,
                        "source_reference": receipt["source_reference"],
                        "native_proof_sha256": replay.current.native_proof_sha256,
                        "lineage_sha256": replay.lineage.lineage_sha256,
                        "original": value,
                    }
                )
            ),
        )
        for label, value in prefix
    )
    result += (
        ("local_reconciliation", journal.digest(point.document_json)),
        ("operator_ack", control.digest(operator_json)),
    )
    if replay.purpose == "policy_counter_initialize":
        result += (
            ("configured_policy", replay.configured_policy.selected_settings_sha256),
            ("measured_genesis", journal.digest(replay.original_measurement)),
        )
    return result


def _operator_document(replay):
    scope = [
        replay.scope.environment,
        replay.scope.account_id,
        replay.scope.settlement_currency,
    ]
    common = {
        "purpose": replay.purpose,
        "scope": scope,
        "command_id": replay.command_id,
        "source_lineage_sha256": replay.lineage.lineage_sha256,
        "original_expires_at": replay.expires_at.isoformat(),
        "confirmation": replay.confirmation,
    }
    if replay.purpose == "clear_stop":
        return control.canonical(
            {
                "schema_version": clear.OPERATOR_SCHEMA_V2,
                **common,
                "stopped_control_revision": replay.previous_control.revision,
                "stopped_event_sha256": replay.previous_control_event_sha256,
            }
        )
    if replay.purpose == "policy_counter_initialize":
        return control.canonical(
            {
                "schema_version": counter.COUNTER_OPERATOR_SCHEMA_V1,
                **common,
                "control_revision": replay.previous_control.revision,
                "control_event_sha256": replay.previous_control_event_sha256,
                "configured_policy_sha256": replay.configured_policy.selected_settings_sha256,
                "measured_genesis_sha256": journal.digest(replay.original_measurement),
            }
        )
    _deny("control55_operator_purpose_invalid")


def _ack(replay, scope_row, point, metadata, sampled_at):
    if not metadata[9] <= sampled_at < replay.expires_at:
        _deny("control55_operator_actual_clock_order_invalid")
    operator_json = _operator_document(replay)
    common = {
        "scope": replay.previous_control.scope,
        "settlement_currency": replay.scope.settlement_currency,
        "command_id": replay.command_id,
        "source_lineage_sha256": replay.lineage.lineage_sha256,
        "account_revision": scope_row.account_revision,
        "ledger_revision": scope_row.ledger_revision,
        "claims_sha256": scope_row.claims_sha256,
        "owner_sampled_at": sampled_at,
        "original_expires_at": replay.expires_at,
        "db_role_oid": metadata[3],
        "db_backend_pid": metadata[7],
        "db_transaction_id": metadata[8],
        "proofs": _proofs(replay, point, operator_json),
        "operator_ack_json": operator_json,
    }
    if replay.purpose == "clear_stop":
        target = replace(
            replay.previous_control,
            revision=replay.previous_control.revision + 1,
            emergency_stop=False,
            arm_request_id=None,
            arm_expires_at=None,
            updated_at=sampled_at,
        )
        value = clear.ClearStopAckV2(
            **common,
            stopped_control_revision=replay.previous_control.revision,
            stopped_event_sha256=replay.previous_control_event_sha256,
            stopped_state_sha256=control.digest(
                control.canonical(control.document(replay.previous_control))
            ),
            clear_state_sha256=control.digest(
                control.canonical(control.document(target))
            ),
            owner_sha256=replay.previous_control.owner_sha256,
            owner_epoch=replay.previous_control.owner_epoch,
            original_lease_until=replay.previous_control.lease_until,
        )
        target = clear.advance_clear_stop_v2(
            replay.previous_control,
            owner_sha256=replay.previous_control.owner_sha256,
            owner_epoch=replay.previous_control.owner_epoch,
            expected_revision=replay.previous_control.revision,
            previous_event_sha256=replay.previous_control_event_sha256,
            command_id=replay.command_id,
            now=sampled_at,
            fresh_ack=value,
        )
        return value, target
    value = counter.PolicyCounterInitializationAckV1(
        **common,
        control_revision=replay.previous_control.revision,
        control_event_sha256=replay.previous_control_event_sha256,
        control_state_sha256=control.digest(
            control.canonical(control.document(replay.previous_control))
        ),
        configured_policy_json=replay.configured_policy.selected_settings_json.decode(
            "ascii"
        ),
        measured_genesis_json=replay.original_measurement.decode("ascii"),
    )
    return value, replay.previous_control


def _new_row(ack, target, replay):
    if type(ack) is clear.ClearStopAckV2:
        event = clear.clear_event_document_v2(
            target,
            command_id=ack.command_id,
            previous_sha256=ack.stopped_event_sha256,
            ack_sha256=ack.ack_sha256,
        )
        raw_event = control.canonical(event)
        raw_state = control.canonical(control.document(target))
        return ClearRow(
            ack_sha256=ack.ack_sha256,
            environment=ack.scope.environment,
            account_id=ack.scope.account_id,
            settlement_currency=ack.settlement_currency,
            command_id=ack.command_id,
            stopped_control_revision=ack.stopped_control_revision,
            stopped_event_sha256=ack.stopped_event_sha256,
            stopped_state_sha256=ack.stopped_state_sha256,
            clear_control_revision=target.revision,
            clear_state_sha256=ack.clear_state_sha256,
            clear_event_sha256=control.digest(raw_event),
            source_lineage_sha256=ack.source_lineage_sha256,
            account_revision=ack.account_revision,
            ledger_revision=ack.ledger_revision,
            claims_sha256=ack.claims_sha256,
            owner_sha256=ack.owner_sha256,
            owner_epoch=ack.owner_epoch,
            original_lease_until=ack.original_lease_until,
            owner_sampled_at=ack.owner_sampled_at,
            original_expires_at=ack.original_expires_at,
            ack_json=control.canonical(clear.clear_ack_document_v2(ack)),
            state_json=raw_state,
            event_json=raw_event,
        )
    if type(ack) is counter.PolicyCounterInitializationAckV1:
        return CounterRow(
            initialization_sha256=ack.initialization_sha256,
            environment=ack.scope.environment,
            account_id=ack.scope.account_id,
            settlement_currency=ack.settlement_currency,
            command_id=ack.command_id,
            control_revision=ack.control_revision,
            control_event_sha256=ack.control_event_sha256,
            control_state_sha256=ack.control_state_sha256,
            source_lineage_sha256=ack.source_lineage_sha256,
            account_revision=ack.account_revision,
            ledger_revision=ack.ledger_revision,
            claims_sha256=ack.claims_sha256,
            configured_policy_sha256=control.digest(ack.configured_policy_json),
            configured_policy_json=ack.configured_policy_json,
            measured_genesis_sha256=control.digest(ack.measured_genesis_json),
            measured_genesis_json=ack.measured_genesis_json,
            owner_sampled_at=ack.owner_sampled_at,
            original_expires_at=ack.original_expires_at,
            ack_json=control.canonical(counter.policy_counter_ack_document_v1(ack)),
        )
    _deny("control55_operator_exact_ack_required")


def _make_store_clear_v1():
    replay_source = native._replay_owned_control_operator_source_v1
    source_key = _SOURCE_KEY

    async def store(session, row, target, state, held):
        # Ack row is already flushed in this same XID. Old54 _store remains exact
        # v1 and is deliberately not used to serialize a v2 clear event.
        replay = replay_source(state, key=source_key)
        if held is None:
            live = await DemoControlRepositoryV2._locked(session, target.scope)
        else:
            _same_control(*await held.recheck_control(), replay)
            live = await DemoControlRepository._locked(session, target.scope)
            held.check()
        replay_source(state, key=source_key)
        if (
            type(live) is not DemoAccountControl
            or live.control_revision != row.stopped_control_revision
            or (live.environment, live.account_id)
            != (target.scope.environment, target.scope.account_id)
        ):
            _deny("control55_operator_control_CAS_changed")
        for name, value in {
            "control_revision": target.revision,
            "owner_sha256": target.owner_sha256,
            "owner_epoch": target.owner_epoch,
            "lease_until": target.lease_until,
            "emergency_stop": False,
            "arm_request_id": None,
            "arm_expires_at": None,
            "state_json": row.state_json,
            "state_sha256": row.clear_state_sha256,
            "created_at": target.created_at,
            "updated_at": target.updated_at,
        }.items():
            setattr(live, name, value)
        await session.flush()
        replay_source(state, key=source_key)
        session.add(
            DemoControlJournal(
                environment=target.scope.environment,
                account_id=target.scope.account_id,
                control_revision=target.revision,
                command_id=row.command_id,
                action="clear_stop",
                previous_sha256=row.stopped_event_sha256,
                event_sha256=row.clear_event_sha256,
                state_sha256=row.clear_state_sha256,
                event_json=row.event_json,
                occurred_at=target.updated_at,
            )
        )
        await session.flush()
        replay = replay_source(state, key=source_key)
        if held is not None:
            _same_control(
                *await held.recheck_control(),
                replay,
                target=target,
                target_event=row.clear_event_sha256,
            )
            replay_source(state, key=source_key)

    return store


_store_clear = _make_store_clear_v1()


def _validate_counter_row(row, replay):
    if type(row) is not CounterRow:
        _deny("control55_counter_exact_row_required")
    ack = counter.decode_policy_counter_ack_v1(
        row.ack_json,
        configured_policy_json=row.configured_policy_json,
        measured_genesis_json=row.measured_genesis_json,
    )
    if (
        row.initialization_sha256 != ack.initialization_sha256
        or (row.environment, row.account_id, row.settlement_currency, row.command_id)
        != (
            ack.scope.environment,
            ack.scope.account_id,
            ack.settlement_currency,
            ack.command_id,
        )
        or (row.control_revision, row.control_event_sha256, row.control_state_sha256)
        != (ack.control_revision, ack.control_event_sha256, ack.control_state_sha256)
        or (
            row.source_lineage_sha256,
            row.account_revision,
            row.ledger_revision,
            row.claims_sha256,
        )
        != (
            ack.source_lineage_sha256,
            ack.account_revision,
            ack.ledger_revision,
            ack.claims_sha256,
        )
        or row.configured_policy_sha256 != control.digest(ack.configured_policy_json)
        or row.measured_genesis_sha256 != control.digest(ack.measured_genesis_json)
        or row.owner_sampled_at != ack.owner_sampled_at
        or row.original_expires_at != ack.original_expires_at
        or type(row.initialized_at) is not datetime
        or row.initialized_at.tzinfo is not UTC
        or row.initialized_at != row.db_recorded_at
        or not ack.owner_sampled_at <= row.initialized_at < ack.original_expires_at
        or ack.configured_policy_json.encode("ascii")
        != replay.configured_policy.selected_settings_json
        or ack.measured_genesis_json.encode("ascii") != replay.original_measurement
    ):
        _deny("control55_counter_actual_columns_changed")
    return ack


@dataclass(frozen=True, slots=True, repr=False)
class _OperatorReadbackV1:
    purpose: str
    row_json: bytes = field(repr=False)
    control_observation: ControlObservation = field(repr=False)
    value: object = field(repr=False)
    write_metadata: tuple = field(repr=False)
    readback_metadata: tuple = field(repr=False)
    expected_row_json: bytes = field(repr=False)


def _verify_operator_readback_v1(value, replay):
    if (
        type(value) is not _OperatorReadbackV1
        or value.purpose != replay.purpose
        or type(value.row_json) is not bytes
        or value.row_json != value.expected_row_json
        or value.write_metadata[3] != value.readback_metadata[3]
        or value.write_metadata[8] == value.readback_metadata[8]
        or value.write_metadata[9] > value.readback_metadata[9]
        or not value.readback_metadata[9]
        <= value.control_observation.observed_at
        < replay.expires_at
    ):
        _deny("control55_operator_independent_readback_changed")
    value.control_observation.__post_init__()
    if replay.purpose == "policy_counter_initialize":
        if type(value.value) is not counter.PolicyCounterInitializationReadbackV1:
            _deny("control55_counter_actual_readback_required")
        if value.value.row_json != value.row_json:
            _deny("control55_counter_actual_readback_changed")
    elif value.value is not value.control_observation:
        _deny("control55_clear_actual_observation_required")


def _make_entry_v1():
    # The public callable closes over the genuine original take/replay/finish
    # functions. Alias replacement cannot turn a DTO into a source or skip the
    # native source/kernel check after an awaited database operation.
    take = native._take_owned_control_operator_source_v1
    replay_source = native._replay_owned_control_operator_source_v1
    finish = native._finish_owned_control_operator_source_v1
    unknown = native._retain_unknown_control_operator_source_v1
    source_key = _SOURCE_KEY
    store_clear = _store_clear

    async def run(state):
        def source():
            return replay_source(state, key=source_key)

        try:
            replay = source()
            ledger = QualificationLedgerRepository(replay.session_factory, clock=_time)
            remaining = min(
                (replay.expires_at - _time()).total_seconds(),
                (
                    replay.monotonic_deadline_ns
                    - native.native_clock.native_stamp()["monotonic_ns"]
                )
                / 1_000_000_000,
            )
            if remaining <= 0:
                _deny("control55_operator_original_expiry")
            async with asyncio.timeout(remaining):
                async with (
                    replay.session_factory() as session,
                    session.begin(),
                    _transaction(session, source) as held,
                ):
                    write_session = session
                    scope_row, _, _, _, point = await _locked(
                        session, source(), ledger, held=held
                    )
                    source()
                    if replay.purpose == "policy_counter_initialize":
                        existing = await session.scalar(
                            select(CounterRow).where(
                                CounterRow.environment == replay.scope.environment,
                                CounterRow.account_id == replay.scope.account_id,
                                CounterRow.settlement_currency
                                == replay.scope.settlement_currency,
                            )
                        )
                        source()
                        if existing is not None:
                            _deny("control55_policy_counter_already_initialized")
                    metadata = await _metadata(session)
                    replay = source()
                    sampled = _time()
                    ack, target = _ack(replay, scope_row, point, metadata, sampled)
                    saved = _new_row(ack, target, replay)
                    session.add(saved)
                    await session.flush()
                    source()
                    await session.refresh(saved)
                    source()
                    if replay.purpose == "clear_stop":
                        await store_clear(session, saved, target, state, held)
                        source()
                        _validated_clear_row_v2(saved)
                        target_event = saved.clear_event_sha256
                        columns = _CLEAR_COLUMNS
                        pin = saved.ack_sha256
                    else:
                        _validate_counter_row(saved, replay)
                        target_event = replay.previous_control_event_sha256
                        columns = _COUNTER_COLUMNS
                        pin = saved.initialization_sha256
                    written = _row_raw(saved, columns)
                    if held is not None:
                        _same_control(
                            *await held.recheck_control(),
                            source(),
                            target=target,
                            target_event=target_event,
                        )
                source()
                async with (
                    replay.session_factory() as session,
                    session.begin(),
                    _transaction(session, source) as held,
                ):
                    if session is write_session:
                        _deny("control55_operator_independent_session_required")
                    scope_row, current_state, current_event, _, _ = await _locked(
                        session,
                        source(),
                        ledger,
                        target=target,
                        target_event=target_event,
                        held=held,
                    )
                    source()
                    readback_metadata = await _metadata(session)
                    source()
                    model = ClearRow if replay.purpose == "clear_stop" else CounterRow
                    saved = await session.get(model, pin, populate_existing=True)
                    source()
                    if _row_raw(saved, columns) != written:
                        _deny("control55_operator_committed_row_changed")
                    if replay.purpose == "clear_stop":
                        _validated_clear_row_v2(saved)
                    else:
                        _validate_counter_row(saved, replay)
                    observation = ControlObservation(
                        current_state, current_event, _time()
                    )
                    if replay.purpose == "policy_counter_initialize":
                        result = counter.PolicyCounterInitializationReadbackV1(
                            ack,
                            saved.initialized_at,
                            saved.db_recorded_at,
                            written,
                            metadata,
                            readback_metadata,
                        )
                    else:
                        result = observation
                    value = _OperatorReadbackV1(
                        replay.purpose,
                        written,
                        observation,
                        result,
                        metadata,
                        readback_metadata,
                        written,
                    )
                    _verify_operator_readback_v1(value, source())
                    if held is not None:
                        _same_control(
                            *await held.recheck_control(),
                            source(),
                            target=target,
                            target_event=target_event,
                        )
                source()
                return finish(state, value, key=source_key)
        except BaseException as error:  # noqa: BLE001 -- Retain unknown state; preserve cancellation without private error text.
            unknown(state, key=source_key)
            if isinstance(error, asyncio.CancelledError):
                raise asyncio.CancelledError() from None
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise type(error)() from None
            _deny("control55_operator_commit_or_readback_unknown")

    def entry(owner):
        state = take(owner, key=source_key)
        return run(state)

    return entry


record_owned_operator_acknowledgement_v1 = _make_entry_v1()
