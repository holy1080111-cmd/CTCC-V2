"""One-use native archival source facts, not portfolio or execution authority.

The only issuer acquires its own source, closes it, replays original B1/B3 and
positions bytes, and consumes an actual separate-transaction DB0046 readback.
Saved bundles have only the pure audit replay API in the companion module.
"""

import asyncio
import os
import re
import uuid
from dataclasses import dataclass
from datetime import timedelta
from threading import get_ident
from weakref import WeakKeyDictionary, ref

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.repositories import public_runtime_bundle_custody as custody
from app.database.repositories.account_capture_journal import (
    AccountCaptureJournalRepository,
)
from app.database.repositories.account_observation_index import (
    AccountObservationIndexRepository,
)
from app.database.repositories.qualification_ledger import QualificationLedgerRepository
from app.domain.source_primitives import canonical, sha, utc_from_ns, validate_stamps
from app.exchange.okx.private_rest import (
    DemoPinnedReadSession,
    OkxDemoPrivateRestClient,
)
from app.trade_qualification import account_bootstrap_runtime as bootstrap
from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_capture_journal as journal
from app.trade_qualification import account_collector as collector
from app.trade_qualification import account_history_native_stage_v1 as stage_api
from app.trade_qualification import account_history_source_bundle_v1 as bundle
from app.trade_qualification import account_lifecycle_history as lifecycle
from app.trade_qualification import account_native_clock as native
from app.trade_qualification import account_native_proof as proof
from app.trade_qualification import account_native_proof_storage as storage
from app.trade_qualification import account_native_runtime as runtime
from app.trade_qualification import account_observation_index as observed
from app.trade_qualification import account_positions_history_collector_v1 as positions
from app.trade_qualification import account_positions_history_v1 as position_pages
from app.trade_qualification.account_runtime import ControlledDemoAccountSession
from app.trade_qualification.reservations import LedgerScope

_OWNERS = WeakKeyDictionary()
_SESSION_USES = set()
_MAX_SESSION_TOMBSTONES = 4096
_MAX_RAW_ROOT_BYTES = (
    custody.MAX_BUNDLE_BYTES + stage_api.MAX_POSITION_RAW_BYTES + 2 * 1024 * 1024
)
_MAX_COMPANION_ROOT_BYTES = len(proof.FILES) * storage.MAX_PART


class NativeHistorySourceError(ValueError):
    """Fixed local codes; no private SQL, credential or source text."""


def _deny(code="native_history_source_unavailable"):
    raise NativeHistorySourceError(code) from None


@dataclass(frozen=True, slots=True, repr=False)
class NativeHistoryScopeV1:
    """Expected identity and declared routing only, never source attestation."""

    expected_uid: str
    expected_main_uid: str
    registration_evidence_sha256: str
    leverage_instrument_ids: tuple[str, ...]

    def __post_init__(self):
        if (
            any(
                type(v) is not str or re.fullmatch(r"[1-9][0-9]{0,39}", v) is None
                for v in (self.expected_uid, self.expected_main_uid)
            )
            or type(self.registration_evidence_sha256) is not str
            or re.fullmatch(r"[a-f0-9]{64}", self.registration_evidence_sha256) is None
            or type(self.leverage_instrument_ids) is not tuple
            or not 1 <= len(self.leverage_instrument_ids) <= 20
            or len(set(self.leverage_instrument_ids))
            != len(self.leverage_instrument_ids)
            or any(
                type(v) is not str
                or re.fullmatch(r"[A-Z0-9]+-[A-Z0-9]+-SWAP", v) is None
                for v in self.leverage_instrument_ids
            )
        ):
            _deny("native_history_scope_invalid")


class OwnedNativeHistorySourceFactsV1:
    __slots__ = ("__weakref__",)

    def __new__(cls):
        _deny("native_history_owner_not_constructible")

    def __copy__(self):
        _deny("native_history_owner_not_transferable")

    def __deepcopy__(self, memo):
        _deny("native_history_owner_not_transferable")

    def __reduce_ex__(self, protocol):
        _deny("native_history_owner_not_transferable")


