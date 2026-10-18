"""Synthetic B1/B3/B5 join tests; no database, native clock or account authority."""

import json
from dataclasses import replace

import pytest

from app.database.repositories.account_measured_hwm_v2 import (
    AccountMeasuredHWMError,
    AccountMeasuredHWMRepository,
    MeasuredHWMPrefixPreflight,
    MeasuredHWMReadback,
)
from app.database.repositories.account_observation_index import (
    AccountObservationIndexRepository,
    BalanceSourcePage,
)
from app.trade_qualification import account_capture_journal as journal
from app.trade_qualification import account_measured_hwm_continuation as hwm
from app.trade_qualification import account_observation_index as observed
from app.trade_qualification import account_portfolio_components as components
from app.trade_qualification import account_portfolio_runtime as runtime
from tests.unit.test_account_portfolio_runtime import prepare


def bridge(monkeypatch):
    session, harness, checkpoints, args, events, reads, appended = prepare(monkeypatch)
    args["hwm_repository"] = AccountMeasuredHWMRepository(None, clock=harness.clock)
    calls = []
    result = None
    original_b3_append = AccountObservationIndexRepository.append

    async def preflight(self, scope):
        calls.append("preflight")
        return MeasuredHWMPrefixPreflight(0, None, None)

    async def guarded_b3_append(
        self, scope, reference, window, *, expected_revision, require_hwm_prefix=False
    ):
        calls.append("b3_append")
        assert require_hwm_prefix is True
        return await original_b3_append(
            self, scope, reference, window, expected_revision=expected_revision
        )

    async def append(self, scope, *, expected_sequence, expected_head_sha256):
        nonlocal result
        calls.append("append")
        assert expected_sequence == 1 and expected_head_sha256 == "a" * 64
        reference = observed.source_reference(tuple(events))
        measurement = components.observe_balance(
            tuple(events), reference=reference, scope=scope
        )
        projection = hwm.advance_measured_hwm(
            scope,
            sequence=expected_sequence,
            previous_event_sha256=None,
            event_sha256=expected_head_sha256,
            measurement=measurement,
            expected_reference=reference,
        )
        result = MeasuredHWMReadback(
            projection.receipt_json,
            projection.receipt_sha256,
            expected_sequence,
            expected_head_sha256,
        )
        return result

    async def readback(self, scope, *, expected_sequence, expected_head_sha256):
        calls.append("readback")
        assert expected_sequence == 1 and expected_head_sha256 == "a" * 64
        return result

    monkeypatch.setattr(AccountMeasuredHWMRepository, "append_next", append)
    monkeypatch.setattr(AccountMeasuredHWMRepository, "readback", readback)
    monkeypatch.setattr(AccountMeasuredHWMRepository, "preflight_prefix", preflight)
    monkeypatch.setattr(AccountObservationIndexRepository, "append", guarded_b3_append)
    return session, harness, checkpoints, args, events, reads, appended, calls


@pytest.mark.asyncio
async def test_same_invocation_db_hwm_join_is_only_a_diagnostic(monkeypatch):
    session, harness, _, args, events, _, appended, calls = bridge(monkeypatch)
    result = await runtime.collect_owned_portfolio_components_v3(session, **args)
    data = json.loads(result.receipt_json)
    assert calls == ["preflight", "b3_append", "append", "readback"]
    assert len(appended) == 1
    assert data["schema_version"] == "ctcc.portfolio_components_diagnostic.v3"
    assert data["policy_sha256"] == runtime.DIAGNOSTIC_V3_POLICY_SHA256
    durable = data["durable_measured_hwm"]
    assert durable["through_sequence"] == 1
    assert durable["head_sha256"] == "a" * 64
    assert durable["db_readback_matched"] is True
    assert durable["native_clock_verified"] is False
    assert durable["account_complete"] is False
    assert durable["execution_authority"] is False
    assert result.owner is result.snapshot is data["snapshot"] is None
    assert data["account_complete"] is data["execution_authority"] is False
    assert data["admission"] == "DENY"
    assert events and all(request.method == "GET" for request in harness.requests)
    harness.assert_closed()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["peak", "scope", "native", "readback_head"])
