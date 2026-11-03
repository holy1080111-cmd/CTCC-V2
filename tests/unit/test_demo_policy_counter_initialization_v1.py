"""Declared pure counter DATA and original synthetic measurement values only.

No database, native owner, actual operator approval, reset or account evidence.
"""

import json
from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from app.config.settings import Settings
from app.trade_qualification import account_owned_financial_v1 as financial
from app.trade_qualification import demo_policy_counter_initialization_v1 as counter
from app.trade_qualification.account_ledger_claims_v2 import (
    finite_risk_policy_sha256_v1,
)
from app.trade_qualification.demo_control import (
    ControlScope,
    DemoControlError,
    canonical,
    digest,
)
from tests.unit import test_account_original_balance_measurement_v1 as original_values


async def _declared_ack(monkeypatch):
    _, _, original = await original_values.raw_measurement(monkeypatch, "V5")
    genesis_json = original.decode("ascii")
    genesis = financial.measurement._read(original)
    at = datetime.fromisoformat(genesis["body_completed_at"])
    selected = financial._configured_policy_from_settings_v1(
        Settings(_env_file=None),
        drawdown_window_started_at=at,
    )
    policy_json = selected.selected_settings_json.decode("ascii")
    expiry = at + timedelta(seconds=20)
    operator = canonical(
        {
            "schema_version": counter.COUNTER_OPERATOR_SCHEMA_V1,
            "purpose": "policy_counter_initialize",
            "scope": ["demo", "1234", "USDT"],
            "command_id": "1" * 64,
            "control_revision": 2,
            "control_event_sha256": "2" * 64,
            "source_lineage_sha256": "4" * 64,
            "configured_policy_sha256": digest(policy_json),
            "measured_genesis_sha256": digest(genesis_json),
            "original_expires_at": expiry.isoformat(),
            "confirmation": counter.COUNTER_CONFIRMATION_V1,
        }
    )
    scope = ControlScope(genesis["environment"], genesis["account_id"])
    # The existing fixture's exact UID is declared before capture. Do not
    # substitute a hardcoded different account into the operator document.
    doc = json.loads(operator)
    doc["scope"] = [scope.environment, scope.account_id, "USDT"]
    operator = canonical(doc)
    hashes = {
        "configured_policy": digest(policy_json),
        "measured_genesis": digest(genesis_json),
        "operator_ack": digest(operator),
    }
    return counter.PolicyCounterInitializationAckV1(
        scope,
        "USDT",
        "1" * 64,
        2,
        "2" * 64,
        "3" * 64,
        "4" * 64,
        0,
        0,
        None,
        policy_json,
        genesis_json,
        at + timedelta(seconds=1),
        expiry,
        100,
        200,
        "300",
        tuple(
            (name, hashes.get(name, "5" * 64))
            for name in counter.COUNTER_PROOF_NAMES_V1
        ),
        operator,
    )


@pytest.mark.asyncio
async def test_counter_ack_preserves_full_policy_and_original_measured_genesis(
    monkeypatch,
):
    ack = await _declared_ack(monkeypatch)
    raw = canonical(counter.policy_counter_ack_document_v1(ack))
    parsed = counter.decode_policy_counter_ack_v1(
        raw,
        configured_policy_json=ack.configured_policy_json,
        measured_genesis_json=ack.measured_genesis_json,
    )
    assert parsed == ack
    assert "initialized_at" not in counter.policy_counter_ack_document_v1(ack)
    assert parsed.execution_authority is False
    # Explicit synthetic transport: these values never become native source.
    at = ack.owner_sampled_at + timedelta(milliseconds=1)
    readback = counter.PolicyCounterInitializationReadbackV1(ack, at, at, b"{}", (), ())
    assert (
        readback.source_authenticity_verified is readback.execution_authority is False
    )


