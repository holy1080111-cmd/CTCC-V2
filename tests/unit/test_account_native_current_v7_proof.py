"""Synthetic V7 native chronology proof; never a Demo/Live acceptance."""

import json
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path

import pytest

from app.domain.source_primitives import canonical, sha, utc_from_ns
from app.trade_qualification import account_native_current_v7_proof as chronology
from app.trade_qualification import account_native_proof as native
from app.trade_qualification import account_native_proof_storage as storage
from app.trade_qualification import account_observation_index as observed
from app.trade_qualification import account_row_gateway_causality as row_gateway
from app.trade_qualification.account_runtime import AccountRuntimeError
from tests.unit.research.test_public_journal_contracts import MemoryDirectory
from tests.unit.test_account_native_proof import companion_fixture


def prove(raw, files, chain, scope):
    at = utc_from_ns(json.loads(raw)["proof_persist_start"]["utc_ns"])
    return chronology.prove_v7_native_current_chronology(
        native_proof_json=raw,
        clock_files=files,
        chain=chain,
        scope=scope,
        validated_at=at,
        expected_native_proof_sha256=sha(raw),
    )


@pytest.mark.asyncio
async def test_ordered_v7_native_source_has_only_denied_versioned_readback(monkeypatch):
    raw, files, chain, scope, _ = await companion_fixture(
        monkeypatch, gateway_case="ordered"
    )
    receipt = prove(raw, files, chain, scope)
    value = json.loads(receipt.receipt_json)
    assert value["schema_version"] == chronology.SCHEMA_VERSION
    assert value["policy_sha256"] == chronology.POLICY_SHA256
    assert value["native_proof_sha256"] == sha(raw)
    assert value["page_count"] >= 17
    assert value["known_source_timestamp_count"] > 0
    assert value["zero_clock_row_count"] > 0
    assert value["source_time_coverage_complete"] is False
    source = json.loads(
        native.replay_native_account_proof(
            raw, files=files, chain=chain, scope=scope, expected_proof_sha256=sha(raw)
        ).current_source_receipt_json
    )
    config_rows = [
        row
        for row in source["current_rows"]
        if row["stream"] in {"config_before", "config_after"}
    ]
    assert len(config_rows) == 2
    assert all(row["source_times"] == [] for row in config_rows)
    assert value["zero_clock_row_count"] >= len(config_rows)
    assert value["gateway_causal_coverage_complete"] is True
    assert value["known_source_row_times_ordered"] is True
    assert value["current_flat_qualified"] is False
    assert value["flat_start_permission"] is False
    assert value["account_complete"] is receipt.account_complete is False
    assert value["execution_authority"] is receipt.execution_authority is False
    assert value["admission"] == "DENY"
    at = utc_from_ns(json.loads(raw)["proof_persist_start"]["utc_ns"])
    assert (
        chronology.verify_v7_native_current_chronology(
            receipt,
            native_proof_json=raw,
            clock_files=files,
            chain=chain,
            scope=scope,
            validated_at=at,
            expected_native_proof_sha256=sha(raw),
            expected_receipt_sha256=receipt.receipt_sha256,
        ).receipt_json
        == receipt.receipt_json
    )
    with pytest.raises(
        chronology.NativeCurrentV7ProofError,
        match="native_current_v7_readback_mismatch",
    ):
        chronology.verify_v7_native_current_chronology(
            receipt,
            native_proof_json=raw,
            clock_files=files,
            chain=chain,
            scope=scope,
            validated_at=at,
            expected_native_proof_sha256=sha(raw),
            expected_receipt_sha256="0" * 64,
        )


