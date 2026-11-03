"""Fresh declared plan+credential sessions; source arithmetic, not native credit.

Every binding/response is chosen before its original B1 acquisition. No old
chain is relabelled. An outer AllProduct/synthetic-wire construction context is
preserved by the helper, so V3 stream tests can reuse the same fresh sources.
"""

import copy
import json
from contextlib import contextmanager
from dataclasses import replace
from datetime import timedelta

import pytest

from app.trade_qualification import account_capture_journal as journal
from app.trade_qualification import account_financial_operands_v2 as legacy_positions
from app.trade_qualification import (
    account_financial_position_session_v3 as position_sessions,
)
from app.trade_qualification import account_history_global_financial_v1 as legacy_core
from app.trade_qualification import account_history_global_financial_v2 as financial
from app.trade_qualification import account_lifecycle_history as lifecycle
from app.trade_qualification import account_observation_index as observed
from app.trade_qualification.account_runtime import AccountRuntimeError
from tests.unit import (
    test_account_history_global_financial_v1 as original_batches_fixture,
)
from tests.unit import test_account_lifecycle_history as original
from tests.unit.test_account_financial_operands_v2 import native_position_source
from tests.unit.test_qualification_account_capture import NOW

SCOPE = original.SCOPE
CHECKED = NOW + timedelta(seconds=2010)


@contextmanager
def declared_capture_session(monkeypatch, binding):
    """Matching fresh plan AND credentials, before capture; preserve outer plan factory."""
    real_plan, real_credentials = original.regional, original.credentials

    def declared_plan(**values):
        return real_plan(**{**values, "session_binding_id": binding})

    def declared_credentials(**values):
        return real_credentials(**{**values, "session_binding_id": binding})

    with monkeypatch.context() as current:
        current.setattr(original, "regional", declared_plan)
        current.setattr(original, "credentials", declared_credentials)
        yield
    assert original.regional is real_plan and original.credentials is real_credentials


async def acquire_distinct_session_sources(
    monkeypatch, bindings=("declared-lease-A", "declared-lease-B", "declared-lease-C")
):
    assert len(bindings) >= 3 and len(set(bindings)) == len(bindings)
    sources = []
    for index, binding in enumerate(bindings):
        first, last = index == 0, index == len(bindings) - 1
        at = NOW if first else NOW + timedelta(seconds=1800 if last else 600 + index)
        with declared_capture_session(monkeypatch, binding):
            chain = await original.original_capture(
                monkeypatch,
                at,
                base=NOW,
                filled=not first,
                case="A01" if first or last else "A03",
            )
        sources.append((chain, observed.source_reference(chain)))
    sources = tuple(sources)
    windows = tuple(
        observed.ExecutionWindow(
            packet.plan.history_start, packet.plan.history_end - timedelta(seconds=300)
        )
        for chain, reference in sources
        for packet in (lifecycle._packet(chain, reference),)
    )
    batches = original_batches_fixture.original_batches(sources, windows)
    return sources, batches


def accounts_from_sources(sources):
    return position_sessions.original_account_observations_v3(
        [lifecycle._packet(chain, reference) for chain, reference in sources],
        [reference for _, reference in sources],
        SCOPE,
    )


def consume(sources, batches):
    accumulator = financial.OriginalHistoryFinancialAccumulatorV2(scope=SCOPE)
    for (chain, reference), batch in zip(sources, batches, strict=True):
        accumulator.consume_current(
            chain=chain,
            reference=reference,
            batch=batch,
            witnesses=original_batches_fixture.original_witnesses(sources, batch),
        )
    return accumulator


def finish(accumulator, sources, batches, *, archive=None):
    return json.loads(
        accumulator.finish(
            expected_through=batches[-1].sequence,
            expected_head=batches[-1].event_sha256,
            checked_at=CHECKED,
            position_source=native_position_source(sources[-1][1])
            if archive is None
            else archive,
        ).receipt_json
    )


@pytest.mark.asyncio
async def test_three_fresh_original_leases_join_only_by_stable_account_identity(
    monkeypatch,
):
    sources, batches = await acquire_distinct_session_sources(monkeypatch)
    pins = [reference.session_binding_sha256 for _, reference in sources]
    assert len(set(pins)) == 3
    raw_pins = tuple(financial.stream._chain_measure(chain)[1] for chain, _ in sources)
    data = finish(consume(sources, batches), sources, batches)
    assert data["schema_version"] == "ctcc.global_original_financial_facts.v2"
    assert [
        item["session_binding_sha256"] for item in data["original_capture_sessions"]
    ] == pins
    assert data["closed_runs"][0]["reconciled_closed_run_net_pnl"] == {
        "numerator": "7",
        "denominator": "20",
    }
    assert (
        tuple(financial.stream._chain_measure(chain)[1] for chain, _ in sources)
        == raw_pins
    )
    assert (
        data["account_complete"]
        is data["execution_authority"]
        is data["source_authenticity_verified"]
        is False
    )
    assert (
        data["loss_streak_seed"]
        is data["historical_native_hwm"]
        is data["current_history_tail_finality"]
        is None
    )