def _context():
    task = asyncio.current_task()
    if task is None or task.cancelling():
        _deny("native_history_context_invalid")
    return os.getpid(), get_ident(), asyncio.get_running_loop(), task


def _factory_pin(factory):
    if (
        type(factory) is not async_sessionmaker
        or factory.class_ is not AsyncSession
        or type(factory.kw) is not dict
        or set(factory.kw) != {"bind", "autoflush", "expire_on_commit"}
        or type(factory.kw["autoflush"]) is not bool
        or type(factory.kw["expire_on_commit"]) is not bool
        or not custody._native_engine(factory.kw["bind"])
    ):
        _deny("native_history_native_factory_required")
    return (
        factory,
        factory.kw["bind"],
        factory.kw["autoflush"],
        factory.kw["expire_on_commit"],
    )


def _clone_factory(factory):
    pin = _factory_pin(factory)
    clone = async_sessionmaker(
        pin[1], class_=AsyncSession, autoflush=pin[2], expire_on_commit=pin[3]
    )
    return pin, clone, _factory_pin(clone)


def _check_pins(state):
    current = _context()
    if current[:3] != state["context"][:3] or current[3] is not state["context"][3]():
        _deny("native_history_context_changed")
    for pin in state["factory_pins"]:
        actual = _factory_pin(pin[0])
        if actual[0] is not pin[0] or actual[1] is not pin[1] or actual[2:] != pin[2:]:
            _deny("native_history_native_factory_changed")
    lease = state["lease"]
    session = state.get("session")
    if (
        lease is None
        or type(lease) is not DemoPinnedReadSession
        or lease._owner is not state["private_client"]
    ):
        _deny("native_history_lease_changed")
    lease.verify_current()
    if session is not None and (
        session._used is not True
        or session._pin != state["plan_sha256"]
        or session._checked_read_session(state["plan"]) is not lease
        or native._checked_credential_pin(session) != state["credential_pin"]
    ):
        _deny("native_history_session_changed")
    now = native.clock.native_stamp()
    validate_stamps((state["original"], state["last"], now))
    state["last"] = now
    return now


async def _checked_await(state, awaitable):
    try:
        _check_pins(state)
    except BaseException:
        awaitable.close()
        raise
    result = await awaitable
    _check_pins(state)
    return result


