"""Same-task original-OCO DATA writer after read-source retirement.

This capability is minted only by the original LIVE recovery. It never enters
an Arm, source/qualification, event-reservation or exchange-submit registry.
After retirement it uses the separately pinned shared writer factory; it never
falls back to journal._source or resurrects a retired selection.
"""

import asyncio
import os
import sys
import threading
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from weakref import WeakKeyDictionary

from sqlalchemy import select, text

from app.config.settings import get_settings
from app.database.models.qualification_ledger import QualificationAccountScope as Scope
from app.database.models.submission_reporting import QualificationReportSpool as Spool
from app.database.repositories import native_closed_reporting_0056_v1 as writer
from app.database.repositories import native_postsubmit_v1 as journal
from app.database.repositories import native_protective_reporting_v58 as replay
from app.database.repositories.submission_reporting import spool_identity
from app.domain import native_database_profile_v1 as profiles
from app.domain import native_protective_report_schema_v58 as schema
from app.domain.native_clock import native_stamp
from app.domain.source_primitives import canonical, decode, sha, utc_from_ns
from app.trade_evidence import native_protective_forensics_v58 as mapper
from app.trade_evidence import outbox
from app.trade_evidence.submission_reporting import policy_record, queue
from app.trade_qualification import native_continuation_guard_v1 as guard

_WRITERS = WeakKeyDictionary()
_MAX_WRITERS = 16


def _need(value):
    replay._need(value)


class OwnedProtectiveReportWriterV58:
    __slots__ = ("__weakref__",)

    def __new__(cls):
        _need(False)

    def __copy__(self):
        _need(False)

    def __deepcopy__(self, memo):
        _need(False)

    def __reduce_ex__(self, protocol):
        _need(False)


@dataclass(frozen=True, slots=True)
class ProtectiveReportSpoolReadbackV58:
    spool_id: str
    report_sha256: str
    queue_namespace: str
    status: str = field(default="durable_pending_projection", init=False)
    execution_authority: bool = field(default=False, init=False)
    order_retry_authority: bool = field(default=False, init=False)


def _key(key):
    from app.trade_qualification import native_postsubmit_reconciliation_v1 as post

    expected = getattr(post, "_PROTECTIVE_REPORT_KEY_V58", None)
    _need(expected is not None and key is expected)
    return post


def _record(cap, *, key):
    post = _key(key)
    value = _WRITERS.get(cap) if type(cap) is OwnedProtectiveReportWriterV58 else None
    _need(
        type(value) is dict
        and value["task"] is asyncio.current_task()
        and value["loop"] is asyncio.get_running_loop()
        and value["pid"] == os.getpid()
        and value["thread"] == threading.get_ident()
    )
    guard.check_saved_native_guard_v1(value["guard"])
    _need(value["post"] is post)
    return value


