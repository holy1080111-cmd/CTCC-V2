"""Synthetic native providers, real V4/raw replay; no OS/TLS/PG acceptance."""

import asyncio
import copy
import json
import pickle
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.database.repositories import public_runtime_bundle_custody as custody
from app.domain.source_primitives import sha
from app.trade_qualification import account_bootstrap_runtime as bootstrap
from app.trade_qualification import account_current_source_v4 as account
from app.trade_qualification import account_native_clock as native
from app.trade_qualification import account_native_proof as proof
from app.trade_qualification import account_native_proof_storage as storage
from app.trade_qualification import account_native_runtime as account_native
from app.trade_qualification import account_observation_index as observed
from app.trade_qualification import demo_market_source_v4 as market
from app.trade_qualification import demo_operational_route_v4 as route
from app.trade_qualification import owned_current_original_frame_v1 as frame
from tests.unit.research import test_demo_market_source_v4 as public_fixture
from tests.unit.research.test_demo_operational_route_v4 import (
    _config,
    _native_config_fixture,
)
from tests.unit.research.test_demo_session_route_preflight import _client
from tests.unit.research.test_owned_public_runtime_v2 import policy
from tests.unit.research.test_public_journal_contracts import MemoryDirectory
from tests.unit.test_account_current_source_v7 import current_plan_v7
from tests.unit.test_account_ingestion_journal_contracts import setup as journal_setup
from tests.unit.test_account_native_proof import (
    declared_native_source,
    synthetic_host,
    synthetic_time_probe,
    synthetic_tls_chain,
)
from tests.unit.test_qualification_account_capture import row as account_row
from tests.unit.test_qualification_account_capture import wire
from tests.unit.test_qualification_account_collector import (
    API_KEY,
    API_SECRET,
    PASSPHRASE,
    Stream,
)
from tests.unit.test_qualification_market_bridge import v2_engine_source


@pytest.fixture(scope="module")
def raw_market():
    return v2_engine_source("long")


