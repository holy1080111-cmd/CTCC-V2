"""V7 synthetic proof readback and private clock fence; no live issuer."""

import json
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

import pytest

from app.domain.source_primitives import canonical, sha
from app.trade_qualification import account_clock_boundary as boundary
from app.trade_qualification import account_native_proof as proof
from app.trade_qualification import account_native_proof_storage as storage
from app.trade_qualification import account_portfolio_components as components
from tests.unit.research.test_public_journal_contracts import MemoryDirectory
from tests.unit.test_account_clock_boundary import (
    assert_burned,
    consume,
    private_context_fixture,
)
from tests.unit.test_account_native_proof import companion_fixture, replay


def test_v7_readback_schema_is_exact_and_distinct_from_old_proofs():
    assert storage._readback_schema({"schema_version": proof.SCHEMA}) == (
        storage.READBACK_SCHEMA
    )
    assert storage._readback_schema({"schema_version": proof.V3_SCHEMA}) == (
        storage.V3_READBACK_SCHEMA
    )
    assert storage._readback_schema({"schema_version": proof.V7_FLAT_SCHEMA}) == (
        storage.V7_FLAT_READBACK_SCHEMA
    )
    assert storage.V7_FLAT_READBACK_SCHEMA == (
        "ctcc.demo_account_native_clock_readback.v5"
    )
    assert storage.V7_FLAT_READBACK_SCHEMA not in {
        storage.READBACK_SCHEMA,
        storage.V3_READBACK_SCHEMA,
        storage.EXPOSED_V4_READBACK_SCHEMA,
    }
    for version in (
        "ctcc.demo_current_account_capture.v6",
        "ctcc.demo_account_native_clock_proof.v6",
    ):
        with pytest.raises(proof.NativeAccountProofError):
            storage._readback_schema({"schema_version": version})


@pytest.mark.asyncio
async def test_v7_proof_seals_no_clobber_and_replays_from_separate_readback(
    monkeypatch,
):
    raw, files, chain, scope, samples = await companion_fixture(
        monkeypatch, current_v7=True
    )
    directory = MemoryDirectory(dict(files))
    monkeypatch.setattr(storage, "native_stamp", samples)
    monkeypatch.setattr(storage, "_root_identity", lambda root: (11, 22))

    @contextmanager
    def roots(root):
        yield directory

    monkeypatch.setattr(storage, "_root_context", roots)
    readback_pin = storage._seal_companion(directory, raw, chain=chain, scope=scope)
    receipt = json.loads(directory.content["readback.json"])
    assert receipt["schema_version"] == storage.V7_FLAT_READBACK_SCHEMA
    assert receipt["proof_sha256"] == sha(raw)
    assert receipt["execution_authority"] is False
    with pytest.raises(FileExistsError):
        directory.publish("proof.json", b"caller replacement")
    checked, readback_raw = storage.read_native_account_companion(
        Path.cwd(),
        chain=chain,
        scope=scope,
        expected_proof_sha256=sha(raw),
        expected_readback_sha256=readback_pin,
    )
    assert checked.proof_sha256 == sha(raw)
    assert checked.account_complete is checked.execution_authority is False
    assert readback_raw == directory.content["readback.json"]
    with pytest.raises(proof.NativeAccountProofError):
        storage.read_native_account_companion(
            Path.cwd(),
            chain=chain,
            scope=scope,
            expected_proof_sha256=sha(raw),
            expected_readback_sha256="0" * 64,
        )
    assert directory.content["proof.json"] == raw


@pytest.mark.asyncio
async def test_revoked_v6_flat_proof_cannot_publish_any_companion_readback(monkeypatch):
    raw, files, chain, scope, _ = await companion_fixture(
        monkeypatch, current_only=True, current_v7=False, verify_baseline=False
    )
    directory = MemoryDirectory(dict(files))
    with pytest.raises(
        proof.NativeAccountProofError,
        match="native_account_current_sources_incomplete_or_exposed",
    ):
        storage._seal_companion(directory, raw, chain=chain, scope=scope)
    assert set(directory.content) == set(proof.FILES)
    assert "proof.json" not in directory.content
    assert "readback.json" not in directory.content


