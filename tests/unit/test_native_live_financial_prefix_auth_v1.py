"""Synthetic cryptography and capability negatives; no credentials or issuer."""

from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace

import pytest

from app.database.repositories import native_live_financial_epoch_v1 as repository
from app.domain.source_primitives import canonical, decode, sha
from app.okx_live import financial_epoch_math_v1 as math
from app.okx_live import financial_prefix_auth_v1 as subject
from app.okx_live import financial_rows_v1 as rows
from tests.unit import test_native_live_financial_epoch_math_v1 as epoch_fixture
from tests.unit import test_native_live_financial_rows_v1 as row_fixture

KEY = b"synthetic-financial-key-not-an-api-credential"


@pytest.mark.parametrize("purpose", ("epoch", "sample"))
def test_seal_refuses_modified_rehashed_missing_or_rotated_key_payload(purpose):
    raw = subject._seal_data_v1(
        KEY, purpose, {"loss_streak": 3, "execution_authority": False}
    )
    assert subject._verify_data_v1(KEY, purpose, raw)["loss_streak"] == 3
    tampered = decode(raw, maximum=65536)
    tampered["loss_streak"] = 0
    rehashed = canonical(tampered)
    assert sha(rehashed) != sha(raw)
    for changed, key in (
        (rehashed, KEY),
        (canonical({"loss_streak": 0}), KEY),
        (raw, KEY + b"rotation"),
    ):
        with pytest.raises(ValueError, match="authentication_refused"):
            subject._verify_data_v1(key, purpose, changed)


def test_separate_purpose_tags_cannot_authorize_okx_or_another_financial_kind():
    raw = subject._seal_data_v1(KEY, "epoch", {"execution_authority": False})
    with pytest.raises(ValueError, match="authentication_refused"):
        subject._verify_data_v1(KEY, "sample", raw)
    with pytest.raises(ValueError, match="authentication_refused"):
        subject._seal_data_v1(KEY, "okx_post_order", {"passed": True})


@pytest.mark.asyncio
async def test_native_signing_requires_original_source_and_actual_held_collector_scope():
    for call in (
        subject.issue_original_genesis_v1,
        subject.issue_original_sample_v1,
        subject.verify_original_prefix_v1,
    ):
        with pytest.raises((repository.LiveFinancialEpochError, ValueError)):
            call({"passed": True}, key=repository._SEAL_KEY)
    assert not hasattr(subject, "_credential_key_v1")


def test_prior_closed_loss_is_recomputed_from_original_fill_bill_and_position_json(
    monkeypatch,
):
    first = rows._derive_original_financial_rows_v1(row_fixture._view())
    epoch = math.epoch_document(first)
    genesis = math.sample_document(first, original_epoch_json=epoch, original_prefix=())
    monkeypatch.setattr(row_fixture, "NOW", row_fixture.NOW + timedelta(minutes=10))
    loss_rows = rows._derive_original_financial_rows_v1(
        row_fixture._view(mutation=epoch_fixture._loss)
    )
    loss = math.sample_document(
        loss_rows, original_epoch_json=epoch, original_prefix=(genesis,)
    )
    changed = deepcopy(decode(loss, maximum=2097152))
    changed["controlled_outcomes"][0]["net_pnl"] = math.ratio(0)
    changed["loss_streak"] = 0
    from dataclasses import replace

    later = replace(
        loss_rows, observed_at=loss_rows.observed_at + timedelta(minutes=10)
    )
    with pytest.raises(ValueError, match="prior_raw_outcome_conflict"):
        math.sample_document(
            later,
            original_epoch_json=epoch,
            original_prefix=(genesis, canonical(changed)),
        )


