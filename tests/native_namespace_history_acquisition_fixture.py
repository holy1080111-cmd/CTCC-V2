"""Whole-body synthetic IO fixture; no native TLS, PG, catalog seal or owner claim.

The real new _acquire body, collector/B1 finalization, V4/V6 raw/proof readers,
Control replay, coverage observation, phase._join, file publishers and receipt
consumer remain unchanged. Synthetic IO replaces network/TLS/host samples and
the lowest journal/checkpoint/custody repository reads. The private moved state
is explicitly synthetic test input, never a public positive adapter.
"""

import json
from contextlib import asynccontextmanager, contextmanager
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from weakref import ref

from app.database.repositories import controlled_writer_coverage as coverage_rows
from app.database.repositories import public_runtime_bundle_custody as custody
from app.database.repositories.account_capture_journal import (
    AccountCaptureJournalRepository,
)
from app.database.repositories.qualification_ledger import (
    LedgerBootstrapCheckpoint,
    _ConsumedEventJournalInspection,
)
from app.domain.source_primitives import canonical, sha, utc_from_ns
from app.trade_qualification import account_bootstrap_runtime as bootstrap
from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_capture_journal as journal
from app.trade_qualification import account_current_source_v4 as account
from app.trade_qualification import account_native_clock as native
from app.trade_qualification import account_native_proof as proof
from app.trade_qualification import account_native_proof_storage as storage
from app.trade_qualification import account_observation_index as observed
from app.trade_qualification import controlled_writer_coverage_v1 as coverage
from app.trade_qualification import demo_control as control
from app.trade_qualification import demo_operational_route_v4 as route
from app.trade_qualification import native_namespace_history_bundle_v1 as bundle
from app.trade_qualification import native_namespace_history_source_v1 as source
from app.trade_qualification import native_namespace_phase_v1 as phase
from app.trade_qualification import prospective_event_cutover_v2 as cutover
from app.trade_qualification.account_runtime import ControlledDemoAccountSession
from app.trade_qualification.reservations import digest as ledger_digest
from tests.controlled_writer_coverage_fixtures import (
    coverage_observation_fixture,
    coverage_record_fixture,
)
from tests.prospective_cutover_fixtures import (
    cutover_record_fixture,
    empty_scan_document,
)
from tests.unit.research.test_demo_operational_route_v4 import (
    _config,
    _native_config_fixture,
)
from tests.unit.test_account_current_source_v4 import setup as original_io_setup
from tests.unit.test_account_native_proof import (
    declared_native_source,
    synthetic_tls_chain,
)
from tests.unit.test_qualification_account_algo_history_v6 import STREAM, scripted_pages
from tests.unit.test_qualification_account_collector import Stream
from tests.unit.test_qualification_bootstrap_runtime import unknown_state


def refresh_source_provider_pins(monkeypatch):
    # Only explicitly declared IO providers above are substituted. New source,
    # crypto/clock leaves, pure replay, MOVE and context functions stay original.
    for module in (source, phase, cutover):
        monkeypatch.setattr(
            module,
            "_PINS",
            tuple(
                (
                    owner,
                    name,
                    getattr(getattr(owner, name), "__func__", getattr(owner, name)),
                )
                for owner, name, _ in module._PINS
            ),
        )


