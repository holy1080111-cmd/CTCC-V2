"""Synthetic native control flow only; no real TLS/OKX/PG/credential acceptance."""

import asyncio
import copy
import json
import pickle
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.database.repositories import public_runtime_bundle_custody as custody
from app.domain.source_primitives import canonical, sha
from app.trade_qualification import account_bootstrap_runtime as bootstrap
from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_current_source_v4 as source
from app.trade_qualification import account_native_clock as native
from app.trade_qualification import account_native_proof as proof
from app.trade_qualification import account_native_proof_storage as storage
from app.trade_qualification import account_native_runtime as runtime
from app.trade_qualification import account_observation_index as observed
from app.trade_qualification import demo_operational_route_v4 as route
from app.trade_qualification.account_runtime import ControlledDemoAccountSession
from tests.unit.research.test_demo_operational_route_v4 import (
    _config,
    _native_config_fixture,
)
from tests.unit.research.test_demo_session_route_preflight import _client
from tests.unit.research.test_public_journal_contracts import MemoryDirectory
from tests.unit.test_account_current_source_v7 import current_plan_v7
from tests.unit.test_account_ingestion_journal_contracts import setup as journal_setup
from tests.unit.test_account_native_clock import Samples
from tests.unit.test_account_native_proof import (
    declared_native_source,
    synthetic_host,
    synthetic_time_probe,
    synthetic_tls_chain,
)
from tests.unit.test_qualification_account_capture import wire
from tests.unit.test_qualification_account_collector import (
    API_KEY,
    API_SECRET,
    PASSPHRASE,
    Stream,
)


