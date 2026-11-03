"""Declared synthetic contracts only; no PG/native/credential acceptance.

Policy positives verify inert stored-document algebra. Synthetic V4 control
flow reuses the existing fake TLS/DB fixtures and cannot qualify real lineage.
No test constructs a complete account stamp or grants execution permission.
"""

import asyncio
import copy

import pytest

from app.database.repositories import account_revision_collector_lineage as repository
from app.domain import account_revision_collector_lineage_schema_v1 as schema
from app.domain import account_revision_collector_lineage_v1 as policy
from app.trade_qualification import account_capture_journal as journal
from app.trade_qualification import account_current_source_v4 as current
from tests.unit.test_account_current_source_v4 import setup

SCOPE = ["demo", "123456", "USDT"]
REFERENCE = {
    "capture_id": "a" * 32,
    "head_sha256": "b" * 64,
    "plan_sha256": "c" * 64,
    "packet_sha256": "d" * 64,
    "session_binding_sha256": "e" * 64,
}


def document(*, claims=False):
    claims_json = (
        journal.canonical({"declared_synthetic_claim_snapshot": True}).decode()
        if claims
        else None
    )
    return {
        "schema_version": policy.SCHEMA,
        "scope": list(SCOPE),
        "source_reference": dict(REFERENCE),
        "source_bundle_sha256": "1" * 64,
        "source_policy_sha256": "2" * 64,
        "proof_envelope_sha256": "3" * 64,
        "native_proof_sha256": "4" * 64,
        "native_readback_sha256": "5" * 64,
        "custody_id": "6" * 32,
        "owner_sampled_at": "2026-10-31T00:00:03+00:00",
        "original_expires_at": "2026-10-31T00:00:30+00:00",
        "packet_completed_at": "2026-10-31T00:00:01+00:00",
        "account_revision": 1 if claims else 0,
        "ledger_revision": 2 if claims else 0,
        "claims_sha256": None
        if claims_json is None
        else journal.digest(claims_json.encode()),
        "claims_binding_kind": "UNAUTHENTICATED_LEDGER_SNAPSHOT",
        "control_revision": 1,
        "control_event_sha256": "7" * 64,
        "control_state_sha256": "8" * 64,
        "db_role_oid": 123,
        "db_backend_pid": 456,
        "db_transaction_id": "789",
        "db_recorded_at": "2026-10-31T00:00:02+00:00",
        "unresolved_obligations": list(policy.UNRESOLVED),
        "account_revision_issued": False,
        "account_complete": False,
        "execution_authority": False,
        "admission": "DENY",
    }, claims_json


def validate(value, claims_json=None):
    return policy.validate_document(
        journal.canonical(value),
        scope=SCOPE,
        reference=REFERENCE,
        claims_json=claims_json,
        account_revision=0 if claims_json is None else 1,
        ledger_revision=0 if claims_json is None else 2,
        control_revision=1,
        control_event_sha256="7" * 64,
        control_state_sha256="8" * 64,
    )


@pytest.mark.parametrize("claims", [False, True])
def test_inert_snapshot_can_validate_without_authenticating_claims(claims):
    value, raw_claims = document(claims=claims)
    result = validate(value, raw_claims)
    assert result["claims_binding_kind"] == "UNAUTHENTICATED_LEDGER_SNAPSHOT"
    assert result["unresolved_obligations"] == list(policy.UNRESOLVED)
    assert (
        result["account_revision_issued"]
        is result["account_complete"]
        is result["execution_authority"]
        is False
    )


@pytest.mark.parametrize(
    "field",
    [
        "scope",
        "source_reference",
        "claims_sha256",
        "control_revision",
        "control_event_sha256",
        "control_state_sha256",
        "account_revision",
    ],
)
def test_rehashed_source_claim_revision_and_control_substitution_is_denied(field):
    value, _ = document()
    if field == "scope":
        value[field][1] = "999999"
    elif field == "source_reference":
        value[field]["capture_id"] = "f" * 32
    elif field == "control_revision":
        value[field] = 2
    elif field == "account_revision":
        value[field] = True
    else:
        value[field] = "f" * 64
    with pytest.raises(policy.CollectorRevisionLineageError):
        validate(value)


