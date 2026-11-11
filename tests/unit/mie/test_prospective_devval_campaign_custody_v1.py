"""Campaign custody transaction ordering with no database or provider traffic."""

from __future__ import annotations

import ssl
from dataclasses import replace
from datetime import timedelta

import pytest
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.source_primitives import ClockStamp
from app.mie.validation import prospective_devval_campaign_custody_v1 as custody
from app.mie.validation.prospective_development_validation_plan import _ns
from app.mie.validation.prospective_devval_campaign_plan_v1 import (
    ProspectiveDevvalCampaignPlanV1,
    freeze_prospective_devval_campaign_v1,
)
from tests.unit.mie.test_prospective_devval_campaign_plan_v1 import START, plan_input

DB = 81
PUBLISHER = 82
OBSERVER = 83
COLLECTOR = 84
SOURCE_CHECKPOINT = {
    "expected_source_commit": "a" * 40,
    "expected_source_tree": "b" * 40,
    "expected_source_manifest_sha256": "c" * 64,
}


@pytest.fixture(scope="module")
def campaign():
    return freeze_prospective_devval_campaign_v1(
        ProspectiveDevvalCampaignPlanV1(**plan_input())
    )


def _stamp(at):
    return ClockStamp(utc_ns=_ns(at), monotonic_ns=1)


def _row(campaign, *, acknowledged=False):
    return {
        "campaign_sha256": campaign.sha256,
        "campaign_json": campaign.payload.decode(),
        "campaign_id": campaign.contract.plan.campaign_id,
        "recorded_at": START - timedelta(minutes=3),
        "publisher_role_oid": PUBLISHER,
        "publication_xid": "110",
        "acknowledged_at": START - timedelta(minutes=2) if acknowledged else None,
        "observer_role_oid": OBSERVER if acknowledged else None,
        "acknowledgement_xid": "113" if acknowledged else None,
        "database_read_at": START - timedelta(minutes=1),
    }


def _settings(role, *, hostname="evidence.invalid"):
    login = "devval_publisher" if role == "publisher" else "devval_observer"
    return {
        "role": role,
        "host": hostname,
        "port": 5432,
        "database": "ctcc_evidence",
        "login": login,
        "expected_database_oid": DB,
        "expected_login_oid": PUBLISHER if role == "publisher" else OBSERVER,
        "expected_publisher_role_oid": PUBLISHER,
    }


@pytest.mark.parametrize("tls", ["valid", "no_host_check", "no_ssl", "wrong_target"])
def test_native_custody_engine_requires_exact_verified_endpoint(tls):
    settings = _settings("publisher")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    if tls == "no_host_check":
        context.check_hostname = False
    host = "other.invalid" if tls == "wrong_target" else settings["host"]
    engine = create_async_engine(
        URL.create(
            "postgresql+asyncpg",
            host=host,
            port=settings["port"],
            database=settings["database"],
            username=settings["login"],
        ),
        connect_args={} if tls == "no_ssl" else {"ssl": context},
    )
    factory = async_sessionmaker(engine)
    if tls == "valid":
        repo = custody.DevvalCampaignCustodyRepositoryV1(factory, **settings)
        assert repo._engine is engine and repo._factory is not factory
    else:
        with pytest.raises(custody.DevvalCampaignCustodyError):
            custody.DevvalCampaignCustodyRepositoryV1(factory, **settings)


def _setup(monkeypatch, campaign, *, role, outputs):
    repo = object.__new__(custody.DevvalCampaignCustodyRepositoryV1)
    repo._role = role
    repo._publisher_oid = PUBLISHER
    repo._observer_oid = OBSERVER
    repo._login_oid = COLLECTOR if role == "collector" else OBSERVER
    commands = []
    iterator = iter(outputs)

    def checked(payload, **pins):
        assert payload == campaign.payload
        assert pins == {
            "expected_campaign_sha256": campaign.sha256,
            **SOURCE_CHECKPOINT,
        }
        return campaign.contract

    async def transaction(statement, parameters):
        commands.append(statement)
        assert parameters["sha"] == campaign.sha256
        if statement is custody._PUBLISH:
            assert parameters["payload"].encode() == campaign.payload
        value = next(iterator)
        if isinstance(value, Exception):
            raise value
        row, login, xid = value
        center = row["database_read_at"] if row else START - timedelta(minutes=1)
        return (
            row,
            custody._Transaction(DB, login, xid),
            (
                _stamp(center - timedelta(seconds=1)),
                _stamp(center + timedelta(seconds=1)),
            ),
        )

    monkeypatch.setattr(repo, "_manifest", checked)
    monkeypatch.setattr(repo, "_transaction", transaction)
    monkeypatch.setattr(
        custody,
        "native_stamp",
        lambda: _stamp(START - timedelta(seconds=20)).model_dump(),
    )
    return repo, commands


def _args(campaign):
    return {"expected_campaign_sha256": campaign.sha256, **SOURCE_CHECKPOINT}


@pytest.mark.asyncio
async def test_publisher_commits_once_and_cannot_read_as_observer(
    monkeypatch, campaign
):
    repo, commands = _setup(
        monkeypatch, campaign, role="publisher", outputs=((None, PUBLISHER, "110"),)
    )
    result = await repo.publish(campaign.payload, **_args(campaign))
    assert result == custody.DevvalCampaignCommitV1(
        campaign.sha256, DB, PUBLISHER, "110"
    )
    with pytest.raises(custody.DevvalCampaignCustodyError, match="wrong_login_role"):
        await repo.read(campaign.payload, **_args(campaign))
    assert commands == [custody._PUBLISH]


