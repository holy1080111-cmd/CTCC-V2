"""Synthetic foundation checks; no installed key, venue or lifecycle authority."""

import hashlib
import hmac

import pytest

from app.database.repositories import native_live_lifecycle_v1 as repository
from app.domain import native_live_ledger_schema_v1 as ancestor
from app.domain import native_live_lifecycle_schema_v1 as spec
from app.domain.native_live_intent_v1 import exact_live_intent_document_v1
from app.domain.source_primitives import canonical, decode, sha
from app.okx_live import lifecycle_key_v1 as keys
from app.okx_live import owned_lifecycle_v1 as lifecycle
from tests.native_live_postsubmit_fixtures_v1 import intent


def _key():
    # Published synthetic bytes, never an installed credential.
    value = {
        "schema": keys.SCHEMA,
        "environment": "live",
        "account_uid": "730058",
        "main_uid": "730058",
        "database_oid": 101,
        "owner_oid": 102,
        "reconciler_oid": 103,
        "origin": "https://openapi.okx.com",
        "key_id": "0" * 64,
        "key_hex": bytes(range(32)).hex(),
    }
    value["key_id"] = keys.key_identity_v1(value)
    return value


@pytest.mark.parametrize(
    "name",
    (
        "public",
        "PUBLIC",
        "pg_monitor",
        "pg_read_all_data",
        "postgres;DROP",
        "x-y",
        "1actor",
        "x" * 64,
    ),
)
def test_system_or_unquoted_actor_names_never_generate_sql(name):
    with pytest.raises(ValueError, match="identifier_refused"):
        spec.live_lifecycle_reconciler_grant_sql_v1(name)


def test_reconciler_grants_are_read_only_except_closed_procedures_and_hide_key_table():
    statements = spec.live_lifecycle_reconciler_grant_sql_v1("ctcc_reconciler")
    assert len(statements) == 17
    assert not any(
        word in statement
        for statement in statements
        for word in (
            "INSERT",
            "UPDATE",
            "DELETE",
            "TRUNCATE",
            "MAINTAIN",
            "WITH GRANT OPTION",
        )
    )
    assert not any(
        "TABLE public.native_live_lifecycle_keys_v1" in statement
        for statement in statements
    )
    assert sum("GRANT EXECUTE" in statement for statement in statements) == 5


def test_guard_delta_preserves_all_original_insert_caps_and_exact_rollback():
    old = next(
        sql
        for sql in ancestor.UPGRADE_SQL
        if sql.startswith("CREATE FUNCTION public.native_live_reservation_guard_v1()")
    )
    start = " IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'native_live_reservation_immutable'; END IF;"
    assert spec.RESERVATION_GUARD_SQL.split(start)[1] == old.split(start)[1]
    assert spec.live_lifecycle_ancestor_reservation_guard_v1() == old.replace(
        "CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1
    )
    assert spec.UPGRADE_SQL[-1] == spec.RESERVATION_GUARD_SQL
    assert spec.DOWNGRADE_SQL.index(
        spec.live_lifecycle_ancestor_reservation_guard_v1()
    ) < next(
        i for i, sql in enumerate(spec.DOWNGRADE_SQL) if sql.startswith("DROP TABLE")
    )


@pytest.mark.parametrize(
    "field,replacement",
    (
        ("account_uid", "730059"),
        ("main_uid", "730059"),
        ("database_oid", 104),
        ("owner_oid", 104),
        ("reconciler_oid", 104),
        ("origin", "https://eea.okx.com"),
        ("key_hex", "ff" * 32),
    ),
)
def test_key_identity_binds_exact_scope_and_material(field, replacement):
    value = _key()
    assert keys.key_identity_v1({**value, field: replacement}) != value["key_id"]


