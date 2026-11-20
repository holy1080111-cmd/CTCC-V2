"""Live-only DATA contracts and failures, with no venue calls or database."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext

import pytest
from pydantic import ValidationError

from app.database.repositories import native_live_ledger_v1 as repository
from app.domain import native_live_intent_v1 as intent
from app.domain import native_live_ledger_schema_v1 as schema
from app.domain.source_primitives import ClockStamp, canonical, sha
from app.okx_live import trusted_source_bridge_v1 as bridge

NOW = datetime(2026, 11, 20, tzinfo=UTC)


def _value(**changes):
    deadline = changes.get("deadline", NOW + timedelta(seconds=20))
    candidate = canonical(
        {
            "environment": "live",
            "instrument_id": "ETH-USDT-SWAP",
            "direction": "long",
            "entry": "100",
            "stop_loss": "99",
            "take_profit": "103",
            "event_key": "f" * 64,
            "policy_sha256": "d" * 64,
            "expires_at": deadline.isoformat(),
        }
    )
    values = {
        "account_uid": "730058",
        "main_uid": "730058",
        "credential_session_sha256": "a" * 64,
        "source_revision_sha256": "b" * 64,
        "process_sha256": "c" * 64,
        "policy_sha256": "d" * 64,
        "arm_id": "e" * 64,
        "expected_control_revision": 7,
        "event_key": "f" * 64,
        "candidate_sha256": sha(candidate),
        "original_candidate_json": candidate.decode("ascii"),
        "report_sha256": "1" * 64,
        "evidence_sha256": "2" * 64,
        "recheck_sha256": "3" * 64,
        "instrument_id": "ETH-USDT-SWAP",
        "direction": "long",
        "entry": Decimal(100),
        "stop_loss": Decimal(99),
        "take_profit": Decimal(103),
        "executable_price_bound": Decimal("100.1"),
        "contracts": Decimal(1),
        "leverage": 1,
        "position_side": "net",
        "client_order_id": "CTCCLsynthetic",
        "protection_client_id": "CTCCAsynthetic",
        "risk_samples": (Decimal("0.1"), Decimal("0.2")),
        "margin_amount": Decimal(1),
        "notional_amount": Decimal(10),
        "deadline": NOW + timedelta(seconds=20),
    }
    values.update(changes)
    return intent.LiveIntentDataV1(**values)


def _control(value):
    return {
        "environment": "live",
        "account_uid": value.account_uid,
        "main_uid": value.main_uid,
        "credential_session_sha256": value.credential_session_sha256,
        "source_revision_sha256": value.source_revision_sha256,
        "policy_sha256": value.policy_sha256,
        "process_sha256": value.process_sha256,
        "revision": value.expected_control_revision,
        "armed": True,
        "emergency_stop": False,
        "arm_id": value.arm_id,
        "updated_at": NOW - timedelta(seconds=1),
        "arm_expires_at": NOW + timedelta(seconds=25),
        "lease_until": NOW + timedelta(seconds=30),
        "per_trade_risk_cap": Decimal("0.5"),
        "portfolio_risk_cap": Decimal(1),
        "margin_cap": Decimal(5),
        "micro_notional_cap": Decimal(20),
    }


def _plan(**changes):
    values = {
        "invocation_id": "a" * 32,
        "region": "global",
        "history_start": NOW - timedelta(days=7),
        "history_end": NOW,
        "publication_barrier": ClockStamp.model_validate(
            {"utc_ns": 1_795_132_800_000_000_000, "monotonic_ns": 100}
        ),
    }
    values.update(changes)
    return bridge.LiveSourcePlanV1(**values)


def test_exact_intent_retains_original_prices_and_fixed_fok_payload_without_authority():
    value = _value()
    before = value.model_dump(mode="python")
    rid, raw, digest = intent.exact_live_intent_document_v1(value)
    from app.domain.source_primitives import decode

    document = decode(raw, maximum=65536)
    request = decode(document["request_json"].encode("ascii"), maximum=8192)
    assert digest == sha(raw) and rid == document["reservation_id"]
    assert document["risk_amount"] == "0.2"
    assert (
        document["entry"] == "100"
        and document["stop_loss"] == "99"
        and document["take_profit"] == "103"
    )
    assert request["ordType"] == "fok" and request["tdMode"] == "isolated"
    assert request["attachAlgoOrds"][0]["slTriggerPxType"] == "mark"
    assert (
        document["qualification_verified"] is document["execution_authority"] is False
    )
    assert value.model_dump(mode="python") == before


@pytest.mark.parametrize(
    "changes",
    (
        {"environment": "demo"},
        {"leverage": 20},
        {"leverage": True},
        {"margin_mode": "cross"},
        {"contracts": Decimal("NaN")},
        {"stop_loss": Decimal(98)},
        {"candidate_sha256": "0" * 64},
        {"deadline": NOW.replace(tzinfo=None)},
        {"risk_samples": ()},
        {"risk_samples": (Decimal(0),)},
        {"position_side": "short"},
        {"execution_authority": True},
    ),
)
def test_live_intent_refuses_environment_drift_optimization_unknowns_or_fake_authority(
    changes,
):
    with pytest.raises((ValidationError, ValueError)):
        _value(**changes)


def test_same_event_in_different_report_still_has_the_same_reservation_identity():
    first = intent.exact_live_intent_document_v1(_value())
    second = intent.exact_live_intent_document_v1(_value(report_sha256="4" * 64))
    assert first[0] == second[0] and first[2] != second[2]
    assert intent.live_account_lock_key_v1("730058") != intent.live_account_lock_key_v1(
        "730059"
    )


def test_live_hold_caps_reserve_worst_sample_using_exact_arithmetic():
    value = _value()
    row = _control(value)
    assert repository._caps(
        row, value, (Decimal("0.8"), Decimal(1), Decimal(1))
    ) == Decimal("0.2")
    with localcontext() as context:
        context.prec = 2
        row["portfolio_risk_cap"] = Decimal("1.00000000000000000001")
        with pytest.raises(repository.LiveLedgerDataError):
            repository._caps(
                row, value, (Decimal("0.80000000000000000002"), Decimal(1), Decimal(1))
            )


def test_shared_hard_caps_cannot_be_loosened_by_a_control_policy():
    from types import SimpleNamespace

    row = _control(_value())
    view = {
        "current_readback": SimpleNamespace(
            usdt_available_equity=SimpleNamespace(available_equity=Decimal(100))
        )
    }
    repository._hard_caps(row, view)
    row["per_trade_risk_cap"] = Decimal("0.50000000000000000001")
    with pytest.raises(repository.LiveLedgerDataError):
        repository._hard_caps(row, view)


def test_original_expiry_can_shorten_but_cannot_be_extended_by_data_copy():
    value = _value()
    intent.checked_live_intent_data_v1(
        value.model_copy(update={"deadline": value.deadline - timedelta(seconds=1)})
    )
    with pytest.raises(ValidationError):
        intent.checked_live_intent_data_v1(
            value.model_copy(update={"deadline": value.deadline + timedelta(seconds=1)})
        )


def test_owned_raw_bank_digest_binds_selector_headers_bytes_and_original_chain():
    from dataclasses import replace

    raw = canonical({"code": "0", "data": []})
    headers = ((b"content-type", b"application/json"),)
    stamp = _plan().publication_barrier
    page = bridge.LiveSourcePageDataV1(
        bridge.current.select_live_account_raw_page_v1("positions"),
        raw,
        headers,
        (stamp, stamp, stamp),
        sha(raw),
        sha(raw),
        bridge._header_digest(headers),
        "a" * 64,
        "b" * 64,
        "https://openapi.okx.com",
        "c" * 64,
        "2026-11-20T00:00:00.000Z",
        "d" * 64,
        "e" * 64,
    )
    original = bridge._bank_sha((page,))
    assert (
        bridge._bank_sha(
            (
                replace(
                    page,
                    selector=bridge.current.select_live_account_raw_page_v1("balance"),
                ),
            )
        )
        != original
    )
    with pytest.raises(bridge.LiveSourceBridgeError):
        bridge._bank_sha((replace(page, raw_body=b'{"code":"0","data":[{}]}'),))


@pytest.mark.parametrize(
    "active",
    (
        (None, Decimal(0), Decimal(0)),
        (Decimal(-1), Decimal(0), Decimal(0)),
        (Decimal("NaN"), Decimal(0), Decimal(0)),
        (Decimal(1), Decimal(0), Decimal(0)),
        (Decimal(0), Decimal(5), Decimal(0)),
        (Decimal(0), Decimal(0), Decimal(20)),
    ),
)
def test_unknown_negative_or_exceeded_existing_holds_cannot_be_treated_as_zero(active):
    with pytest.raises((repository.LiveLedgerDataError, AttributeError)):
        repository._caps(_control(_value()), _value(), active)


@pytest.mark.parametrize(
    "field,changed",
    (
        ("armed", False),
        ("emergency_stop", True),
        ("revision", 8),
        ("arm_id", "0" * 64),
        ("account_uid", "730059"),
        ("main_uid", "730059"),
        ("source_revision_sha256", "0" * 64),
        ("credential_session_sha256", "0" * 64),
        ("policy_sha256", "0" * 64),
        ("process_sha256", "0" * 64),
        ("arm_expires_at", NOW),
        ("lease_until", NOW),
        ("portfolio_risk_cap", None),
    ),
)
def test_final_locked_control_refuses_stop_expiry_identity_and_revision_drift(
    field, changed
):
    value = _value()
    row = _control(value)
    row[field] = changed
    with pytest.raises(repository.LiveLedgerDataError):
        repository._control(row, value, NOW)


def test_source_plan_enumerates_every_existing_history_product_bill_and_algo_partition():
    selectors = bridge._selectors(_plan())
    assert len(selectors) == 50
    assert len({(item.path, item.query) for item in selectors}) == 50
    assert all(item.after is None for item in selectors)
    assert {item.inst_type for item in selectors if item.kind == "fills_recent"} == set(
        bridge.HISTORY_INST_TYPES
    )
    assert {item.kind for item in selectors} == {
        *bridge._FAMILIES,
        "bills_recent",
        "bills_archive",
        "algo_history",
    }
    assert all(
        item.cursor_type == "billId"
        for item in selectors
        if item.kind.startswith("fills_")
    )


@pytest.mark.parametrize(
    "changes",
    (
        {"region": "us"},
        {"region": "demo"},
        {"invocation_id": "bad"},
        {"history_start": NOW - timedelta(days=6)},
        {"history_start": NOW - timedelta(days=29)},
        {"history_end": NOW.replace(tzinfo=None)},
        {"max_current_age_ns": True},
        {"max_current_age_ns": 5_000_000_001},
        {"publication_barrier": False},
    ),
)
def test_source_plan_refuses_scope_clock_window_and_tolerance_drift(changes):
    with pytest.raises(bridge.LiveSourceBridgeError):
        bridge._selectors(_plan(**changes))


def test_source_uses_integer_utc_conversion_without_rounding_future_time():
    value = NOW.replace(microsecond=999999)
    assert bridge._utc_ns(value) % 1_000_000_000 == 999_999_000


@pytest.mark.asyncio
async def test_constructor_saved_receipt_or_foreign_task_cannot_create_owned_source():
    with pytest.raises(bridge.LiveSourceBridgeError):
        bridge.OwnedLiveSourceV1()
    forged = object.__new__(bridge.OwnedLiveSourceV1)
    with pytest.raises(bridge.LiveSourceBridgeError):
        bridge._replay_owned_live_source_v1(forged, key=bridge._SOURCE_KEY)
    bridge._SOURCES[forged] = {"task": object()}
    try:
        with pytest.raises(bridge.LiveSourceBridgeError):
            bridge._replay_owned_live_source_v1(forged, key=bridge._SOURCE_KEY)
    finally:
        bridge._SOURCES.pop(forged, None)
    assert forged.execution_authority is False


def test_schema_is_additive_live_only_append_only_and_dedicated_writer_has_no_arm_or_delete():
    grants = schema.live_ledger_grant_sql_v1("ctcc_live_ledger")
    assert len(grants) == 9 and all("demo" not in sql for sql in grants)
    assert all(
        "UPDATE" not in sql and "DELETE" not in sql and "TRUNCATE" not in sql
        for sql in grants
    )
    assert sum("GRANT INSERT" in sql for sql in grants) == 3
    combined = "\n".join(schema.UPGRADE_SQL)
    assert "DROP " not in combined and "ALTER TABLE" not in combined
    assert "UNIQUE(environment,account_uid,event_key)" in combined
    assert "native_live_reservation_immutable" in combined
    assert "native_live_transition_chain_denied" in combined
    assert schema.SPEC_SHA256 == sha(
        canonical({"schema": schema.SCHEMA, "head": "0058", "sql": schema.UPGRADE_SQL})
    )


@pytest.mark.parametrize("writer", ("ctcc; GRANT ALL", "Demo Writer", 'x"', "", None))
def test_grant_renderer_refuses_unreviewed_identifiers(writer):
    with pytest.raises(ValueError):
        schema.live_ledger_grant_sql_v1(writer)


def test_control_and_cap_checks_do_not_mutate_loss_or_stop_state():
    value = _value()
    row = _control(value)
    before = deepcopy(row)
    repository._control(row, value, NOW)
    repository._caps(row, value, (Decimal(0), Decimal(0), Decimal(0)))
    assert row == before


@pytest.mark.asyncio
async def test_source_binding_requires_original_live_owner_not_account_uid_in_a_payload():
    value = _value()
    with pytest.raises(bridge.LiveSourceBridgeError):
        repository._source(value, object())
    with pytest.raises(bridge.LiveSourceBridgeError):
        await bridge.refresh_owned_live_source_v1(
            object(),
            publication_barrier=_plan().publication_barrier,
            invocation_id="b" * 32,
        )


def test_data_repository_has_no_order_arm_or_release_entrypoint():
    names = set(repository.NativeLiveLedgerDataRepositoryV1.__dict__)
    assert (
        "reserve_and_commit_intent_data_v1" in names and "read_intent_data_v1" in names
    )
    assert (
        not {"arm", "place_order", "submit", "release", "resolve", "clear_stop"} & names
    )
    from app.database.models import native_live_ledger_v1 as mappings

    assert mappings.NativeLiveControlV1.__table__.c.environment.type.length == 4
    assert {
        item.__tablename__
        for item in (
            mappings.NativeLiveControlV1,
            mappings.NativeLiveReservationV1,
            mappings.NativeLiveIntentV1,
            mappings.NativeLiveTransitionV1,
        )
    } == set(schema.TABLES)