def _coverage_pair(sources, samples):
    events, previous_control, original_active, revoked = [], None, None, None
    series = []
    uid = sources[0]["replay"].packet.plan.expected_uid
    scope = control.ControlScope("demo", uid)
    for number, captured in enumerate(sources, 1):
        replay, raw = captured["replay"], captured["raw"]
        identity = json.loads(raw)["route_proof"]["account_identity"]
        credential = json.loads(raw)["route_proof"]["credential_session_sha256"]
        now = captured["after"]
        pins = control.ControlPins(str(number) * 64, "2" * 64, credential)
        acquired = control.acquire(
            previous_control,
            scope=scope,
            owner_sha256=str(number + 3) * 64,
            pins=pins,
            now=now,
        )
        stopped = control.advance(
            acquired,
            owner_sha256=acquired.owner_sha256,
            owner_epoch=acquired.owner_epoch,
            expected_revision=acquired.revision,
            action="estop",
            command_id=str(number + 6) * 64,
            now=now + timedelta(microseconds=1),
        )
        for action, state in (("acquire", acquired), ("estop", stopped)):
            command = f"{state.revision:x}" * 64
            raw_event = control.canonical(
                {
                    "version": "ctcc.demo_control_event.v1",
                    "command_id": command,
                    "action": action,
                    "previous_sha256": None if not events else events[-1].event_sha256,
                    "state": control.document(state),
                }
            )
            events.append(
                control.ControlJournalSourceRowV1(
                    scope,
                    state.revision,
                    command,
                    action,
                    None if not events else events[-1].event_sha256,
                    control.digest(raw_event),
                    control.digest(control.canonical(control.document(state))),
                    raw_event,
                    state.updated_at,
                )
            )
        previous_control = stopped
        source_pins = {
            "source_bundle_sha256": sha(raw),
            "source_custody_id": captured["custody_id"],
            "source_plan_sha256": replay.reference.plan_sha256,
            "source_chain_head_sha256": replay.reference.head_sha256,
            "source_proof_envelope_sha256": replay.proof_envelope_sha256,
            "source_current_receipt_sha256": sha(replay.current_receipt_json),
            "source_native_proof_sha256": replay.native_proof_sha256,
            "source_native_readback_sha256": replay.native_readback_sha256,
            "source_current_expires_at": replay.expires_at.isoformat(),
            "source_current_monotonic_deadline_ns": replay.monotonic_deadline_ns,
            "credential_session_sha256": credential,
            "identity_sha256": sha(canonical(identity)),
        }
        base = {
            **source_pins,
            "generation": number,
            "epoch_id": str(number + 7) * 32,
            "process_domain_nonce": str(number + 4) * 32,
            "journal_sequence": 3 * number - 2,
            "created_at": stopped.updated_at.isoformat()
            if original_active is None
            else original_active.state_document["created_at"],
        }
        prepared = coverage_record_fixture(
            uid=uid,
            now=stopped.updated_at,
            control=stopped,
            control_event_sha256=events[-1].event_sha256,
            state_overrides=base,
            previous_sha256=None if revoked is None else revoked["event_sha256"],
        )
        activated = coverage_record_fixture(
            uid=uid,
            now=stopped.updated_at,
            control=stopped,
            control_event_sha256=events[-1].event_sha256,
            state_overrides={
                **prepared["state"],
                "state": "active",
                "journal_sequence": 3 * number - 1,
            },
            previous_sha256=prepared["event_sha256"],
            audit_raw=prepared["audit_raw"],
        )
        active = coverage_observation_fixture(
            activated["state_raw"],
            activated["event_raw"],
            activated["audit_raw"],
            prior_records=(prepared,),
            previous_terminal_anchor=revoked,
        )
        lineage = control.replay_control_journal_lineage_v1(
            tuple(events),
            scope=scope,
            target_state=stopped,
            expected_head_sha256=events[-1].event_sha256,
        )
        keys = frozenset({"a" * 64})
        scan_time = stopped.updated_at + timedelta(microseconds=1)
        scan = empty_scan_document(uid, scan_time)
        scan.update(event_count=1, event_keys_sha256=sha(canonical(sorted(keys))))
        inspection = _ConsumedEventJournalInspection(
            keys, canonical(scan), scan_time, scan_time
        )
        series.append((active, lineage, inspection))
        if number == 1:
            original_active = active
            revoked_at = stopped.updated_at + timedelta(minutes=5)
            revoke_doc = coverage_rows._revocation_document(
                active.state_document,
                now=revoked_at,
                reason="expired",
                observed={
                    "revision": stopped.revision,
                    "owner_epoch": stopped.owner_epoch,
                    "event_sha256": events[-1].event_sha256,
                    "state_sha256": control.digest(
                        control.canonical(control.document(stopped))
                    ),
                    "emergency_stop": True,
                    "arm_request_id": None,
                },
            )
            revoked = coverage_record_fixture(
                uid=uid,
                now=revoked_at,
                control=stopped,
                control_event_sha256=events[-1].event_sha256,
                state_overrides=revoke_doc,
                previous_sha256=active.journal_sha256,
                audit_raw=active.audit_json,
            )
    del samples  # No clock is rewound or old current window altered.
    return series