@pytest.mark.asyncio
async def test_counter_policy_cannot_move_drawdown_start_to_initialization(monkeypatch):
    ack = await _declared_ack(monkeypatch)
    policy = counter._plain_canonical(
        ack.configured_policy_json, counter.MAX_CONFIGURED_POLICY_BYTES_V1
    )
    changed = financial.portfolio.PortfolioRiskPolicy.model_validate_json(
        canonical(policy["policy"]), strict=True
    ).model_copy(update={"drawdown_window_started_at": ack.owner_sampled_at})
    policy["policy"] = changed.model_dump(mode="json")
    policy["portfolio_risk_policy_sha256"] = finite_risk_policy_sha256_v1(changed)
    # Coherently update the policy's own digest so this reaches the original
    # measurement/window comparison, rather than failing an earlier hash.
    with pytest.raises(DemoControlError, match="genesis_invalid"):
        counter._policy_and_genesis(
            canonical(policy),
            ack.measured_genesis_json,
            scope=ack.scope,
            settlement_currency=ack.settlement_currency,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("control_revision", True),
        ("account_revision", True),
        ("claims_sha256", "1" * 64),
        ("settlement_currency", "BTC"),
        ("db_transaction_id", "01"),
        ("proofs", ()),
        ("operator_ack_json", "{}"),
    ],
)
async def test_counter_exact_data_never_defaults_missing_or_wrong_type_to_zero(
    monkeypatch, field, value
):
    ack = await _declared_ack(monkeypatch)
    with pytest.raises(DemoControlError):
        replace(ack, **{field: value})


@pytest.mark.asyncio
async def test_actual_initialization_time_must_be_after_sample_and_before_original_expiry(
    monkeypatch,
):
    ack = await _declared_ack(monkeypatch)
    for initialized, recorded in (
        (ack.owner_sampled_at - timedelta(microseconds=1), ack.owner_sampled_at),
        (ack.original_expires_at, ack.original_expires_at),
        (ack.owner_sampled_at, ack.owner_sampled_at + timedelta(microseconds=1)),
    ):
        with pytest.raises(DemoControlError):
            counter.PolicyCounterInitializationReadbackV1(
                ack, initialized, recorded, b"{}", (), ()
            )


@pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity"])
def test_counter_canonical_policy_envelope_rejects_nonfinite_json(token):
    with pytest.raises(DemoControlError):
        counter._plain_canonical('{"unused":' + token + "}", 1024)


@pytest.mark.asyncio
async def test_counter_full_policy_JSON_mode_roundtrip_does_not_relax_python_types(
    monkeypatch,
):
    """Actual producer JSON wire values require JSON mode, not coercive Python."""
    from pydantic import ValidationError

    ack = await _declared_ack(monkeypatch)
    doc = json.loads(ack.configured_policy_json)
    assert type(doc["policy"]["risk_per_trade_pct"]) is str
    assert type(doc["policy"]["drawdown_window_started_at"]) is str
    with pytest.raises(ValidationError):
        financial.portfolio.PortfolioRiskPolicy.model_validate(
            doc["policy"], strict=True
        )
    policy, genesis = counter._policy_and_genesis(
        ack.configured_policy_json,
        ack.measured_genesis_json,
        scope=ack.scope,
        settlement_currency=ack.settlement_currency,
    )
    assert canonical(policy.model_dump(mode="json")) == canonical(doc["policy"])
    assert finite_risk_policy_sha256_v1(policy) == doc["portfolio_risk_policy_sha256"]
    assert (
        datetime.fromisoformat(genesis["body_completed_at"])
        == policy.drawdown_window_started_at
    )
    assert ack.execution_authority is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("max_leverage", True),
        ("max_order_contracts", None),
        ("risk_per_trade_pct", "NaN"),
        ("drawdown_window_started_at", None),
    ],
)
async def test_counter_JSON_mode_still_denies_wrong_type_missing_and_nonfinite_policy(
    monkeypatch, field, value
):
    ack = await _declared_ack(monkeypatch)
    doc = json.loads(ack.configured_policy_json)
    doc["policy"][field] = value
    with pytest.raises(DemoControlError):
        counter._policy_and_genesis(
            canonical(doc),
            ack.measured_genesis_json,
            scope=ack.scope,
            settlement_currency=ack.settlement_currency,
        )
