"""Original-raw replay/phase mutations over synthetic providers, no acceptance."""

import asyncio
import json
from datetime import datetime, timedelta

import httpx
import pytest

from app.domain.source_primitives import sha
from app.trade_qualification import account_history_native_stage_v1 as phases
from app.trade_qualification import account_history_source_bundle_v1 as bundle
from app.trade_qualification import account_history_source_v1 as source
from app.trade_qualification import account_native_clock as native
from app.trade_qualification import account_positions_history_collector_v1 as positions
from tests.unit.research.test_public_journal_contracts import MemoryDirectory
from tests.unit.test_account_history_source_v1 import acquisition_fixture
from tests.unit.test_account_native_clock import Samples
from tests.unit.test_account_positions_history_collector_v1 import (
    NOW,
    NOW_MS,
    config,
    position,
)


def test_versioned_decoder_accepts_real_scale_native_ns_and_large_hex():
    value = {
        "utc_ns": 1_800_000_000_000_000_000,
        "monotonic_ns": 123_456_789_012_345,
        "raw_hex": "ab" * 300000,
        "complete": False,
    }
    raw = bundle.journal.canonical(value)
    assert bundle._json(raw) == value
    assert bundle._encoded_size(value) == len(raw)


@pytest.mark.parametrize("setting", [None, "USDC"])
@pytest.mark.parametrize("currency", [None, "USDT", "USDC"])
def test_native_archive_binds_four_source_keys_and_keeps_currency_semantics_separate(
    setting, currency
):
    identity = positions._NativePositionIdentityV1("123456", "123456", "2", "net_mode")
    plan = positions.history.PositionHistoryPlanV1(
        "global",
        "https://openapi.okx.com",
        identity.sha256,
        "a" * 64,
        NOW,
        NOW_MS - 10000,
    )
    config_row = config()
    if setting is None:
        del config_row["settleCcy"]
    else:
        config_row["settleCcy"] = setting
    row = position()
    if currency is not None:
        row["ccy"] = currency
    raw_config = positions._canonical({"code": "0", "msg": "", "data": [config_row]})

    def raw_page(body, offset):
        return positions._RawPage(
            body,
            NOW + timedelta(milliseconds=offset),
            NOW + timedelta(milliseconds=offset + 1),
            NOW + timedelta(milliseconds=offset + 2),
            "c" * 64,
        )

    before, after = raw_page(raw_config, 1), raw_page(raw_config, 10)
    first = positions.history.parse_page(
        plan,
        positions._canonical({"code": "0", "msg": "", "data": [row]}),
        page_index=0,
        after=None,
        previous_page_sha256=None,
        request_started_at=NOW + timedelta(milliseconds=4),
        headers_received_at=NOW + timedelta(milliseconds=5),
        body_completed_at=NOW + timedelta(milliseconds=6),
    )
    terminal = positions.history.parse_page(
        plan,
        b'{"code":"0","data":[],"msg":""}',
        page_index=1,
        after=first.next_after,
        previous_page_sha256=first.page_sha256,
        request_started_at=NOW + timedelta(milliseconds=7),
        headers_received_at=NOW + timedelta(milliseconds=8),
        body_completed_at=NOW + timedelta(milliseconds=9),
    )
    archive = positions._freeze_native_source_archive(
        plan, identity, before, (first, terminal), after, ("c" * 64, "c" * 64)
    )
    result = positions._verify_native_source_archive(
        plan, identity, archive, expected_sha256=sha(archive)
    )
    assert result.declared_scope_currency == "USDT"
    assert result.account_usd_contract_settle_currency == setting
    assert result.observed_margin_currencies == (currency,)
    assert not result.account_complete and not result.execution_authority
    assert json.loads(archive)["version"] == positions.NATIVE_POSITION_VERSION
    with pytest.raises(positions.OwnedPositionHistoryError):
        positions.verify_source_bound_archive(
            plan, identity, archive, expected_sha256=sha(archive)
        )


@pytest.mark.parametrize(
    "value",
    [
        1.0,
        float("inf"),
        float("nan"),
        {"stamp": bundle.MAX_TIMESTAMP_NS + 1},
        [[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[0]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]],
    ],
)
def test_versioned_decoder_keeps_finite_integer_and_depth_bounds(value):
    raw = json.dumps(value, separators=(",", ":")).encode()
    with pytest.raises((bundle.HistorySourceBundleError, ValueError)):
        bundle._json(raw)