@asynccontextmanager
async def whole_acquisition_fixture(
    monkeypatch, tmp_path, *, failure=None, carried_note=None, carried_note_phase=1
):
    if carried_note is not None:
        assert type(carried_note) is str and carried_note.isascii()
        assert carried_note_phase in (1, 2)
    original_companion = storage._companion_attempt
    original_root = storage._root_context
    original_identity = storage._root_identity
    original_public_root = account.public_storage._native_recheck_root
    native_client_factory = route.collector._new_client
    original_collect = bootstrap._collect_recorded
    original_stage = native._initial_stage
    chains, retained, source_captures, config_clients = {}, {}, [], []
    with original_io_setup(monkeypatch, tmp_path / "bootstrap-provider") as io:
        # The borrowed setup creates one unused config-provider pair. Close it
        # before installing the fresh per-phase providers below.
        await io.route_response.aclose()
        await io.route_client.aclose()
        # Real source publishers/readback/stat checks in the owned tmp directory.
        monkeypatch.setattr(storage, "_companion_attempt", original_companion)
        monkeypatch.setattr(storage, "_root_context", original_root)
        monkeypatch.setattr(storage, "_root_identity", original_identity)
        monkeypatch.setattr(
            account.public_storage, "_native_recheck_root", original_public_root
        )

        async def append(repository, scope, event):
            value = journal.checked_event(event)
            points = chains.setdefault(value["capture_id"], [])
            assert value["sequence"] == len(points) + 1
            assert value["previous_sha256"] == (
                None if not points else sha(points[-1].event.event_json)
            )
            readback = journal.JournalReadback(
                event, repository.clock(), repository.clock()
            )
            points.append(readback)
            return readback

        async def read(_repository, _scope, capture_id):
            return tuple(chains[capture_id])

        async def checkpoint(repository, scope):
            state = unknown_state().model_copy(update={"scope": scope})
            return LedgerBootstrapCheckpoint(
                state, repository.clock(), repository.clock(), ledger_digest(state)
            )

        async def collect(session, **kwargs):
            io.harness.clock = kwargs["clock"]
            value, owner = await original_collect(session, **kwargs)
            relabeled = synthetic_tls_chain(tuple(chains[owner.capture_id]))
            chains[owner.capture_id][:] = relabeled
            owner.previous = observed.source_reference(relabeled).head_sha256
            return replace(
                value,
                bootstrap=replace(
                    value.bootstrap, transport_provenance="owned_signed_verified_tls"
                ),
            ), owner

        async def deployment(_repository):
            return None

        async def seal(
            _repository, *, bundle_raw, expected_sha256, proof_envelope_sha256
        ):
            assert sha(bundle_raw) == expected_sha256
            document = json.loads(bundle_raw)
            if document["schema_version"] == account.SCHEMA:
                assert (
                    account.replay_current_account_bundle_v4(
                        bundle_raw, expected_sha256=expected_sha256
                    ).proof_envelope_sha256
                    == proof_envelope_sha256
                )
            else:
                bundle.replay_native_namespace_history_bundle_v1(
                    bundle_raw, expected_sha256=expected_sha256
                )
                if failure == "custody_commit_unknown":
                    raise RuntimeError("synthetic-commit-unknown")
            identifier = f"{len(retained) + 1:032x}"
            at = utc_from_ns(io.samples()["utc_ns"])
            readback_raw = (
                bundle_raw + b" "
                if document["schema_version"] == bundle.SCHEMA
                and failure == "custody_readback_changed"
                else bundle_raw
            )
            readback = custody.RetainedRuntimeBundleReadback(
                identifier, expected_sha256, proof_envelope_sha256, at, at, readback_raw
            )
            retained[identifier] = readback
            receipt = custody.OwnedRuntimeBundleCustodyReceipt(custody._ISSUER)
            custody._RECEIPTS[receipt] = (
                readback,
                custody._owner(),
                custody.time.monotonic_ns(),
            )
            return receipt

        async def retained_read(
            _repository, *, bundle_id, expected_sha256, proof_envelope_sha256
        ):
            value = retained[bundle_id]
            assert (
                value.bundle_sha256 == expected_sha256
                and value.proof_envelope_sha256 == proof_envelope_sha256
            )
            return value

        monkeypatch.setattr(AccountCaptureJournalRepository, "_append", append)
        monkeypatch.setattr(AccountCaptureJournalRepository, "read_chain", read)
        for name in ("initialize_capture_scope", "read_bootstrap_checkpoint"):
            monkeypatch.setattr(source.QualificationLedgerRepository, name, checkpoint)
        monkeypatch.setattr(bootstrap, "_collect_recorded", collect)
        monkeypatch.setattr(
            custody.RuntimeBundleCustodyRepository, "verify_deployment", deployment
        )
        monkeypatch.setattr(custody.RuntimeBundleCustodyRepository, "seal_bundle", seal)
        monkeypatch.setattr(
            custody.RuntimeBundleCustodyRepository,
            "read_retained_bundle",
            retained_read,
        )

        def fresh_route():
            monkeypatch.setattr(route, "_native_client", native_client_factory)
            client, response = _native_config_fixture(
                monkeypatch,
                Stream(
                    _config(
                        uid=io.session._plan.expected_uid,
                        main_uid=io.session._plan.expected_main_uid,
                    )
                ),
            )
            monkeypatch.setattr(route, "native_stamp", io.samples)
            config_clients.append((client, response))

        for number in (1, 2):
            if number == 2:
                # A new declared later source, with an actually different plan,
                # in the same still-active original read lease. Never rewrite
                # the old bundle, old receive stamp or old expiry.
                io.samples.calls += 360_000
            io.harness.requests.clear()
            io.harness.script = declared_native_source("fresh_flat", current_v7=True)[0]
            if carried_note is not None and number == carried_note_phase:
                # Predeclared synthetic lower HTTP source, before any original
                # page request. Never rewrite an accepted bundle or timestamp.
                for stream, rows in io.harness.script:
                    if stream == "balance":
                        rows[0]["audit_note"] = carried_note
            fresh_route()
            selected = (
                io.session._plan
                if number == 1
                else io.session._plan.model_copy(
                    update={
                        "plan_id": "synthetic-second-phase",
                        "created_at": utc_from_ns(io.samples()["utc_ns"]),
                    }
                )
            )
            lease = io.session._checked_read_session(io.session._plan)
            session = (
                io.session
                if number == 1
                else ControlledDemoAccountSession.from_pinned_read_session(
                    read_session=lease,
                    plan=selected,
                    expected_plan_sha256=capture.plan_sha256(selected),
                )
            )
            private_root, native_root = (
                tmp_path / f"phase-{number}-private",
                tmp_path / f"phase-{number}-native",
            )
            for root in (private_root, native_root):
                root.mkdir(parents=True)
            owner = await account.acquire_initial_demo_account_source_v4(
                **{
                    **io.args,
                    "account_session": session,
                    "private_proof_root": private_root,
                    "native_proof_root": native_root,
                }
            )
            held = dict(account._CURRENT[owner])
            account.consume_owned_current_demo_account_v4(owner)
            raw = held["bundle_raw"]
            after_stamp = io.samples()
            source_captures.append(
                {
                    "raw": raw,
                    "replay": account.replay_current_account_bundle_v4(
                        raw, expected_sha256=sha(raw)
                    ),
                    "custody_id": held["custody_id"],
                    "private_root": private_root,
                    "native_root": native_root,
                    "held": held,
                    "after": utc_from_ns(after_stamp["utc_ns"]),
                    "after_stamp": after_stamp,
                }
            )

        first, latest = source_captures
        series = _coverage_pair(source_captures, io.samples)
        first_active, _, first_scan = series[0]
        last_active, lineage, scan = series[1]
        template = first_active.state_document
        after_first = first["after"] + timedelta(microseconds=3)
        # The exact synthetic initial readback/start is captured before the
        # second source; the original record never acquires current authority.
        first_mono = first["after_stamp"]["monotonic_ns"] + 3000
        from tests.prospective_cutover_fixtures import stamp

        post = stamp(after_first, first_mono)
        start = {name: value + 2000 for name, value in post.items()}
        record_raw = cutover_record_fixture(
            template=template,
            active=first_active,
            post_active=post,
            start=start,
            scan=json.loads(first_scan.document_json),
        )
        record = SimpleNamespace(
            record_json=record_raw,
            record_sha256=sha(record_raw),
            recorded_at=utc_from_ns(start["utc_ns"] + 1000),
        )
        actual_context = source._context()
        original = first["held"]
        historical = {
            "context": (*actual_context[:3], ref(actual_context[3])),
            "record": record,
            "active": first_active,
            "event_keys": first_scan.consumed_event_keys,
            "factory": io.args["session_factory"],
            "phase_source": {
                **original,
                "custody": io.args["custody_repository"],
                "source_custody_id": first["custody_id"],
                "source_bundle_sha256": sha(first["raw"]),
                "source_plan_sha256": first["replay"].reference.plan_sha256,
                "source_proof_envelope_sha256": first["replay"].proof_envelope_sha256,
            },
        }
        observed_hop = SimpleNamespace(coverage=last_active, lineage=lineage, scan=scan)
        hops = phase._join(
            {"historical": historical, "hops": ()},
            {"active": last_active, "template": last_active.state_document},
            observed_hop,
        )
        moved = {
            "historical": historical,
            "hops": hops,
            "current_account": latest["held"],
            "active": last_active,
            "template": last_active.state_document,
            "factory": io.args["session_factory"],
            "custody": io.args["custody_repository"],
            "private_root": latest["private_root"],
            "native_root": latest["native_root"],
            "source_files": {
                "private_root": coverage._source_files(
                    latest["private_root"],
                    coverage._PRIVATE_NAMES,
                    coverage._MAX_SOURCE_FILE,
                ),
                "native_root": coverage._source_files(
                    latest["native_root"],
                    (*proof.FILES, "proof.json", "readback.json"),
                    proof.MAX_PROOF,
                ),
            },
            "phase_checked": io.samples(),
        }
        raw_root, native_root = tmp_path / "history-raw", tmp_path / "history-native"
        for root in (raw_root, native_root):
            root.mkdir()
        io.harness.requests.clear()
        io.harness.script = scripted_pages(extra={STREAM: [[]]})
        fresh_route()
        original_stamp = io.samples()
        state = dict.__new__(source._NamespaceHistoryStateV1)
        state.update(
            {
                "moved": moved,
                "context": (*actual_context[:3], ref(actual_context[3])),
                "lease": latest["held"]["read_session"],
                "private_client": latest["held"]["private_client"],
                "factory_pins": coverage._factory_pins(
                    moved["factory"], moved["custody"]
                ),
                "original": original_stamp,
                "last": original_stamp,
                "loop_deadline": actual_context[2].time() + 60,
                "raw_root": raw_root,
                "native_root": native_root,
                "transferred": False,
            }
        )
        # Explicit synthetic predecessor-capability seam. This exercises only
        # the new history body; no genuine DB0051 phase owner is claimed.
        state["binding"] = source._register_moved_namespace_history_v1(
            moved, key=phase._HOOK_KEY
        )
        source._bind_moved_state(state, key=source._HOOK_KEY)
        refresh_source_provider_pins(monkeypatch)
        state["prepared_plan"] = source._plan(state)
        if failure in {"native_context_exit", "native_context_exit_alias"}:

            @contextmanager
            def stage_with_late_exit(**kwargs):
                with original_stage(**kwargs) as stage:
                    yield stage
                if failure == "native_context_exit":
                    raise RuntimeError("synthetic-late-native-context-exit")
                monkeypatch.setattr(source, "sha", lambda _raw: "0" * 64)

            monkeypatch.setattr(native, "_initial_stage", stage_with_late_exit)
            refresh_source_provider_pins(monkeypatch)
        if failure == "raw_context_exit":

            @contextmanager
            def companion_with_late_exit(root):
                with original_companion(root) as directory:
                    yield directory
                if root == raw_root:
                    raise RuntimeError("synthetic-late-raw-context-exit")

            monkeypatch.setattr(storage, "_companion_attempt", companion_with_late_exit)
            refresh_source_provider_pins(monkeypatch)
        fixture = SimpleNamespace(
            state=state,
            raw_root=raw_root,
            native_root=native_root,
            chains=chains,
            retained=retained,
            io=io,
            config_clients=config_clients,
            initial_windows=tuple(
                captured["replay"].expires_at for captured in source_captures
            ),
            initial_record=record_raw,
        )
        # Keep no hidden strong state across yield. Lifetime tests can drop
        # fixture.state while retaining only a binding and detached audit data.
        state_ref = ref(state)
        del state
        try:
            yield fixture
        finally:
            remaining = state_ref()
            if remaining is not None:
                for owner, owner_state in tuple(source._OWNERS.items()):
                    if owner_state is remaining:
                        owner.close()
                source._release_moved(remaining)
