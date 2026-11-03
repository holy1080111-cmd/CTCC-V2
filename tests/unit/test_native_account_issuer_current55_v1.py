"""Private pure component vectors, never native capability/SQL/account evidence.

The fixture supplies fictional DATA with all source/execution flags false.
No native take/replay/finish, DB connection, Session listener or public permit
is patched to manufacture a positive owner/INSERT case.
"""

import hashlib
import inspect
import json
from dataclasses import replace
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.database.repositories import account_issued_revision_current55_v1 as module
from app.trade_qualification import account_ledger_claims_v2 as codec
from app.trade_qualification import demo_control as control
from tests.unit.test_account_ledger_claims_finite_v1 import (
    finite_claims as finite_claims,  # noqa: PLC0414 -- Pytest collects this imported fixture.
)


@pytest.fixture
def source_values(finite_claims):
    now = finite_claims.financial.evaluated_at
    stopped = control.stopped_genesis(
        control.ControlScope("demo", finite_claims.scope.account_id), now
    )
    view = module._IssuerSourceV1(
        session_factory=async_sessionmaker(),
        scope=finite_claims.scope,
        control_state=stopped,
        control_event_sha256="1" * 64,
        previous_account_revision=0,
        previous_ledger_revision=0,
        previous_claims_sha256=None,
        expires_at=finite_claims.financial.original_expires_at,
        monotonic_deadline_ns=1,
        claims_json=codec.encode_finite_account_ledger_claims_v1(finite_claims),
        lineage_row_json=b'{"lineage_sha256":"' + b"2" * 64 + b'"}',
        hwm_custody_id="3" * 32,
        hwm_bundle_json=b"DECLARED_NOT_AN_ACCEPTED_HWM",
        hwm_db_recorded_at=now - timedelta(seconds=1),
        hwm_proof_envelope_sha256="4" * 64,
        proof_operands=tuple(
            (name, ("DECLARED_" + name).encode("ascii"))
            for name in codec.ISSUER_PROOF_KEYS
        ),
    )
    # No factory is bound to a DB and no native owner is constructed. Private
    # pure builders can inspect DATA; they cannot pass the public MOVE seam.
    metadata = (
        "fictional_writer",
        "fictional_writer",
        "none",
        100,
        False,
        False,
        False,
        200,
        "300",
        now,
    )
    lineage = SimpleNamespace(
        lineage_sha256="2" * 64, db_recorded_at=now - timedelta(seconds=1)
    )
    hwm = SimpleNamespace(recorded_at=now - timedelta(seconds=1))
    return view, metadata, lineage, hwm, now


def test_public_issuer_accepts_only_one_source_argument_and_has_no_write_flag():
    assert tuple(
        inspect.signature(module.issue_owned_account_revision_v1).parameters
    ) == ("source",)
    for value in (object(), {"passed": True}, b"saved-proof", None):
        with pytest.raises(ValueError):
            module.issue_owned_account_revision_v1(value)


def test_private_proof_builder_hashes_all_original_bytes_never_supplied_digests(
    source_values,
):
    view, metadata, lineage, hwm, now = source_values
    raw = module._build_proof(view, metadata, now, lineage, hwm)
    document = json.loads(raw)
    assert document["scope"] == ["demo", "100001", "USDT"]
    assert document["proofs"] == {
        name: hashlib.sha256(body).hexdigest() for name, body in view.proof_operands
    }
    assert (
        document["db_role_oid"] == metadata[3]
        and document["db_transaction_id"] == metadata[8]
    )
    assert document["account_revision"] == document["ledger_revision"] == 1
    assert document["execution_authority"] is False
    name, body = view.proof_operands[0]
    changed = replace(
        view, proof_operands=((name, body + b"X"), *view.proof_operands[1:])
    )
    assert module._build_proof(changed, metadata, now, lineage, hwm) != raw
    # Exact-looking saved DATA is still rejected by the genuine public source hook.
    with pytest.raises(ValueError):
        module.issue_owned_account_revision_v1(view)


@pytest.mark.parametrize(
    "damage", ["missing", "duplicate", "reordered", "empty_bytes", "wrong_type"]
)
def test_private_proof_source_membership_rejects_before_build(source_values, damage):
    view, metadata, lineage, hwm, now = source_values
    values = view.proof_operands
    if damage == "missing":
        values = values[:-1]
    elif damage == "duplicate":
        values = (values[0], values[0], *values[2:])
    elif damage == "reordered":
        values = tuple(reversed(values))
    elif damage == "empty_bytes":
        values = ((values[0][0], b""), *values[1:])
    else:
        values = ((values[0][0], "caller string"), *values[1:])
    with pytest.raises(module.OwnedAccountIssuerError):
        module._build_proof(
            replace(view, proof_operands=values), metadata, now, lineage, hwm
        )