@pytest.mark.asyncio
async def test_rolling33_distinct_original_sessions_keep_all_source_bindings(
    monkeypatch,
):
    sources, batches = await acquire_distinct_session_sources(
        monkeypatch,
        tuple("declared-distinct-lease-" + str(number) for number in range(33)),
    )
    assert len({reference.session_binding_sha256 for _, reference in sources}) == 33
    data = finish(consume(sources, batches), sources, batches)
    assert (
        len(data["original_current_sources"])
        == len(data["original_capture_sessions"])
        == 33
    )
    assert [item["reference"] for item in data["original_current_sources"]] == [
        observed.reference_document(reference) for _, reference in sources
    ]
    assert [item["window"] for item in data["original_current_sources"]] == [
        json.loads(batch.document_json)["window"] for batch in batches
    ]
    assert (
        data["aggregate_generation_window"]
        == json.loads(batches[-1].document_json)["window"]
    )
    assert data["closed_runs"][0]["reconciled_closed_run_net_pnl"] == {
        "numerator": "7",
        "denominator": "20",
    }
    assert (
        data["original_stream_prefix_verified"]
        is data["account_complete"]
        is data["execution_authority"]
        is False
    )


@pytest.mark.asyncio
async def test_position_archive_from_earlier_real_session_cannot_replace_current(
    monkeypatch,
):
    sources, batches = await acquire_distinct_session_sources(monkeypatch)
    with pytest.raises(financial.GlobalFinancialHistoryError):
        finish(
            consume(sources, batches),
            sources,
            batches,
            archive=native_position_source(sources[0][1]),
        )


@pytest.mark.asyncio
async def test_legacy_core_and_legacy_position_public_contract_still_reject_distinct_sessions(
    monkeypatch,
):
    sources, batches = await acquire_distinct_session_sources(monkeypatch)
    accumulator = legacy_core.OriginalHistoryFinancialAccumulatorV1(scope=SCOPE)
    accumulator.consume_current(
        chain=sources[0][0], reference=sources[0][1], batch=batches[0]
    )
    with pytest.raises(
        legacy_core.GlobalFinancialHistoryError,
        match="^global_financial_account_session_or_mode_changed$",
    ):
        accumulator.consume_current(
            chain=sources[1][0],
            reference=sources[1][1],
            batch=batches[1],
            witnesses=original_batches_fixture.original_witnesses(sources, batches[1]),
        )
    packets = [lifecycle._packet(chain, reference) for chain, reference in sources]
    legacy_accounts = legacy_positions._native_account_observations(
        packets, [reference for _, reference in sources]
    )
    with pytest.raises(
        legacy_positions.AccountFinancialOperandsError,
        match="^account_financial_original_position_session_changed$",
    ):
        legacy_positions._native_position_rows_from_accounts(
            native_position_source(sources[-1][1]), legacy_accounts, SCOPE, CHECKED
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "damage",
    [
        "uid",
        "main_uid",
        "level",
        "mode",
        "origin",
        "region",
        "environment",
        "currency",
        "session_shape",
        "settle_currency",
    ],
)
async def test_historical_identity_environment_currency_and_session_shape_remain_strict(
    monkeypatch, damage
):
    sources, _ = await acquire_distinct_session_sources(monkeypatch)
    accounts = copy.deepcopy(accounts_from_sources(sources))
    if damage == "uid":
        accounts[0]["config"]["uid"] = "999999"
    elif damage == "main_uid":
        accounts[0]["config"]["mainUid"] = "999999"
    elif damage == "level":
        accounts[0]["config"]["acctLv"] = "3"
    elif damage == "mode":
        accounts[0]["config"]["posMode"] = "long_short_mode"
    elif damage == "origin":
        accounts[0]["origin"] = "https://us.okx.com"
    elif damage == "region":
        accounts[0]["registration_region"] = "us_au"
    elif damage == "environment":
        accounts[0]["environment"] = "live"
    elif damage == "currency":
        accounts[0]["settlement_currency"] = "USDC"
    elif damage == "settle_currency":
        accounts[0]["config"]["settleCcy"] = "USDC"
    else:
        accounts[0]["session_binding_sha256"] = True
    with pytest.raises(position_sessions.OriginalPrefixPositionSessionError):
        position_sessions.native_position_rows_for_original_prefix_v3(
            native_position_source(sources[-1][1]), accounts, SCOPE, CHECKED
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["session", "proof_head", "packet"])
async def test_rehashed_reference_cannot_replace_original_session_or_proof(
    monkeypatch, damage
):
    sources, batches = await acquire_distinct_session_sources(monkeypatch)
    reference = sources[0][1]
    field = {
        "session": "session_binding_sha256",
        "proof_head": "head_sha256",
        "packet": "packet_sha256",
    }[damage]
    changed = replace(reference, **{field: "f" * 64})
    document = json.loads(batches[0].document_json)
    document["reference"] = observed.reference_document(changed)
    raw = journal.canonical(document)
    candidate = replace(batches[0], document_json=raw, event_sha256=journal.digest(raw))
    original_pin = financial.stream._chain_measure(sources[0][0])[1]
    accumulator = financial.OriginalHistoryFinancialAccumulatorV2(scope=SCOPE)
    with pytest.raises(financial.GlobalFinancialHistoryError):
        accumulator.consume_current(
            chain=sources[0][0], reference=changed, batch=candidate
        )
    assert accumulator._state is None
    assert financial.stream._chain_measure(sources[0][0])[1] == original_pin


@pytest.mark.asyncio
async def test_matching_plan_only_is_not_enough_without_fresh_matching_credentials(
    monkeypatch,
):
    real_credentials = original.credentials
    with (
        declared_capture_session(monkeypatch, "declared-plan-lease"),
        monkeypatch.context() as wrong,
    ):
        wrong.setattr(
            original,
            "credentials",
            lambda **values: real_credentials(
                **{
                    **values,
                    "session_binding_id": "different-declared-credential-lease",
                }
            ),
        )
        with pytest.raises(AccountRuntimeError, match="^credential_session_mismatch$"):
            await original.original_capture(
                monkeypatch, NOW, base=NOW, filled=False, case="A01"
            )
