"""Read-only same-task join mechanism; synthetic evidence is never acceptance."""

import asyncio
import os
from datetime import timedelta
from types import SimpleNamespace

import pytest

from app.domain.source_primitives import canonical, decode, sha, utc_from_ns
from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_native_runtime as account_native
from app.trade_qualification import original_source_coordinator_v2 as coordinator
from app.trade_qualification import public_source_runtime
from app.trade_qualification.account_runtime import ControlledDemoAccountSession
from tests.unit.research.test_owned_public_runtime_v2 import policy
from tests.unit.test_account_current_history_join import current_plan
from tests.unit.test_qualification_account_capture import NOW
from tests.unit.test_qualification_account_collector import credentials


def session():
    plan = current_plan()
    return ControlledDemoAccountSession(
        credentials=credentials(session_binding_id=plan.session_binding_id),
        plan=plan,
        expected_plan_sha256=capture.plan_sha256(plan),
    )


def clock():
    count = 0
    base = int(NOW.timestamp() * 1_000_000_000)

    def sample():
        nonlocal count
        count += 1
        return {
            "utc_ns": base + count * 1_000_000,
            "monotonic_ns": 1_000_000_000 + count * 1_000_000,
        }

    return sample


@pytest.mark.asyncio
async def test_current_native_public_origin_is_still_denied_before_io(
    tmp_path, monkeypatch
):
    controlled = session()
    sampled = clock()
    monkeypatch.setattr(coordinator, "native_stamp", sampled)
    monkeypatch.setattr(coordinator.initial, "native_stamp", sampled)
    monkeypatch.setattr(account_native, "_configured_factory", lambda _: True)

    def forbidden(*_args, **_kwargs):
        pytest.fail("public route denial must precede HTTP/account acquisition")

    monkeypatch.setattr(public_source_runtime, "_new_owned_client", forbidden)
    monkeypatch.setattr(account_native, "capture_initial_native_account", forbidden)
    result = await coordinator.capture_owned_original_sources_v2(
        tmp_path / "public",
        tmp_path / "account",
        instrument_id="BTC-USDT-SWAP",
        market_policy=policy(),
        account_session=controlled,
        session_factory=object(),
    )
    receipt = decode(result.receipt_json)
    assert result.receipt_sha256 == sha(result.receipt_json)
    assert receipt["code"] == "original_source_public_unavailable"
    assert receipt["public_packet_sha256"] is None
    assert receipt["account_receipt_sha256"] is None
    assert receipt["admission"] == "DENY"
    assert all(
        receipt[key] is False
        for key in (
            "candidate_created",
            "g1_g11_complete",
            "g12_published",
            "account_complete",
            "source_authenticity_verified",
            "execution_recheck_performed",
            "atomic_risk_reserved",
            "execution_authority",
            "order_submitted",
        )
    )
    assert result.execution_authority is False and controlled._used is True
    assert not (tmp_path / "public").exists()
    assert not (tmp_path / "account").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "extra",
    [
        "original_market",
        "candidate",
        "run",
        "report_id",
        "barrier",
        "receipt",
        "passed",
    ],
)
async def test_caller_cannot_supply_replay_or_permission(tmp_path, extra):
    with pytest.raises(TypeError):
        await coordinator.capture_owned_original_sources_v2(
            tmp_path / "public",
            tmp_path / "account",
            instrument_id="BTC-USDT-SWAP",
            market_policy=policy(),
            account_session=session(),
            session_factory=object(),
            **{extra: object()},
        )


