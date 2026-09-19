"""Versioned real computation with synthetic source/account, never a submit permit."""

import json
from decimal import Decimal

import pytest

from app.trade_qualification import submission_intent as intents
from tests.unit.qualification_execution_binding_fixtures import (
    consumed_receipt,
    execution_binding,
    repin_json,
)
from tests.unit.qualification_history_intent_fixtures import history_ledger_fixture


@pytest.fixture(
    scope="module", params=((2, "long"), (2, "short"), (3, "long"), (3, "short"))
)
def chain(request, tmp_path_factory):
    version, direction = request.param
    fixture = history_ledger_fixture(
        tmp_path_factory.mktemp(f"history-v{version}-{direction}"),
        version=version,
        direction=direction,
    )
    consumed = consumed_receipt(fixture)
    binding = execution_binding(fixture)
    return fixture, consumed, binding, version


def test_history_g12_recheck_reservation_and_intent_keep_original_trade(chain):
    fixture, consumed, binding, version = chain
    record = intents.build_submission_intent(
        fixture.request,
        consumed,
        execution_binding=binding,
    )
    replay, receipt = intents.replay_submission_intent(
        record.canonical_json,
        fixture.request,
        expected_sha256=record.sha256,
    )
    assert replay == record and receipt == consumed
    body = json.loads(record.canonical_json)
    policy = json.loads(body["execution_binding"]["original_inputs_json"])["policy"]
    assert policy["contract_version"] == f"ctcc-history-pre-evidence-v{version}"
    order = body["exchange_request"]["body"]
    candidate = fixture.request.origin.candidate
    assert body["candidate_entry"] == str(candidate.candidate_entry)
    assert Decimal(order["px"]) == consumed.coverage.execution.entry
    assert Decimal(order["attachAlgoOrds"][0]["slTriggerPx"]) == candidate.stop_loss
    assert Decimal(order["attachAlgoOrds"][0]["tpTriggerPx"]) == candidate.take_profit
    assert body["origin_sha256"] == fixture.request.origin.evaluation_sha256
    assert order["ordType"] == "fok" and order["tdMode"] == "isolated"
    for key in (
        "account_complete",
        "execution_authority",
        "source_authenticity_verified",
        "order_submitted",
        "order_retry_authority",
    ):
        assert body[key] is False


@pytest.mark.parametrize("path", ("policy", "prefix"))
@pytest.mark.parametrize("marker", (None, "unknown", "ctcc-history-pre-evidence-v1"))
def test_missing_unknown_or_legacy_history_marker_cannot_be_upcast(chain, path, marker):
    fixture, consumed, binding, _ = chain
    doc = json.loads(binding.original_inputs_json)
    node = doc["policy"] if path == "policy" else doc["policy"]["prefix"]
    if marker is None:
        node.pop("contract_version")
    else:
        node["contract_version"] = marker
    raw, _ = repin_json(doc)
    changed = binding.model_copy(update={"original_inputs_json": raw})
    with pytest.raises(ValueError):
        intents.build_submission_intent(
            fixture.request, consumed, execution_binding=changed
        )


def test_relabeling_complete_policy_cannot_replace_original_family(chain):
    fixture, consumed, binding, version = chain
    doc = json.loads(binding.original_inputs_json)
    other = 3 if version == 2 else 2
    doc["policy"]["contract_version"] = f"ctcc-history-pre-evidence-v{other}"
    doc["policy"]["prefix"]["contract_version"] = (
        f"ctcc-history-qualification-prefix-v{other}"
    )
    raw, _ = repin_json(doc)
    with pytest.raises(ValueError):
        intents.build_submission_intent(
            fixture.request,
            consumed,
            execution_binding=binding.model_copy(update={"original_inputs_json": raw}),
        )


@pytest.mark.parametrize("field", ("candidate_entry", "stop_loss", "take_profit"))
def test_rehashed_history_intent_cannot_change_original_geometry(chain, field):
    fixture, consumed, binding, _ = chain
    record = intents.build_submission_intent(
        fixture.request, consumed, execution_binding=binding
    )
    body = json.loads(record.canonical_json)
    body[field] = "1"
    raw, pin = repin_json(body)
    with pytest.raises(ValueError):
        intents.replay_submission_intent(raw, fixture.request, expected_sha256=pin)