def test_foreign_source_account_cannot_adopt_a_valid_source_receipt():
    tail = canonical(
        {"environment": "live", "account_complete": False, "execution_authority": False}
    )
    bridge = canonical(
        {
            "schema": "ctcc.native_live.owned_source_data.v1",
            "environment": "live",
            "origin": "https://openapi.okx.com",
            "account_identity_sha256": sha(canonical(["live", "100", "100"])),
            "financial_tail_sha256": sha(tail),
            "execution_authority": False,
            "account_complete": False,
        }
    )
    document = {
        "source_revision_sha256": sha(bridge),
        "native_source_receipt_json": bridge.decode(),
        "native_financial_tail_json": tail.decode(),
    }
    with pytest.raises(ValueError, match="authentication_refused"):
        subject._checked_original_source_documents_v1(
            document, {"uid": "101", "main_uid": "100", "receipt_json": bridge}
        )


def test_native_issuer_fixed_documents_bind_credential_site_and_database_continuity(
    monkeypatch,
):
    """Declared crypto-only seams, not a real source or held SQL acceptance."""
    tail = canonical(
        {"environment": "live", "account_complete": False, "execution_authority": False}
    )
    bridge = canonical(
        {
            "schema": "ctcc.native_live.owned_source_data.v1",
            "environment": "live",
            "origin": "https://openapi.okx.com",
            "account_identity_sha256": sha(canonical(["live", "100", "100"])),
            "financial_tail_sha256": sha(tail),
            "raw_bank_sha256": "2" * 64,
            "account_complete": False,
            "execution_authority": False,
        }
    )
    original = rows._derive_original_financial_rows_v1(row_fixture._view())
    receipt = decode(original.receipt_json, maximum=2097152)
    receipt.update(source_revision_sha256=sha(bridge), financial_tail_sha256=sha(tail))
    original = replace(
        original, source_revision_sha256=sha(bridge), receipt_json=canonical(receipt)
    )

    class SyntheticSource:
        pass

    owner = SyntheticSource()
    pins = SimpleNamespace(database_oid=1, owner_oid=2)
    scope = {"pins": pins, "rows": original, "epoch": None, "prefix": ()}
    view = {
        "uid": "100",
        "main_uid": "100",
        "receipt_json": bridge,
        "financial_tail_json": tail,
    }
    pin = SimpleNamespace(
        origin="https://openapi.okx.com",
        credentials=("synthetic-key", "synthetic-secret", "synthetic-passphrase"),
    )
    monkeypatch.setitem(
        subject.source._SOURCES,
        owner,
        {"lease": SimpleNamespace(verify_current=lambda: None, _pin=pin)},
    )
    monkeypatch.setattr(
        subject,
        "_held_scope_v1",
        lambda source, key: scope if source is owner else pytest.fail("foreign scope"),
    )
    monkeypatch.setattr(
        subject.source, "_replay_owned_live_source_v1", lambda *a, **k: view
    )
    monkeypatch.setattr(
        subject.rows_source,
        "read_owned_live_financial_rows_data_v1",
        lambda *a: original,
    )
    epoch = subject.issue_original_genesis_v1(owner, key=object())
    scope["epoch"] = epoch
    sample = subject.issue_original_sample_v1(owner, key=object())
    scope["prefix"] = (sample,)
    subject.verify_original_prefix_v1(owner, key=object())
    for mutate, restore in (
        (
            lambda: setattr(
                pin, "credentials", ("synthetic-key", "rotated", "synthetic-passphrase")
            ),
            lambda: setattr(
                pin,
                "credentials",
                ("synthetic-key", "synthetic-secret", "synthetic-passphrase"),
            ),
        ),
        (
            lambda: setattr(pin, "origin", "https://eea.okx.com"),
            lambda: setattr(pin, "origin", "https://openapi.okx.com"),
        ),
        (
            lambda: setattr(pins, "database_oid", 3),
            lambda: setattr(pins, "database_oid", 1),
        ),
    ):
        mutate()
        with pytest.raises(ValueError, match="authentication_refused"):
            subject.verify_original_prefix_v1(owner, key=object())
        restore()
    with pytest.raises(ValueError, match="authentication_refused"):
        subject.issue_original_sample_v1(owner, key=object())