@pytest.mark.asyncio
async def test_v7_replayed_proof_rejects_expiry_mutation_after_valid_baseline(
    monkeypatch,
):
    raw, files, chain, scope, _ = await companion_fixture(monkeypatch, current_v7=True)
    assert replay(raw, files, chain, scope).proof_sha256 == sha(raw)
    changed = json.loads(raw)
    elapsed_ns = (
        changed["monotonic_deadline_ns"]
        - changed["proof_persist_start"]["monotonic_ns"]
    )
    changed["proof_persist_start"]["monotonic_ns"] += elapsed_ns
    changed["proof_persist_start"]["utc_ns"] += elapsed_ns
    with pytest.raises(
        proof.NativeAccountProofError, match="native_account_original_expiry_invalid"
    ):
        replay(canonical(changed), files, chain, scope)


@pytest.mark.asyncio
async def test_v7_tampered_readback_schema_denies_without_clobbering_proof(monkeypatch):
    raw, files, chain, scope, samples = await companion_fixture(
        monkeypatch, current_v7=True
    )
    directory = MemoryDirectory(dict(files))
    monkeypatch.setattr(storage, "native_stamp", samples)
    monkeypatch.setattr(storage, "_root_identity", lambda _root: (11, 22))

    @contextmanager
    def roots(_root):
        yield directory

    monkeypatch.setattr(storage, "_root_context", roots)
    pin = storage._seal_companion(directory, raw, chain=chain, scope=scope)
    verified, _ = storage.read_native_account_companion(
        Path.cwd(),
        chain=chain,
        scope=scope,
        expected_proof_sha256=sha(raw),
        expected_readback_sha256=pin,
    )
    assert verified.proof_sha256 == sha(raw)
    changed = json.loads(directory.content["readback.json"])
    changed["schema_version"] = storage.V3_READBACK_SCHEMA
    directory.content["readback.json"] = canonical(changed)
    with pytest.raises(proof.NativeAccountProofError):
        storage.read_native_account_companion(
            Path.cwd(),
            chain=chain,
            scope=scope,
            expected_proof_sha256=sha(raw),
            expected_readback_sha256=sha(directory.content["readback.json"]),
        )
    assert directory.content["proof.json"] == raw
    with pytest.raises(FileExistsError):
        directory.publish("proof.json", b"caller replacement")


@pytest.mark.asyncio
async def test_private_v7_clock_schema_consumes_once_under_same_context(monkeypatch):
    owner, handle, invocation, pin, samples = private_context_fixture(monkeypatch)
    boundary._BOUNDARIES[handle] = replace(
        boundary._BOUNDARIES[handle], proof_schema=boundary.V7_FLAT_PROOF_SCHEMA
    )
    assert consume(owner, invocation, pin).receipt_json == (
        b"synthetic context fence only"
    )
    assert len(samples) == 1
    assert_burned(owner, handle, invocation, pin)


@pytest.mark.asyncio
async def test_v7_schema_string_and_forged_boundary_cannot_self_register(monkeypatch):
    samples = []
    monkeypatch.setattr(boundary, "native_stamp", lambda: samples.append(True))
    forged = object.__new__(boundary._AccountClockBoundary)
    with pytest.raises(boundary.AccountClockBoundaryError, match="issuer_unavailable"):
        boundary._consume_boundary(
            forged,
            invocation=object(),
            receipt_sha256=sha(boundary.V7_FLAT_PROOF_SCHEMA.encode()),
        )
    assert samples == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "schema",
    [
        "ctcc.demo_current_account_capture.v6",
        proof.EXPOSED_V4_SCHEMA,
        "ctcc.demo_account_native_clock_proof.v6",
    ],
)
async def test_private_clock_rejects_other_schemas_before_sampling(monkeypatch, schema):
    owner, handle, invocation, pin, samples = private_context_fixture(monkeypatch)
    boundary._BOUNDARIES[handle] = replace(
        boundary._BOUNDARIES[handle], proof_schema=schema
    )
    with pytest.raises(components.PortfolioComponentError, match="native_clock"):
        consume(owner, invocation, pin)
    assert samples == []
    assert_burned(owner, handle, invocation, pin)