@pytest.mark.parametrize(
    "field",
    [
        "daily_net_pnl",
        "weekly_net_pnl",
        "current_loss_streak_upper_bound",
        "measured_current_equity",
        "measured_hwm_receipt_sha256",
    ],
)
def test_native_issue_preconditions_do_not_promote_codec_unknowns(
    source_values, finite_claims, field
):
    view, metadata, lineage, hwm, now = source_values
    document = json.loads(codec.encode_finite_account_ledger_claims_v1(finite_claims))
    document["claims"]["financial"][field] = None
    raw = json.dumps(
        document, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")
    # The DATA codec retains None; native transaction preparation refuses it.
    assert (
        getattr(codec.decode_finite_account_ledger_claims_v1(raw).financial, field)
        is None
    )
    with pytest.raises(module.OwnedAccountIssuerError, match="financial_incomplete"):
        module._build_proof(replace(view, claims_json=raw), metadata, now, lineage, hwm)


def test_preinsert_expected_values_survive_without_detached_ORM_reload(source_values):
    view, metadata, lineage, hwm, now = source_values
    proof = module._build_proof(view, metadata, now, lineage, hwm)
    model = module._issued_model(view, proof)
    expected = module._expected_values(model)
    model.db_recorded_at = now + timedelta(microseconds=1)
    raw = module._validate_issued(
        model, expected, sampled_at=now, observed_at=now + timedelta(microseconds=2)
    )
    assert set(json.loads(raw)) == set(module._ISSUED_COLUMNS)
    original_claims = model.claims_json
    model.claims_json += " "
    with pytest.raises(module.OwnedAccountIssuerError, match="readback_changed"):
        module._validate_issued(
            model, expected, sampled_at=now, observed_at=now + timedelta(microseconds=2)
        )
    model.claims_json = original_claims
    assert (
        module._validate_issued(
            model, expected, sampled_at=now, observed_at=now + timedelta(microseconds=2)
        )
        == raw
    )


@pytest.mark.parametrize(
    "field", [name for name in module._ISSUED_COLUMNS if name != "db_recorded_at"]
)
def test_whole_row_each_column_is_bound_to_preinsert_original(source_values, field):
    view, metadata, lineage, hwm, now = source_values
    model = module._issued_model(
        view, module._build_proof(view, metadata, now, lineage, hwm)
    )
    expected = module._expected_values(model)
    model.db_recorded_at = now
    value = getattr(model, field)
    changed = (
        "0" * 64
        if value is None
        else value + 1
        if type(value) is int
        else value + timedelta(microseconds=1)
        if type(value) is datetime
        else value + "X"
    )
    setattr(model, field, changed)
    with pytest.raises(module.OwnedAccountIssuerError):
        module._validate_issued(model, expected, sampled_at=now, observed_at=now)


@pytest.mark.parametrize("offset", [-1, 20])
def test_database_timestamp_before_sample_or_at_expiry_is_rejected(
    source_values, offset
):
    view, metadata, lineage, hwm, now = source_values
    model = module._issued_model(
        view, module._build_proof(view, metadata, now, lineage, hwm)
    )
    expected = module._expected_values(model)
    model.db_recorded_at = now + timedelta(seconds=offset)
    with pytest.raises(module.OwnedAccountIssuerError):
        module._validate_issued(
            model, expected, sampled_at=now, observed_at=max(now, model.db_recorded_at)
        )


@pytest.mark.parametrize(
    "index,value",
    [(3, True), (7, True), (8, 300), (8, "0e2"), (2, "alternate_role"), (4, True)],
)
def test_locked_reader_metadata_rejects_nonexact_types_and_role_state(
    source_values, index, value
):
    _, metadata, _, _, _ = source_values
    changed = list(metadata)
    changed[index] = value
    with pytest.raises(module.OwnedAccountIssuerError):
        module._plain_locked_metadata_v1(tuple(changed))


def test_locked_reader_metadata_is_data_not_an_issued_capability(source_values):
    _, metadata, _, _, _ = source_values
    assert module._plain_locked_metadata_v1(metadata) == metadata
    with pytest.raises(ValueError):
        module.issue_owned_account_revision_v1(metadata)