@pytest.mark.parametrize(
    "field",
    [
        "account_revision_issued",
        "account_complete",
        "execution_authority",
        "unresolved_obligations",
    ],
)
def test_no_rehashed_complete_flag_or_empty_gap_list_promotes_current(field):
    value, _ = document()
    value[field] = [] if field == "unresolved_obligations" else True
    with pytest.raises(
        policy.CollectorRevisionLineageError,
        match="^collector_lineage_source_not_complete$",
    ):
        validate(value)


@pytest.mark.parametrize(
    "damage",
    ["expired_sample", "recorded_before_packet", "float", "duplicate", "oversize"],
)
def test_invalid_clock_or_json_never_yields_partial_lineage(damage):
    value, _ = document()
    if damage == "expired_sample":
        value["owner_sampled_at"] = value["original_expires_at"]
    elif damage == "recorded_before_packet":
        value["db_recorded_at"] = "2026-10-31T00:00:00+00:00"
    raw = journal.canonical(value)
    if damage == "float":
        raw = raw.replace(b'"db_role_oid":123', b'"db_role_oid":123.0')
    elif damage == "duplicate":
        raw = b'{"schema_version":"duplicate",' + raw[1:]
    elif damage == "oversize":
        raw = b" " * (policy.MAX_DOCUMENT_BYTES + 1)
    with pytest.raises(policy.CollectorRevisionLineageError):
        policy.validate_document(
            raw,
            scope=SCOPE,
            reference=REFERENCE,
            claims_json=None,
            account_revision=0,
            ledger_revision=0,
            control_revision=1,
            control_event_sha256="7" * 64,
            control_state_sha256="8" * 64,
        )


def catalog():
    keys = [
        {
            "name": name,
            "kind": kind,
            "validated": True,
            "deferrable": False,
            "columns": columns,
            "parent": parent,
            "parent_columns": parent_columns,
        }
        for name, (kind, columns, parent, parent_columns) in schema.KEYS.items()
    ]
    keys.extend(
        {
            "name": name,
            "kind": "c",
            "validated": True,
            "deferrable": False,
            "columns": [],
            "parent": None,
            "parent_columns": None,
        }
        for name in sorted(schema.CHECKS)
    )
    return {
        "owner": 10,
        "actor": 20,
        "table_ok": True,
        "parent_owners": True,
        "columns": copy.deepcopy(schema.COLUMNS),
        "constraints": keys,
        "triggers": copy.deepcopy(schema.TRIGGERS),
        "bad_table_acl": False,
        "column_acl": False,
        "public_schema_create": False,
        "functions": [
            {
                "name": name,
                "owner": 10,
                "body": body,
                "security_definer": definer,
                "language": "plpgsql",
                "args": 0,
                "returns": "trigger",
                "volatility": "v",
                "parallel": "u",
                "config": ["search_path=pg_catalog, pg_temp"],
                "other_acl": False,
            }
            for name, body, definer in (
                (schema.GUARD, schema.GUARD_BODY, True),
                (schema.IMMUTABLE, schema.IMMUTABLE_BODY, False),
            )
        ],
    }


def test_source_catalog_contract_has_an_admissible_inert_shape():
    assert schema.verify_catalog(catalog()) is None


@pytest.mark.parametrize(
    "damage", ["body", "acl", "owner", "disabled", "foreign_key", "column"]
)
def test_database_guard_body_acl_owner_and_structural_drift_are_denied(damage):
    value = catalog()
    if damage == "body":
        value["functions"][0]["body"] = "BEGIN RETURN NEW; END;"
    elif damage == "acl":
        value["functions"][0]["other_acl"] = True
    elif damage == "owner":
        value["actor"] = value["owner"]
    elif damage == "disabled":
        value["triggers"][0][2] = "D"
    elif damage == "foreign_key":
        target = next(
            item
            for item in value["constraints"]
            if item["name"] == "fk_collector_current_source"
        )
        target["parent_columns"] = ["capture_id", "event_sha256"]
    else:
        value["columns"][0][2] = False
    with pytest.raises(
        policy.CollectorRevisionLineageError,
        match="^collector_lineage_schema_or_role_changed$",
    ):
        schema.verify_catalog(value)


