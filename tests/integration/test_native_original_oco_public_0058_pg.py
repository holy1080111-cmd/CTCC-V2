"""Actual public0058/TLS protective closure and restart journal mechanics.

Raw venue/qualification ingress is DECLARED SYNTHETIC. No native source owner,
runtime Arm, API, order or real-account acceptance is created. Every actual
public migration, role, source/issuer/intent/journal/closure trigger stays on.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from app.core.native_role_witness_0056_v1 import (
    observe_restricted_native_roles_0056_witness_v1,
)
from app.domain import native_database_profile_v1 as profiles
from tests.integration.native_public_protective_0058_fixture_v1 import (
    prepare_public_reservation,
    prepare_public_source,
)
from tests.integration.test_native_demo_profile_0058_pg import (
    _actual58_demo_lab as _actual58_demo_lab,  # noqa: PLC0414 -- exact isolated fixture registration.
)
from tests.unit import test_native_ledger_contracts_v2 as wire_fixture
from tests.unit import test_native_reconciliation_facts_v1 as target_fixture

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def _template(request):
    direction, mode = getattr(request, "param", ("long", "net_mode"))
    declared = wire_fixture.synthetic.__wrapped__(
        SimpleNamespace(param=(direction, mode))
    )
    return target_fixture.target.__wrapped__(declared)


async def _guards(owner):
    async with owner.connect() as connection:
        return tuple(
            (
                await connection.execute(
                    text(
                        "SELECT c.relname,t.tgname,t.tgenabled::text,p.proname,pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(p.prosrc,'UTF8')),'hex') FROM pg_catalog.pg_trigger t JOIN pg_catalog.pg_class c ON c.oid=t.tgrelid JOIN pg_catalog.pg_proc p ON p.oid=t.tgfoid WHERE c.relnamespace='public'::regnamespace AND NOT t.tgisinternal ORDER BY c.relname,t.tgname"
                    )
                )
            ).all()
        )


@pytest.mark.parametrize(
    "_template",
    [
        ("long", "net_mode"),
        ("short", "net_mode"),
        ("long", "long_short_mode"),
        ("short", "long_short_mode"),
    ],
    indirect=True,
)
async def test_actual0058_public_original_oco_closure_and_new_postflat_journal_preserve_lineage(
    monkeypatch, _actual58_demo_lab, _template
):
    owner, selected, _ = _actual58_demo_lab
    before = await _guards(owner)
    roles = await observe_restricted_native_roles_0056_witness_v1(
        selected, source=ROOT, profile=profiles.DB0058
    )
    assert roles.observation.database_head == "0058"
    _, item, recorded, issued, source_plan, _ = await prepare_public_source(
        monkeypatch, owner, selected, _template
    )
    journal, _ = await prepare_public_reservation(
        selected, item, recorded, issued, source_plan, _template
    )
    original, current, hold = await journal.seed_hold(recorded)
    closed = await journal.close(original, current, hold)
    assert (
        await journal.replay_and_append_after_flat(original, current, closed)
        != closed.write_xid
    )
    await journal.journal_data_readback("reconciled_flat")
    assert await _guards(owner) == before
    assert (
        await observe_restricted_native_roles_0056_witness_v1(
            selected, source=ROOT, profile=profiles.DB0058
        )
    ) == roles


@pytest.mark.parametrize(
    "mutation", ["changed_protection", "partial_fill", "ambiguous_child"]
)
async def test_actual0058_public_changed_or_incomplete_oco_remains_durably_held(
    monkeypatch, _actual58_demo_lab, _template, mutation
):
    owner, selected, _ = _actual58_demo_lab
    before = await _guards(owner)
    _, item, recorded, issued, source_plan, _ = await prepare_public_source(
        monkeypatch, owner, selected, _template
    )
    journal, _ = await prepare_public_reservation(
        selected, item, recorded, issued, source_plan, _template
    )
    original, current, hold = await journal.seed_hold(recorded)
    rejected = await journal.close(original, current, hold, mutation=mutation)
    assert await journal.readback_held() != rejected.write_xid
    await journal.journal_data_readback("uncertain")
    assert await _guards(owner) == before
