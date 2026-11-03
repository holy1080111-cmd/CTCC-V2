"""Live-owned CURRENT MOVE -> guarded lineage commit -> separate readback.

This callable accepts only a genuine V4 owner, not claims/scope/factory/clocks
or a receipt. A successful result proves the recorded CURRENT bindings only.
It never changes the ledger claims/revision, issues an account-complete stamp,
or relaxes the existing operational _require_owned_account_revision guard.
"""

import asyncio
from datetime import UTC, datetime

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.account_revision_collector_lineage import (
    DemoCollectorCurrentRevisionLineage as Lineage,
)
from app.database.repositories import public_runtime_bundle_custody as custody
from app.database.repositories.account_capture_journal import (
    AccountCaptureJournalRepository,
)
from app.database.repositories.demo_control import DemoControlRepository
from app.database.repositories.qualification_ledger import QualificationLedgerRepository
from app.domain import account_revision_collector_lineage_schema_v1 as schema
from app.domain import account_revision_collector_lineage_v1 as policy
from app.domain.native_clock import native_stamp
from app.domain.source_primitives import decode, utc_from_ns
from app.trade_qualification import account_capture_journal as journal
from app.trade_qualification import account_current_source_v4 as current
from app.trade_qualification import account_observation_index as observed
from app.trade_qualification import demo_control as control
from app.trade_qualification import reservations

_SOURCE_KEY = object()
_META_SQL = text("""
SELECT current_user,session_user,current_setting('role'),r.oid::bigint,r.rolsuper,
       r.rolcreaterole,r.rolbypassrls,pg_backend_pid(),pg_current_xact_id()::text,
       clock_timestamp()
FROM pg_roles r WHERE r.rolname=current_user
""")


def _now():
    return utc_from_ns(native_stamp()["utc_ns"])


async def _metadata(session):
    if (
        type(session) is not AsyncSession
        or session.get_bind().dialect.name != "postgresql"
    ):
        policy.deny("collector_lineage_postgresql_required")
    row = tuple((await session.execute(_META_SQL)).one())
    if (
        len(row) != 10
        or type(row[0]) is not str
        or row[0] != row[1]
        or row[2] != "none"
        or type(row[3]) is not int
        or row[3] <= 0
        or any(value is not False for value in row[4:7])
        or type(row[7]) is not int
        or not 0 < row[7] < 2**31
        or type(row[8]) is not str
        or not row[8].isascii()
        or not row[8].isdigit()
        or not 1 <= len(row[8]) <= 20
        or type(row[9]) is not datetime
        or row[9].tzinfo is not UTC
    ):
        policy.deny("collector_lineage_database_identity_invalid")
    return row


def _owner(state):
    return current._replay_account_revision_lineage_v1(state, key=_SOURCE_KEY)


def _remaining(replay, stamp):
    remaining = min(
        (replay.expires_at - utc_from_ns(stamp["utc_ns"])).total_seconds(),
        (replay.monotonic_deadline_ns - stamp["monotonic_ns"]) / 1_000_000_000,
    )
    if remaining <= 0:
        policy.deny("collector_lineage_original_deadline_expired")
    return remaining


async def _admit(session, replay, stamp):
    if (
        type(session) is not AsyncSession
        or session.get_bind().dialect.name != "postgresql"
    ):
        policy.deny("collector_lineage_postgresql_required")
    await session.execute(text("SET LOCAL search_path=pg_catalog,public,pg_temp"))
    await session.execute(text("SET LOCAL TIME ZONE 'UTC'"))
    await _metadata(session)
    # The same original lease also bounds waiting for table/row locks and SQL.
    milliseconds = max(1, int(_remaining(replay, stamp) * 1000))
    for name in ("statement_timeout", "lock_timeout"):
        await session.execute(
            text("SELECT set_config(:name,:value,true)"),
            {"name": name, "value": str(milliseconds)},
        )
    await session.execute(
        text(
            "LOCK TABLE public.demo_collector_current_revision_lineages IN ROW SHARE MODE NOWAIT"
        )
    )
    schema.verify_catalog(await session.scalar(text(schema.CATALOG_SQL)))


