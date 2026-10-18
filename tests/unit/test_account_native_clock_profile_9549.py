"""Synthetic replay of the DENY-only signed 9549 exposed-account contract."""

import json
from contextlib import contextmanager
from pathlib import Path

import pytest

from app.domain.source_primitives import canonical, sha
from app.trade_qualification import account_native_clock as native
from app.trade_qualification import account_native_exposed_observation as exposed
from app.trade_qualification import account_native_proof as proof
from app.trade_qualification import account_native_proof_storage as storage
from app.trade_qualification import account_native_runtime as runtime
from tests.unit.research.test_public_clock_v2 import fixture as clock_fixture
from tests.unit.research.test_public_clock_v2 import host_change, resign
from tests.unit.research.test_public_journal_contracts import MemoryDirectory
from tests.unit.test_account_current_source_v7 import current_plan_v7
from tests.unit.test_account_native_clock import Samples
from tests.unit.test_account_native_exposed_observation import recorded_exposure
from tests.unit.test_account_native_proof import companion_fixture


def _updated_hosts(files):
    changed = dict(files)
    for phase in ("before", "after"):
        name = f"host-{phase}.json"
        host = json.loads(changed[name])
        old = host["observation"]
        updated = clock_fixture(profile_id=proof.public_clock.UPDATED_PROFILE_ID)
        updated["sample"] = old["sample"]
        for kind in ("host_before", "status", "host_after"):
            for edge in ("request_start", "completed"):
                updated["diagnostic"][kind][edge] = old["diagnostic"][kind][edge]
        host["observation"] = resign(updated)
        assert proof.public_clock.validate_os_clock(host["observation"])
        changed[name] = canonical(host)
    return changed


def _proof(raw, files, *, schema, policy):
    value = json.loads(raw)
    value["schema_version"] = schema
    value["policy_sha256"] = policy
    value["clock_file_sha256"] = {name: sha(data) for name, data in files.items()}
    return canonical(value)


@pytest.mark.asyncio
async def test_owned_before_selects_exact_policy_without_rewriting_old_contract(
    monkeypatch,
):
    raw, files, _chain, _scope, _reference, packet = await recorded_exposure(
        monkeypatch
    )
    updated = _updated_hosts(files)
    assert proof.contract_for_exposed_v6_owned_before(
        packet.plan, files["host-before.json"]
    ) == (proof.EXPOSED_V4_SCHEMA, proof.EXPOSED_V4_POLICY_SHA256)
    assert proof.contract_for_exposed_v6_owned_before(
        packet.plan, updated["host-before.json"]
    ) == (proof.EXPOSED_9549_SCHEMA, proof.EXPOSED_9549_POLICY_SHA256)
    assert json.loads(proof.EXPOSED_V4_POLICY_BYTES)["prior_flat_policy_sha256"] == (
        proof.V3_POLICY_SHA256
    )
    updated_policy = json.loads(proof.EXPOSED_9549_POLICY_BYTES)
    assert updated_policy["clock_profile"] == proof.public_clock.UPDATED_PROFILE_ID
    for flag in (
        "flat_start_permission",
        "source_authenticity_verified",
        "historical_hwm_clock_verified",
        "account_complete",
        "execution_authority",
    ):
        assert updated_policy[flag] is False
    assert (
        json.loads(exposed.V3_9549_POLICY_BYTES)["native_companion_policy_sha256"]
        == proof.EXPOSED_9549_POLICY_SHA256
    )
    assert (
        storage._readback_schema({"schema_version": proof.EXPOSED_9549_SCHEMA})
        == storage.EXPOSED_9549_READBACK_SCHEMA
    )
    assert (
        json.loads(exposed.V3_9549_POLICY_BYTES)["native_companion_readback_schema"]
        == storage.EXPOSED_9549_READBACK_SCHEMA
    )
    assert json.loads(raw)["policy_sha256"] == proof.V3_POLICY_SHA256
    assert proof.EXPOSED_V4_POLICY_SHA256 == (
        "af20645d7170285165ff65c5006392a73684889d4446a8f273e471afcde0dd75"
    )
    assert proof.EXPOSED_9549_POLICY_SHA256 == (
        "b4ac37649546f824bcb0850034bb61c79e681518b0a929db1ebe3239e2e280f1"
    )
    assert exposed.V3_9549_POLICY_SHA256 == (
        "729c3ef5c601f7f67d265b9c5340146f695d27746475bfca8d8aa7a920b87fd6"
    )