@contextmanager
def setup(monkeypatch, tmp_path, raw_market, *, mutation=None):
    """Only replace native provider boundaries; run both original V4 issuers.

    The SQLAlchemy engines are genuine unconnected native profiles. Transport,
    clock, filesystem and database observations below are explicitly synthetic.
    No supplied DTO replaces a V4 carrier, raw replay, G1 or precursor evaluator.
    """
    source = raw_market
    native_client_factory = route.collector._new_client
    native_route_probe = route.probe_initial_demo_operational_route_v4
    private = _client(
        okx_demo_api_key=API_KEY,
        okx_demo_api_secret=API_SECRET,
        okx_demo_api_passphrase=PASSPHRASE,
    )
    engine = create_async_engine(
        "postgresql+asyncpg://synthetic:synthetic@127.0.0.1:65432/synthetic",
        poolclass=NullPool,
        connect_args={"ssl": False},
    )
    factory = async_sessionmaker(
        engine, class_=AsyncSession, autoflush=False, expire_on_commit=False
    )
    custody_factory = async_sessionmaker(
        engine, class_=AsyncSession, autoflush=False, expire_on_commit=False
    )
    monkeypatch.setattr(
        account_native, "_configured_factory", lambda value: value is factory
    )
    holder = {"calls": [], "factory": factory, "engine": engine}
    with private.pinned_read_session() as lease:
        at = source.evaluated_at
        plans = tuple(
            current_plan_v7(
                plan_id=name,
                session_binding_id=lease.binding_id,
                created_at=at - timedelta(seconds=1),
                history_start=at - timedelta(days=1),
                history_end=at - timedelta(seconds=1),
            )
            for name in ("frame-public-v1", "frame-current-v1")
        )
        original_public = market.acquire_initial_demo_market_source_v4
        original_account = account.acquire_initial_demo_account_source_v4

        async def public_capture(public_root, **kwargs):
            holder["calls"].append("public")
            assert kwargs["account_session"]._used is False
            holder["public_session"] = kwargs["account_session"]
            monkeypatch.setattr(
                public_fixture, "_session", lambda: kwargs["account_session"]
            )
            monkeypatch.setattr(public_fixture, "_client", lambda: private)
            public = public_fixture.setup(monkeypatch, source)
            holder["public_fixture"] = public
            clock = public[0]
            monkeypatch.setattr(frame, "native_stamp", clock.stamp)
            owner = await original_public(public_root, **kwargs)
            holder["public_owner"] = owner
            holder["public_state"] = market._CURRENT[owner]
            return owner

        async def private_capture(**kwargs):
            holder["calls"].append("account")
            assert kwargs["account_session"]._used is False
            assert kwargs["account_session"] is not holder["public_session"]
            assert (
                kwargs["account_session"]._checked_read_session(
                    kwargs["account_session"]._plan
                )
                is lease
            )
            clock = holder["public_fixture"][0]

            def gateway(stream, _index, rows):
                if stream == "account_instruments":
                    rows = [account_row("account_instruments", tickSz="0.01")]
                request = holder["harness"].requests[-1]
                started = datetime.fromisoformat(request.headers["OK-ACCESS-TIMESTAMP"])
                delta = started - datetime(1970, 1, 1, tzinfo=UTC)
                micros = (
                    delta.days * 86400 + delta.seconds
                ) * 1_000_000 + delta.microseconds

                def supplied(value):
                    if type(value) is dict:
                        for key, item in value.items():
                            if key == "uTime":
                                value[key] = str(micros // 1000 - 500)
                            else:
                                supplied(item)
                    elif type(value) is list:
                        for item in value:
                            supplied(item)

                supplied(rows)
                if stream == "account_position_risk":
                    for row in rows:
                        row["ts"] = str(micros // 1000)
                return wire(rows, inTime=str(micros + 100), outTime=str(micros + 200))

            _, harness, _, _, events = journal_setup(
                monkeypatch, v4=True, change=gateway
            )
            holder["harness"] = harness
            harness.script = declared_native_source("fresh_flat", current_v7=True)[0]
            monkeypatch.setattr(route, "_native_client", native_client_factory)
            monkeypatch.setattr(
                route, "probe_initial_demo_operational_route_v4", native_route_probe
            )
            _native_config_fixture(
                monkeypatch,
                Stream(
                    _config(
                        uid=plans[1].expected_uid, main_uid=plans[1].expected_main_uid
                    )
                ),
            )
            for module in (account, route, storage):
                monkeypatch.setattr(module, "native_stamp", clock.stamp)
            monkeypatch.setattr(native.clock, "native_stamp", clock.stamp)
            monkeypatch.setattr(
                native.clock,
                "native_os_clock",
                lambda: synthetic_host(
                    clock.stamp, profile_id=proof.public_clock.UPDATED_PROFILE_ID
                ),
            )

            async def time_probe(stage, plan, *, directory, phase, tokens):
                outputs = synthetic_time_probe(
                    stage, native._state(stage), plan, clock.stamp, phase
                )
                for suffix, raw in zip(
                    ("json", "raw", "trace.json"), outputs, strict=True
                ):
                    name = (
                        f"exchange-{phase}.{suffix}"
                        if suffix != "trace.json"
                        else f"exchange-{phase}-trace.json"
                    )
                    storage._publish_part(directory, name, raw)
                return outputs

            monkeypatch.setattr(account_native, "_time_probe", time_probe)
            original_collect = bootstrap._collect_recorded

            async def collect(session, **arguments):
                harness.clock = arguments["clock"]
                recorded, owner = await original_collect(session, **arguments)
                relabeled = synthetic_tls_chain(tuple(events))
                events[:] = relabeled
                owner.previous = observed.source_reference(relabeled).head_sha256
                return replace(
                    recorded,
                    bootstrap=replace(
                        recorded.bootstrap,
                        transport_provenance="owned_signed_verified_tls",
                    ),
                ), owner

            monkeypatch.setattr(bootstrap, "_collect_recorded", collect)
            native_files, route_files = {}, {}
            holder["account_native_files"] = native_files

            @contextmanager
            def root(_root):
                yield MemoryDirectory(native_files)

            @contextmanager
            def private_root(_root):
                yield MemoryDirectory(route_files)

            monkeypatch.setattr(storage, "_companion_attempt", root)
            monkeypatch.setattr(storage, "_root_context", root)
            monkeypatch.setattr(storage, "_root_identity", lambda _root: (11, 22))
            monkeypatch.setattr(
                account.public_storage, "_native_recheck_root", private_root
            )
            monkeypatch.setattr(
                route, "_native_attempt_directory", lambda directory: directory
            )

            async def deployment(_self):
                return None

            async def seal(
                _self, *, bundle_raw, expected_sha256, proof_envelope_sha256
            ):
                replay = account.replay_current_account_bundle_v4(
                    bundle_raw, expected_sha256=expected_sha256
                )
                assert replay.proof_envelope_sha256 == proof_envelope_sha256
                result = custody.RetainedRuntimeBundleReadback(
                    "a" * 32,
                    expected_sha256,
                    proof_envelope_sha256,
                    clock.last,
                    clock.last,
                    bundle_raw,
                )
                receipt = custody.OwnedRuntimeBundleCustodyReceipt(custody._ISSUER)
                custody._RECEIPTS[receipt] = (
                    result,
                    custody._owner(),
                    custody.time.monotonic_ns(),
                )
                holder["accepted_account_bundle"] = bundle_raw
                if mutation == "late_readback":
                    raise ValueError("synthetic-late-readback-unknown")
                return receipt

            monkeypatch.setattr(
                custody.RuntimeBundleCustodyRepository, "verify_deployment", deployment
            )
            monkeypatch.setattr(
                custody.RuntimeBundleCustodyRepository, "seal_bundle", seal
            )
            if mutation == "cancel":
                raise asyncio.CancelledError
            owner = await original_account(**kwargs)
            holder["account_owner"] = owner
            holder["account_state"] = account._CURRENT[owner]
            harness.assert_closed()
            if mutation == "expire":
                clock.count += 31_000
            elif mutation == "public_pin":
                holder["public_state"]["bundle_sha256"] = "0" * 64
            elif mutation == "database_pin":
                factory.kw["expire_on_commit"] = True
            return owner

        monkeypatch.setattr(
            market, "acquire_initial_demo_market_source_v4", public_capture
        )
        monkeypatch.setattr(
            account, "acquire_initial_demo_account_source_v4", private_capture
        )
        arguments = {
            "read_session": lease,
            "private_client": private,
            "public_account_plan": plans[0],
            "current_account_plan": plans[1],
            "public_root": tmp_path / "public",
            "public_account_proof_root": tmp_path / "public-private",
            "account_proof_root": tmp_path / "account-private",
            "account_native_root": tmp_path / "account-native",
            "instrument_id": "BTC-USDT-SWAP",
            "strategy": "trend_pullback",
            "market_policy": policy(),
            "session_factory": factory,
            "custody_session_factory": custody_factory,
        }
        holder.update(arguments=arguments, lease=lease)
        yield SimpleNamespace(**holder, held=holder)


@pytest.mark.asyncio
async def test_positive_real_V4_issuer_raw_replay_and_one_use_handoff(
    monkeypatch, tmp_path, raw_market
):
    with setup(monkeypatch, tmp_path, raw_market) as state:
        invocation = frame.open_current_original_capture_v1(**state.arguments)
        owned = await invocation.capture()
        with pytest.raises(market.CurrentMarketSourceV4Error):
            market.evaluate_owned_current_market_v4(state.held["public_owner"])
        with pytest.raises(account.CurrentAccountSourceV4Error):
            account.consume_owned_current_demo_account_v4(state.held["account_owner"])
        with pytest.raises(market.CurrentMarketSourceV4Error):
            market._take_original_frame_v1(
                state.held["public_owner"], key=frame._HOOK_KEY
            )
        with pytest.raises(account.CurrentAccountSourceV4Error):
            account._take_original_frame_v1(
                state.held["account_owner"], key=frame._HOOK_KEY
            )
        operands = frame.consume_current_original_inputs_v1(owned)
        assert state.calls == ["public", "account"]
        assert operands.market.instrument_id == "BTC-USDT-SWAP"
        assert (
            operands.account_packet.plan.expected_uid
            == state.arguments["current_account_plan"].expected_uid
        )
        assert operands.g1.gate.passed
        assert operands.precursor.receipt_json
        assert all(
            json.loads(operands.receipt_json)[name] is False
            for name in (
                "account_complete",
                "account_revision_published",
                "candidate_qualified",
                "g12_published",
                "post_g12_eligible",
                "execution_authority",
            )
        )
        with pytest.raises(frame.CurrentOriginalFrameError):
            frame.consume_current_original_inputs_v1(owned)
        with pytest.raises(frame.CurrentOriginalFrameError):
            frame.consume_current_original_inputs_v1(operands)
        with pytest.raises(market.CurrentMarketSourceV4Error):
            market._take_original_frame_v1(
                state.held["public_state"]["bundle_raw"], key=frame._HOOK_KEY
            )
        with pytest.raises(frame.CurrentOriginalFrameError):
            invocation.capture()
        assert not frame._FRAMES and not market._CURRENT and not account._CURRENT
        assert not route._OWNED_ROUTES


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation", ["expire", "public_pin", "database_pin", "late_readback"]
)
async def test_sequential_or_final_DB_failure_never_refreshes_or_retries(
    monkeypatch, tmp_path, raw_market, mutation
):
    with setup(monkeypatch, tmp_path, raw_market, mutation=mutation) as state:
        invocation = frame.open_current_original_capture_v1(**state.arguments)
        with pytest.raises(frame.CurrentOriginalFrameError):
            await invocation.capture()
        with pytest.raises(frame.CurrentOriginalFrameError):
            invocation.capture()
        assert state.calls == ["public", "account"]
        if mutation == "late_readback":
            assert state.held["accepted_account_bundle"]
            assert state.held["account_native_files"]
        assert not frame._FRAMES and not route._OWNED_ROUTES


@pytest.mark.asyncio
async def test_cancellation_releases_original_public_claim_without_resume(
    monkeypatch, tmp_path, raw_market
):
    with setup(monkeypatch, tmp_path, raw_market, mutation="cancel") as state:
        invocation = frame.open_current_original_capture_v1(**state.arguments)
        with pytest.raises(asyncio.CancelledError):
            await invocation.capture()
        with pytest.raises(frame.CurrentOriginalFrameError):
            invocation.capture()
        assert not route._OWNED_ROUTES and not frame._FRAMES


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    [
        "same_plan",
        "uid",
        "same_root",
        "factory",
        "strategy",
        "saved_DTO",
        "source_flag",
    ],
)
async def test_input_claims_and_callbacks_cannot_replace_owned_acquisition(
    monkeypatch, tmp_path, raw_market, case
):
    with setup(monkeypatch, tmp_path, raw_market) as state:
        args = dict(state.arguments)
        if case == "same_plan":
            args["current_account_plan"] = args["public_account_plan"]
        elif case == "uid":
            args["current_account_plan"] = args["current_account_plan"].model_copy(
                update={"expected_uid": "999999"}
            )
        elif case == "same_root":
            args["account_native_root"] = args["public_root"]
        elif case == "factory":
            args["session_factory"] = lambda: None
        elif case == "strategy":
            args["strategy"] = object()
        elif case == "saved_DTO":
            args["read_session"] = SimpleNamespace(source_authenticity_verified=True)
        else:
            args["passed"] = True
        with pytest.raises((frame.CurrentOriginalFrameError, TypeError)):
            frame.open_current_original_capture_v1(**args)
        assert not state.calls