async def test_v3_rejects_v1_v2_or_separate_readback_conflict(monkeypatch, change):
    session, harness, _, args, _, _, appended, calls = bridge(monkeypatch)
    original_append = AccountMeasuredHWMRepository.append_next
    original_readback = AccountMeasuredHWMRepository.readback
    if change != "readback_head":
        altered = None

        async def altered_append(self, scope, **kwargs):
            nonlocal altered
            receipt = await original_append(self, scope, **kwargs)
            projection = json.loads(receipt.projection_json)
            if change == "peak":
                projection["peak_equity"]["numerator"] = "1001"
            elif change == "scope":
                projection["account_id"] = "999999"
            else:
                projection["all_sources_recorded_native"] = True
            raw = journal.canonical(projection)
            altered = replace(
                receipt, projection_json=raw, projection_sha256=journal.digest(raw)
            )
            return altered

        async def matching_readback(self, scope, **kwargs):
            await original_readback(self, scope, **kwargs)
            return altered

        monkeypatch.setattr(AccountMeasuredHWMRepository, "append_next", altered_append)
        monkeypatch.setattr(AccountMeasuredHWMRepository, "readback", matching_readback)
    else:

        async def altered_readback(self, scope, **kwargs):
            receipt = await original_readback(self, scope, **kwargs)
            return replace(receipt, head_sha256="b" * 64)

        monkeypatch.setattr(AccountMeasuredHWMRepository, "readback", altered_readback)
    with pytest.raises(
        components.PortfolioComponentError,
        match="owned_portfolio_component_capture_failed",
    ):
        await runtime.collect_owned_portfolio_components_v3(session, **args)
    assert calls == ["preflight", "b3_append", "append", "readback"]
    assert len(appended) == 1
    assert all(request.method == "GET" for request in harness.requests)
    harness.assert_closed()


@pytest.mark.asyncio
async def test_v3_late_hwm_readback_failure_retains_earlier_source(monkeypatch):
    session, harness, _, args, events, _, appended, calls = bridge(monkeypatch)

    async def fail_readback(self, scope, **kwargs):
        calls.append("readback")
        raise RuntimeError("synthetic DB readback failure")

    monkeypatch.setattr(AccountMeasuredHWMRepository, "readback", fail_readback)
    with pytest.raises(
        components.PortfolioComponentError,
        match="owned_portfolio_component_capture_failed",
    ):
        await runtime.collect_owned_portfolio_components_v3(session, **args)
    assert calls == ["preflight", "b3_append", "append", "readback"]
    assert len(appended) == 1 and events
    assert all(request.method == "GET" for request in harness.requests)
    harness.assert_closed()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        "v2_policy",
        "foreign_repository",
        "foreign_session_factory",
        "foreign_inner_session_factory",
    ],
)
async def test_v3_rejects_wrong_policy_or_repository_before_io(monkeypatch, change):
    session, harness, _, args, events, _, appended, calls = bridge(monkeypatch)
    if change == "v2_policy":
        args["expected_policy_sha256"] = runtime.DIAGNOSTIC_POLICY_SHA256
    elif change == "foreign_repository":
        args["hwm_repository"] = object()
    elif change == "foreign_session_factory":
        args["hwm_repository"].session_factory = object()
    else:
        args["hwm_repository"].journal.session_factory = object()
    with pytest.raises(components.PortfolioComponentError):
        await runtime.collect_owned_portfolio_components_v3(session, **args)
    assert not harness.requests and not events and not appended and not calls


@pytest.mark.asyncio
async def test_existing_b3_only_scope_denies_before_any_account_get_or_new_b3(
    monkeypatch,
):
    session, harness, _, args, events, _, appended, calls = bridge(monkeypatch)

    async def missing_prefix(self, scope):
        calls.append("preflight")
        raise AccountMeasuredHWMError("account_measured_hwm_prefix_preflight_denied")

    monkeypatch.setattr(
        AccountMeasuredHWMRepository, "preflight_prefix", missing_prefix
    )
    with pytest.raises(
        components.PortfolioComponentError,
        match="owned_portfolio_component_capture_failed",
    ):
        await runtime.collect_owned_portfolio_components_v3(session, **args)
    assert calls == ["preflight"]
    assert not harness.requests and not events and not appended


