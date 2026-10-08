"""Native Demo raw account handoff is one-use and always DENY-only."""

import asyncio
from dataclasses import replace

import pytest

from app.domain.source_primitives import canonical, sha, utc_from_ns
from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_native_clock as native
from app.trade_qualification import account_native_proof as proof
from app.trade_qualification import account_native_runtime as runtime
from app.trade_qualification import account_observation_index as observed
from app.trade_qualification.account_runtime import ControlledDemoAccountSession
from tests.unit.test_account_native_proof import companion_fixture, replay
from tests.unit.test_qualification_account_collector import credentials


@pytest.mark.asyncio
async def test_claimed_v7_stage_raw_packet_is_one_use_and_never_authoritative(
    monkeypatch,
):
    raw, files, chain, scope, samples = await companion_fixture(
        monkeypatch, current_v7=True
    )
    checked = replay(raw, files, chain, scope)
    reference, packet, _records, joins = proof._source(
        chain, scope, proof_schema=proof.V7_FLAT_SCHEMA
    )
    session = ControlledDemoAccountSession(
        credentials=credentials(session_binding_id=packet.plan.session_binding_id),
        plan=packet.plan,
        expected_plan_sha256=capture.plan_sha256(packet.plan),
    )

    with (
        native._claim_initial_session(session),
        native._initial_stage(
            plan_sha256=session._pin,
            scope_sha256=proof.scope_sha256(scope),
            _claimed_session=session,
        ) as stage,
    ):
        state = native._state(stage)
        state["closed"] = True
        issued = samples()
        receipt = canonical(
            {
                "schema_version": "ctcc.initial_native_account_diagnostic.v3",
                "policy_sha256": proof.V7_FLAT_POLICY_SHA256,
                "source_reference": observed.reference_document(reference),
                "proof_sha256": sha(raw),
                "proof_readback_sha256": "b" * 64,
                "current_native_source_observed": True,
                "native_sampled_hwm_verified": False,
                "observed_at": utc_from_ns(issued["utc_ns"]).isoformat(),
                "expires_at": checked.expires_at.isoformat(),
                "snapshot": None,
                "account_complete": False,
                "account_revision_published": False,
                "flat_start_permission": False,
                "execution_authority": False,
                "admission": "DENY",
            }
        )
        arguments = {
            "receipt_json": receipt,
            "issued": issued,
            "expires": checked.expires_at,
            "deadline": checked.monotonic_deadline_ns,
        }

        # The session is already burned by the real claim, but the collector
        # must have adopted that same claim before either handoff can mint.
        assert session._used is True
        assert state["session_claim"] is native._session_claim(session)
        with pytest.raises(runtime.NativeAccountRawPacketError):
            runtime._mint_native_demo_raw_packet(
                stage, session, packet, reference, **arguments
            )
        with pytest.raises(runtime.NativeAccountOriginError):
            runtime._mint_demo_account_origin(
                stage,
                session,
                packet,
                reference,
                joins,
                proof_sha256=sha(raw),
                readback_sha256="b" * 64,
                **arguments,
            )
        state["session_claim"]["bootstrap_used"] = True

        raw_lease = runtime._mint_native_demo_raw_packet(
            stage, session, packet, reference, **arguments
        )
        origin_lease = runtime._mint_demo_account_origin(
            stage,
            session,
            packet,
            reference,
            joins,
            proof_sha256=sha(raw),
            readback_sha256="b" * 64,
            **arguments,
        )
        diagnostic = runtime.InitialNativeAccountDiagnostic(
            receipt, origin_lease, raw_lease
        )
        readback = runtime._consume_native_demo_raw_packet(diagnostic, session)
        assert readback.packet == packet
        assert readback.reference == reference
        assert readback.receipt_sha256 == sha(receipt)
        assert readback.proof_sha256 == sha(raw)
        assert readback.readback_sha256 == "b" * 64
        assert readback.account_complete is False
        assert readback.source_authenticity_verified is False
        assert readback.execution_authority is False
        assert readback.admission == "DENY"
        with pytest.raises(runtime.NativeAccountRawPacketError):
            runtime._consume_native_demo_raw_packet(diagnostic, session)
        assert runtime._consume_demo_account_origin(diagnostic, session).admission == (
            "DENY"
        )

        foreign_lease = runtime._mint_native_demo_raw_packet(
            stage, session, packet, reference, **arguments
        )
        foreign = runtime.InitialNativeAccountDiagnostic(receipt, None, foreign_lease)

        async def other_task():
            with pytest.raises(runtime.NativeAccountRawPacketError):
                runtime._consume_native_demo_raw_packet(foreign, session)

        await asyncio.create_task(other_task())
        with pytest.raises(runtime.NativeAccountRawPacketError):
            runtime._consume_native_demo_raw_packet(foreign, session)

        tamper_lease = runtime._mint_native_demo_raw_packet(
            stage, session, packet, reference, **arguments
        )
        unchanged = runtime.InitialNativeAccountDiagnostic(receipt, None, tamper_lease)
        with pytest.raises(runtime.NativeAccountRawPacketError):
            runtime._consume_native_demo_raw_packet(
                replace(unchanged, receipt_json=b"{}"), session
            )
        with pytest.raises(runtime.NativeAccountRawPacketError):
            runtime._consume_native_demo_raw_packet(unchanged, session)

        changed_lease = runtime._mint_native_demo_raw_packet(
            stage, session, packet, reference, **arguments
        )
        changed = runtime.InitialNativeAccountDiagnostic(receipt, None, changed_lease)
        original = session._credentials.api_secret
        object.__setattr__(
            session._credentials, "api_secret", "synthetic-only-altered-account-secret"
        )
        with pytest.raises(runtime.NativeAccountRawPacketError):
            runtime._consume_native_demo_raw_packet(changed, session)
        object.__setattr__(session._credentials, "api_secret", original)
        with pytest.raises(runtime.NativeAccountRawPacketError):
            runtime._consume_native_demo_raw_packet(changed, session)