def _native_plan(scope, lease, stamp):
    end = utc_from_ns(stamp["utc_ns"])
    end = end.replace(microsecond=(end.microsecond // 1000) * 1000)
    return capture.AllProductDemoAccountCapturePlan(
        contract_version="ctcc.demo_account_plan.v5",
        plan_id="native-history-" + uuid.uuid4().hex,
        created_at=utc_from_ns(stamp["utc_ns"]),
        expected_uid=scope.expected_uid,
        expected_main_uid=scope.expected_main_uid,
        session_binding_id=lease.binding_id,
        settlement_currency="USDT",
        leverage_instrument_ids=scope.leverage_instrument_ids,
        history_start=end - timedelta(days=7),
        history_end=end,
        max_response_bytes=262144,
        max_total_bytes=4194304,
        registration_region="global",
        origin="https://openapi.okx.com",
        registration_evidence_sha256=scope.registration_evidence_sha256,
    )


def _identity(packet):
    matches = [
        page
        for page in packet.observations
        if page.request.endpoint == positions.CONFIG_ENDPOINT
    ]
    if len(matches) != 2:
        _deny("native_history_config_inventory_invalid")
    rows = [
        capture._decode_json(
            page.response_body, limit=positions.MAX_CONFIG_BYTES, wire=True
        )[0]["data"][0]
        for page in matches
    ]
    if journal.canonical(rows[0]) != journal.canonical(rows[1]):
        _deny("native_history_identity_changed")
    row = rows[0]
    return positions._NativePositionIdentityV1(
        row["uid"], row["mainUid"], row["acctLv"], row["posMode"]
    )


async def _population(state, repository, journal_repository, scope, *, fresh_reference):
    """Read the actual bounded full prefix, never manufacture a new genesis."""
    head = await _checked_await(state, repository._batch(scope))
    if (
        head is None
        or type(head.sequence) is not int
        or not 1 <= head.sequence <= observed.MAX_CAPTURES
    ):
        _deny("native_history_prefix_population_bound")
    batches, index_bytes = [], 0
    for number in range(1, head.sequence + 1):
        page = await _checked_await(
            state, repository.read(scope, after=number - 1, limit=1)
        )
        if type(page) is not tuple or len(page) != 1:
            _deny("native_history_prefix_changed")
        batch = page[0]
        if (
            type(batch) is not bundle.ObservationBatchReadback
            or type(batch.replay) is not observed.ObservationReplay
            or type(batch.document_json) is not bytes
            or type(batch.replay.receipt_json) is not bytes
        ):
            _deny("native_history_prefix_source_bound")
        index_bytes += len(batch.document_json) + len(batch.replay.receipt_json)
        if index_bytes > observed.MAX_SOURCE_BYTES:
            _deny("native_history_prefix_source_bound")
        batches.append(batch)
    batches = tuple(batches)
    if len(batches) != head.sequence or batches[-1].event_sha256 != head.event_sha256:
        _deny("native_history_prefix_changed")
    sources, source_bytes = [], 0
    for batch in batches:
        data = bundle._json(batch.document_json, observed.MAX_SOURCE_BYTES)
        reference = observed.CaptureReference(**data["reference"])
        chain = await _checked_await(
            state, journal_repository.read_chain(scope, reference.capture_id)
        )
        if type(chain) is not tuple or not 1 <= len(chain) <= journal.MAX_EVENTS:
            _deny("native_history_prefix_source_bound")
        for point in chain:
            if (
                type(point) is not journal.JournalReadback
                or type(point.event) is not journal._JournalEvent
            ):
                _deny("native_history_prefix_source_bound")
            point.__post_init__()
            source_bytes += sum(
                len(raw)
                for raw in (
                    point.event.event_json,
                    point.event.raw_body,
                    point.event.packet_payload,
                )
                if raw is not None
            )
            if source_bytes > observed.MAX_SOURCE_BYTES:
                _deny("native_history_prefix_source_bound")
        replayed, _, _, _ = proof._source(chain, scope)
        if replayed != reference:
            _deny("native_history_prefix_source_changed")
        sources.append((chain, reference))
    if sources[-1][1] != fresh_reference:
        _deny("native_history_latest_source_changed")
    sources = tuple(sources)
    lifecycle.source_set_sha256(sources)
    lifecycle._index(sources, batches, scope, head.event_sha256)
    again = await _checked_await(state, repository._batch(scope))
    if (
        again is None
        or again.sequence != head.sequence
        or again.event_sha256 != head.event_sha256
    ):
        _deny("native_history_latest_head_changed")
    return sources, batches


def _same_population(earlier, later):
    left_sources, left_batches = earlier
    right_sources, right_batches = later
    if [bundle.batch_document(v) for v in left_batches] != [
        bundle.batch_document(v) for v in right_batches
    ] or len(left_sources) != len(right_sources):
        _deny("native_history_readback_changed")
    for (old, old_ref), (new, new_ref) in zip(left_sources, right_sources, strict=True):
        if old_ref != new_ref or len(old) != len(new):
            _deny("native_history_readback_changed")
        for a, b in zip(old, new, strict=True):
            if (
                a.event != b.event
                or a.db_recorded_at != b.db_recorded_at
                or b.readback_at < a.readback_at
            ):
                _deny("native_history_readback_changed")


def _part(directory, name, raw):
    _file_pin(raw)
    directory.publish(name, raw)
    if directory.read(name, len(raw)) != raw:
        _deny("native_history_file_readback_changed")


def _check_original_wire_secrets(population, position, position_plan, tokens):
    """Scan original bodies with the final union before any hex encoding.

    Older accepted journals stay immutable. A later discovered echo prevents
    this invocation from promoting any member of the population into custody.
    """
    for chain, _ in population[0]:
        for point in chain:
            if point.event.raw_body is not None:
                runtime._secret_checked(point.event.raw_body, tokens)
    archive = bundle._json(position.raw_archive, positions.MAX_ARCHIVE_BYTES)
    pages = positions._unfreeze_pages(position_plan, archive["history_archive"])
    for body in (
        positions._read_record(archive["config_before"]).raw,
        *(page.raw_body for page in pages),
        positions._read_record(archive["config_after"]).raw,
    ):
        runtime._secret_checked(body, tokens)


def _file_pin(raw):
    if type(raw) is not bytes or not 0 < len(raw) <= custody.MAX_BUNDLE_BYTES:
        _deny("native_history_file_inventory_invalid")
    return len(raw), sha(raw)


def _produced_raw_inventory(*, claim, raw, accepted, position, position_plan, trace):
    """Derive the exact success inventory from owned bytes, never adopt files."""
    result = {
        "attempt.claim.json": _file_pin(claim),
        "source.bundle.json": _file_pin(raw),
        "accepted.json": _file_pin(accepted),
    }
    archive = bundle._json(position.raw_archive, positions.MAX_ARCHIVE_BYTES)
    pages = positions._unfreeze_pages(position_plan, archive["history_archive"])
    raw_pages = (
        positions._read_record(archive["config_before"]).raw,
        *(page.raw_body for page in pages),
        positions._read_record(archive["config_after"]).raw,
    )
    if (
        len(raw_pages) != len(trace["pages"])
        or len(raw_pages) > stage_api.MAX_POSITION_PAGES
        or len(trace["events"]) > stage_api.MAX_POSITION_EVENTS
    ):
        _deny("native_history_file_inventory_invalid")
    for index, (body, marker) in enumerate(zip(raw_pages, trace["pages"], strict=True)):
        result[f"positions-{index:02d}.raw"] = _file_pin(body)
        result[f"positions-{index:02d}.json"] = _file_pin(canonical(marker))
    for index, event in enumerate(trace["events"]):
        result[f"position-phase-{index:05d}.json"] = _file_pin(canonical(event))
    return result


def _inventory(state, directory, expected, *, max_total_bytes):
    _check_pins(state)
    names = directory.names()
    if (
        type(expected) is not dict
        or len(names) != len(set(names))
        or set(names) != set(expected)
    ):
        _deny("native_history_file_inventory_invalid")
    total = 0
    for size, digest in expected.values():
        if (
            type(size) is not int
            or not 0 < size <= custody.MAX_BUNDLE_BYTES
            or type(digest) is not str
            or re.fullmatch(r"[a-f0-9]{64}", digest) is None
        ):
            _deny("native_history_file_inventory_invalid")
        total += size
        if total > max_total_bytes:
            _deny("native_history_file_inventory_limit")
    for name in sorted(expected):
        _check_pins(state)
        size, digest = expected[name]
        raw = directory.read(name, size)
        if type(raw) is not bytes or len(raw) != size or sha(raw) != digest:
            _deny("native_history_file_inventory_changed")
        _check_pins(state)
    if set(directory.names()) != set(expected):
        _deny("native_history_file_inventory_changed")


async def acquire_native_history_source_facts_v1(
    *,
    read_session,
    private_client,
    scope_config,
    session_factory,
    custody_session_factory,
    raw_root,
    companion_root,
):
    """Fixed fresh native source path; no input clock, raw, policy or callbacks."""
    state, successful = None, False
    try:
        if (
            type(read_session) is not DemoPinnedReadSession
            or type(private_client) is not OkxDemoPrivateRestClient
            or read_session._owner is not private_client
            or read_session._origin != "https://openapi.okx.com"
            or type(scope_config) is not NativeHistoryScopeV1
        ):
            _deny("native_history_lease_required")
        scope_config.__post_init__()
        # A runtime-issued read lease is burned before native sampling/file/DB I/O.
        session_key = sha(read_session.binding_id.encode("ascii"))
        if (
            session_key in _SESSION_USES
            or len(_SESSION_USES) >= _MAX_SESSION_TOMBSTONES
        ):
            _deny("native_history_lease_already_used")
        _SESSION_USES.add(session_key)
        read_session.verify_current()
        original = native.clock.native_stamp()
        main_pin, factory, private_pin = _clone_factory(session_factory)
        custody_pin, custody_factory, private_custody_pin = _clone_factory(
            custody_session_factory
        )
        context = _context()
        state = {
            "context": (*context[:3], ref(context[3])),
            "lease": read_session,
            "private_client": private_client,
            "original": original,
            "last": original,
            "factory_pins": (main_pin, private_pin, custody_pin, private_custody_pin),
            "factory": factory,
            "raw_root": raw_root,
            "companion_root": companion_root,
        }
        plan = _native_plan(scope_config, read_session, original)
        pin = capture.plan_sha256(plan)
        session = ControlledDemoAccountSession.from_pinned_read_session(
            read_session=read_session, plan=plan, expected_plan_sha256=pin
        )
        scope = LedgerScope(account_id=plan.expected_uid, settlement_currency="USDT")
        with (
            storage._companion_attempt(raw_root) as raw_dir,
            storage._companion_attempt(companion_root) as clock_dir,
        ):
            if storage._root_identity(raw_dir.path) == storage._root_identity(
                clock_dir.path
            ):
                _deny("native_history_distinct_roots_required")
            claim = canonical(
                {
                    "schema_version": "ctcc.native_history_attempt.v1",
                    "attempt_id": uuid.uuid4().hex,
                    "policy_sha256": bundle.POLICY_SHA256,
                    "plan_sha256": pin,
                    "original_started": original,
                    "execution_authority": False,
                }
            )
            _part(raw_dir, "attempt.claim.json", claim)
            try:
                with (
                    native._claim_initial_session(session),
                    native._initial_stage(
                        plan_sha256=pin,
                        scope_sha256=proof.scope_sha256(scope),
                        _claimed_session=session,
                    ) as stage,
                ):
                    ns = native._state(stage)
                    state.update(
                        session=session,
                        plan=plan,
                        plan_sha256=pin,
                        credential_pin=native._checked_credential_pin(session),
                    )
                    clock = ns["clock"]
                    ledger = QualificationLedgerRepository(factory, clock=clock)
                    journal_repo = AccountCaptureJournalRepository(factory, clock=clock)
                    index_repo = AccountObservationIndexRepository(factory, clock=clock)
                    custody_repo = custody.RuntimeBundleCustodyRepository(
                        custody_factory
                    )
                    await _checked_await(state, custody_repo.verify_deployment())
                    files = {
                        "host-before.json": native._host_observation(stage, "before")
                    }
                    tokens = list(
                        collector._credential_values(session._credentials)[:3]
                    )
                    runtime._secret_checked(files["host-before.json"], tokens)
                    storage._publish_part(
                        clock_dir, "host-before.json", files["host-before.json"]
                    )
                    if ns["clock_results"] != {"before": "accepted"}:
                        _deny("native_history_host_clock_rejected")
                    time_plan = proof._exchange_plan(ns["started"])
                    files["exchange-plan.json"] = time_plan.canonical_bytes()
                    storage._publish_part(
                        clock_dir, "exchange-plan.json", files["exchange-plan.json"]
                    )
                    before = await _checked_await(
                        state,
                        runtime._time_probe(
                            stage,
                            time_plan,
                            directory=clock_dir,
                            phase="before",
                            tokens=tokens,
                        ),
                    )
                    for name, raw in zip(
                        (
                            "exchange-before.json",
                            "exchange-before.raw",
                            "exchange-before-trace.json",
                        ),
                        before,
                        strict=True,
                    ):
                        files[name] = raw
                    recorded, journal_owner = await _checked_await(
                        state,
                        bootstrap._collect_recorded(
                            session,
                            repository=ledger,
                            journal_repository=journal_repo,
                            clock=clock,
                            barrier_completed_at=utc_from_ns(ns["started"]["utc_ns"]),
                            _native_observer=ns["observer"],
                        ),
                    )
                    if (
                        type(journal_owner) is not journal._OwnedAccountJournal
                        or journal_owner is not ns.get("journal")
                        or not journal_owner.finished
                        or not journal_owner.acquisition_ok
                        or not journal_owner.closed_safe
                        or journal_owner.repository is not journal_repo
                        or recorded.bootstrap.transport_provenance
                        != "owned_signed_verified_tls"
                        or ns["closed"] is not True
                    ):
                        _deny("native_history_original_source_required")
                    chain = await _checked_await(
                        state, journal_repo.read_chain(scope, journal_owner.capture_id)
                    )
                    reference, packet, _, _ = proof._source(chain, scope)
                    if (
                        reference.head_sha256 != journal_owner.previous
                        or reference.packet_sha256 != journal_owner.owned_packet_sha256
                        or packet.plan != plan
                    ):
                        _deny("native_history_original_source_changed")
                    latest = await _checked_await(state, index_repo._batch(scope))
                    revision = 0 if latest is None else latest.sequence
                    if (
                        type(revision) is not int
                        or not 0 <= revision < observed.MAX_CAPTURES
                    ):
                        _deny("native_history_prefix_population_bound")
                    window = observed.ExecutionWindow(
                        plan.history_start, plan.history_end - timedelta(seconds=300)
                    )
                    await _checked_await(
                        state,
                        index_repo.append(
                            scope, reference, window, expected_revision=revision
                        ),
                    )
                    population = await _population(
                        state,
                        index_repo,
                        journal_repo,
                        scope,
                        fresh_reference=reference,
                    )
                    identity = _identity(packet)
                    position_plan = position_pages.PositionHistoryPlanV1(
                        "global",
                        plan.origin,
                        identity.sha256,
                        reference.session_binding_sha256,
                        utc_from_ns(_check_pins(state)["utc_ns"]),
                        position_pages._milliseconds(plan.history_start),
                    )
                    with stage_api._positions_observer(
                        stage, directory=raw_dir, tokens=journal_owner.tokens
                    ) as observer:
                        position, trace = await _checked_await(
                            state,
                            positions._collect_native_position_history(
                                credentials=session._credentials,
                                read_session=read_session,
                                clock=clock,
                                plan=position_plan,
                                identity=identity,
                                barrier_completed_at=position_plan.created_at,
                                _native_observer=observer,
                            ),
                        )
                        final_tokens = tuple(stage_api._known_tokens(observer))
                    files["host-after.json"] = native._host_observation(stage, "after")
                    runtime._secret_checked(files["host-after.json"], final_tokens)
                    storage._publish_part(
                        clock_dir, "host-after.json", files["host-after.json"]
                    )
                    if ns["clock_results"].get("after") != "accepted":
                        _deny("native_history_host_clock_rejected")
                    after = await _checked_await(
                        state,
                        runtime._time_probe(
                            stage,
                            time_plan,
                            directory=clock_dir,
                            phase="after",
                            tokens=final_tokens,
                        ),
                    )
                    for name, raw in zip(
                        (
                            "exchange-after.json",
                            "exchange-after.raw",
                            "exchange-after-trace.json",
                        ),
                        after,
                        strict=True,
                    ):
                        files[name] = raw
                    confirmed = await _population(
                        state,
                        index_repo,
                        journal_repo,
                        scope,
                        fresh_reference=reference,
                    )
                    _same_population(population, confirmed)
                    _check_original_wire_secrets(
                        population, position, position_plan, final_tokens
                    )
                    for source_bytes in files.values():
                        runtime._secret_checked(source_bytes, final_tokens)
                    temporal = {
                        "schema_version": bundle.PROOF_SCHEMA,
                        "policy_sha256": bundle.POLICY_SHA256,
                        "stage": {
                            "kind": "native_archival_acquisition_v1",
                            "invocation_id": ns["invocation_id"],
                            "started": ns["started"],
                        },
                        "original_started": original,
                        "witnesses": ns["witnesses"],
                        "finalization_witnesses": ns["finalization_witnesses"],
                        "positions_trace": trace,
                        "clock_files_hex": {
                            name: raw.hex() for name, raw in files.items()
                        },
                        "source_readback_complete": _check_pins(state),
                        "proof_persist_start": _check_pins(state),
                    }
                    raw = bundle.freeze_history_source_bundle_v1(
                        scope=scope,
                        sources=population[0],
                        batches=population[1],
                        position_plan=position_plan,
                        identity=identity,
                        position_archive=position,
                        temporal=temporal,
                    )
                    # Only the pinned nonsecret session label is permitted in
                    # metadata. Original wire bodies were scanned above with
                    # the full set, before base64/hex could hide an echo.
                    metadata_tokens = (*final_tokens[:3], *final_tokens[4:])
                    runtime._secret_checked(position.raw_archive, metadata_tokens)
                    runtime._secret_checked(raw, metadata_tokens)
                    proof_pin = sha(journal.canonical(temporal))
                    receipt = await _checked_await(
                        state,
                        custody_repo.seal_bundle(
                            bundle_raw=raw,
                            expected_sha256=sha(raw),
                            proof_envelope_sha256=proof_pin,
                        ),
                    )
                    retained = custody._consume_runtime_bundle_custody_receipt(
                        receipt,
                        expected_bundle_sha256=sha(raw),
                        expected_proof_envelope_sha256=proof_pin,
                    )
                    if retained.bundle_raw != raw:
                        _deny("native_history_custody_readback_changed")
                    _same_population(
                        population,
                        await _population(
                            state,
                            index_repo,
                            journal_repo,
                            scope,
                            fresh_reference=reference,
                        ),
                    )
                    facts = bundle.replay_history_source_bundle_v1(
                        raw, expected_sha256=sha(raw)
                    )
                    _part(raw_dir, "source.bundle.json", raw)
                    accepted = canonical(
                        {
                            "schema_version": "ctcc.native_history_source_readback.v1",
                            "bundle_sha256": sha(raw),
                            "proof_sha256": proof_pin,
                            "custody_bundle_id": retained.bundle_id,
                            "custody_recorded_at": retained.recorded_at.isoformat(),
                            "custody_readback_at": retained.database_readback_at.isoformat(),
                            "execution_authority": False,
                        }
                    )
                    _part(raw_dir, "accepted.json", accepted)
                    expected_raw = _produced_raw_inventory(
                        claim=claim,
                        raw=raw,
                        accepted=accepted,
                        position=position,
                        position_plan=position_plan,
                        trace=trace,
                    )
                    expected_companions = {
                        name: _file_pin(files[name]) for name in proof.FILES
                    }
                    _inventory(
                        state,
                        raw_dir,
                        expected_raw,
                        max_total_bytes=_MAX_RAW_ROOT_BYTES,
                    )
                    _inventory(
                        state,
                        clock_dir,
                        expected_companions,
                        max_total_bytes=_MAX_COMPANION_ROOT_BYTES,
                    )
                    state.update(
                        facts=facts,
                        population=population,
                        reference=reference,
                        scope=scope,
                        root_identity=storage._root_identity(raw_dir.path),
                        companion_identity=storage._root_identity(clock_dir.path),
                        raw_inventory=expected_raw,
                        companion_inventory=expected_companions,
                        source_sha256=sha(raw),
                        claim_sha256=sha(claim),
                    )
                    _check_pins(state)
                    owner = object.__new__(OwnedNativeHistorySourceFactsV1)
                    _OWNERS[owner] = state
                    successful = True
                    return owner
            finally:
                if not successful:
                    # No overwrite, removal, retry or promotion of an uncertain
                    # earlier DB/file outcome. An existing claim stays consumed.
                    try:
                        _part(
                            raw_dir,
                            "failed.json",
                            canonical(
                                {
                                    "schema_version": "ctcc.native_history_attempt_failed.v1",
                                    "outcome": "INCOMPLETE",
                                    "execution_authority": False,
                                }
                            ),
                        )
                    except Exception:  # noqa: BLE001 -- retain failure without exposing private I/O errors
                        if not asyncio.current_task().cancelling():
                            _deny("native_history_failure_retention_incomplete")
    except asyncio.CancelledError:
        raise
    except NativeHistorySourceError:
        raise
    except Exception:  # noqa: BLE001 -- never expose private transport/SQL errors
        _deny()


def consume_owned_native_history_source_facts_v1(owner):
    """Synchronously burn before any consumer await; all late failures stay burned."""
    state = (
        _OWNERS.pop(owner, None)
        if type(owner) is OwnedNativeHistorySourceFactsV1
        else None
    )
    if state is None:
        _deny("native_history_owner_unavailable")
    try:
        _check_pins(state)
    except BaseException:  # noqa: BLE001 -- burn on interruption without exposing private errors
        state.clear()
        _deny("native_history_owner_unavailable")

    async def consume():
        try:

            def clock():
                return utc_from_ns(_check_pins(state)["utc_ns"])

            _check_pins(state)
            with storage._root_context(state["raw_root"]) as directory:
                if storage._root_identity(directory.path) != state["root_identity"]:
                    _deny("native_history_original_files_changed")
                _inventory(
                    state,
                    directory,
                    state["raw_inventory"],
                    max_total_bytes=_MAX_RAW_ROOT_BYTES,
                )
            with storage._root_context(state["companion_root"]) as directory:
                if (
                    storage._root_identity(directory.path)
                    != state["companion_identity"]
                ):
                    _deny("native_history_original_files_changed")
                _inventory(
                    state,
                    directory,
                    state["companion_inventory"],
                    max_total_bytes=_MAX_COMPANION_ROOT_BYTES,
                )
            factory = state["factory"]
            journal_repo = AccountCaptureJournalRepository(factory, clock=clock)
            index_repo = AccountObservationIndexRepository(factory, clock=clock)
            current = await _population(
                state,
                index_repo,
                journal_repo,
                state["scope"],
                fresh_reference=state["reference"],
            )
            _same_population(state["population"], current)
            facts = bundle.replay_history_source_bundle_v1(
                state["facts"].bundle_raw, expected_sha256=state["source_sha256"]
            )
            for root_key, identity_key, inventory_key in (
                ("raw_root", "root_identity", "raw_inventory"),
                ("companion_root", "companion_identity", "companion_inventory"),
            ):
                with storage._root_context(state[root_key]) as directory:
                    if storage._root_identity(directory.path) != state[identity_key]:
                        _deny("native_history_original_files_changed")
                    _inventory(
                        state,
                        directory,
                        state[inventory_key],
                        max_total_bytes=_MAX_RAW_ROOT_BYTES
                        if root_key == "raw_root"
                        else _MAX_COMPANION_ROOT_BYTES,
                    )
            _check_pins(state)
            return facts
        except asyncio.CancelledError:
            raise
        except NativeHistorySourceError:
            raise
        except Exception:  # noqa: BLE001 -- no private details in rejection
            _deny("native_history_owner_unavailable")
        finally:
            state.clear()

    return consume()