@pytest.mark.parametrize("kind", ("short", "changed_message", "changed_signature"))
def test_signature_verification_does_not_return_a_signing_oracle(monkeypatch, kind):
    key = bytes(range(32))
    monkeypatch.setattr(keys, "_record", lambda _: {"document": {"key_hex": key.hex()}})
    raw = canonical({"synthetic_data": True})
    signature = hmac.digest(key, spec.DOMAIN + raw, hashlib.sha256).hex()
    assert (
        keys._verify_retained_lifecycle_signature_v1(object(), raw, signature) is True
    )
    if kind == "short":
        signature = signature[:-1]
    elif kind == "changed_message":
        raw = canonical({"synthetic_data": False})
    else:
        signature = "0" * 64
    with pytest.raises(keys.LiveLifecycleKeyError):
        keys._verify_retained_lifecycle_signature_v1(object(), raw, signature)


def test_exact_intent_readback_refuses_rehashed_lineage_changes():
    value = intent()
    rid, raw, digest = exact_live_intent_document_v1(value)
    metadata = {"account_uid": value.account_uid, "main_uid": value.main_uid}
    observed, encoded = repository._intent(raw.decode(), digest, rid, metadata)
    assert observed == value and encoded == raw
    forged = decode(raw, maximum=65536)
    forged["request_sha256"] = "0" * 64
    changed = canonical(forged)
    with pytest.raises(repository.LiveLifecycleRepositoryError):
        repository._intent(changed.decode(), sha(changed), rid, metadata)


def test_historical_journal_never_advertises_current_exchange_protection():
    from types import SimpleNamespace

    journal = SimpleNamespace(
        proofs=(canonical({"outcome": "PROTECTED_HOLD", "report_id": "real-report"}),),
        reservation_state="reserved",
        reservation_id="a" * 64,
    )
    result = lifecycle._journal_projection(
        journal, {"emergency_stop": True, "disarmed": True}
    )
    assert (
        result["risk_hold_retained"] is True and result["protection_verified"] is None
    )
    assert (
        set(result) == lifecycle._PUBLIC_KEYS and result["execution_authority"] is False
    )


def test_saved_projection_or_unregistered_receipt_is_never_owned():
    for value in (
        {"status": "ZERO_FILL_FLAT"},
        object.__new__(lifecycle.OwnedLiveLifecycleReceiptV1),
    ):
        with pytest.raises(lifecycle.NativeLiveLifecycleError):
            lifecycle.live_lifecycle_public_projection_v1(value)


def test_source_less_owner_cannot_access_key_or_sign_proof():
    owner = lifecycle.OwnedLiveLifecycleV1(lifecycle._OWNER_KEY_V1)
    with pytest.raises(lifecycle.NativeLiveLifecycleError):
        lifecycle._verify_source_for_signing_v1(owner, key_owner=object())


def test_incomplete_capture_flow_does_not_unlock_positive_arm(monkeypatch):
    monkeypatch.delattr(lifecycle, "_capture_owned_live_postsubmit_v1", raising=False)
    with pytest.raises(
        lifecycle.NativeLiveLifecycleError, match="entry_flow_unavailable"
    ):
        lifecycle._require_complete_entry_flow_v1()


@pytest.mark.parametrize("wire", (False, True))
def test_catalog_plan_decodes_only_root_json_and_preserves_string_predicates(wire):
    plan = [
        {
            "Plan": {
                "Node Type": "Seq Scan",
                "Relation Name": "native_live_lifecycle_keys_v1",
                "Filter": "((environment)::text = 'live'::text)",
                "Plan Rows": 1,
            }
        }
    ]
    expected = [
        {
            "Plan": {
                "Node Type": "Seq Scan",
                "Relation Name": "native_live_lifecycle_keys_v1",
                "Filter": "((environment)::text = 'live'::text)",
            }
        }
    ]
    value = canonical(plan).decode() if wire else plan
    assert repository._plan_shape(value) == expected
    changed = [
        {
            "Plan": {
                **expected[0]["Plan"],
                "Filter": "((environment)::text = 'demo'::text)",
            }
        }
    ]
    assert repository._plan_shape(value) != repository._plan_shape(changed)
