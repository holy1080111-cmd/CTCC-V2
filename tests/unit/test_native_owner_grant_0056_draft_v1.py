"""No database: grant draft remains closed and classifies one COMMIT exactly."""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest

from app.domain.source_primitives import sha
from scripts import native_owner_grant_0056_draft_v1 as grant
from scripts.native_owner_route_preflight_0056_v1 import OwnerRoutePinsV1


def _pins(**changes):
    names = {
        "writer": "ctcc_test_writer",
        "custody": "ctcc_test_custody",
        "frame_reader": "ctcc_test_frame",
    }
    statements = grant.pregrant56.exact_42_grant_sql_0056(**names)
    route = OwnerRoutePinsV1(
        "a" * 64, "b" * 64, "c" * 64, "localhost", 5432, "ctcc", "owner", 41, 42, "0056"
    )
    values = dict(
        source_commit="a" * 40,
        source_tree="b" * 40,
        source_archive_sha256="c" * 64,
        route=route,
        **names,
        writer_oid=101,
        custody_oid=102,
        frame_reader_oid=103,
        portable_inherited_definitions_sha256="d" * 64,
        portable_fitted_pregrant_normalized_sha256="e" * 64,
        raw_inherited_owner_readonly_sha256="f" * 64,
        raw_inherited_owner_readwrite_sha256="1" * 64,
        raw_fitted_owner_readonly_sha256="2" * 64,
        raw_fitted_owner_readwrite_sha256="3" * 64,
        exact_42_plan_sha256=sha("\n".join(statements).encode()),
    )
    values.update(changes)
    return grant.OwnerGrantDraftPinsV1(**values)


@pytest.mark.asyncio
async def test_public_grant_refuses_before_source_or_database(monkeypatch):
    called = []
    monkeypatch.setattr(
        grant, "create_async_engine", lambda *_a, **_k: called.append("opened")
    )
    assert grant._OWNER_GRANT_APPROVAL_SHA256 == ""
    with pytest.raises(
        grant.OwnerGrantRefusal, match="^native_owner_0056_grant_denied$"
    ):
        await grant.grant_reviewed_owner_0056_v1(_pins())
    monkeypatch.setattr(grant, "_OWNER_GRANT_APPROVAL_SHA256", "a" * 64)
    with pytest.raises(
        grant.OwnerGrantRefusal, match="^native_owner_0056_grant_denied$"
    ):
        await grant.grant_reviewed_owner_0056_v1(_pins())
    assert called == []


def test_plan_and_target_specific_raw_pins_are_required():
    assert grant._names(_pins()) == (
        "ctcc_test_writer",
        "ctcc_test_custody",
        "ctcc_test_frame",
    )
    for changes in (
        {"raw_inherited_owner_readwrite_sha256": ""},
        {"raw_fitted_owner_readwrite_sha256": ""},
        {"exact_42_plan_sha256": "0" * 64},
        {"custody_oid": 101},
        {"writer": "owner"},
    ):
        with pytest.raises(grant.OwnerGrantRefusal):
            grant._names(_pins(**changes))


def test_only_profile_fields_are_removed_from_fitted_physical_comparison():
    assert grant._physical({"read_only": True, "repeatable": True, "acl": [1]}) == {
        "acl": [1]
    }
    assert grant._physical(
        {"read_only": False, "read_committed": True, "acl": [1]}
    ) == {"acl": [1]}
    assert grant._physical(
        {"read_only": False, "read_committed": True, "acl": [2]}
    ) != {"acl": [1]}


class _Transaction:
    def __init__(self, events, *, commit_error=False):
        self.events = events
        self.commit_error = commit_error

    async def commit(self):
        self.events.append("commit")
        if self.commit_error:
            raise RuntimeError("synthetic ambiguous commit")

    async def rollback(self):
        self.events.append("rollback")


class _Connection:
    def __init__(self, transaction):
        self.transaction = transaction

    async def begin(self):
        return self.transaction


class _Engine:
    def __init__(self, *, commit_error=False, dispose_error=False):
        self.events = []
        self.transaction = _Transaction(self.events, commit_error=commit_error)
        self.dispose_error = dispose_error

    @asynccontextmanager
    async def connect(self):
        self.events.append("connect")
        yield _Connection(self.transaction)
        self.events.append("disconnect")

    async def dispose(self):
        self.events.append("dispose")
        if self.dispose_error:
            raise RuntimeError("synthetic dispose failure")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("before_error", "commit_error", "dispose_error", "expected", "events"),
    [
        (
            False,
            False,
            False,
            None,
            ["connect", "check", "commit", "disconnect", "dispose"],
        ),
        (
            True,
            False,
            False,
            grant.OwnerGrantRefusal,
            ["connect", "check", "rollback", "dispose"],
        ),
        (
            False,
            True,
            False,
            grant.OwnerGrantCommitUnknown,
            ["connect", "check", "commit", "dispose"],
        ),
        (
            False,
            True,
            True,
            grant.OwnerGrantCommitUnknown,
            ["connect", "check", "commit", "dispose"],
        ),
        (
            False,
            False,
            True,
            grant.OwnerGrantPostcommitIncomplete,
            ["connect", "check", "commit", "disconnect", "dispose"],
        ),
    ],
)
async def test_single_commit_classification(
    monkeypatch, before_error, commit_error, dispose_error, expected, events
):
    async def checked(_connection, _pins, _physical):
        engine.events.append("check")
        if before_error:
            raise RuntimeError("synthetic precommit refusal")

    engine = _Engine(commit_error=commit_error, dispose_error=dispose_error)
    monkeypatch.setattr(grant, "_locked_grant", checked)
    if expected is None:
        await grant._commit_once(engine, _pins(), {})
    else:
        with pytest.raises(expected):
            await grant._commit_once(engine, _pins(), {})
    assert engine.events == events