async def prepare_owned_protective_report_writer_v58(state, *, result, key):
    """Capture exact committed closure/forensics while the source is LIVE."""
    post = _key(key)
    verified = (
        state.get("verified_normal_protected_recovery") if type(state) is dict else None
    )
    _need(
        type(state) is dict
        and type(state.get("binding")) is object
        and post._LIVE.get(state.get("binding")) is state
        and post._UNKNOWN.get(state["binding"]) is state
        and state["kind"] == "recovery"
        and state["closed"] is False
        and type(verified) is tuple
        and len(verified) == 4
        and verified[0] is result
        and type(result) is post.NativeReconciliationResultV1
        and result.risk_hold_retained is False
        and len(_WRITERS) < _MAX_WRITERS
    )
    post._source(state)
    selected, roles = journal._database_profile(state["binding"])
    _need(writer._profile_for_original_roles_v1(roles) is profiles.DB0058)
    factory, engine, names, routes = writer._factory_pins(selected)
    route = writer.locked._direct_tls_route(engine)
    _need(factory is state["source"].session_factory)
    source_module = post._captured_flat_cleanup_source_v1(state)
    generation = state["source"].source_generation
    read_record = source_module._RECONCILIATION_READ_V6.get(generation)
    _need(type(read_record) is dict and read_record["generation"] is generation)
    selector = post._selector._record(state["recovery_selection"])
    initial = vars(selector["private_client"]).get(
        "_native_reconciliation_read_journals_v1"
    )
    fresh = vars(read_record["client"]).get("_native_reconciliation_read_journals_v1")
    _need(
        type(initial) is list
        and len(initial) == 1
        and type(fresh) is list
        and len(fresh) == len(state["durable_observations"])
    )
    expected_reads = (
        (initial[0], selector["read_session"].binding_id),
        *((item, read_record["read_lease_binding_id"]) for item in fresh),
    )
    async with journal._transaction(state["binding"]) as parts:
        rows = await journal._outcomes(parts[3], state["target"])
        by_id = {row.outcome_id: row for row in rows}
        closed = by_id[result.outcome_id]
        witness = decode(closed.capture_json.encode(), 140000)
        forensic = by_id[witness["forensics_outcome_id"]]
        record = decode(forensic.capture_json.encode(), 140000)
        _need(
            record["version"] == schema.FORENSICS_VERSION
            and witness["forensics_sha256"] == record["forensics_sha256"]
        )
        _, original = journal._read_manifest(
            rows, record["original_manifest_outcome_id"]
        )
        _, final = journal._read_manifest(rows, record["final_manifest_outcome_id"])
        computed = mapper.reconstruct_native_protective_forensics_v58(
            state["target"].facts, original, final
        )
        _need(
            computed.receipt_json == record["forensics_json"].encode()
            and sha(computed.receipt_json) == record["forensics_sha256"]
        )
        closure_json = closed.capture_json
        forensic_id = forensic.outcome_id
        closure_at = closed.recorded_at
    cap = object.__new__(OwnedProtectiveReportWriterV58)
    value = {
        "post": post,
        "binding": state["binding"],
        "state": state,
        "selection": state["recovery_selection"],
        "selected": selected,
        "roles": roles,
        "factory": factory,
        "engine": engine,
        "names": names,
        "routes": routes,
        "route": route,
        "original_read_source": source_module,
        "original_read_record": read_record,
        "expected_reads": expected_reads,
        "original_pools": (
            selected.frame_reader_engine.sync_engine.pool,
            selected.custody_engine.sync_engine.pool,
        ),
        "target": state["target"],
        "result": result,
        "closure_json": closure_json,
        "closure_at": closure_at,
        "forensic_id": forensic_id,
        "report": computed,
        "current_manifest_id": state["protective_durable_assessment"][0],
        "namespace": queue(get_settings().submission_outbox_queue_namespace),
        "task": asyncio.current_task(),
        "loop": asyncio.get_running_loop(),
        "pid": os.getpid(),
        "thread": threading.get_ident(),
        "guard": {},
        "phase": "PREPARED",
    }
    guard.save_native_guard_v1(value["guard"], post._NATIVE_GUARD)
    _WRITERS[cap] = value
    state["protective_report_writer"] = cap
    return cap


