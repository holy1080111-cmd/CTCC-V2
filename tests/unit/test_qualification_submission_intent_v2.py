"""Explicit FOK body journals from replayed synthetic inputs; no order permit."""

import json
from decimal import Decimal

import pytest

from app.trade_qualification.submission_intent import (
    QualificationLedgerError,
    build_submission_intent,
    replay_submission_intent,
)
from tests.unit.qualification_execution_binding_fixtures import (
    consumed_receipt,
    execution_binding,
    repin_json,
)
from tests.unit.qualification_ledger_fixtures import ledger_fixture


@pytest.fixture(scope="module", params=("long", "short"))
def v2(request):
    fixture = ledger_fixture(request.param)
    return fixture, consumed_receipt(fixture), execution_binding(fixture)


@pytest.fixture(scope="module")
def v2_record(v2):
    fixture, consumed, binding = v2
    return build_submission_intent(fixture.request, consumed, execution_binding=binding)


def test_exact_fok_body_replay_and_legacy_record_remain_separate(v2):
    fixture, consumed, binding = v2
    old = build_submission_intent(fixture.request, consumed)
    result = build_submission_intent(
        fixture.request, consumed, execution_binding=binding
    )
    body = json.loads(result.canonical_json)
    order = body["exchange_request"]["body"]
    assert body["version"] == "ctcc-demo-submit-intent-v2"
    assert json.loads(old.canonical_json)["version"] == "ctcc-demo-submit-intent-v1"
    assert order["instId"] == consumed.instrument_id
    assert order["tdMode"] == "isolated" and order["posSide"] == "net"
    assert order["ordType"] == "fok"
    assert order["side"] == ("buy" if consumed.direction == "long" else "sell")
    assert Decimal(order["px"]) == consumed.coverage.execution.entry
    assert Decimal(order["sz"]) == consumed.coverage.candidate.contracts
    protection = order["attachAlgoOrds"][0]
    assert (
        Decimal(protection["slTriggerPx"]) == fixture.request.origin.candidate.stop_loss
    )
    assert (
        Decimal(protection["tpTriggerPx"])
        == fixture.request.origin.candidate.take_profit
    )
    assert protection["tpTriggerPxType"] == protection["slTriggerPxType"] == "mark"
    for record in (old, result):
        replayed, _ = replay_submission_intent(
            record.canonical_json, fixture.request, expected_sha256=record.sha256
        )
        assert replayed == record and not replayed.execution_authority
        assert not replayed.order_retry_authority
    assert old == build_submission_intent(fixture.request, consumed)
    assert body["all_fill_prices_covered"] is False
    assert body["source_authenticity_verified"] is False
    assert body["account_complete"] is False


@pytest.mark.parametrize(
    "field", ("px", "sz", "side", "posSide", "tdMode", "clOrdId", "ordType")
)
def test_rehashed_exchange_body_substitution_rejected(v2, v2_record, field):
    fixture, _, _ = v2
    result = v2_record
    body = json.loads(result.canonical_json)
    body["exchange_request"]["body"][field] = "altered"
    _, body["exchange_request_sha256"] = repin_json(body["exchange_request"])
    raw, digest = repin_json(body)
    with pytest.raises(QualificationLedgerError, match="submit_intent_record_invalid"):
        replay_submission_intent(raw, fixture.request, expected_sha256=digest)


def test_missing_binding_never_creates_v2_and_hash_cannot_promote_v1(v2):
    fixture, consumed, _ = v2
    body = json.loads(build_submission_intent(fixture.request, consumed).canonical_json)
    assert "exchange_request" not in body
    body["version"] = "ctcc-demo-submit-intent-v2"
    raw, digest = repin_json(body)
    with pytest.raises(QualificationLedgerError):
        replay_submission_intent(raw, fixture.request, expected_sha256=digest)


def test_hedged_position_side_comes_from_replayed_configuration(v2):
    fixture, consumed, _ = v2
    binding = execution_binding(fixture, position_mode="long_short_mode")
    result = build_submission_intent(
        fixture.request, consumed, execution_binding=binding
    )
    body = json.loads(result.canonical_json)
    assert body["position_mode"] == "long_short_mode"
    assert body["exchange_request"]["body"]["posSide"] == consumed.direction


@pytest.mark.parametrize(
    "stream,field,value",
    (
        ("account_instruments", "tickSz", "0.02"),
        ("leverage_isolated", "lever", "125"),
        ("leverage_isolated", "posSide", "short"),
    ),
)
def test_replayed_account_metadata_must_match_binding(v2, stream, field, value):
    fixture, consumed, _ = v2

    def change(rows):
        rows[stream][0][field] = value

    binding = execution_binding(fixture, raw_changes=change)
    with pytest.raises(
        QualificationLedgerError, match="submit_execution_binding_invalid"
    ):
        build_submission_intent(fixture.request, consumed, execution_binding=binding)


@pytest.mark.parametrize(
    "field",
    ("quote_json", "current_market_json", "original_inputs_json", "recheck_json"),
)
def test_replay_sources_cannot_be_replaced_by_pass(v2, field):
    fixture, consumed, binding = v2
    changed = binding.model_copy(update={field: '{"passed":true}'})
    with pytest.raises(
        QualificationLedgerError, match="submit_execution_binding_invalid"
    ):
        build_submission_intent(fixture.request, consumed, execution_binding=changed)


@pytest.mark.parametrize(
    "field", ("slTriggerPx", "tpTriggerPx", "slTriggerPxType", "tpTriggerPxType")
)
def test_protection_body_is_exact_and_cannot_be_rehashed(v2, v2_record, field):
    fixture, _, _ = v2
    body = json.loads(v2_record.canonical_json)
    body["exchange_request"]["body"]["attachAlgoOrds"][0][field] = "changed"
    _, body["exchange_request_sha256"] = repin_json(body["exchange_request"])
    raw, digest = repin_json(body)
    with pytest.raises(QualificationLedgerError, match="submit_intent_record_invalid"):
        replay_submission_intent(raw, fixture.request, expected_sha256=digest)


def test_new_account_revision_requires_new_bound_recheck(v2):
    fixture, consumed, binding = v2
    changed = consumed.model_copy(
        update={"account_revision": consumed.account_revision + 1}
    )
    with pytest.raises(
        QualificationLedgerError, match="submit_execution_binding_invalid"
    ):
        build_submission_intent(fixture.request, changed, execution_binding=binding)


def test_invalid_binding_field_returns_fixed_code_without_echoing_input(v2):
    fixture, consumed, binding = v2
    changed = binding.model_copy(
        update={"account_plan_sha256": "synthetic-private-value"}
    )
    with pytest.raises(
        QualificationLedgerError, match="^submit_execution_binding_invalid$"
    ) as caught:
        build_submission_intent(fixture.request, consumed, execution_binding=changed)
    assert "synthetic-private-value" not in str(caught.value)


def test_foreign_binding_cannot_execute_callbacks(v2):
    fixture, consumed, _ = v2
    calls = []

    class Foreign:
        @property
        def __class__(self):
            calls.append("class")
            raise AssertionError("foreign class callback")

        def __str__(self):
            calls.append("str")
            raise AssertionError("foreign string callback")

    with pytest.raises(QualificationLedgerError, match="submit_intent_input_invalid"):
        build_submission_intent(fixture.request, consumed, execution_binding=Foreign())
    assert calls == []