@pytest.mark.asyncio
async def test_complete_two_sample_prefix_can_continue_to_third_without_reseed(
    monkeypatch,
):
    session, harness, _, args, events, _, appended, calls = bridge(monkeypatch)
    args["expected_revision"] = 2
    initial_b3_append = AccountObservationIndexRepository.append
    projected = None

    async def complete_prefix(self, scope):
        calls.append("preflight")
        return MeasuredHWMPrefixPreflight(2, "b" * 64, "d" * 64)

    async def guarded_third(
        self, scope, reference, window, *, expected_revision, require_hwm_prefix=False
    ):
        assert expected_revision == 2 and require_hwm_prefix is True
        first = await initial_b3_append(
            self,
            scope,
            reference,
            window,
            expected_revision=0,
            require_hwm_prefix=True,
        )
        return replace(first, sequence=3, event_sha256="c" * 64)

    async def three_samples(
        self, scope, *, through_sequence, expected_head_sha256, after=0
    ):
        assert (through_sequence, expected_head_sha256, after) == (3, "c" * 64, 0)
        reference = observed.source_reference(tuple(events))
        measured = components.observe_balance(
            tuple(events), reference=reference, scope=scope
        )
        return BalanceSourcePage(
            3,
            "c" * 64,
            0,
            (
                (1, None, "a" * 64, measured),
                (2, "a" * 64, "b" * 64, measured),
                (3, "b" * 64, "c" * 64, measured),
            ),
        )

    async def append_third(self, scope, *, expected_sequence, expected_head_sha256):
        nonlocal projected
        calls.append("append")
        assert (expected_sequence, expected_head_sha256) == (3, "c" * 64)
        reference = observed.source_reference(tuple(events))
        measured = components.observe_balance(
            tuple(events), reference=reference, scope=scope
        )
        predecessor = None
        for sequence, head, previous in (
            (1, "a" * 64, None),
            (2, "b" * 64, "a" * 64),
            (3, "c" * 64, "b" * 64),
        ):
            predecessor = hwm.advance_measured_hwm(
                scope,
                sequence=sequence,
                previous_event_sha256=previous,
                event_sha256=head,
                measurement=measured,
                expected_reference=reference,
                predecessor=predecessor,
                expected_predecessor_sha256=None
                if predecessor is None
                else predecessor.receipt_sha256,
                genesis_witness=None if predecessor is None else measured,
                selected_peak_witness=None if predecessor is None else measured,
            )
        projected = MeasuredHWMReadback(
            predecessor.receipt_json, predecessor.receipt_sha256, 3, "c" * 64
        )
        return projected

    async def read_third(self, scope, *, expected_sequence, expected_head_sha256):
        calls.append("readback")
        assert (expected_sequence, expected_head_sha256) == (3, "c" * 64)
        return projected

    monkeypatch.setattr(
        AccountMeasuredHWMRepository, "preflight_prefix", complete_prefix
    )
    monkeypatch.setattr(AccountObservationIndexRepository, "append", guarded_third)
    monkeypatch.setattr(
        AccountObservationIndexRepository, "read_balance_source_page", three_samples
    )
    monkeypatch.setattr(AccountMeasuredHWMRepository, "append_next", append_third)
    monkeypatch.setattr(AccountMeasuredHWMRepository, "readback", read_third)
    result = await runtime.collect_owned_portfolio_components_v3(session, **args)
    data = json.loads(result.receipt_json)
    assert calls == ["preflight", "b3_append", "append", "readback"]
    assert len(appended) == 1
    assert data["durable_measured_hwm"]["through_sequence"] == 3
    assert data["measured_hwm"]["sample_count"] == 3
    assert data["admission"] == "DENY" and result.owner is None
    assert not data["account_complete"] and not result.execution_authority
    harness.assert_closed()