@pytest.mark.asyncio
async def test_synthetic_real_v4_move_is_single_use_not_saved_bundle_authority(
    monkeypatch, tmp_path
):
    with setup(monkeypatch, tmp_path) as fixture:
        owner = await current.acquire_initial_demo_account_source_v4(**fixture.args)
        state = current._take_account_revision_lineage_v1(
            owner, key=repository._SOURCE_KEY
        )
        try:
            assert state["lineage_factory"] is fixture.args["session_factory"]
            assert state["lineage_custody"] is fixture.args["custody_repository"]
            assert owner not in current._CURRENT
            replay, _ = repository._owner(state)
            assert replay.account_complete is replay.execution_authority is False
            with pytest.raises(current.CurrentAccountSourceV4Error):
                current.consume_owned_current_demo_account_v4(owner)
            with pytest.raises(current.CurrentAccountSourceV4Error):
                current._take_account_revision_lineage_v1(
                    replay, key=repository._SOURCE_KEY
                )
        finally:
            state.clear()
        assert not current._CURRENT


@pytest.mark.asyncio
async def test_wrong_handoff_key_cannot_move_an_existing_owner(monkeypatch, tmp_path):
    with setup(monkeypatch, tmp_path) as fixture:
        owner = await current.acquire_initial_demo_account_source_v4(**fixture.args)
        with pytest.raises(
            current.CurrentAccountSourceV4Error,
            match="^account_v4_collector_lineage_key_required$",
        ):
            current._take_account_revision_lineage_v1(owner, key=object())
        assert owner in current._CURRENT
        state = current._take_account_revision_lineage_v1(
            owner, key=repository._SOURCE_KEY
        )
        state.clear()


@pytest.mark.asyncio
async def test_changed_original_factory_burns_owner_before_any_database_call(
    monkeypatch, tmp_path
):
    with setup(monkeypatch, tmp_path) as fixture:
        owner = await current.acquire_initial_demo_account_source_v4(**fixture.args)
        fixture.args["session_factory"].kw["bind"] = object()
        with pytest.raises(current.CurrentAccountSourceV4Error):
            current._take_account_revision_lineage_v1(owner, key=repository._SOURCE_KEY)
        assert owner not in current._CURRENT


@pytest.mark.asyncio
async def test_foreign_task_cannot_take_or_resurrect_current_owner(
    monkeypatch, tmp_path
):
    with setup(monkeypatch, tmp_path) as fixture:
        owner = await current.acquire_initial_demo_account_source_v4(**fixture.args)

        async def other_task():
            with pytest.raises(current.CurrentAccountSourceV4Error):
                current._take_account_revision_lineage_v1(
                    owner, key=repository._SOURCE_KEY
                )

        await asyncio.create_task(other_task())
        assert owner not in current._CURRENT
        with pytest.raises(current.CurrentAccountSourceV4Error):
            current.consume_owned_current_demo_account_v4(owner)


@pytest.mark.asyncio
@pytest.mark.parametrize("interrupt", ["cancel", "unknown"])
async def test_uncertain_custody_readback_clears_moved_state_and_never_returns_receipt(
    monkeypatch, tmp_path, interrupt
):
    with setup(monkeypatch, tmp_path) as fixture:
        owner = await current.acquire_initial_demo_account_source_v4(**fixture.args)
        moved = current._CURRENT[owner]

        async def failed(*_args):
            if interrupt == "cancel":
                raise asyncio.CancelledError("DECLARED_SYNTHETIC_ARGUMENT")
            raise RuntimeError("DECLARED_SYNTHETIC_ARGUMENT")

        monkeypatch.setattr(repository, "_custody_readback", failed)
        if interrupt == "cancel":
            with pytest.raises(asyncio.CancelledError) as caught:
                await repository.record_owned_current_revision_lineage_v1(owner)
            assert caught.value.args == ()
        else:
            with pytest.raises(
                policy.CollectorRevisionLineageError,
                match="^collector_lineage_write_or_readback_unknown$",
            ):
                await repository.record_owned_current_revision_lineage_v1(owner)
        assert moved == {} and owner not in current._CURRENT