def capture_actual_protective_read_cleanup_v58(
    cap, *, caller, source, record, read_set, pages, pools, started, key
):
    """Called after actual SDK/NullPool disposal, before deleting old registries."""
    value = _record(cap, key=key)
    post = value["post"]
    state = value["state"]
    _need(
        value["phase"] == "PREPARED"
        and post._UNKNOWN.get(value["binding"]) is state
        and post._LIVE.get(value["binding"]) is None
        and state["closed"] is True
        and post._caller._HELD.get(caller["id"]) is caller
        and caller["selection"] is value["selection"]
        and tuple(pages) == state["durable_observations"]
        and source is value["original_read_source"]
        and record is value["original_read_record"]
        and all(
            actual is expected
            for actual, expected in zip(pools, value["original_pools"], strict=True)
        )
        and len(read_set) == len(value["expected_reads"])
        and all(
            item is expected_item and lease == expected_lease
            for (item, lease), (expected_item, expected_lease) in zip(
                read_set, value["expected_reads"], strict=True
            )
        )
    )
    gets = []
    _need(len(read_set) == len(pages) + 1)
    for index, (item, lease) in enumerate(read_set):
        closed = source._closed_reconciliation_get_v6(item, lease)
        _need(type(closed) is tuple and len(closed) == 5)
        page = item.observation
        if index:
            role, expected = pages[index - 1]
            _need(page == expected)
        else:
            role = "identity_selector"
            _need(page.path == "/api/v5/account/config" and page.parameters == ())
        gets.append(
            {
                "role": role,
                "path": page.path,
                "parameters": list(page.parameters),
                "body_sha256": sha(page.raw_body),
                "request_signature_sha256": page.request_signature_sha256,
                "read_session_id": page.read_session_id,
                "closed_observation": list(closed),
            }
        )
    selected = value["selected"]
    retired = []
    for role, engine, old in zip(
        ("frame_reader", "custody"),
        (selected.frame_reader_engine, selected.custody_engine),
        pools,
        strict=True,
    ):
        _need(
            type(old) is post._selector.NullPool
            and type(engine.sync_engine.pool) is post._selector.NullPool
            and engine.sync_engine.pool is not old
        )
        retired.append(
            {
                "role": role,
                "kind": "sqlalchemy.pool.NullPool",
                "original_pool_sha256": sha(canonical([role, id(old)])),
                "retired_pool_sha256": sha(
                    canonical([role, id(engine.sync_engine.pool)])
                ),
            }
        )
    end = native_stamp()
    _need(
        all(
            started[name] <= end[name] < started[name] + 5_000_000_000
            for name in ("utc_ns", "monotonic_ns")
        )
    )
    value["cleanup"] = {
        "version": "ctcc.native_original_oco_read_resources_retired.v1",
        "reservation_id": value["target"].reservation_id,
        "closure_outcome_id": value["result"].outcome_id,
        "request_sha256": sha(value["target"].request_json.encode()),
        "intent_sha256": value["target"].intent.sha256,
        "forensics_outcome_id": value["forensic_id"],
        "forensics_sha256": sha(value["report"].receipt_json),
        "manifest_outcome_id": value["current_manifest_id"],
        "read_only_purpose": True,
        "started_at": utc_from_ns(started["utc_ns"]).isoformat(),
        "completed_at": utc_from_ns(end["utc_ns"]).isoformat(),
        "started_monotonic_ns": started["monotonic_ns"],
        "completed_monotonic_ns": end["monotonic_ns"],
        "started_utc_ns": started["utc_ns"],
        "completed_utc_ns": end["utc_ns"],
        "original_role_route_sha256": value["roles"].role_route_sha256,
        "original_server_incarnation_sha256": value["roles"].server_incarnation_sha256,
        "closed_gets": gets,
        "retired_pools": retired,
        "execution_authority": False,
        "order_retry_authority": False,
    }
    value["source_registry"] = (source, record, record["generation"])
    value["caller_id"] = caller["id"]
    value["phase"] = "RESOURCES_CLOSED"


def mark_original_protective_selection_retired_v58(cap, *, key):
    value = _record(cap, key=key)
    post = value["post"]
    source, record, generation = value["source_registry"]
    _need(
        value["phase"] == "RESOURCES_CLOSED"
        and record is value["original_read_record"]
        and value["selection"] not in post._selector._SELECTIONS
        and value["binding"] not in post._LIVE
        and value["binding"] not in post._UNKNOWN
        and value["caller_id"] not in post._caller._HELD
        and generation not in source._RECONCILIATION_READ_V6
        and value["selection"] not in source._RECOVERY_SELECTION_USES_V6
    )
    end = native_stamp()
    cleanup = value["cleanup"]
    _need(
        all(
            cleanup["started_" + name]
            <= end[name]
            < cleanup["started_" + name] + 5_000_000_000
            for name in ("utc_ns", "monotonic_ns")
        )
    )
    cleanup["completed_at"] = utc_from_ns(end["utc_ns"]).isoformat()
    cleanup["completed_utc_ns"] = end["utc_ns"]
    cleanup["completed_monotonic_ns"] = end["monotonic_ns"]
    value["cleanup"]["original_selection_retired"] = True
    value["state"] = value["selection"] = value["source_registry"] = None
    value["original_read_record"] = value["original_read_source"] = None
    value["expected_reads"] = None
    value["phase"] = "RETIRED"


