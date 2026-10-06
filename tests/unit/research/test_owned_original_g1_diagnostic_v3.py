"""Source-owned original G1 inspection; synthetic IO never grants admission."""

from datetime import timedelta

import pytest

from app.domain.source_primitives import canonical, decode, sha, utc_from_ns
from app.trade_qualification import account_native_runtime as account_native
from app.trade_qualification import data_v2
from app.trade_qualification import native_original_g1_policy_v1 as native_g1
from app.trade_qualification import original_source_coordinator_v2 as coordinator
from tests.unit.research.test_owned_original_source_coordinator_v2 import session
from tests.unit.research.test_owned_public_runtime_v2 import (
    empty_registries,
    policy,
    setup,
)
from tests.unit.test_qualification_market_bridge import v2_engine_source


@pytest.fixture(scope="module")
def source():
    return v2_engine_source("long")


def _native_account_receipt(controlled, observed_at):
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
                "observed_at": observed_at.isoformat(),
                "expires_at": (observed_at + timedelta(seconds=10)).isoformat(),
                "snapshot": None,
                "account_complete": False,
                "account_revision_published": False,
                "flat_start_permission": False,
                "execution_authority": False,
                "admission": "DENY",
            }
        )
    )


@pytest.mark.asyncio
async def test_native_packet_is_replayed_into_pinned_g1_and_never_grants_authority(
    source, tmp_path, monkeypatch
):
    clock, directory, harness, publications = setup(monkeypatch, source)
    monkeypatch.setattr(coordinator, "native_stamp", clock.stamp)
    monkeypatch.setattr(account_native, "_configured_factory", lambda _: True)
    controlled = session()
    account_calls = []
    g1_calls = []
    verify_calls = []
    evaluate = data_v2.evaluate_public_market_data_v2
    verify = data_v2.verify_public_market_data_v2

    def observed_evaluate(packet, *, expected_bundle_sha256, policy, evaluated_at):
        g1_calls.append((packet, expected_bundle_sha256, policy, evaluated_at))
        return evaluate(
            packet,
            expected_bundle_sha256=expected_bundle_sha256,
            policy=policy,
            evaluated_at=evaluated_at,
        )

    def observed_verify(result, packet, **kwargs):
        verify_calls.append((result, packet, kwargs))
        return verify(result, packet, **kwargs)

    async def account_source(received, *, session_factory, proof_root):
        account_calls.append((received, session_factory, proof_root))
        at = utc_from_ns(clock.stamp()["utc_ns"])
        received._used = True
        return _native_account_receipt(received, at)

    factory = object()
    monkeypatch.setattr(data_v2, "evaluate_public_market_data_v2", observed_evaluate)
    monkeypatch.setattr(data_v2, "verify_public_market_data_v2", observed_verify)
    monkeypatch.setattr(
        account_native, "capture_initial_native_account", account_source
    )
    result = await coordinator.capture_owned_original_sources_v3(
        tmp_path / "public",
        tmp_path / "account",
        instrument_id="BTC-USDT-SWAP",
        market_policy=policy(),
        account_session=controlled,
        session_factory=factory,
    )
    receipt = decode(result.receipt_json)
    assert receipt["schema_version"] == "ctcc.original_owned_sources_diagnostic.v3"
    assert receipt["g1_evaluated"] is True
    assert receipt["g1_policy_sha256"] == native_g1.DATA_POLICY_SHA256
    assert receipt["g1_policy_record_sha256"] == native_g1.POLICY_RECORD_SHA256
    assert receipt["g1_source_sha256"] == receipt["public_packet_sha256"]
    assert len(receipt["g1_result_sha256"]) == 64
    assert type(receipt["g1_result_code"]) is str
    assert receipt["g1_passed"] is True, receipt["g1_result_code"]
    # The verifier independently evaluates the same retained packet a second time.
    assert len(g1_calls) == 2 and len(verify_calls) == 1
    assert g1_calls[0] == g1_calls[1]
    packet, packet_pin, policy_value, observed_at = g1_calls[0]
    assert packet_pin == packet.bundle_sha256 == receipt["g1_source_sha256"]
    assert sha(data_v2.data._canonical(policy_value)) == native_g1.DATA_POLICY_SHA256
    assert observed_at.isoformat() == receipt["g1_evaluated_at"]
    assert receipt["code"] == "original_sources_g1_observed_candidate_required"
    assert account_calls == [(controlled, factory, tmp_path / "account")]
    assert receipt["candidate_created"] is False
    assert receipt["g12_published"] is False
    assert receipt["atomic_risk_reserved"] is False
    assert receipt["execution_authority"] is False
    assert result.admission == "DENY" and controlled._used
    assert len(harness.requests) == 9 and not publications
    assert directory.content["plan.json"]
    harness.assert_closed()
    empty_registries()


