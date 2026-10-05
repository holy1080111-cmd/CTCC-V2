"""Diagnostic event identity/time contracts; no DB or issuer substitute."""

import json
from datetime import timedelta

import pytest

from app.database.repositories.qualification_ledger import QualificationLedgerRepository
from app.trade_qualification.event_observation import VERSION, LedgerEventObservation
from app.trade_qualification.reservations import (
    LedgerScope,
    QualificationLedgerError,
    checked,
    reservation_id,
)
from tests.unit.qualification_execution_binding_fixtures import consumed_receipt
from tests.unit.qualification_ledger_fixtures import ledger_fixture


@pytest.fixture(scope="module")
def document():
    fixture = ledger_fixture()
    receipt = consumed_receipt(fixture)
    return {
        "contract_version": VERSION,
        "scope": fixture.request.scope.model_dump(mode="python"),
        "original_event_key": receipt.original_event_key,
        "account_revision": receipt.account_revision,
        "ledger_revision": receipt.ledger_revision,
        "matched": receipt.model_dump(mode="python"),
        "request_started_at": fixture.now,
        "observed_at": fixture.now,
        "received_at": fixture.now + timedelta(microseconds=1),
        "monotonic_started_ns": 10,
        "monotonic_received_ns": 20,
    }


def test_observation_is_bounded_canonical_and_never_registered_as_ledger_authority(
    document,
):
    value = LedgerEventObservation.model_validate(document, strict=True)
    raw = value.canonical_json
    assert len(raw.encode()) <= 65536 and value.sha256 == value.sha256
    assert json.loads(raw)["contract_version"] == VERSION
    assert value.admission == "DENY"
    assert not value.execution_authority and not value.order_retry_authority
    assert not value.source_authenticity_verified
    with pytest.raises(QualificationLedgerError):
        checked(value, LedgerEventObservation)


@pytest.mark.parametrize(
    "state", ["reserved", "consumed", "uncertain", "reconciled_flat"]
)
def test_all_states_including_expired_terminal_tombstone_remain_visible(
    document, state
):
    raw = dict(document)
    raw["matched"] = {**raw["matched"], "state": state}
    # Lookup does not erase an event merely because its qualification expired.
    for name in ("request_started_at", "observed_at", "received_at"):
        raw[name] += timedelta(days=30)
    result = LedgerEventObservation.model_validate(raw, strict=True)
    assert result.matched.state == state and result.admission == "DENY"


@pytest.mark.parametrize(
    "mutation",
    [
        "version",
        "event",
        "uid",
        "rid",
        "revision",
        "requested_revision",
        "monotonic_order",
        "monotonic_bool",
        "monotonic_negative",
        "utc_order",
        "naive_time",
        "match_future",
        "retry_int",
        "authority",
        "extra",
    ],
)
def test_exact_versions_scopes_revisions_and_measured_bounds_are_required(
    document, mutation
):
    raw = {**document, "matched": dict(document["matched"])}
    if mutation == "version":
        raw["contract_version"] = "ctcc-ledger-event-observation-v0"
    elif mutation == "event":
        raw["original_event_key"] = "e" * 64
    elif mutation == "uid":
        raw["scope"] = {**raw["scope"], "account_id": "999999"}
    elif mutation == "rid":
        raw["matched"]["reservation_id"] = "e" * 64
    elif mutation == "revision":
        raw["matched"]["ledger_revision"] += 1
    elif mutation == "requested_revision":
        raw["account_revision"] = raw["ledger_revision"] + 1
    elif mutation == "monotonic_order":
        raw["monotonic_received_ns"] = 9
    elif mutation == "monotonic_bool":
        raw["monotonic_started_ns"] = True
    elif mutation == "monotonic_negative":
        raw["monotonic_started_ns"] = -1
    elif mutation == "utc_order":
        raw["request_started_at"] = raw["received_at"] + timedelta(seconds=1)
    elif mutation == "naive_time":
        raw["observed_at"] = raw["observed_at"].replace(tzinfo=None)
    elif mutation == "match_future":
        raw["matched"]["updated_at"] = raw["received_at"] + timedelta(seconds=1)
    elif mutation == "retry_int":
        raw["order_retry_authority"] = 0
    elif mutation == "authority":
        raw["execution_authority"] = True
    else:
        raw["passed"] = True
    with pytest.raises(ValueError):
        LedgerEventObservation.model_validate(raw, strict=True)


def test_other_currency_receipt_retains_its_own_revisions_without_becoming_absence(
    document,
):
    raw = {**document, "scope": {**document["scope"], "settlement_currency": "USDC"}}
    raw["account_revision"] = raw["ledger_revision"] = 0
    value = LedgerEventObservation.model_validate(raw, strict=True)
    assert value.scope.settlement_currency == "USDC"
    assert value.account_revision == value.ledger_revision == 0
    assert value.matched.scope.settlement_currency == "USDT"
    assert value.matched.account_revision > 0
    assert value.matched.reservation_id == reservation_id(
        value.matched.scope, value.original_event_key
    )


def test_absence_is_explicit_and_cannot_upgrade_or_hide_unknown_local_scope(document):
    raw = {**document, "matched": None, "account_revision": 0, "ledger_revision": 0}
    value = LedgerEventObservation.model_validate(raw, strict=True)
    assert value.matched is None and value.admission == "DENY"
    assert not value.account_evidence_authenticated
    with pytest.raises(ValueError):
        LedgerEventObservation.model_validate(
            {k: v for k, v in raw.items() if k != "matched"}, strict=True
        )
    mutated = value.model_copy(update={"execution_authority": True})
    with pytest.raises(QualificationLedgerError, match="event_observation_invalid"):
        _ = mutated.canonical_json


@pytest.mark.asyncio
@pytest.mark.parametrize("key", [None, True, "", "a" * 63, "A" * 64, "a" * 65])
async def test_invalid_key_denies_before_opening_a_session(key):
    def forbidden_session():
        raise AssertionError("must reject before DB IO")

    repo = QualificationLedgerRepository(forbidden_session, clock=lambda: None)
    with pytest.raises(QualificationLedgerError, match="invalid_ledger_event_key"):
        await repo.read_event_observation(
            LedgerScope(account_id="123", settlement_currency="USDT"), key
        )