@pytest.mark.asyncio
async def test_overlap_and_unreviewed_region_reject_before_any_capture(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(account_native, "_configured_factory", lambda _: True)

    async def forbidden(*_args, **_kwargs):
        pytest.fail("invalid preflight cannot acquire a source")

    monkeypatch.setattr(coordinator.initial, "_capture_initial_lineage_v2", forbidden)
    with pytest.raises(
        coordinator.OriginalSourceCoordinatorError, match="roots_overlap"
    ):
        await coordinator.capture_owned_original_sources_v2(
            tmp_path / "same",
            tmp_path / "same",
            instrument_id="BTC-USDT-SWAP",
            market_policy=policy(),
            account_session=session(),
            session_factory=object(),
        )
    selected = current_plan(registration_region="tr", origin="https://tr.okx.com")
    controlled = ControlledDemoAccountSession(
        credentials=credentials(session_binding_id=selected.session_binding_id),
        plan=selected,
        expected_plan_sha256=capture.plan_sha256(selected),
    )
    with pytest.raises(
        coordinator.OriginalSourceCoordinatorError, match="account_scope_unsupported"
    ):
        await coordinator.capture_owned_original_sources_v2(
            tmp_path / "public",
            tmp_path / "account",
            instrument_id="BTC-USDT-SWAP",
            market_policy=policy(),
            account_session=controlled,
            session_factory=object(),
        )


def test_resolved_alias_is_denied_even_for_distinct_lexical_roots(
    tmp_path, monkeypatch
):
    first, second = tmp_path / "public", tmp_path / "account"
    resolved = tmp_path / "same-physical-root"
    original = type(tmp_path).resolve

    def alias(self, *args, **kwargs):
        return resolved if self in (first, second) else original(self, *args, **kwargs)

    with monkeypatch.context() as patcher:
        patcher.setattr(type(tmp_path), "resolve", alias)
        with pytest.raises(
            coordinator.OriginalSourceCoordinatorError,
            match="resolved_roots_overlap",
        ):
            coordinator._roots(first, second)
    assert not first.exists() and not second.exists()


def test_existing_symlink_or_junction_ancestor_is_denied(tmp_path):
    target = tmp_path / "real"
    target.mkdir()
    alias = tmp_path / "alias"
    try:
        os.symlink(target, alias, target_is_directory=True)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"directory link unavailable on this host: {type(exc).__name__}")
    with pytest.raises(coordinator.OriginalSourceCoordinatorError, match="linked_root"):
        coordinator._roots(tmp_path / "public", alias / "account")
    assert not (target / "account").exists()


@pytest.mark.asyncio
async def test_diagnostic_constructor_rejects_inconsistent_observed_claims(
    tmp_path, monkeypatch
):
    controlled = session()
    sampled = clock()
    monkeypatch.setattr(coordinator, "native_stamp", sampled)
    monkeypatch.setattr(coordinator.initial, "native_stamp", sampled)
    monkeypatch.setattr(account_native, "_configured_factory", lambda _: True)
    original = await coordinator.capture_owned_original_sources_v2(
        tmp_path / "public",
        tmp_path / "account",
        instrument_id="BTC-USDT-SWAP",
        market_policy=policy(),
        account_session=controlled,
        session_factory=object(),
    )
    baseline = decode(original.receipt_json)
    changes = (
        {"code": "passed"},
        {"code": True},
        {"code": "original_sources_observed_g1_candidate_required"},
        {"public_packet_sha256": "f" * 64},
        {"account_plan_sha256": "F" * 64},
        {"declared_demo_route_policy_sha256": None},
        {"observed_at": NOW.isoformat()},
        {"execution_authority": True},
        {"extra": "unexpected"},
    )
    for changed in changes:
        with pytest.raises(
            coordinator.OriginalSourceCoordinatorError,
            match="original_source_receipt_invalid",
        ):
            coordinator.InitialOwnedSourcesDiagnosticV2(canonical(baseline | changed))