async def _locked(session, ledger, scope):
    control_state, control_event = await DemoControlRepository.read_in_transaction(
        session, control.ControlScope(scope.environment, scope.account_id)
    )
    row = await ledger._locked(session, scope)
    return row, control_state, control_event


def _control_pin(state):
    return control.digest(control.canonical(control.document(state)))


def _original_chain(state):
    document = decode(state["bundle_raw"], custody.MAX_BUNDLE_BYTES)
    return current._chain_replay(document["chain"])


def _same_chain(expected, actual):
    if (
        type(expected) is not tuple
        or type(actual) is not tuple
        or len(expected) != len(actual)
    ):
        policy.deny("collector_lineage_original_source_changed")
    for old, new in zip(expected, actual, strict=True):
        if (
            type(old) is not journal.JournalReadback
            or type(new) is not journal.JournalReadback
            or (
                old.event.event_json,
                old.event.raw_body,
                old.event.packet_payload,
                old.db_recorded_at,
            )
            != (
                new.event.event_json,
                new.event.raw_body,
                new.event.packet_payload,
                new.db_recorded_at,
            )
            or new.readback_at < old.readback_at
        ):
            policy.deny("collector_lineage_original_source_changed")


async def _custody_readback(state, replay):
    repository = state["lineage_custody"]
    if type(repository) is not custody.RuntimeBundleCustodyRepository:
        policy.deny("collector_lineage_custody_changed")
    result = await repository.read_retained_bundle(
        bundle_id=state["custody_id"],
        expected_sha256=state["bundle_sha256"],
        proof_envelope_sha256=replay.proof_envelope_sha256,
    )
    if (
        type(result) is not custody.RetainedRuntimeBundleReadback
        or result.bundle_raw != state["bundle_raw"]
        or result.bundle_id != state["custody_id"]
        or result.bundle_sha256 != state["bundle_sha256"]
        or result.proof_envelope_sha256 != replay.proof_envelope_sha256
    ):
        policy.deny("collector_lineage_custody_changed")
    _owner(state)
    return result


def _validate_row(saved, *, scope, reference, scope_row, control_state, control_event):
    """Validate every column against actual locked scope/control and source pins."""
    if type(saved) is not Lineage:
        policy.deny("collector_lineage_committed_readback_changed")
    raw = saved.lineage_json.encode("utf-8")
    document = policy.validate_document(
        raw,
        scope=[scope.environment, scope.account_id, scope.settlement_currency],
        reference=observed.reference_document(reference),
        claims_json=scope_row.claims_json,
        account_revision=scope_row.account_revision,
        ledger_revision=scope_row.ledger_revision,
        control_revision=control_state.revision,
        control_event_sha256=control_event,
        control_state_sha256=_control_pin(control_state),
    )
    if (
        (saved.environment, saved.account_id, saved.settlement_currency)
        != (scope.environment, scope.account_id, scope.settlement_currency)
        or saved.account_revision != scope_row.account_revision
        or saved.ledger_revision != scope_row.ledger_revision
        or saved.claims_json != scope_row.claims_json
        or saved.claims_sha256 != scope_row.claims_sha256
        or saved.control_revision != control_state.revision
        or saved.control_event_sha256 != control_event
        or saved.control_state_sha256 != _control_pin(control_state)
        or saved.current_capture_id != reference.capture_id
        or saved.current_head_sha256 != reference.head_sha256
        or saved.custody_id != document["custody_id"]
        or saved.lineage_sha256 != journal.digest(raw)
        or type(saved.current_terminal_sequence) is not int
        or saved.current_terminal_sequence <= 0
        or type(saved.db_recorded_at) is not datetime
        or saved.db_recorded_at.tzinfo is not UTC
        or not datetime.fromisoformat(document["db_recorded_at"])
        <= saved.db_recorded_at
        < datetime.fromisoformat(document["original_expires_at"])
    ):
        policy.deny("collector_lineage_committed_readback_changed")
    return document