@pytest.mark.asyncio
async def test_global_hex_budget_rejects_before_any_chain_hex_allocation(
    monkeypatch, tmp_path
):
    with acquisition_fixture(monkeypatch, tmp_path) as value:
        owner = await source.acquire_native_history_source_facts_v1(**value.args)
        facts = await source.consume_owned_native_history_source_facts_v1(owner)
        called = []
        monkeypatch.setattr(bundle, "chain_document", lambda _: called.append(True))
        monkeypatch.setattr(bundle, "MAX_BUNDLE_BYTES", 128)
        data = json.loads(facts.bundle_raw)
        plan = positions.history.PositionHistoryPlanV1(
            **{
                **data["position_plan"],
                "created_at": datetime.fromisoformat(
                    data["position_plan"]["created_at"]
                ),
            }
        )
        identity = positions._NativePositionIdentityV1(
            **{
                key: item
                for key, item in data["position_identity"].items()
                if key != "declared_scope_currency"
            }
        )
        with pytest.raises(bundle.HistorySourceBundleError):
            bundle.freeze_history_source_bundle_v1(
                scope=facts.scope,
                sources=facts.sources,
                batches=facts.batches,
                position_plan=plan,
                identity=identity,
                position_archive=facts.positions,
                temporal=data["temporal"],
            )
        assert called == []


@pytest.mark.asyncio
async def test_exact_saved_replay_yields_only_non_authoritative_source_facts(
    monkeypatch, tmp_path
):
    with acquisition_fixture(monkeypatch, tmp_path) as value:
        owner = await source.acquire_native_history_source_facts_v1(**value.args)
        facts = await source.consume_owned_native_history_source_facts_v1(owner)
        replayed = bundle.replay_history_source_bundle_v1(
            facts.bundle_raw, expected_sha256=facts.bundle_sha256
        )
        assert replayed.packet == facts.packet
        assert replayed.sources == facts.sources
        assert replayed.positions == facts.positions
        assert (
            replayed.account_complete
            is replayed.native_history_prefix_verified
            is False
        )
        assert replayed.execution_authority is replayed.predictive_oos_eligible is False
        with pytest.raises(source.NativeHistorySourceError):
            source.consume_owned_native_history_source_facts_v1(replayed)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation",
    [
        "old_schema",
        "old_policy",
        "funding",
        "authority_alias",
        "missing_prefix",
        "missing_phase",
        "native_ordinal_alias",
        "tls",
        "chunk",
        "cutoff",
        "clock",
        "position_phase",
        "recorded_at",
        "original_anchor",
    ],
)
async def test_rehashed_envelope_cannot_hide_source_or_native_binding_failure(
    monkeypatch, tmp_path, mutation
):
    with acquisition_fixture(monkeypatch, tmp_path) as value:
        owner = await source.acquire_native_history_source_facts_v1(**value.args)
        facts = await source.consume_owned_native_history_source_facts_v1(owner)
        data = json.loads(facts.bundle_raw)
        if mutation == "old_schema":
            data["temporal"]["schema_version"] = (
                "ctcc.demo_account_native_clock_proof.v5"
            )
        elif mutation == "old_policy":
            data["policy_sha256"] = "f" * 64
        elif mutation == "funding":
            data["funding_source_status"] = "COMPLETE"
        elif mutation == "authority_alias":
            data["account_complete"] = 0
        elif mutation == "missing_prefix":
            data["sources"] = []
        elif mutation == "missing_phase":
            data["temporal"]["witnesses"].pop(1)
        elif mutation == "native_ordinal_alias":
            data["temporal"]["witnesses"][0]["index"] = False
        elif mutation == "tls":
            data["temporal"]["positions_trace"]["pages"][0]["certificate_sha256"] = None
        elif mutation == "chunk":
            event = next(
                v
                for v in data["temporal"]["positions_trace"]["events"]
                if v["phase"] == "body_chunk"
            )
            event["prefix_sha256"] = "f" * 64
        elif mutation == "cutoff":
            data["position_plan"]["cutoff_u_time_ms"] += 1
        elif mutation == "clock":
            data["temporal"]["clock_files_hex"]["exchange-after.raw"] = b"{}".hex()
        elif mutation == "position_phase":
            data["temporal"]["positions_trace"]["events"][2]["phase"] = "tls_verified"
        elif mutation == "recorded_at":
            data["sources"][0][-1]["recorded_at"] = "1970-01-01T00:00:00+00:00"
        elif mutation == "original_anchor":
            data["temporal"]["original_started"]["monotonic_ns"] -= 61_000_000_000
        raw = bundle.journal.canonical(data)
        with pytest.raises(bundle.HistorySourceBundleError):
            bundle.replay_history_source_bundle_v1(raw, expected_sha256=sha(raw))


@pytest.mark.parametrize("raw", [b'{"a":0,"a":1}', b"{} ", b"[]", b"null"])
def test_duplicate_noncanonical_and_foreign_json_never_mint(raw):
    with pytest.raises((bundle.HistorySourceBundleError, ValueError)):
        bundle.replay_history_source_bundle_v1(raw, expected_sha256=sha(raw))