@asynccontextmanager
async def _writer(value):
    """Only the independently pinned shared SQL writer survives retirement."""
    _need(value["phase"] in {"RETIRED", "COMMITTED"})
    _writer_pins(value)
    async with (
        value["factory"]() as session,
        session.begin(),
        writer._verified_terminal_writer(
            session,
            value["engine"],
            value["roles"],
            value["names"],
            value["route"],
            profile=profiles.DB0058,
        ),
    ):
        scope = value["target"].request.scope
        await session.execute(
            text("SELECT pg_advisory_xact_lock(:key)"),
            {"key": journal.ledger._account_uid_lock_key(scope)},
        )
        await session.execute(
            text(
                "SELECT 1 FROM public.demo_account_controls WHERE environment=:env AND account_id=:uid FOR UPDATE"
            ),
            {"env": scope.environment, "uid": scope.account_id},
        )
        row = await session.get(
            Scope,
            (scope.environment, scope.account_id, scope.settlement_currency),
            with_for_update=True,
        )
        _need(type(row) is Scope)
        _writer_pins(value)
        yield session, row
        _writer_pins(value)


def _writer_pins(value):
    """Replay factory and source pins without consulting any retired owner."""
    _NATIVE_GUARD()
    guard.check_saved_native_guard_v1(value["guard"])
    _need(
        value["task"] is asyncio.current_task()
        and value["loop"] is asyncio.get_running_loop()
        and value["pid"] == os.getpid()
        and value["thread"] == threading.get_ident()
        and writer._factory_pins(value["selected"])
        == (value["factory"], value["engine"], value["names"], value["routes"])
        and writer.locked._direct_tls_route(value["engine"]) == value["route"]
    )


async def _append(value, session, scope, capture):
    _writer_pins(value)
    target = value["target"]
    rows = await journal._outcomes(session, target)
    _writer_pins(value)
    prior = rows[-1]
    at = utc_from_ns(native_stamp()["utc_ns"])
    _need(at >= scope.updated_at and at >= prior.recorded_at)
    scope.ledger_revision += 1
    scope.updated_at = at
    raw = canonical(
        {**capture, "execution_authority": False, "order_retry_authority": False}
    ).decode()
    body = {
        "version": "ctcc.native_submission_outcome.v1",
        "reservation_id": target.reservation_id,
        "intent_transition_id": prior.intent_transition_id,
        "intent_sha256": target.intent.sha256,
        "exchange_request_sha256": prior.exchange_request_sha256,
        "request_sha256": sha(target.request_json.encode()),
        "sequence": prior.sequence + 1,
        "previous_sha256": prior.outcome_id,
        "observation_kind": "reconciliation",
        "status": "acknowledged",
        "capture_sha256": sha(raw.encode()),
        "ledger_revision": scope.ledger_revision,
        "observed_at": at.isoformat(),
        "recorded_at": at.isoformat(),
        "execution_authority": False,
        "order_retry_authority": False,
    }
    encoded = canonical(body).decode()
    row = journal.Outcome(
        **{
            key: body[key]
            for key in (
                "reservation_id",
                "intent_transition_id",
                "sequence",
                "previous_sha256",
                "observation_kind",
                "status",
                "intent_sha256",
                "exchange_request_sha256",
                "capture_sha256",
                "ledger_revision",
            )
        },
        outcome_id=sha(encoded.encode()),
        capture_json=raw,
        outcome_json=encoded,
        observed_at=at,
        recorded_at=at,
    )
    session.add(row)
    await session.flush()
    _writer_pins(value)
    return row