@pytest.mark.asyncio
async def test_foreign_task_before_capture_is_burned_before_source_IO(
    monkeypatch, tmp_path, raw_market
):
    with setup(monkeypatch, tmp_path, raw_market) as state:
        invocation = frame.open_current_original_capture_v1(**state.arguments)
        coroutine = invocation.capture()
        with pytest.raises(frame.CurrentOriginalFrameError):
            await asyncio.create_task(coroutine)
        with pytest.raises(frame.CurrentOriginalFrameError):
            invocation.capture()
        assert not state.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["foreign_task", "lease", "raw_and_pin", "expiry"])
async def test_handoff_failure_burns_and_cleans_original_resources(
    monkeypatch, tmp_path, raw_market, case
):
    with setup(monkeypatch, tmp_path, raw_market) as state:
        invocation = frame.open_current_original_capture_v1(**state.arguments)
        owned = await invocation.capture()
        if case == "foreign_task":

            async def other_task():
                frame.consume_current_original_inputs_v1(owned)

            with pytest.raises(frame.CurrentOriginalFrameError):
                await asyncio.create_task(other_task())
        else:
            if case == "lease":
                state.lease._lease.active = False
            elif case == "raw_and_pin":
                original = state.held["public_state"]
                original["bundle_raw"] += b" "
                original["bundle_sha256"] = sha(original["bundle_raw"])
            else:
                state.held["public_fixture"][0].count += 31_000
            with pytest.raises(frame.CurrentOriginalFrameError):
                frame.consume_current_original_inputs_v1(owned)
        with pytest.raises(frame.CurrentOriginalFrameError):
            frame.consume_current_original_inputs_v1(owned)
        assert state.calls == ["public", "account"]
        assert not frame._FRAMES and not route._OWNED_ROUTES


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", [copy.copy, copy.deepcopy, pickle.dumps])
async def test_constructor_and_serialization_do_not_mint_handoff(
    monkeypatch, tmp_path, raw_market, operation
):
    with setup(monkeypatch, tmp_path, raw_market) as state:
        invocation = frame.open_current_original_capture_v1(**state.arguments)
        with pytest.raises(frame.CurrentOriginalFrameError):
            operation(invocation)
        with pytest.raises(frame.CurrentOriginalFrameError):
            frame.OwnedCurrentOriginalFrameV1()
        coroutine = invocation.capture()
        coroutine.close()  # Cancellation/abandonment before its first step.
        with pytest.raises(frame.CurrentOriginalFrameError):
            invocation.capture()
        assert not state.calls
