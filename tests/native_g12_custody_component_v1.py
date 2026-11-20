"""Actual SQL custody component with declared synthetic private-owner DATA.

This helper cannot qualify a public candidate, publish G12 images, reserve risk,
submit an order or create any production owner. It replaces only the private
in-memory handoff during this test, preserving all original SQL custody hooks.
"""

import asyncio
from dataclasses import fields
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from app.core import native_role_witness_0056_v1 as witness
from app.domain import native_database_profile_v1 as profiles
from app.domain.source_primitives import canonical, sha
from app.trade_qualification import native_g12_publication_v2 as publisher
from tests.integration.test_g12_invocation_custody_repository import (
    _claim,
    _publication,
)

ROOT = Path(__file__).resolve().parents[1]


async def exercise_native_custody_component_v1(
    owner, selected, *, profile, monkeypatch
):
    head = profiles.head(profile)
    assert head in {"0057", "0058"}
    roles = await witness.observe_restricted_native_roles_0056_witness_v1(
        selected, source=ROOT, profile=profile
    )
    assert roles.observation.database_head == head
    factory = selected.writer_factory
    async with factory() as session, session.begin():
        actual_head, oid = (
            await session.execute(
                text(
                    "SELECT (SELECT array_agg(version_num::text) FROM public.alembic_version),r.oid::bigint FROM pg_catalog.pg_roles r WHERE rolname=current_user"
                )
            )
        ).one()
    assert actual_head == [head] and type(oid) is int and oid > 0
    start = datetime.now(UTC)
    deadline = start + timedelta(seconds=60)
    values = {
        field.name: None
        for field in fields(publisher.candidate.account.IssuedAccountForG11V1)
        if field.init
    }
    values.update(
        session_factory=factory,
        database_head=head,
        writer_role_oid=oid,
        database_factories=selected,
        database_roles=roles,
    )
    issued = publisher.candidate.account.IssuedAccountForG11V1(**values)
    assert issued.execution_authority is False
    repository = publisher.custody.G12InvocationCustodyRepository(factory)
    initial = _claim()
    document = initial.values
    document["deadline"] = deadline.isoformat()
    raw = canonical(document)
    claim = publisher.custody.G12InvocationClaim(initial.invocation_id, raw, sha(raw))
    binding, task = object(), asyncio.current_task()
    state = {
        "candidate": {"facts": SimpleNamespace(created_at=start), "deadline": deadline},
        "custody_sessions": [],
        "custody_pending_session": None,
        "custody_claim_raw": claim.claim_json,
        "custody_issued_writer_pin": (head, oid),
    }

    def synthetic_owner_handoff(actual_repository, actual_binding, *, key):
        assert actual_repository is repository and actual_binding is binding
        assert (
            key is publisher.custody._NATIVE_V2_KEY and asyncio.current_task() is task
        )
        return state, issued

    # Both physical head/catalog observations per stage, all effective ACLs,
    # native SQL clocks, INSERTs, commits and independent XIDs stay original.
    with monkeypatch.context() as handoff:
        handoff.setattr(publisher, "_custody_context", synthetic_owner_handoff)
        committed = await repository.claim(claim, _native_binding=binding)
        prepared_publication = _publication(committed)
        state["custody_publication_raw"] = prepared_publication.publication_json
        publication = await repository.publication(
            committed, prepared_publication, _native_binding=binding
        )
        transcript = publisher._custody_transcript_sha256(state)
        assert len(state["custody_sessions"]) == 6
        assert len({session[2][4] for session in state["custody_sessions"]}) == 6
        assert state["custody_catalog_pin"][:2] == (head, oid)
        assert len(transcript) == 64 and publication.execution_authority is False
        with pytest.raises(
            publisher.custody.G12InvocationCustodyError,
            match="g12_custody_claim_unavailable",
        ):
            await repository.claim(claim, _native_binding=binding)
    async with owner.connect() as connection:
        assert tuple(
            [
                await connection.scalar(text("SELECT count(*) FROM public." + table))
                for table in ("g12_invocation_claims", "g12_invocation_publications")
            ]
        ) == (1, 1)
    stages = []
    for stage, _session, before, after in state["custody_sessions"]:
        assert before[:5] == after[:5] and before[6:] == after[6:]
        assert before[:3] == (before[0], before[0], "none")
        assert before[6:] == (
            "off",
            "read committed",
            "pg_catalog, public, pg_temp",
            "UTC",
            "on",
        )
        stages.append(
            {
                "stage": stage,
                "database_head": head,
                "writer_oid": oid,
                "backend_pid": before[3],
                "transaction_id": before[4],
                "started_at": before[5].astimezone(UTC).isoformat(),
                "completed_at": after[5].astimezone(UTC).isoformat(),
            }
        )
    return {
        "database_head": head,
        "writer_oid": oid,
        "catalog_sha256": state["custody_catalog_pin"][2],
        "transcript_sha256": transcript,
        "claim_sha256": claim.claim_sha256,
        "publication_sha256": publication.publication_sha256,
        "stages": stages,
        "claim_count": 1,
        "publication_count": 1,
        "synthetic_private_owner_handoff": True,
        "qualification_performed": False,
        "execution_authority": False,
    }
