"""Synthetic native-provider control flow only; no native/TLS/PG acceptance.

Real original B1 parsing/journal/phase code, B3 math, positions collector and
new envelope replay run. The explicitly labelled private OS/TLS/DB providers
are substituted. No production receipt or exchange history is fabricated.
"""

import asyncio
import copy
import json
import pickle
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.database.repositories import public_runtime_bundle_custody as custody
from app.database.repositories.account_observation_index import (
    AccountObservationIndexRepository,
)
from app.domain.source_primitives import canonical
from app.trade_qualification import account_bootstrap_runtime as bootstrap
from app.trade_qualification import account_history_native_stage_v1 as phases
from app.trade_qualification import account_history_source_bundle_v1 as bundle
from app.trade_qualification import account_history_source_v1 as source
from app.trade_qualification import account_native_clock as native
from app.trade_qualification import account_native_proof_storage as storage
from app.trade_qualification import account_native_runtime as runtime
from app.trade_qualification import account_observation_index as observed
from app.trade_qualification import account_positions_history_collector_v1 as positions
from tests.unit.research.test_demo_session_route_preflight import _client
from tests.unit.research.test_public_journal_contracts import MemoryDirectory
from tests.unit.test_account_ingestion_journal_contracts import setup as journal_setup
from tests.unit.test_account_lifecycle_history import original_index
from tests.unit.test_account_native_clock import Samples
from tests.unit.test_account_native_proof import (
    declared_native_source,
    synthetic_host,
    synthetic_time_probe,
    synthetic_tls_chain,
)
from tests.unit.test_qualification_account_collector import (
    API_KEY,
    API_SECRET,
    PASSPHRASE,
)