@pytest.mark.asyncio
async def test_selector_rejects_untrusted_resource_and_v7_scope(monkeypatch):
    _raw, files, _chain, _scope, _reference, packet = await recorded_exposure(
        monkeypatch
    )
    updated = _updated_hosts(files)
    host = json.loads(updated["host-before.json"])
    changed = host["observation"]
    files_list = list(proof.public_clock._UPDATED_FILES)
    files_list[0] = {**files_list[0], "sha256": "a" * 64}
    host_change(changed, "files", files_list)
    host["observation"] = resign(changed)
    with pytest.raises(
        proof.NativeAccountProofError, match="native_account_clock_profile_invalid"
    ):
        proof.contract_for_exposed_v6_owned_before(packet.plan, canonical(host))
    with pytest.raises(
        proof.NativeAccountProofError, match="native_account_exposure_scope_unsupported"
    ):
        proof.contract_for_exposed_v6_owned_before(
            current_plan_v7(), updated["host-before.json"]
        )


@pytest.mark.asyncio
async def test_owned_native_stage_supplies_the_selected_before_observation(monkeypatch):
    _raw, _files, _chain, _scope, _reference, packet = await recorded_exposure(
        monkeypatch
    )
    samples = Samples()
    monkeypatch.setattr(native.clock, "native_stamp", samples)

    def host():
        observed = clock_fixture(profile_id=proof.public_clock.UPDATED_PROFILE_ID)
        sample = samples()
        observed["sample"] = sample
        for kind in ("host_before", "status", "host_after"):
            observed["diagnostic"][kind]["request_start"] = sample
            observed["diagnostic"][kind]["completed"] = sample
        return resign(observed)

    monkeypatch.setattr(native.clock, "native_os_clock", host)
    with native._initial_stage(plan_sha256="a" * 64, scope_sha256="b" * 64) as stage:
        raw = native._host_observation(stage, "before")
        assert native._state(stage)["clock_results"]["before"] == "accepted"
        assert proof.contract_for_exposed_v6_owned_before(packet.plan, raw) == (
            proof.EXPOSED_9549_SCHEMA,
            proof.EXPOSED_9549_POLICY_SHA256,
        )