@pytest.mark.asyncio
async def test_sealed_native_companion_is_read_back_again_before_chronology(
    monkeypatch,
):
    raw, files, chain, scope, samples = await companion_fixture(
        monkeypatch, gateway_case="ordered"
    )
    directory = MemoryDirectory(dict(files))
    monkeypatch.setattr(storage, "native_stamp", samples)
    monkeypatch.setattr(storage, "_root_identity", lambda _root: (11, 22))

    @contextmanager
    def roots(_root):
        yield directory

    monkeypatch.setattr(storage, "_root_context", roots)
    readback_sha = storage._seal_companion(directory, raw, chain=chain, scope=scope)
    at = utc_from_ns(
        json.loads(directory.content["readback.json"])["readback_complete"]["utc_ns"]
    )
    receipt = chronology.prove_v7_native_current_from_companion(
        Path.cwd(),
        chain=chain,
        scope=scope,
        validated_at=at,
        expected_native_proof_sha256=sha(raw),
        expected_native_readback_sha256=readback_sha,
    )
    assert (
        receipt.receipt_json
        == chronology.prove_v7_native_current_chronology(
            native_proof_json=raw,
            clock_files=files,
            chain=chain,
            scope=scope,
            validated_at=at,
            expected_native_proof_sha256=sha(raw),
        ).receipt_json
    )
    assert (
        chronology.verify_v7_native_current_from_companion(
            receipt,
            Path.cwd(),
            chain=chain,
            scope=scope,
            validated_at=at,
            expected_native_proof_sha256=sha(raw),
            expected_native_readback_sha256=readback_sha,
            expected_receipt_sha256=receipt.receipt_sha256,
        ).receipt_sha256
        == receipt.receipt_sha256
    )
    with pytest.raises(
        chronology.NativeCurrentV7ProofError,
        match="native_current_v7_readback_after_validation",
    ):
        chronology.prove_v7_native_current_from_companion(
            Path.cwd(),
            chain=chain,
            scope=scope,
            validated_at=utc_from_ns(json.loads(raw)["proof_persist_start"]["utc_ns"]),
            expected_native_proof_sha256=sha(raw),
            expected_native_readback_sha256=readback_sha,
        )
    with pytest.raises(
        chronology.NativeCurrentV7ProofError,
        match="native_current_v7_companion_readback_invalid",
    ):
        chronology.prove_v7_native_current_from_companion(
            Path.cwd(),
            chain=chain,
            scope=scope,
            validated_at=at,
            expected_native_proof_sha256=sha(raw),
            expected_native_readback_sha256="0" * 64,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("gateway_case", "expected"),
    [
        ("missing", "native_current_v7_gateway_or_row_time_incomplete"),
        ("row_after_outtime", "native_current_v7_gateway_or_row_time_incomplete"),
    ],
)
async def test_missing_or_future_row_clock_cannot_mint_native_proof(
    monkeypatch, gateway_case, expected
):
    raw, files, chain, scope, _ = await companion_fixture(
        monkeypatch, gateway_case=gateway_case, verify_baseline=False
    )
    # Both original V7 proofs still replay with the unchanged old contract.
    native.replay_native_account_proof(
        raw, files=files, chain=chain, scope=scope, expected_proof_sha256=sha(raw)
    )
    if gateway_case == "row_after_outtime":
        # The risk row is after the exchange response-send timestamp yet
        # before local headers/body receipt. The old native proof passes;
        # only the new per-page row/gateway chronology rejects it.
        at = utc_from_ns(json.loads(raw)["proof_persist_start"]["utc_ns"])
        row = row_gateway.audit_v7_account_row_gateway_causality(
            chain=chain,
            reference=observed.source_reference(chain),
            scope=scope,
            validated_at=at,
        )
        assert json.loads(row.receipt_json)[
            "source_timestamp_after_gateway_response_request_indices"
        ]
    with pytest.raises(chronology.NativeCurrentV7ProofError, match=expected):
        prove(raw, files, chain, scope)


@pytest.mark.asyncio
async def test_malformed_gateway_clock_is_rejected_before_native_proof(monkeypatch):
    with pytest.raises(AccountRuntimeError, match="account_runtime_invalid"):
        await companion_fixture(
            monkeypatch, gateway_case="malformed", verify_baseline=False
        )


@pytest.mark.asyncio
async def test_old_proof_relabel_or_caller_receipt_cannot_gain_current_claim(
    monkeypatch,
):
    raw, files, chain, scope, _ = await companion_fixture(
        monkeypatch, gateway_case="ordered"
    )
    receipt = prove(raw, files, chain, scope)
    with pytest.raises(
        chronology.NativeCurrentV7ProofError,
        match="native_current_v7_receipt_invalid",
    ):
        chronology.NativeCurrentV7ChronologyProof(receipt.receipt_json)
    value = json.loads(receipt.receipt_json)
    with pytest.raises(
        chronology.NativeCurrentV7ProofError,
        match="native_current_v7_receipt_invalid",
    ):
        chronology.NativeCurrentV7ChronologyProof(
            canonical({**value, "execution_authority": True}),
            _issuer=chronology._ISSUER,
        )
    with pytest.raises(chronology.NativeCurrentV7ProofError):
        chronology.prove_v7_native_current_chronology(
            native_proof_json=raw,
            clock_files=files,
            chain=chain,
            scope=scope,
            validated_at=utc_from_ns(json.loads(raw)["proof_persist_start"]["utc_ns"])
            + timedelta(seconds=31),
            expected_native_proof_sha256=sha(raw),
        )
    at = utc_from_ns(json.loads(raw)["proof_persist_start"]["utc_ns"])
    with pytest.raises(
        chronology.NativeCurrentV7ProofError,
        match="native_current_v7_proof_invalid",
    ):
        chronology.prove_v7_native_current_chronology(
            native_proof_json=raw,
            clock_files=files,
            chain=chain,
            scope=scope,
            validated_at=at,
            expected_native_proof_sha256="0" * 64,
        )
    for field in ("page_source_hashes_sha256", "row_source_hashes_sha256"):
        tampered = chronology.NativeCurrentV7ChronologyProof(
            canonical({**value, field: "0" * 64}), _issuer=chronology._ISSUER
        )
        with pytest.raises(
            chronology.NativeCurrentV7ProofError,
            match="native_current_v7_readback_mismatch",
        ):
            chronology.verify_v7_native_current_chronology(
                tampered,
                native_proof_json=raw,
                clock_files=files,
                chain=chain,
                scope=scope,
                validated_at=at,
                expected_native_proof_sha256=sha(raw),
                expected_receipt_sha256=tampered.receipt_sha256,
            )