@pytest.mark.asyncio
async def test_diagnostic_mock_transport_does_not_become_native_transport(monkeypatch):
    samples = Samples()
    monkeypatch.setattr(native.clock, "native_stamp", samples)
    with native._initial_stage(plan_sha256="a" * 64, scope_sha256="b" * 64) as stage:
        state = native._state(stage)
        state["closed"] = True
        state["clock_results"] = {"before": "accepted"}
        with phases._positions_observer(
            stage, directory=MemoryDirectory({}), tokens=[]
        ) as observer:
            client = httpx.AsyncClient(
                transport=httpx.MockTransport(lambda _: httpx.Response(200)),
                trust_env=False,
                follow_redirects=False,
            )
            try:
                with pytest.raises(
                    phases.HistoryNativeStageError, match="native_tls_required"
                ):
                    phases._transport(observer, client)
            finally:
                await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "defect",
    [
        "secret",
        "escaped_secret",
        "malformed_partial",
        "nested_partial",
        "wire_depth",
        "phase_budget",
        "total_bytes",
        "empty_chunks",
        "cancellation",
    ],
)
async def test_private_phase_failure_retains_fixed_safe_evidence_without_success(
    monkeypatch, defect
):
    samples = Samples()
    monkeypatch.setattr(native.clock, "native_stamp", samples)
    content = {}
    with native._initial_stage(plan_sha256="a" * 64, scope_sha256="b" * 64) as stage:
        state = native._state(stage)
        state["closed"] = True
        state["clock_results"] = {"before": "accepted"}
        with phases._positions_observer(
            stage,
            directory=MemoryDirectory(content),
            tokens=["synthetic-sensitive-token"],
        ) as observer:
            spec = positions._GetSpec(
                "https://openapi.okx.com", positions.CONFIG_ENDPOINT, ()
            )
            tokens = phases._known_tokens(observer)
            with pytest.raises(
                phases.HistoryNativeStageError, match="secret_inventory_changed"
            ):
                phases._begin(observer, spec, 65536, list(tokens))
            phases._begin(observer, spec, 65536, tokens)
            phases._phase(observer, "request_dispatch")
            phases._phase(observer, "headers_received")
            phases._phase(observer, "tls_verified", certificate="c" * 64)
            if defect in {
                "secret",
                "escaped_secret",
                "malformed_partial",
                "nested_partial",
                "wire_depth",
            }:
                raw = {
                    "secret": b'{"value":"synthetic-sensitive-token"}',
                    "escaped_secret": b'{"value":"synthetic-sensitive-\\u0074oken"}',
                    "malformed_partial": b'{"value":',
                    "nested_partial": b"[" * 2000 + b"0" + b"]" * 2000,
                    "wire_depth": b"[" * 14 + b'"x"' + b"]" * 14,
                }[defect]
                phases._chunk(observer, raw)
            elif defect == "phase_budget":
                monkeypatch.setattr(phases, "MAX_POSITION_EVENTS", 4)
                with pytest.raises(phases.HistoryNativeStageError):
                    phases._chunk(observer, b"{}")
            elif defect == "total_bytes":
                monkeypatch.setattr(phases, "MAX_POSITION_RAW_BYTES", 1)
                with pytest.raises(phases.HistoryNativeStageError):
                    phases._chunk(observer, b"{}")
            elif defect == "empty_chunks":
                for _ in range(8):
                    phases._chunk(observer, b"")
                with pytest.raises(phases.HistoryNativeStageError):
                    phases._chunk(observer, b"")
            elif defect == "cancellation":
                task = asyncio.current_task()
                task.cancel()
                try:
                    with pytest.raises(asyncio.CancelledError):
                        await asyncio.sleep(0)
                    phases._response_closed(observer)
                    phases._retain(observer, failed=True)
                    phases._abort(observer)
                finally:
                    task.uncancel()
                assert "positions-failure.json" in content
                return
            phases._response_closed(observer)
            phases._retain(observer, failed=True)
            phases._abort(observer)
            marker = json.loads(content["positions-00.json"])
            assert marker["complete"] is False
            if defect in {
                "secret",
                "escaped_secret",
                "malformed_partial",
                "nested_partial",
                "wire_depth",
            }:
                assert "positions-00.raw" not in content
                assert marker["retention"].startswith("withheld_")
                assert marker["observed_bytes"] == len(raw)
                assert marker["raw_sha256"] == phases.sha(raw)
                if defect in {"nested_partial", "wire_depth"}:
                    assert marker["retention"] == "withheld_unverifiable_partial"
            assert (
                json.loads(content["positions-failure.json"])["execution_authority"]
                is False
            )
            assert all(
                b"synthetic-sensitive-token" not in raw for raw in content.values()
            )