@pytest.mark.asyncio
async def test_updated_exposure_replays_seals_and_reads_back_without_authority(
    monkeypatch,
):
    raw, updated, chain, scope, _samples = await companion_fixture(
        monkeypatch,
        source_case="fresh_exposed",
        current_only=True,
        current_v7=False,
        exposed_9549=True,
        verify_baseline=False,
    )
    original = json.loads(raw)
    assert original["schema_version"] == proof.EXPOSED_9549_SCHEMA
    assert original["policy_sha256"] == proof.EXPOSED_9549_POLICY_SHA256
    hosts, probes, traces = proof._clocks(
        updated,
        stage=original["stage"],
        scope_sha=original["scope_sha256"],
        expected_profile=proof.public_clock.UPDATED_PROFILE_ID,
    )
    assert len(hosts) == len(probes) == len(traces) == 2
    assert all(
        host["diagnostic"]["profile_id"] == proof.public_clock.UPDATED_PROFILE_ID
        for host in hosts
    )
    new_raw = raw
    replay = proof.replay_native_account_proof(
        new_raw,
        files=updated,
        chain=chain,
        scope=scope,
        expected_proof_sha256=sha(new_raw),
    )
    current_receipt = json.loads(replay.current_source_receipt_json)
    assert current_receipt["blocking_reasons"] == [
        "algo_type_coverage_incomplete",
        "current_exposure_requires_protection_and_local_join",
    ]
    assert current_receipt["observed_flat"] is False
    assert any(current_receipt["inventory_row_counts"].values())
    assert not replay.account_complete and not replay.execution_authority
    assert not hasattr(replay, "snapshot")
    assert json.loads(new_raw)["admission"] == "DENY"

    # A separate root handle reads the immutable proof and readback receipt.
    # This memory fixture exercises storage semantics only, not native issuance.
    directory = MemoryDirectory(dict(updated))
    persist = json.loads(new_raw)["proof_persist_start"]
    completed = {
        "utc_ns": persist["utc_ns"] + 1_000_000,
        "monotonic_ns": persist["monotonic_ns"] + 1_000_000,
    }
    monkeypatch.setattr(storage, "native_stamp", lambda: completed)
    monkeypatch.setattr(storage, "_root_identity", lambda _root: (11, 22))
    handles = []

    @contextmanager
    def roots(root):
        handles.append(root)
        yield directory

    monkeypatch.setattr(storage, "_root_context", roots)
    pin = storage._seal_companion(directory, new_raw, chain=chain, scope=scope)
    readback, receipt_raw = storage.read_native_account_companion(
        Path.cwd(),
        chain=chain,
        scope=scope,
        expected_proof_sha256=sha(new_raw),
        expected_readback_sha256=pin,
    )
    assert len(handles) == 1
    assert directory.content["proof.json"] == new_raw
    assert receipt_raw == directory.content["readback.json"]
    assert json.loads(receipt_raw)["schema_version"] == (
        storage.EXPOSED_9549_READBACK_SCHEMA
    )
    assert readback.proof_sha256 == replay.proof_sha256 == sha(new_raw)
    assert not readback.account_complete and not readback.execution_authority
    assert not runtime._CAPTURES and not runtime._RAW_PACKET_LEASES
    old_raw = _proof(
        raw,
        updated,
        schema=proof.EXPOSED_V4_SCHEMA,
        policy=proof.EXPOSED_V4_POLICY_SHA256,
    )
    with pytest.raises(
        proof.NativeAccountProofError, match="native_account_clock_profile_invalid"
    ):
        proof.replay_native_account_proof(
            old_raw,
            files=updated,
            chain=chain,
            scope=scope,
            expected_proof_sha256=sha(old_raw),
        )


@pytest.mark.asyncio
async def test_updated_exposure_contract_rejects_flat_v6_source(monkeypatch):
    raw, files, chain, scope, _samples = await companion_fixture(
        monkeypatch,
        source_case="fresh_flat",
        current_only=True,
        current_v7=False,
        verify_baseline=False,
    )
    updated = _updated_hosts(files)
    changed = _proof(
        raw,
        updated,
        schema=proof.EXPOSED_9549_SCHEMA,
        policy=proof.EXPOSED_9549_POLICY_SHA256,
    )
    with pytest.raises(
        proof.NativeAccountProofError, match="native_account_exposure_source_missing"
    ):
        proof.replay_native_account_proof(
            changed,
            files=updated,
            chain=chain,
            scope=scope,
            expected_proof_sha256=sha(changed),
        )
    assert not runtime._CAPTURES and not runtime._RAW_PACKET_LEASES


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["mixed", "old_relabelled", "wrong_policy"])
async def test_mixed_or_relabelled_profile_fails_closed(monkeypatch, mutation):
    raw, files, chain, scope, _reference, _packet = await recorded_exposure(monkeypatch)
    updated = _updated_hosts(files)
    if mutation == "mixed":
        updated["host-after.json"] = files["host-after.json"]
    elif mutation == "old_relabelled":
        updated["host-before.json"] = files["host-before.json"]
        updated["host-after.json"] = files["host-after.json"]
    policy = (
        proof.EXPOSED_V4_POLICY_SHA256
        if mutation == "wrong_policy"
        else proof.EXPOSED_9549_POLICY_SHA256
    )
    new_raw = _proof(raw, updated, schema=proof.EXPOSED_9549_SCHEMA, policy=policy)
    expected = (
        "native_account_proof_contract_invalid"
        if mutation == "wrong_policy"
        else "native_account_clock_profile_invalid"
    )
    with pytest.raises(proof.NativeAccountProofError, match=expected):
        proof.replay_native_account_proof(
            new_raw,
            files=updated,
            chain=chain,
            scope=scope,
            expected_proof_sha256=sha(new_raw),
        )