@pytest.mark.asyncio
async def test_synthetic_same_task_handoffs_still_only_record_denial(
    tmp_path, monkeypatch
):
    """Mechanism test: substitutes both sources; proves no real Demo capture."""
    controlled = session()
    sampled = clock()
    monkeypatch.setattr(coordinator, "native_stamp", sampled)
    monkeypatch.setattr(account_native, "_configured_factory", lambda _: True)
    owner = asyncio.current_task()
    marker = object()
    token = None
    report = "initial-synthetic"
    packet_json = canonical(
        {
            "stage": "initial_public",
            "barrier_completed_at": None,
            "environment": "demo",
            "instrument_id": "BTC-USDT-SWAP",
            "report_id": report,
        }
    )
    packet = SimpleNamespace(bundle_sha256=sha(packet_json), packet_json=packet_json)

    async def public_source(_root, *, invocation, **_kwargs):
        nonlocal token
        assert asyncio.current_task() is owner
        token = invocation
        return marker, report, NOW + timedelta(seconds=10)

    def consume(carrier, invocation):
        assert asyncio.current_task() is owner
        assert carrier is marker and invocation is token
        return packet, "a" * 64, sampled()

    async def account_source(received, *, session_factory, proof_root):
        assert asyncio.current_task() is owner
        assert received is controlled and proof_root == tmp_path / "account"
        assert session_factory is fake_factory
        received._used = True
        at = utc_from_ns(sampled()["utc_ns"])
        return account_native.InitialNativeAccountDiagnostic(
            canonical(
                {
                    "schema_version": "ctcc.initial_native_account_diagnostic.v2",
                    "policy_sha256": account_native.proof.V3_POLICY_SHA256,
                    "proof_sha256": "1" * 64,
                    "proof_readback_sha256": "2" * 64,
                    "current_source_receipt_sha256": "3" * 64,
                    "source_reference": {
                        "capture_id": "b" * 32,
                        "head_sha256": "c" * 64,
                        "plan_sha256": controlled._pin,
                        "packet_sha256": "d" * 64,
                        "session_binding_sha256": "e" * 64,
                    },
                    "current_native_source_observed": True,
                    "native_sampled_hwm_verified": False,
                    "observed_at": at.isoformat(),
                    "expires_at": (NOW + timedelta(seconds=10)).isoformat(),
                    "snapshot": None,
                    "account_complete": False,
                    "account_revision_published": False,
                    "flat_start_permission": False,
                    "execution_authority": False,
                    "admission": "DENY",
                }
            )
        )

    fake_factory = object()
    monkeypatch.setattr(
        coordinator.initial, "_capture_initial_lineage_v2", public_source
    )
    monkeypatch.setattr(coordinator, "_consume_initial_public_capture_v2", consume)
    monkeypatch.setattr(
        coordinator,
        "public_market_context_v2",
        lambda *_args, **_kwargs: SimpleNamespace(packet_sha256=packet.bundle_sha256),
    )
    monkeypatch.setattr(
        account_native, "capture_initial_native_account", account_source
    )
    result = await coordinator.capture_owned_original_sources_v2(
        tmp_path / "public",
        tmp_path / "account",
        instrument_id="BTC-USDT-SWAP",
        market_policy=policy(),
        account_session=controlled,
        session_factory=fake_factory,
    )
    receipt = decode(result.receipt_json)
    assert receipt["code"] == "original_sources_observed_g1_candidate_required"
    assert receipt["public_packet_sha256"] == packet.bundle_sha256
    assert receipt["account_packet_sha256"] == "d" * 64
    assert receipt["account_plan_sha256"] == controlled._pin
    assert receipt["source_authenticity_verified"] is False
    assert result.admission == "DENY" and result.execution_authority is False
    assert controlled._used


@pytest.mark.asyncio
async def test_cancellation_burns_session_and_propagates(tmp_path, monkeypatch):
    controlled = session()
    monkeypatch.setattr(account_native, "_configured_factory", lambda _: True)

    async def cancelled(*_args, **_kwargs):
        raise asyncio.CancelledError

    monkeypatch.setattr(coordinator.initial, "_capture_initial_lineage_v2", cancelled)
    with pytest.raises(asyncio.CancelledError):
        await coordinator.capture_owned_original_sources_v2(
            tmp_path / "public",
            tmp_path / "account",
            instrument_id="BTC-USDT-SWAP",
            market_policy=policy(),
            account_session=controlled,
            session_factory=object(),
        )
    assert controlled._used is True