@contextmanager
def acquisition_fixture(monkeypatch, tmp_path, *, defect=None):
    selected, harness, _, _, events = journal_setup(monkeypatch, v4=True)
    harness.script = declared_native_source(current_v7=False)[0]
    samples = Samples()
    monkeypatch.setattr(native.clock, "native_stamp", samples)
    monkeypatch.setattr(
        native.clock, "native_os_clock", lambda: synthetic_host(samples)
    )
    private = _client(
        okx_demo_api_key=API_KEY,
        okx_demo_api_secret=API_SECRET,
        okx_demo_api_passphrase=PASSPHRASE,
        okx_demo_read_max_retries=0,
    )
    files, saved, batches = {}, {}, []

    @contextmanager
    def root(path):
        directory = MemoryDirectory(files.setdefault(path.name, {}))
        directory.path = path
        yield directory

    @contextmanager
    def fresh_root(path):
        with root(path) as directory:
            if directory.names():
                raise ValueError("synthetic_no_clobber")
            yield directory

    monkeypatch.setattr(storage, "_root_context", root)
    monkeypatch.setattr(storage, "_companion_attempt", fresh_root)
    monkeypatch.setattr(
        storage, "_root_identity", lambda path: (1, 2 if path.name == "raw" else 3)
    )

    async def time_probe(stage, plan, *, directory, phase, tokens):
        outputs = synthetic_time_probe(
            stage, native._state(stage), plan, samples, phase
        )
        for name, raw in zip(
            (
                f"exchange-{phase}.json",
                f"exchange-{phase}.raw",
                f"exchange-{phase}-trace.json",
            ),
            outputs,
            strict=True,
        ):
            storage._publish_part(directory, name, raw)
        return outputs

    monkeypatch.setattr(runtime, "_time_probe", time_probe)
    original_collect = bootstrap._collect_recorded

    async def collect(session, **kwargs):
        harness.clock = kwargs["clock"]
        saved["session"] = session
        recorded, owner = await original_collect(session, **kwargs)
        saved["prior_signature"] = owner.tokens[4]
        # A declared test-only original TLS provider, never native acceptance.
        events[:] = synthetic_tls_chain(tuple(events))
        owner.previous = observed.source_reference(tuple(events)).head_sha256
        return replace(
            recorded,
            bootstrap=replace(
                recorded.bootstrap, transport_provenance="owned_signed_verified_tls"
            ),
        ), owner

    monkeypatch.setattr(bootstrap, "_collect_recorded", collect)

    async def head(self, scope, sequence=None):
        if not batches:
            return None
        return SimpleNamespace(
            sequence=batches[-1].sequence,
            event_sha256=(
                "f" * 64 if saved.get("late_head") else batches[-1].event_sha256
            ),
        )

    async def append(self, scope, reference, window, *, expected_revision):
        assert expected_revision == 0 and not batches
        batches.extend(original_index(((tuple(events), reference),), window))
        return batches[0]

    async def read(self, scope, *, after=0, limit=32):
        return tuple(batches[after : after + limit])

    monkeypatch.setattr(AccountObservationIndexRepository, "_batch", head)
    monkeypatch.setattr(AccountObservationIndexRepository, "append", append)
    monkeypatch.setattr(AccountObservationIndexRepository, "read", read)

    async def positions_get(
        client,
        credentials,
        lease,
        binding,
        clock,
        spec,
        *,
        previous_completed,
        secret_tokens,
        _native_observer,
    ):
        # Substitute only the private I/O provider. All native phase, buffer,
        # raw archive, cursor/config replay and final owner code remain real.
        maximum = (
            positions.MAX_CONFIG_BYTES
            if spec.endpoint == positions.CONFIG_ENDPOINT
            else positions.history.MAX_PAGE_BYTES
        )
        started = phases._begin(_native_observer, spec, maximum, secret_tokens)
        phases._phase(_native_observer, "request_dispatch")
        headers = phases._phase(_native_observer, "headers_received")
        phases._phase(_native_observer, "tls_verified", certificate="c" * 64)
        if spec.endpoint == positions.CONFIG_ENDPOINT:
            config = json.loads(
                next(
                    page.response_body
                    for page in saved["packet"].observations
                    if page.request.endpoint == positions.CONFIG_ENDPOINT
                )
            )["data"]
            msg = (
                saved["prior_signature"]
                if defect in {"b1_signature_echo", "escaped_b1_signature_echo"}
                else ""
            )
            raw = canonical({"code": "0", "msg": msg, "data": config})
            if defect == "escaped_b1_signature_echo":
                raw = raw.replace(
                    msg.encode("ascii"),
                    f"\\u{ord(msg[0]):04x}{msg[1:]}".encode("ascii"),
                )
        else:
            raw = canonical({"code": "0", "msg": "", "data": []})
        if defect == "partial":
            phases._chunk(_native_observer, raw[: len(raw) // 2])
            phases._response_closed(_native_observer)
            phases._retain(_native_observer, failed=True)
            raise ValueError("synthetic_fixed_failure")
        phases._chunk(_native_observer, raw)
        completed = phases._phase(_native_observer, "body_exhausted")
        phases._response_closed(_native_observer)
        phases._phase(_native_observer, "validated")
        phases._retain(_native_observer)
        return positions._RawPage(raw, started, headers, completed, "c" * 64)

    # Capture's packet is observed at its original provider boundary, never
    # supplied to production acquisition as an authority argument.
    previous_collect = collect

    async def collect_with_packet(session, **kwargs):
        result, owner = await previous_collect(session, **kwargs)
        saved["packet"] = result.bootstrap.packet
        return result, owner

    monkeypatch.setattr(bootstrap, "_collect_recorded", collect_with_packet)
    monkeypatch.setattr(positions, "_signed_get_native", positions_get)

    # _new_client itself is native/unconnected. The fixed private request
    # provider above emits no HTTP and closes no outside resource.
    async def deployment(self):
        assert not events
        saved["deployment"] = True

    async def seal(self, *, bundle_raw, expected_sha256, proof_envelope_sha256):
        assert saved["deployment"]
        facts = bundle.replay_history_source_bundle_v1(
            bundle_raw, expected_sha256=expected_sha256
        )
        assert facts.proof_sha256 == proof_envelope_sha256
        saved["raw"] = bundle_raw
        if defect == "commit_unknown":
            raise custody.RuntimeBundleCustodyError("runtime_bundle_commit_uncertain")
        now = datetime.now(UTC)
        value = custody.RetainedRuntimeBundleReadback(
            "a" * 32, expected_sha256, proof_envelope_sha256, now, now, bundle_raw
        )
        receipt = custody.OwnedRuntimeBundleCustodyReceipt(custody._ISSUER)
        custody._RECEIPTS[receipt] = (
            value,
            custody._owner(),
            custody.time.monotonic_ns(),
        )
        return receipt

    monkeypatch.setattr(
        custody.RuntimeBundleCustodyRepository, "verify_deployment", deployment
    )
    monkeypatch.setattr(custody.RuntimeBundleCustodyRepository, "seal_bundle", seal)
    engine = create_async_engine(
        "postgresql+asyncpg://synthetic@localhost/synthetic", poolclass=NullPool
    )
    factory = async_sessionmaker(engine, autoflush=False, expire_on_commit=False)
    with private.pinned_read_session() as lease:
        args = {
            "read_session": lease,
            "private_client": private,
            "scope_config": source.NativeHistoryScopeV1(
                selected._plan.expected_uid,
                selected._plan.expected_main_uid,
                "e" * 64,
                selected._plan.leverage_instrument_ids,
            ),
            "session_factory": factory,
            "custody_session_factory": factory,
            "raw_root": tmp_path / "raw",
            "companion_root": tmp_path / "clock",
        }
        yield SimpleNamespace(
            args=args,
            files=files,
            saved=saved,
            batches=batches,
            events=events,
            samples=samples,
        )


@pytest.mark.asyncio
async def test_fresh_original_acquisition_custody_and_one_use_consume(
    monkeypatch, tmp_path
):
    with acquisition_fixture(monkeypatch, tmp_path) as value:
        owner = await source.acquire_native_history_source_facts_v1(**value.args)
        facts = await source.consume_owned_native_history_source_facts_v1(owner)
        assert facts.packet.plan.contract_version == "ctcc.demo_account_plan.v5"
        assert len(facts.sources) == len(facts.batches) == 1
        assert facts.reference == facts.sources[-1][1]
        assert facts.positions.chain.page_hashes
        assert facts.positions.account_usd_contract_settle_currency is None
        assert facts.positions.observed_margin_currencies == ()
        assert facts.positions.declared_scope_currency == "USDT"
        assert facts.account_revision is facts.funding_accrual_events is None
        assert facts.funding_source_status == "NOT_ACQUIRED"
        assert facts.registration_site == "UNKNOWN"
        assert not facts.native_history_prefix_verified
        assert not facts.account_complete and not facts.execution_authority
        with pytest.raises(source.NativeHistorySourceError, match="owner_unavailable"):
            source.consume_owned_native_history_source_facts_v1(owner)
        with pytest.raises(source.NativeHistorySourceError):
            await source.acquire_native_history_source_facts_v1(**value.args)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change", ["head", "raw", "clock", "expiry", "factory", "lease"]
)
async def test_late_failure_burns_owner_and_preserves_original_evidence(
    monkeypatch, tmp_path, change
):
    with acquisition_fixture(monkeypatch, tmp_path) as value:
        owner = await source.acquire_native_history_source_facts_v1(**value.args)
        original = dict(value.files["raw"])
        if change == "head":
            value.saved["late_head"] = True
        elif change == "raw":
            value.files["raw"]["source.bundle.json"] += b" "
        elif change == "clock":
            value.files["clock"]["host-after.json"] += b" "
        elif change == "expiry":
            value.samples.calls += 61000
        elif change == "factory":
            value.args["session_factory"].kw["autoflush"] = True
        elif change == "lease":
            value.args["read_session"]._lease.active = False
        with pytest.raises((source.NativeHistorySourceError, ValueError)):
            await source.consume_owned_native_history_source_facts_v1(owner)
        with pytest.raises(source.NativeHistorySourceError):
            source.consume_owned_native_history_source_facts_v1(owner)
        assert "attempt.claim.json" in value.files["raw"]
        assert all(
            value.files["raw"][key] == raw
            for key, raw in original.items()
            if key != "source.bundle.json"
        )


@pytest.mark.asyncio
async def test_foreign_task_burns_before_db_await(monkeypatch, tmp_path):
    with acquisition_fixture(monkeypatch, tmp_path) as value:
        owner = await source.acquire_native_history_source_facts_v1(**value.args)

        async def foreign():
            source.consume_owned_native_history_source_facts_v1(owner)

        with pytest.raises(source.NativeHistorySourceError):
            await asyncio.create_task(foreign())
        with pytest.raises(source.NativeHistorySourceError):
            source.consume_owned_native_history_source_facts_v1(owner)


@pytest.mark.asyncio
async def test_prefix_byte_guard_precedes_original_source_replay(monkeypatch, tmp_path):
    with acquisition_fixture(monkeypatch, tmp_path) as value:
        owner = await source.acquire_native_history_source_facts_v1(**value.args)
        facts = await source.consume_owned_native_history_source_facts_v1(owner)

        class Index:
            async def _batch(self, scope):
                return SimpleNamespace(
                    sequence=1, event_sha256=facts.batches[0].event_sha256
                )

            async def read(self, scope, *, after, limit):
                return facts.batches

        class Journal:
            async def read_chain(self, scope, capture_id):
                return facts.sources[0][0]

        called = []
        monkeypatch.setattr(source, "_check_pins", lambda _: None)
        monkeypatch.setattr(
            source.proof, "_source", lambda *_args, **_kw: called.append(True)
        )
        budget = (
            len(facts.batches[0].document_json)
            + len(facts.batches[0].replay.receipt_json)
            + 1
        )
        assert (
            sum(
                len(raw)
                for point in facts.sources[0][0]
                for raw in (
                    point.event.event_json,
                    point.event.raw_body,
                    point.event.packet_payload,
                )
                if raw is not None
            )
            > budget
        )
        monkeypatch.setattr(source.observed, "MAX_SOURCE_BYTES", budget)
        with pytest.raises(
            source.NativeHistorySourceError, match="prefix_source_bound"
        ):
            await source._population(
                {}, Index(), Journal(), facts.scope, fresh_reference=facts.reference
            )
        assert called == []


@pytest.mark.parametrize("defect", ["unexpected_name", "aggregate", "changed_bytes"])
def test_exact_file_inventory_has_budget_before_reads_and_never_adopts_unknown_files(
    monkeypatch, defect
):
    checked, read = [], []
    monkeypatch.setattr(source, "_check_pins", lambda _: checked.append(True))
    raw = b"{}"
    content = {"attempt.claim.json": raw}
    expected = {"attempt.claim.json": source._file_pin(raw)}
    if defect == "unexpected_name":
        content["unexpected.bin"] = b"ignored"
    if defect == "changed_bytes":
        content["attempt.claim.json"] = b"[]"

    class Directory(MemoryDirectory):
        def read(self, name, maximum):
            read.append((name, maximum))
            return super().read(name, maximum)

    with pytest.raises(source.NativeHistorySourceError):
        source._inventory(
            {},
            Directory(content),
            expected,
            max_total_bytes=1 if defect == "aggregate" else 1024,
        )
    assert checked
    if defect != "changed_bytes":
        assert read == []
    else:
        assert read == [("attempt.claim.json", len(raw))]


@pytest.mark.parametrize(
    "operation",
    [
        lambda: source.OwnedNativeHistorySourceFactsV1(),
        lambda: copy.copy(object.__new__(source.OwnedNativeHistorySourceFactsV1)),
        lambda: pickle.dumps(object.__new__(source.OwnedNativeHistorySourceFactsV1)),
    ],
)
def test_no_constructor_copy_or_saved_receipt_can_mint(operation):
    with pytest.raises(source.NativeHistorySourceError):
        operation()
    with pytest.raises(source.NativeHistorySourceError):
        source.consume_owned_native_history_source_facts_v1(
            {"passed": True, "raw": b"{}"}
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("defect", ["partial", "commit_unknown"])
async def test_failure_keeps_claim_raw_or_privacy_refusal_and_never_issues_owner(
    monkeypatch, tmp_path, defect
):
    with acquisition_fixture(monkeypatch, tmp_path, defect=defect) as value:
        with pytest.raises(source.NativeHistorySourceError):
            await source.acquire_native_history_source_facts_v1(**value.args)
        assert "attempt.claim.json" in value.files["raw"]
        assert "failed.json" in value.files["raw"]
        assert value.events
        assert all(
            secret.encode() not in raw
            for directory in value.files.values()
            for raw in directory.values()
            for secret in (API_KEY, API_SECRET, PASSPHRASE)
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("defect", ["b1_signature_echo", "escaped_b1_signature_echo"])
async def test_prior_b1_signature_echo_is_withheld_without_custody_promotion(
    monkeypatch, tmp_path, defect
):
    with acquisition_fixture(monkeypatch, tmp_path, defect=defect) as value:
        with pytest.raises(source.NativeHistorySourceError):
            await source.acquire_native_history_source_facts_v1(**value.args)
        content = value.files["raw"]
        marker = json.loads(content["positions-00.json"])
        assert marker["retention"] == "withheld_secret"
        assert marker["complete"] is False
        assert "positions-00.raw" not in content
        assert "positions-failure.json" in content and "failed.json" in content
        assert "raw" not in value.saved and "source.bundle.json" not in content
        secret = value.saved["prior_signature"]
        assert all(
            secret not in json.dumps(json.loads(raw))
            for directory in value.files.values()
            for name, raw in directory.items()
            if name.endswith(".json")
        )
        assert all(
            token.encode("ascii") not in raw
            for directory in value.files.values()
            for raw in directory.values()
            for token in (secret, API_KEY, API_SECRET, PASSPHRASE)
        )


@pytest.mark.parametrize("escaped", [False, True])
def test_final_secret_union_scans_older_original_bodies_before_encoded_archives(
    monkeypatch, escaped
):
    signature = "synthetic-later-position-signature"
    raw = canonical({"msg": signature})
    if escaped:
        raw = raw.replace(b"synthetic", b"\\u0073ynthetic")
    older = (SimpleNamespace(event=SimpleNamespace(raw_body=raw)),)
    latest = (SimpleNamespace(event=SimpleNamespace(raw_body=b'{"msg":""}')),)
    encoded = []
    monkeypatch.setattr(bundle, "_json", lambda *_: encoded.append(True))
    with pytest.raises(
        source.collector.AccountCollectionError, match="credential_echo_rejected"
    ):
        source._check_original_wire_secrets(
            (((older, None), (latest, None)), ()), None, None, [signature]
        )
    assert encoded == []