@contextmanager
def setup(monkeypatch, tmp_path, *, exposed=False, anchor="fresh", echo=None):
    holder = {}
    native_client_factory = route.collector._new_client

    def gateway_response(_stream, _index, rows):
        request = holder["harness"].requests[-1]
        started = datetime.fromisoformat(request.headers["OK-ACCESS-TIMESTAMP"])
        delta = started - datetime(1970, 1, 1, tzinfo=UTC)
        micros = (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds
        if _stream == "account_position_risk":
            # The synthetic supplier emits a fresh account-update anchor;
            # balance last-change uTime stays older and is never rewritten.
            for row in rows:
                if anchor == "fresh":
                    row["ts"] = str(micros // 1000)
                elif anchor == "future":
                    row["ts"] = str(micros // 1000 + 1000)
        if echo is not None and _stream == "balance":
            if echo == "value":
                rows[0]["private-note"] = holder["binding"]
            else:
                rows[0][holder["binding"]] = "echo-in-key"
        return wire(rows, inTime=str(micros + 100), outTime=str(micros + 200))

    _, harness, _, _, events = journal_setup(
        monkeypatch, v4=True, change=gateway_response
    )
    holder["harness"] = harness
    private = _client(
        okx_demo_api_key=API_KEY,
        okx_demo_api_secret=API_SECRET,
        okx_demo_api_passphrase=PASSPHRASE,
    )
    with private.pinned_read_session() as read_session:
        holder["binding"] = read_session.binding_id
        selected = current_plan_v7(session_binding_id=read_session.binding_id)
        session = ControlledDemoAccountSession.from_pinned_read_session(
            read_session=read_session,
            plan=selected,
            expected_plan_sha256=capture.plan_sha256(selected),
        )
        declared, _, _ = declared_native_source(
            "fresh_exposed" if exposed else "fresh_flat", current_v7=True
        )
        harness.script = declared
        samples = Samples()
        config_body = Stream(
            _config(uid=selected.expected_uid, main_uid=selected.expected_main_uid)
        )
        monkeypatch.setattr(route, "_native_client", native_client_factory)
        route_client, route_response = _native_config_fixture(monkeypatch, config_body)
        for module in (source, route, storage):
            monkeypatch.setattr(module, "native_stamp", samples)
        monkeypatch.setattr(native.clock, "native_stamp", samples)
        monkeypatch.setattr(
            native.clock,
            "native_os_clock",
            lambda: synthetic_host(
                samples, profile_id=proof.public_clock.UPDATED_PROFILE_ID
            ),
        )
        monkeypatch.setattr(runtime, "_configured_factory", lambda _factory: True)
        monkeypatch.setattr(custody, "_native_engine", lambda _engine: True)

        async def time_probe(stage, plan, *, directory, phase, tokens):
            state = native._state(stage)
            outputs = synthetic_time_probe(stage, state, plan, samples, phase)
            for suffix, raw in zip(("json", "raw", "trace.json"), outputs, strict=True):
                name = f"exchange-{phase}.{suffix}"
                if suffix == "trace.json":
                    name = f"exchange-{phase}-trace.json"
                storage._publish_part(directory, name, raw)
            return outputs

        monkeypatch.setattr(runtime, "_time_probe", time_probe)
        original_collect = bootstrap._collect_recorded

        async def collect(session, **kwargs):
            harness.clock = kwargs["clock"]
            recorded, owner = await original_collect(session, **kwargs)
            relabeled = synthetic_tls_chain(tuple(events))
            events[:] = relabeled
            owner.previous = observed.source_reference(relabeled).head_sha256
            return replace(
                recorded,
                bootstrap=replace(
                    recorded.bootstrap, transport_provenance="owned_signed_verified_tls"
                ),
            ), owner

        monkeypatch.setattr(bootstrap, "_collect_recorded", collect)
        native_files, route_files = {}, {}

        @contextmanager
        def root(_root):
            yield MemoryDirectory(native_files)

        @contextmanager
        def private_root(_root):
            yield MemoryDirectory(route_files)

        monkeypatch.setattr(storage, "_companion_attempt", root)
        monkeypatch.setattr(storage, "_root_context", root)
        monkeypatch.setattr(storage, "_root_identity", lambda _root: (11, 22))
        monkeypatch.setattr(source.public_storage, "_native_recheck_root", private_root)
        monkeypatch.setattr(
            route, "_native_attempt_directory", lambda directory: directory
        )
        repository = object.__new__(custody.RuntimeBundleCustodyRepository)
        repository._factory = SimpleNamespace(kw={"bind": object()})
        repository._engine = None
        repository._deployment = None
        repository._poisoned = False
        monkeypatch.setattr(
            custody.RuntimeBundleCustodyRepository, "_check_factory", lambda _self: None
        )
        saved = {}

        async def deployment(_self):
            assert not harness.requests and not events
            saved["deployment"] = True

        async def seal(_self, *, bundle_raw, expected_sha256, proof_envelope_sha256):
            assert saved["deployment"] and session._used is True
            assert route._PROBES.get(session) is route._SPENT
            replay = source.replay_current_account_bundle_v4(
                bundle_raw, expected_sha256=expected_sha256
            )
            assert replay.proof_envelope_sha256 == proof_envelope_sha256
            saved.update(
                raw=bundle_raw, pin=expected_sha256, proof=proof_envelope_sha256
            )
            # This is explicitly a synthetic database seam, not PG evidence.
            readback = custody.RetainedRuntimeBundleReadback(
                "a" * 32,
                expected_sha256,
                proof_envelope_sha256,
                datetime.now(UTC),
                datetime.now(UTC),
                bundle_raw,
            )
            receipt = custody.OwnedRuntimeBundleCustodyReceipt(custody._ISSUER)
            custody._RECEIPTS[receipt] = (
                readback,
                custody._owner(),
                custody.time.monotonic_ns(),
            )
            return receipt

        monkeypatch.setattr(
            custody.RuntimeBundleCustodyRepository, "verify_deployment", deployment
        )
        monkeypatch.setattr(custody.RuntimeBundleCustodyRepository, "seal_bundle", seal)
        args = {
            "account_session": session,
            "private_client": private,
            "session_factory": SimpleNamespace(kw={"bind": object()}),
            "private_proof_root": tmp_path / "private-route",
            "native_proof_root": tmp_path / "private-native",
            "custody_repository": repository,
        }
        yield SimpleNamespace(
            args=args,
            harness=harness,
            events=events,
            samples=samples,
            saved=saved,
            native_files=native_files,
            route_files=route_files,
            session=session,
            route_client=route_client,
            route_response=route_response,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("exposed", [False, True])
async def test_original_acquisition_and_exact_custody_earn_current_source_only(
    monkeypatch, tmp_path, exposed
):
    with setup(monkeypatch, tmp_path, exposed=exposed) as state:
        owner = await source.acquire_initial_demo_account_source_v4(**state.args)
        operands = source.consume_owned_current_demo_account_v4(owner)
        receipt = json.loads(operands.receipt_json)
        assert operands.source_authenticity_verified is True
        assert operands.current_page_chains_complete is True
        assert operands.durable_raw_readback_verified is True
        assert operands.account_complete is operands.execution_authority is False
        assert operands.post_g12_eligible is receipt["post_g12_eligible"] is False
        assert receipt["snapshot"] is None and receipt["history_complete"] is None
        assert receipt["loss_streak_seed"] is receipt["historical_HWM"] is None
        assert (
            receipt["exchange_capture_atomic"]
            is receipt["flat_start_permission"]
            is False
        )
        assert len(operands.packet.observations) >= 17
        assert (
            bool(
                sum(
                    json.loads(operands.current_receipt_json)[
                        "inventory_row_counts"
                    ].values()
                )
            )
            is exposed
        )
        assert set(state.native_files) == {*proof.FILES, "proof.json", "readback.json"}
        assert "account-route-attempt.v4.raw" in state.route_files
        assert state.route_response.is_closed and state.route_client.is_closed
        assert all(request.method == "GET" for request in state.harness.requests)
        replay = source.replay_current_account_bundle_v4(
            state.saved["raw"], expected_sha256=state.saved["pin"]
        )
        assert (
            replay.source_authenticity_verified is replay.execution_authority is False
        )
        assert replay.packet == operands.packet
        with pytest.raises(source.CurrentAccountSourceV4Error):
            source.consume_owned_current_demo_account_v4(owner)
        with pytest.raises(
            proof.NativeAccountProofError, match="flat_current_use_revoked"
        ):
            proof.current_use_contract_for_plan(state.session._plan)
        state.harness.assert_closed()


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["raw", "partial", "proof", "identity"])
async def test_rehashed_tampered_or_partial_saved_bundle_never_replays(
    monkeypatch, tmp_path, case
):
    with setup(monkeypatch, tmp_path) as state:
        owner = await source.acquire_initial_demo_account_source_v4(**state.args)
        source.consume_owned_current_demo_account_v4(owner)
        document = json.loads(state.saved["raw"])
        if case == "raw":
            target = next(row for row in document["chain"] if row["raw_hex"])
            target["raw_hex"] = b'{"code":"0","data":[]}'.hex()
        elif case == "partial":
            document["chain"].pop()
        elif case == "proof":
            native_doc = json.loads(bytes.fromhex(document["native_proof_hex"]))
            native_doc["policy_sha256"] = proof.V7_FLAT_POLICY_SHA256
            document["native_proof_hex"] = canonical(native_doc).hex()
        else:
            document["route_proof"]["account_identity"]["uid"] = "9999"
        raw = canonical(document)
        with pytest.raises(source.CurrentAccountSourceV4Error, match="bundle_invalid"):
            source.replay_current_account_bundle_v4(raw, expected_sha256=sha(raw))
        assert not source._CURRENT


@pytest.mark.asyncio
async def test_late_custody_failure_keeps_all_durable_source_and_no_owner(
    monkeypatch, tmp_path
):
    with setup(monkeypatch, tmp_path) as state:

        async def fail(*_args, **_kwargs):
            raise RuntimeError("private database failure")

        monkeypatch.setattr(custody.RuntimeBundleCustodyRepository, "seal_bundle", fail)
        with pytest.raises(
            source.CurrentAccountSourceV4Error, match="acquisition_failed"
        ):
            await source.acquire_initial_demo_account_source_v4(**state.args)
        assert state.events[-1].event.packet_payload is None
        assert (
            json.loads(state.events[-1].event.event_json)["outcome"]
            == "complete_recorded"
        )
        assert set(state.native_files) == {*proof.FILES, "proof.json", "readback.json"}
        assert "account-route-attempt.v4.raw" in state.route_files
        assert (
            state.session._used is True and not source._CURRENT and not source._INITIAL
        )
        state.harness.assert_closed()


@pytest.mark.asyncio
async def test_wrong_task_burns_owner_and_cannot_retry_in_original_task(
    monkeypatch, tmp_path
):
    with setup(monkeypatch, tmp_path) as state:
        owner = await source.acquire_initial_demo_account_source_v4(**state.args)

        async def foreign():
            source.consume_owned_current_demo_account_v4(owner)

        with pytest.raises(
            source.CurrentAccountSourceV4Error, match="consumption_failed"
        ):
            await asyncio.create_task(foreign())
        with pytest.raises(source.CurrentAccountSourceV4Error):
            source.consume_owned_current_demo_account_v4(owner)
        assert not source._CURRENT


@pytest.mark.asyncio
async def test_stale_owner_never_extends_original_expiry(monkeypatch, tmp_path):
    with setup(monkeypatch, tmp_path) as state:
        owner = await source.acquire_initial_demo_account_source_v4(**state.args)
        state.samples.wall += 31_000_000_000
        with pytest.raises(
            source.CurrentAccountSourceV4Error, match="consumption_failed"
        ):
            source.consume_owned_current_demo_account_v4(owner)
        assert not source._CURRENT


@pytest.mark.asyncio
async def test_preflight_and_private_helper_reject_without_io(tmp_path):
    with pytest.raises(source.CurrentAccountSourceV4Error, match="inputs_invalid"):
        await source.acquire_initial_demo_account_source_v4(
            account_session=object(),
            private_client=object(),
            session_factory=object(),
            private_proof_root=tmp_path,
            native_proof_root=tmp_path / "other",
            custody_repository=object(),
        )
    with pytest.raises(
        source.CurrentAccountSourceV4Error, match="native_scope_unavailable"
    ):
        await source._acquire_native(source._InitialAccountScopeV4())
    with pytest.raises(
        source.CurrentAccountSourceV4Error, match="source_not_constructible"
    ):
        source.OwnedCurrentDemoAccountSourceV4()
    forged = object.__new__(source.OwnedCurrentDemoAccountSourceV4)
    for operation in (
        copy.copy,
        copy.deepcopy,
        pickle.dumps,
        source.consume_owned_current_demo_account_v4,
    ):
        with pytest.raises(source.CurrentAccountSourceV4Error):
            operation(forged)


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["postcapture_read", "postcustody_recheck"])
async def test_same_deadline_bounds_late_database_hang_and_preserves_sources(
    monkeypatch, tmp_path, stage
):
    with setup(monkeypatch, tmp_path) as state:
        constrain = source._constrain_budget

        def shorter(budget, native_state, stamp):
            constrain(budget, native_state, stamp)
            # Shorten the same actual timeout only after the original source
            # exists; no fake-clock deadline can satisfy this hanging await.
            budget.reschedule(
                min(budget.when(), asyncio.get_running_loop().time() + 0.02)
            )

        monkeypatch.setattr(source, "_constrain_budget", shorter)
        entered = []

        async def hang(*_args, **_kwargs):
            entered.append(stage)
            await asyncio.Event().wait()

        if stage == "postcapture_read":
            original = runtime._capture_initial_current

            async def captured(*args, **kwargs):
                result = await original(*args, **kwargs)
                monkeypatch.setattr(
                    runtime.AccountCaptureJournalRepository, "read_chain", hang
                )
                return result

            monkeypatch.setattr(runtime, "_capture_initial_current", captured)
        else:
            original = custody.RuntimeBundleCustodyRepository.seal_bundle

            async def retained(repository, **kwargs):
                receipt = await original(repository, **kwargs)
                monkeypatch.setattr(runtime, "_recheck_original_db_chain", hang)
                return receipt

            monkeypatch.setattr(
                custody.RuntimeBundleCustodyRepository, "seal_bundle", retained
            )
        with pytest.raises(
            source.CurrentAccountSourceV4Error, match="acquisition_failed"
        ):
            await source.acquire_initial_demo_account_source_v4(**state.args)
        assert entered == [stage]
        assert set(state.native_files) == {*proof.FILES, "proof.json", "readback.json"}
        assert "account-route-attempt.v4.raw" in state.route_files
        assert not source._CURRENT and not source._INITIAL


@pytest.mark.asyncio
@pytest.mark.parametrize("echo", ["value", "key"])
async def test_pinned_session_label_reflection_in_source_never_gets_metadata_exception(
    monkeypatch, tmp_path, echo
):
    with setup(monkeypatch, tmp_path, echo=echo) as state:
        with pytest.raises(source.CurrentAccountSourceV4Error):
            await source.acquire_initial_demo_account_source_v4(**state.args)
        assert not source._CURRENT and "raw" not in state.saved
        state.harness.assert_closed()


@pytest.mark.asyncio
@pytest.mark.parametrize("anchor", ["old", "future"])
async def test_old_or_future_account_anchor_keeps_strict_original_refusal(
    monkeypatch, tmp_path, anchor
):
    with setup(monkeypatch, tmp_path, anchor=anchor) as state:
        with pytest.raises(source.CurrentAccountSourceV4Error):
            await source.acquire_initial_demo_account_source_v4(**state.args)
        assert not source._CURRENT and "raw" not in state.saved
        state.harness.assert_closed()


@pytest.mark.asyncio
async def test_owned_packet_metadata_exception_never_exempts_credentials_or_bad_plan(
    monkeypatch, tmp_path
):
    with setup(monkeypatch, tmp_path) as state:
        owner = await source.acquire_initial_demo_account_source_v4(**state.args)
        operands = source.consume_owned_current_demo_account_v4(owner)
        frozen = capture.freeze_demo_account_packet(
            operands.packet, expected_plan_sha256=state.session._pin
        )
        binding = state.session._plan.session_binding_id
        tokens = [API_KEY, API_SECRET, PASSPHRASE, binding]
        source.journal._check_owned_packet_for_storage(
            frozen.payload, tokens, state.session._plan
        )
        for credential_index in (0, 1, 2, 4):
            changed = [*tokens, "synthetic-signature"]
            changed[credential_index] = binding
            with pytest.raises(source.operational.collector.AccountCollectionError):
                source.journal._check_owned_packet_for_storage(
                    frozen.payload, changed, state.session._plan
                )
        wrong = state.session._plan.model_copy(
            update={"session_binding_id": "different-label"}
        )
        with pytest.raises(
            (
                capture.AccountCaptureError,
                source.operational.collector.AccountCollectionError,
            )
        ):
            source.journal._check_owned_packet_for_storage(
                frozen.payload, tokens, wrong
            )
