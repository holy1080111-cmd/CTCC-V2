"""Synthetic DATA and transaction failure tests; no Live/source authority."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import CheckConstraint, ForeignKeyConstraint, UniqueConstraint

from app.database.models import native_live_controlled_close_v1 as models
from app.database.repositories import native_live_controlled_close_v1 as subject
from app.domain import native_live_controlled_close_schema_v1 as spec
from app.domain import native_live_ledger_schema_v1 as entry_spec
from app.domain import native_live_lifecycle_schema_v1 as lifecycle_spec
from app.domain.source_primitives import canonical, decode, sha, utc_from_ns
from tests.unit.native_live_close_fixture_v1 import (
    METADATA,
    claim_document,
    intent_document,
)


@pytest.mark.parametrize("action", (None, *spec.ACTIONS))
def test_close_records_are_data_with_distinct_action_deadlines(action):
    parent = intent_document()
    if action == "cancel_original_protection":
        later = {
            name: value + 6_000_000_000 for name, value in parent["observation"].items()
        }
        document = claim_document(parent, action=action, observation=later)
        assert datetime.fromisoformat(
            document["request_deadline"]
        ) > datetime.fromisoformat(parent["close_deadline"])
    else:
        document = parent if action is None else claim_document(parent)
    parsed, request, deadline = subject._parse_data(
        canonical(document), metadata=METADATA, claim=action is not None
    )
    assert parsed == document and sha(request) == document["request_sha256"]
    assert deadline.tzinfo is UTC and document["execution_authority"] is False


@pytest.mark.parametrize(
    "change",
    (
        {"environment": "demo"},
        {"account_uid": "999"},
        {"database_oid": True},
        {"execution_authority": True},
        {"position_side": "long"},
        {"expected_control_revision": True},
        {"maximum_close_submissions": 2},
        {"consent_expiry_monotonic_ns": "121000000000"},
        {"close_deadline_monotonic_ns": None},
        {"close_deadline_monotonic_ns": 6_000_000_001},
        {"protection_algo_id": None},
        {"entry_order_id": "0"},
        {"close_client_order_id": "CTCCXother"},
    ),
)
def test_intent_unknown_wrong_identity_or_clock_never_becomes_data(change):
    with pytest.raises((ValueError, TypeError)):
        subject._parse_data(canonical(intent_document(**change)), metadata=METADATA)


@pytest.mark.parametrize(
    "change",
    (
        {"reduceOnly": False},
        {"posSide": "short"},
        {"tdMode": "cross"},
        {"ordType": "market"},
        {"sz": 1},
        {"sz": "0"},
        {"sz": "NaN"},
        {"px": "Infinity"},
        {"clOrdId": "CTCCLwrong"},
        {"side": "unknown"},
        {"attachAlgoOrds": []},
    ),
)
def test_close_request_has_no_alternate_order_route(change):
    document = intent_document()
    request = decode(document["request_json"].encode(), maximum=8192)
    request.update(change)
    raw = canonical(request)
    document.update(request_json=raw.decode(), request_sha256=sha(raw))
    with pytest.raises(ValueError):
        subject._parse_data(canonical(document), metadata=METADATA)


@pytest.mark.parametrize(
    "change",
    (
        {"action": "cancel_all"},
        {"full_account_verified": None},
        {"fills_complete": False},
        {"unknown_exposure": True},
        {"position_count": 1},
        {"position_count": False},
        {"pending_order_count": None},
        {"pending_algo_count": 0},
        {"pending_algo_count": 2},
        {"close_order_state": "partially_filled"},
        {"close_order_id": "123456"},
        {"close_claim_sha256": None},
        {"close_filled_contracts": "0"},
    ),
)
def test_original_protection_cleanup_requires_complete_filled_flat_evidence(change):
    document = claim_document(intent_document(), action="cancel_original_protection")
    document.update(change)
    with pytest.raises(ValueError):
        subject._parse_data(canonical(document), metadata=METADATA, claim=True)


def test_clock_conversion_uses_canonical_ceil_without_new_tolerance():
    document = intent_document()
    stamp = document["observation"]
    stamp["utc_ns"] += 1
    document["consent_received"] = stamp.copy()
    observed = utc_from_ns(stamp["utc_ns"])
    document.update(
        observed_at=observed.isoformat(),
        consent_expires_at=(observed + timedelta(seconds=120)).isoformat(),
        close_deadline=(observed + timedelta(seconds=4)).isoformat(),
    )
    subject._parse_data(canonical(document), metadata=METADATA)
    document["observed_at"] = (observed - timedelta(microseconds=1)).isoformat()
    with pytest.raises(ValueError):
        subject._parse_data(canonical(document), metadata=METADATA)


def test_models_pin_columns_primary_keys_checks_unique_and_foreign_keys():
    for model in (
        models.NativeLiveControlledCloseIntentV1,
        models.NativeLiveControlledCloseClaimV1,
    ):
        table = model.__table__
        assert tuple(table.columns.keys()) == tuple(
            item[0] for item in spec.SOURCE_COLUMNS[table.name]
        )
        assert all(not column.nullable for column in table.columns)
        assert (
            tuple(column.name for column in table.primary_key.columns)
            == spec.SOURCE_PRIMARY_KEYS[table.name]
        )
        assert {
            str(item.name): str(item.sqltext)
            for item in table.constraints
            if type(item) is CheckConstraint
        } == dict(spec.TABLE_CHECKS[table.name])
        assert {
            str(item.name): tuple(item.columns.keys())
            for item in table.constraints
            if type(item) is UniqueConstraint
        } == dict(spec.TABLE_UNIQUES[table.name])
        observed = {
            str(item.name): (
                tuple(item.columns.keys()),
                tuple(e.target_fullname for e in item.elements),
                item.ondelete,
            )
            for item in table.constraints
            if type(item) is ForeignKeyConstraint
        }
        expected = {
            name: (
                fields,
                tuple(parent + "." + column for column in targets),
                "RESTRICT",
            )
            for (child, name), (
                fields,
                parent,
                targets,
            ) in spec.SOURCE_FOREIGN_KEYS.items()
            if child == table.name
        }
        assert observed == expected


def test_reconciler_has_exact_two_reads_two_procedures_and_entry_writer_unchanged():
    extra = spec.additional_reconciler_grant_sql_v1("test_reconciler")
    assert (
        len(extra) == 4
        and len(
            lifecycle_spec.live_lifecycle_reconciler_grant_sql_v1("test_reconciler")
        )
        == 21
    )
    assert sum("GRANT SELECT ON TABLE" in value for value in extra) == 2
    assert sum("GRANT EXECUTE ON FUNCTION" in value for value in extra) == 2
    assert all(
        not any(
            word in value
            for word in (
                " UPDATE ",
                " INSERT ",
                " DELETE ",
                "key_material",
                "GRANT OPTION",
            )
        )
        for value in extra
    )
    assert all(
        "controlled_close" not in value
        for value in entry_spec.live_ledger_grant_sql_v1("test_entry")
    )
    assert len(entry_spec.live_ledger_grant_sql_v1("test_entry")) == 17


@pytest.mark.parametrize(
    "role", ("public", "pg_read_all_data", "PUBLIC", 'x"; DROP ROLE y', "", None)
)
def test_close_privileges_cannot_target_public_or_system_roles(role):
    with pytest.raises(ValueError):
        spec.additional_reconciler_grant_sql_v1(role)


class _Transaction:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self

    async def __aexit__(self, kind, value, tb):
        if kind is None:
            self.session.commits += 1
            if self.session.commit_unknown:
                raise ConnectionError("synthetic lost COMMIT acknowledgement")


class _Session:
    def __init__(self, *, commit_unknown=False):
        self.commit_unknown = commit_unknown
        self.commits = self.calls = self.closed = 0

    def begin(self):
        return _Transaction(self)

    async def scalar(self, statement, arguments):
        self.calls += 1
        return sha(arguments["proof"].encode("ascii"))

    async def close(self):
        self.closed += 1


@pytest.mark.parametrize("failure", ("commit_unknown", "late_readback", None))
async def test_one_commit_then_independent_readback_never_retries_or_erases_intent(
    failure,
):
    document = intent_document()
    raw = canonical(document)
    session = _Session(commit_unknown=failure == "commit_unknown")
    repository = object.__new__(subject.NativeLiveControlledCloseRepositoryV1)
    repository.factory = lambda: session
    repository._document = lambda *args, **kwargs: subject._parse_data(
        raw, metadata=METADATA
    )

    async def settings(*args, **kwargs):
        pass

    reads = []

    async def readback(*args):
        reads.append(args)
        if failure == "late_readback":
            raise ConnectionError("synthetic late readback failure")
        return SimpleNamespace(document_json=raw, signature="a" * 64)

    repository._settings = settings
    repository.read_close_intent_v1 = readback
    errors = {
        "commit_unknown": subject.LiveControlledCloseCommitUnknown,
        "late_readback": subject.LiveControlledCloseReadbackRequired,
    }
    if failure:
        with pytest.raises(errors[failure], match="no_retry"):
            await repository._commit_once(raw, "a" * 64, object(), claim=False)
    else:
        result = await repository._commit_once(raw, "a" * 64, object(), claim=False)
        assert result.document_json == raw
    assert session.commits == session.calls == session.closed == 1
    assert len(reads) == (0 if failure == "commit_unknown" else 1)


def test_data_constructor_cannot_claim_execution_authority():
    values = {
        "close_intent_id": "c" * 64,
        "reservation_id": "b" * 64,
        "document_json": b"{}",
        "document_sha256": "d" * 64,
        "signature": "a" * 64,
        "request_json": b"{}",
        "request_sha256": "e" * 64,
        "deadline": datetime(2026, 11, 21, tzinfo=UTC),
        "recorded_at": datetime(2026, 11, 21, tzinfo=UTC),
    }
    value = subject.LiveControlledCloseIntentDataV1(**values)
    assert value.execution_authority is False
    with pytest.raises(TypeError):
        subject.LiveControlledCloseIntentDataV1(**values, execution_authority=True)
    assert deepcopy(value).execution_authority is False


@pytest.mark.parametrize("claim", (False, True))
async def test_wrong_purpose_or_signature_is_refused_before_opening_a_session(
    monkeypatch, claim
):
    import hashlib
    import hmac

    repository = object.__new__(subject.NativeLiveControlledCloseRepositoryV1)
    repository._key = lambda _: METADATA

    def unavailable():
        raise AssertionError("wrong signature must not reach a database")

    repository.factory = unavailable
    monkeypatch.setattr(
        subject.keys,
        "_record",
        lambda _: {"document": {"key_hex": bytes(range(32)).hex()}},
    )
    document = claim_document(intent_document()) if claim else intent_document()
    raw = canonical(document)
    wrong_domain = spec.INTENT_DOMAIN if claim else spec.CLAIM_DOMAIN
    signature = hmac.digest(bytes(range(32)), wrong_domain + raw, hashlib.sha256).hex()
    with pytest.raises(ValueError):
        await repository._commit_once(raw, signature, object(), claim=claim)


def test_fixed_source_spec_bytes_equal_strict_runtime_canonical_encoding():
    assert spec.SPEC_SHA256 == sha(
        canonical(
            {
                "schema": spec.SCHEMA,
                "upgrade": spec.UPGRADE_SQL,
                "downgrade": spec.DOWNGRADE_SQL,
            }
        )
    )


@pytest.mark.parametrize("claim", (False, True))
def test_new_deadline_pair_cannot_mix_independent_utc_and_monotonic_offsets(claim):
    document = (
        claim_document(intent_document(), action="cancel_original_protection")
        if claim
        else intent_document()
    )
    name = "request_deadline_monotonic_ns" if claim else "close_deadline_monotonic_ns"
    document[name] -= 1000
    with pytest.raises(ValueError):
        subject._parse_data(canonical(document), metadata=METADATA, claim=claim)