@pytest.mark.asyncio
async def test_observer_replays_commit_before_ack_then_reads_the_new_commit(
    monkeypatch, campaign
):
    repo, commands = _setup(
        monkeypatch,
        campaign,
        role="observer",
        outputs=(
            (_row(campaign), OBSERVER, "112"),
            (None, OBSERVER, "113"),
            (_row(campaign, acknowledged=True), OBSERVER, "114"),
        ),
    )
    result = await repo.acknowledge(campaign.payload, **_args(campaign))
    assert commands == [custody._READ, custody._ACK, custody._READ]
    assert (
        result.publication_xid,
        result.acknowledgement_xid,
        result.readback_xid,
    ) == ("110", "113", "114")
    assert result.observer_role_oid == OBSERVER
    assert result.acknowledged_at < START
    assert not result.independently_protected and not result.trusted_clock_verified
    assert not result.predictive_oos_eligible and not result.execution_authority


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change", ["missing", "wrong_bytes", "same_xid", "foreign_writer", "partial_ack"]
)
async def test_observer_refuses_before_ack_if_committed_identity_is_unproven(
    change, monkeypatch, campaign
):
    row = _row(campaign)
    if change == "missing":
        row = None
    elif change == "wrong_bytes":
        row["campaign_json"] += " "
    elif change == "same_xid":
        row["publication_xid"] = "112"
    elif change == "foreign_writer":
        row["publisher_role_oid"] = 999
    else:
        row["observer_role_oid"] = OBSERVER
    repo, commands = _setup(
        monkeypatch, campaign, role="observer", outputs=((row, OBSERVER, "112"),)
    )
    with pytest.raises(custody.DevvalCampaignCustodyError):
        await repo.acknowledge(campaign.payload, **_args(campaign))
    assert commands == [custody._READ]


@pytest.mark.asyncio
async def test_ambiguous_ack_does_not_retry_and_later_read_is_read_only(
    monkeypatch, campaign
):
    repo, commands = _setup(
        monkeypatch,
        campaign,
        role="observer",
        outputs=(
            (_row(campaign), OBSERVER, "112"),
            custody.DevvalCampaignCustodyError("devval_custody_transaction_unresolved"),
            (_row(campaign, acknowledged=True), OBSERVER, "114"),
        ),
    )
    with pytest.raises(
        custody.DevvalCampaignCustodyError, match="transaction_unresolved"
    ):
        await repo.acknowledge(campaign.payload, **_args(campaign))
    assert commands == [custody._READ, custody._ACK]
    assert (
        await repo.read(campaign.payload, **_args(campaign))
    ).acknowledgement_xid == "113"
    assert commands == [custody._READ, custody._ACK, custody._READ]


@pytest.mark.asyncio
async def test_ack_readback_after_first_window_does_not_issue_early_receipt(
    monkeypatch, campaign
):
    late = _row(campaign, acknowledged=True)
    late["database_read_at"] = START
    repo, commands = _setup(
        monkeypatch,
        campaign,
        role="observer",
        outputs=(
            (_row(campaign), OBSERVER, "112"),
            (None, OBSERVER, "113"),
            (late, OBSERVER, "114"),
        ),
    )
    with pytest.raises(custody.DevvalCampaignCustodyError, match="ack_unresolved"):
        await repo.acknowledge(campaign.payload, **_args(campaign))
    assert commands == [custody._READ, custody._ACK, custody._READ]


def test_clock_bracket_refuses_future_server_observation(campaign):
    row = _row(campaign)
    first = _stamp(row["database_read_at"] - timedelta(seconds=2))
    last = _stamp(row["database_read_at"] - timedelta(seconds=1))
    with pytest.raises(
        custody.DevvalCampaignCustodyError, match="publication_readback_changed"
    ):
        custody._readback(
            row,
            campaign=campaign.contract,
            payload=campaign.payload,
            pin=campaign.sha256,
            transaction=custody._Transaction(DB, OBSERVER, "112"),
            stamps=(first, last),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [None, "changed_ack", "unknown", "same_xid"])
async def test_collector_claim_requires_same_committed_ack_and_fresh_readback(
    failure, monkeypatch, campaign
):
    raw = _row(campaign, acknowledged=True)
    root_pin = "e" * 64
    dispatch_row = {
        "campaign_sha256": campaign.sha256,
        "root_binding_sha256": root_pin,
        "collector_role_oid": COLLECTOR,
        "dispatch_xid": "121",
        "claimed_at": START - timedelta(seconds=50),
        "database_read_at": START - timedelta(seconds=49),
    }
    result = (dispatch_row, COLLECTOR, "121" if failure == "same_xid" else "122")
    repo, commands = _setup(
        monkeypatch,
        campaign,
        role="collector",
        outputs=(
            (raw, COLLECTOR, "119"),
            (raw, COLLECTOR, "120"),
            custody.DevvalCampaignCustodyError("devval_custody_transaction_unresolved")
            if failure == "unknown"
            else (None, COLLECTOR, "121"),
            result,
        ),
    )
    publication = await repo.read(campaign.payload, **_args(campaign))
    if failure == "changed_ack":
        publication = replace(publication, acknowledgement_xid="900")
    call = repo.claim_dispatch(
        campaign.payload,
        root_binding_sha256=root_pin,
        expected_publication=publication,
        **_args(campaign),
    )
    if failure is None:
        claim = await call
        assert claim.root_binding_sha256 == root_pin
        assert claim.dispatch_xid == "121" and claim.readback_xid == "122"
    else:
        with pytest.raises(custody.DevvalCampaignCustodyError):
            await call
    expected = [custody._READ, custody._READ]
    if failure != "changed_ack":
        expected.append(custody._DISPATCH_CLAIM)
    if failure not in ("changed_ack", "unknown"):
        expected.append(custody._DISPATCH_READ)
    assert commands == expected
