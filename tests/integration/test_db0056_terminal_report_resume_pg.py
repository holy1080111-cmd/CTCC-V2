"""Check an actual restricted writer refuses a missing terminal cleanup."""

from types import SimpleNamespace

import pytest
from sqlalchemy import text

from app.core import native_role_identity_0056_v2 as identity
from app.database.repositories import native_terminal_report_resume_0056_v1 as resume
from app.domain.source_primitives import sha
from tests.integration.test_gate3_fitted_candidate_custody_pg import (
    _actual_0056_role_observation_lab,  # noqa: F401
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_actual0056_report_resume_refuses_missing_cleanup_no_new_rows(
    _actual_0056_role_observation_lab,  # noqa: F811
    monkeypatch,
):
    _, owner, selected, _ = _actual_0056_role_observation_lab
    roles = await identity.observe_restricted_role_identity_0056_v2(
        selected, source=resume.report56._SOURCE
    )
    settings = SimpleNamespace(submission_outbox_queue_namespace="disposable_test")
    monkeypatch.setattr(resume, "get_settings", lambda: settings)
    counters = text("""
        SELECT (SELECT count(*) FROM public.qualification_submission_outcomes),
               (SELECT count(*) FROM public.qualification_report_spool),
               (SELECT count(*) FROM public.qualification_report_projection_receipts),
               (SELECT coalesce(sum(ledger_revision), 0) FROM public.qualification_account_scopes)
    """)
    async with owner.begin() as session:
        before = (await session.execute(counters)).one()
    with pytest.raises(
        resume.report56.NativeTerminalReport0056Error,
        match="^native_terminal_report_0056_unavailable$",
    ):
        await resume._resume_native_terminal_report_0056_v1(
            selected,
            cleanup_id=sha(b"ctcc-isolated-missing-cleanup"),
            expected_roles=roles,
        )
    async with owner.begin() as session:
        assert (await session.execute(counters)).one() == before