async def persist_retired_protective_report_v58(cap, *, key):
    """Atomic durable ready+spool and a new committed replay; no file/exchange IO."""
    value = _record(cap, key=key)
    _need(value["phase"] == "RETIRED")
    target = value["target"]
    report = value["report"].receipt_json
    _, policy_json, policy_sha = policy_record(outbox.OutboxPolicy())
    async with asyncio.timeout(10):
        async with _writer(value) as (session, scope):
            current = await session.scalar(
                select(Spool).filter_by(
                    reservation_id=target.reservation_id, eligible=True
                )
            )
            if current is None:
                cleanup = value["cleanup"]
                closed = decode(value["closure_json"].encode(), 140000)
                raw = canonical(cleanup)
                saved = await _append(
                    value,
                    session,
                    scope,
                    {
                        "version": schema.CLEANUP_VERSION,
                        "reservation_id": target.reservation_id,
                        "request_sha256": sha(target.request_json.encode()),
                        "intent_sha256": target.intent.sha256,
                        "closure_outcome_id": value["result"].outcome_id,
                        "closure_receipt_sha256": closed["assessment_sha256"],
                        "forensics_outcome_id": value["forensic_id"],
                        "forensics_sha256": sha(report),
                        "manifest_outcome_id": value["current_manifest_id"],
                        "cleanup_receipt_json": raw.decode(),
                        "cleanup_receipt_sha256": sha(raw),
                        "read_resources_retired": True,
                    },
                )
                ready = await _append(
                    value,
                    session,
                    scope,
                    {
                        "version": schema.READY_VERSION,
                        "reservation_id": target.reservation_id,
                        "request_sha256": sha(target.request_json.encode()),
                        "intent_sha256": target.intent.sha256,
                        "closure_outcome_id": value["result"].outcome_id,
                        "cleanup_outcome_id": saved.outcome_id,
                        "forensics_outcome_id": value["forensic_id"],
                        "report_sha256": sha(report),
                        "policy_sha256": policy_sha,
                        "report_policy_version": schema.REPORT_POLICY_VERSION,
                        "report_policy_sha256": schema.REPORT_POLICY_SHA256,
                    },
                )
                current = Spool(
                    spool_id=spool_identity(
                        ready.outcome_id, sha(report), policy_sha, value["namespace"]
                    ),
                    outcome_id=ready.outcome_id,
                    reservation_id=target.reservation_id,
                    report_id=target.request.origin.candidate.report_id,
                    report_bytes=report,
                    report_sha256=sha(report),
                    policy_json=policy_json,
                    policy_sha256=policy_sha,
                    queue_namespace=value["namespace"],
                    eligible=True,
                    created_at=ready.recorded_at,
                )
                session.add(current)
                await session.flush()
            _need(
                current.report_bytes == report
                and current.queue_namespace == value["namespace"]
            )
            await replay.replay_protective_terminal_spool_data_v58(session, current)
            spool_id = current.spool_id
            xid = await session.scalar(text("SELECT pg_current_xact_id()::text"))
        value["phase"] = "COMMITTED"
        async with _writer(value) as (session, _):
            current = await session.get(Spool, spool_id)
            _need(
                current is not None
                and current.report_bytes == report
                and await session.scalar(text("SELECT pg_current_xact_id()::text"))
                != xid
            )
            await replay.replay_protective_terminal_spool_data_v58(session, current)
    result = ProtectiveReportSpoolReadbackV58(spool_id, sha(report), value["namespace"])
    del _WRITERS[cap]
    return result


_NATIVE_GUARD = guard.install_native_guards_v1(
    sys.modules[__name__],
    (
        "prepare_owned_protective_report_writer_v58",
        "capture_actual_protective_read_cleanup_v58",
        "mark_original_protective_selection_retired_v58",
        "persist_retired_protective_report_v58",
    ),
    dependencies=(
        writer,
        writer.writer56,
        writer.roles56,
        writer.roles56.current55,
        writer.witness56,
        writer.locked,
        writer.writer56.inherited,
        writer.writer56.inherited.inherited,
        writer.writer56.inherited.protective58,
        writer.writer56.inherited.protective58.schema,
        writer.writer56.inherited.protective58.submission,
        writer.writer56.inherited.protective58.report,
        writer.writer56.inherited.protective58.report_catalog,
        replay,
        journal,
        mapper,
        mapper.shared,
        mapper.shared.core,
        mapper.shared.entry,
        mapper.shared.execution,
        mapper.shared.protective,
        mapper.shared.facts,
        schema,
        profiles,
        outbox,
    ),
)