@pytest.mark.asyncio
async def test_native_g1_rejection_never_starts_account_capture(
    source, tmp_path, monkeypatch
):
    clock, directory, harness, publications = setup(monkeypatch, source)
    monkeypatch.setattr(coordinator, "native_stamp", clock.stamp)
    monkeypatch.setattr(account_native, "_configured_factory", lambda _: True)
    original = harness.public

    def elevated_funding(request):
        rows = original(request)
        if request.url.path.endswith("/funding-rate"):
            rows[0]["fundingRate"] = "0.001"
        return rows

    async def forbidden(*_args, **_kwargs):
        pytest.fail("rejected original G1 must not start a private account read")

    harness.public = elevated_funding
    monkeypatch.setattr(account_native, "capture_initial_native_account", forbidden)
    controlled = session()
    result = await coordinator.capture_owned_original_sources_v3(
        tmp_path / "public",
        tmp_path / "account",
        instrument_id="BTC-USDT-SWAP",
        market_policy=policy(),
        account_session=controlled,
        session_factory=object(),
    )
    receipt = decode(result.receipt_json)
    assert receipt["code"] == "original_g1_rejected"
    assert receipt["g1_result_code"] == "funding_exceeded"
    assert receipt["g1_evaluated"] is True and receipt["g1_passed"] is False
    assert receipt["g1_source_sha256"] == receipt["public_packet_sha256"]
    assert receipt["account_receipt_sha256"] is None
    assert receipt["admission"] == "DENY" and controlled._used
    assert len(harness.requests) == 9 and not publications
    assert directory.content["plan.json"]
    harness.assert_closed()
    empty_registries()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "extra", ["market", "policy", "g1_result", "passed", "candidate", "barrier"]
)
async def test_v3_caller_cannot_inject_g1_or_cached_source(tmp_path, extra):
    with pytest.raises(TypeError):
        await coordinator.capture_owned_original_sources_v3(
            tmp_path / "public",
            tmp_path / "account",
            instrument_id="BTC-USDT-SWAP",
            market_policy=policy(),
            account_session=session(),
            session_factory=object(),
            **{extra: object()},
        )


def test_v3_receipt_rejects_changed_policy_source_result_and_authority():
    base = {
        "schema_version": "ctcc.original_owned_sources_diagnostic.v3",
        "code": "original_source_public_unavailable",
        "account_plan_sha256": "a" * 64,
        "declared_demo_route_policy_sha256": "b" * 64,
        "public_report_id": None,
        "public_packet_sha256": None,
        "public_journal_sha256": None,
        "account_receipt_sha256": None,
        "account_packet_sha256": None,
        "observed_at": None,
        "g1_policy_record_sha256": native_g1.POLICY_RECORD_SHA256,
        "g1_policy_sha256": native_g1.DATA_POLICY_SHA256,
        "g1_source_sha256": None,
        "g1_result_sha256": None,
        "g1_result_code": None,
        "g1_evaluated_at": None,
        "g1_evaluated": False,
        "g1_passed": False,
        "candidate_created": False,
        "g1_g11_complete": False,
        "g12_published": False,
        "account_complete": False,
        "source_authenticity_verified": False,
        "execution_recheck_performed": False,
        "atomic_risk_reserved": False,
        "execution_authority": False,
        "order_submitted": False,
        "admission": "DENY",
    }
    coordinator.InitialOwnedSourcesDiagnosticV3(canonical(base))
    for change in (
        {"g1_policy_sha256": "f" * 64},
        {"g1_policy_record_sha256": "f" * 64},
        {"g1_source_sha256": "f" * 64},
        {"g1_result_sha256": "f" * 64},
        {"g1_passed": True},
        {"execution_authority": True},
    ):
        with pytest.raises(coordinator.OriginalSourceCoordinatorError):
            coordinator.InitialOwnedSourcesDiagnosticV3(canonical(base | change))

    observed_at = "2026-09-12T01:10:00+00:00"
    source = base | {
        "public_report_id": "initial-synthetic",
        "public_packet_sha256": "c" * 64,
        "public_journal_sha256": "d" * 64,
        "g1_source_sha256": "c" * 64,
        "observed_at": observed_at,
    }
    unavailable = source | {"code": "original_g1_unavailable"}
    rejected = source | {
        "code": "original_g1_rejected",
        "g1_result_sha256": "e" * 64,
        "g1_result_code": "funding_exceeded",
        "g1_evaluated_at": observed_at,
        "g1_evaluated": True,
    }
    account_unavailable = rejected | {
        "code": "original_source_account_unavailable",
        "g1_result_code": "passed",
        "g1_passed": True,
    }
    complete_diagnostic = account_unavailable | {
        "code": "original_sources_g1_observed_candidate_required",
        "account_receipt_sha256": "1" * 64,
        "account_packet_sha256": "2" * 64,
    }
    for valid in (unavailable, rejected, account_unavailable, complete_diagnostic):
        coordinator.InitialOwnedSourcesDiagnosticV3(canonical(valid))
    for invalid in (
        base | {"g1_evaluated": True},
        base | {"g1_source_sha256": "c" * 64},
        unavailable | {"account_receipt_sha256": "1" * 64},
        rejected | {"account_packet_sha256": "2" * 64},
        source | {"code": "original_source_account_unavailable"},
        rejected | {"code": "original_source_account_unavailable"},
        account_unavailable | {"account_receipt_sha256": "1" * 64},
        complete_diagnostic | {"g1_passed": False},
        rejected | {"g1_evaluated_at": "2026-09-12T01:10:01+00:00"},
    ):
        with pytest.raises(coordinator.OriginalSourceCoordinatorError):
            coordinator.InitialOwnedSourcesDiagnosticV3(canonical(invalid))


def test_native_policy_pin_rejects_in_place_record_change(monkeypatch):
    assert native_g1.fixed_native_original_g1_policy().policy_id == native_g1.POLICY_ID
    monkeypatch.setattr(native_g1, "POLICY_RECORD", native_g1.POLICY_RECORD + b" ")
    with pytest.raises(ValueError, match="native_original_g1_policy_record_changed"):
        native_g1.fixed_native_original_g1_policy()