async def record_owned_current_revision_lineage_v1(source):
    """Consume once; return a positive CURRENT binding readback, never claims.

    Main write and separate readback use only the factory captured by the real
    native issuer. All control/scope/source work is inside the original lease.
    Cancellation/uncertain commit may leave an audit row but never a returned
    owner or a resumable accumulator. No cleanup deletes prior evidence.
    """
    state = None
    try:
        state = current._take_account_revision_lineage_v1(source, key=_SOURCE_KEY)
        replay, stamp = _owner(state)
        scope = reservations.LedgerScope(
            account_id=replay.packet.plan.expected_uid,
            settlement_currency=replay.packet.plan.settlement_currency,
        )
        factory = state["lineage_factory"]
        ledger = QualificationLedgerRepository(factory, clock=_now)
        journal_repo = AccountCaptureJournalRepository(factory, clock=_now)
        original = _original_chain(state)
        async with asyncio.timeout(_remaining(replay, stamp)):
            before_custody = await _custody_readback(state, replay)
            replay, stamp = _owner(state)
            async with factory() as session, session.begin():
                write_session = session
                await _admit(session, replay, stamp)
                row, control_state, control_event = await _locked(
                    session, ledger, scope
                )
                chain = await journal_repo._read_chain_locked(
                    session, scope, replay.reference.capture_id
                )
                _same_chain(original, chain)
                if observed.source_reference(chain) != replay.reference:
                    policy.deny("collector_lineage_original_source_changed")
                first = journal.checked_event(chain[0].event)
                local_state = await ledger._state(session, scope, row)
                if first["data"]["local_checkpoint_sha256"] != reservations.digest(
                    local_state
                ):
                    policy.deny("collector_lineage_captured_ledger_changed")
                metadata = await _metadata(session)
                refreshed, stamp = _owner(state)
                if (
                    refreshed.reference != replay.reference
                    or refreshed.proof_envelope_sha256 != replay.proof_envelope_sha256
                ):
                    policy.deny("collector_lineage_original_source_changed")
                document = {
                    "schema_version": policy.SCHEMA,
                    "scope": [
                        scope.environment,
                        scope.account_id,
                        scope.settlement_currency,
                    ],
                    "source_reference": observed.reference_document(replay.reference),
                    "source_bundle_sha256": state["bundle_sha256"],
                    "source_policy_sha256": current.POLICY_SHA256,
                    "proof_envelope_sha256": replay.proof_envelope_sha256,
                    "native_proof_sha256": replay.native_proof_sha256,
                    "native_readback_sha256": replay.native_readback_sha256,
                    "custody_id": state["custody_id"],
                    "owner_sampled_at": utc_from_ns(stamp["utc_ns"]).isoformat(),
                    "original_expires_at": replay.expires_at.isoformat(),
                    "packet_completed_at": replay.packet.completed_at.isoformat(),
                    "account_revision": row.account_revision,
                    "ledger_revision": row.ledger_revision,
                    "claims_sha256": row.claims_sha256,
                    "claims_binding_kind": "UNAUTHENTICATED_LEDGER_SNAPSHOT",
                    "control_revision": control_state.revision,
                    "control_event_sha256": control_event,
                    "control_state_sha256": _control_pin(control_state),
                    "db_role_oid": metadata[3],
                    "db_backend_pid": metadata[7],
                    "db_transaction_id": metadata[8],
                    "db_recorded_at": metadata[9].isoformat(),
                    "unresolved_obligations": list(policy.UNRESOLVED),
                    "account_revision_issued": False,
                    "account_complete": False,
                    "execution_authority": False,
                    "admission": "DENY",
                }
                raw = journal.canonical(document)
                policy.validate_document(
                    raw,
                    scope=document["scope"],
                    reference=document["source_reference"],
                    claims_json=row.claims_json,
                    account_revision=row.account_revision,
                    ledger_revision=row.ledger_revision,
                    control_revision=control_state.revision,
                    control_event_sha256=control_event,
                    control_state_sha256=_control_pin(control_state),
                )
                pin = journal.digest(raw)
                saved = Lineage(
                    lineage_sha256=pin,
                    **QualificationLedgerRepository._keys(scope),
                    account_revision=row.account_revision,
                    ledger_revision=row.ledger_revision,
                    claims_json=row.claims_json,
                    claims_sha256=row.claims_sha256,
                    control_revision=control_state.revision,
                    control_event_sha256=control_event,
                    control_state_sha256=_control_pin(control_state),
                    current_capture_id=replay.reference.capture_id,
                    current_terminal_sequence=len(chain),
                    current_head_sha256=replay.reference.head_sha256,
                    custody_id=state["custody_id"],
                    lineage_json=raw.decode("utf-8"),
                )
                session.add(saved)
                await session.flush()
                await session.refresh(saved)
                _validate_row(
                    saved,
                    scope=scope,
                    reference=replay.reference,
                    scope_row=row,
                    control_state=control_state,
                    control_event=control_event,
                )
                _owner(state)
                schema.verify_catalog(await session.scalar(text(schema.CATALOG_SQL)))
            # A different ORM session and XID independently reopen committed
            # source/scope/control. Same backend PID is allowed for pooled DBs.
            replay, stamp = _owner(state)
            async with factory() as session, session.begin():
                if session is write_session:
                    policy.deny("collector_lineage_independent_session_required")
                await _admit(session, replay, stamp)
                current_row, control_state, control_event = await _locked(
                    session, ledger, scope
                )
                readback = await _metadata(session)
                if (
                    readback[3] != metadata[3]
                    or readback[8] == metadata[8]
                    or readback[9] < metadata[9]
                ):
                    policy.deny("collector_lineage_independent_transaction_required")
                saved = await session.scalar(
                    select(Lineage)
                    .filter_by(lineage_sha256=pin)
                    .execution_options(populate_existing=True)
                )
                document = _validate_row(
                    saved,
                    scope=scope,
                    reference=replay.reference,
                    scope_row=current_row,
                    control_state=control_state,
                    control_event=control_event,
                )
                if saved.db_recorded_at > readback[9]:
                    policy.deny("collector_lineage_database_clock_regressed")
                if saved.lineage_json.encode("utf-8") != raw:
                    policy.deny("collector_lineage_committed_readback_changed")
                after = await journal_repo._read_chain_locked(
                    session, scope, replay.reference.capture_id
                )
                _same_chain(original, after)
                if (
                    observed.source_reference(after) != replay.reference
                    or saved.current_terminal_sequence != len(after)
                    or journal.checked_event(after[0].event)["data"][
                        "local_checkpoint_sha256"
                    ]
                    != reservations.digest(
                        await ledger._state(session, scope, current_row)
                    )
                ):
                    policy.deny("collector_lineage_committed_readback_changed")
                _owner(state)
                schema.verify_catalog(await session.scalar(text(schema.CATALOG_SQL)))
            after_custody = await _custody_readback(state, replay)
            if (
                after_custody.recorded_at != before_custody.recorded_at
                or after_custody.database_readback_at
                < before_custody.database_readback_at
            ):
                policy.deny("collector_lineage_custody_changed")
            _owner(state)
            return policy.CollectorCurrentRevisionLineageReadback(raw, pin)
    except asyncio.CancelledError:
        raise asyncio.CancelledError() from None
    except (KeyboardInterrupt, SystemExit) as error:
        raise type(error)() from None
    except policy.CollectorRevisionLineageError:
        raise
    except Exception:  # noqa: BLE001 -- private failures are replaced by fixed codes
        policy.deny("collector_lineage_write_or_readback_unknown")
    finally:
        if type(state) is dict:
            state.clear()
