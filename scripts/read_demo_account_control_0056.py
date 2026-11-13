"""Read the current, exact Demo Control event under the restricted DB0056 writer."""

from __future__ import annotations

import asyncio
from pathlib import Path

from sqlalchemy import text

from app.core.native_locked_demo_control_0056_v1 import (
    NativeWriterControlPurpose0056,
    held_native_writer_control_0056_v1,
)
from app.domain.source_primitives import canonical
from app.trade_evidence.submission_reporting import pin
from app.trade_qualification.demo_control import ControlScope
from scripts import native_control_status_0056_v1 as startup

_EVENT_SCOPE = text("""
    SELECT account_id
    FROM public.demo_control_journal
    WHERE environment = 'demo' AND event_sha256 = :event
    LIMIT 2
""")


async def _read_selected_control(selected, roles, event):
    deployment = startup.deployment
    async with selected.writer_factory() as session:
        tx = await session.begin()
        try:
            async with asyncio.timeout(5):
                accounts = (await session.scalars(_EVENT_SCOPE, {"event": event})).all()
            deployment._need(len(accounts) == 1)
            scope = ControlScope("demo", accounts[0])
            async with held_native_writer_control_0056_v1(
                selected,
                session,
                scope,
                roles=roles,
                purpose=NativeWriterControlPurpose0056.PREPARE_OBSERVATION,
            ) as held:
                held.check_held()
                proof = held.observation
                deployment._need(
                    proof.issuance_authority is proof.execution_authority is False
                    and proof.control.event_sha256 == event
                    and proof.control.state.scope == scope
                )
                state = proof.control.state
                return {
                    "control_event_sha256": event,
                    "control_revision": state.revision,
                    "emergency_stop": state.emergency_stop,
                    "arm_request_recorded": state.arm_request_id is not None,
                }
        finally:
            await tx.rollback()


async def read_configured_account_control(
    *, descriptor_file: Path, source_archive_file: Path, control_event_sha256: str
) -> dict[str, object]:
    deployment = startup.deployment
    event = pin(control_event_sha256)
    release = startup._read_bound_release(descriptor_file, source_archive_file)
    anchor, descriptor = release
    settings = deployment.get_settings()
    deployment._check_runtime_settings(settings, descriptor)
    selected = deployment.selectors.select_configured_native_demo_factories_v2()
    try:
        roles = await deployment.identity.observe_restricted_role_identity_0056_v2(
            selected, source=deployment._SOURCE
        )
        deployment._check_restricted_role_observation(roles, descriptor)
        first = await _read_selected_control(selected, roles, event)
        observed = await _read_selected_control(selected, roles, event)
        deployment._need(first == observed)
    finally:
        await deployment.selectors._dispose_failed_selection_factories_v2(selected)
    deployment._need(deployment.get_settings() is settings)
    deployment._check_runtime_settings(settings, descriptor)
    deployment._need(
        startup._read_bound_release(descriptor_file, source_archive_file) == release
    )
    return {**observed, "deployment_admin_anchor_sha256": anchor.trust_sha256}


def main(argv: list[str] | None = None) -> int:
    parser = startup.deployment._PrivateParser(allow_abbrev=False, description=__doc__)
    parser.add_argument("--source-archive-file", required=True, type=Path)
    parser.add_argument("--descriptor-file", required=True, type=Path)
    parser.add_argument("--control-event-sha256", required=True, type=pin)
    envelope = {
        "schema": "ctcc.native_only.db0056_account_control_readback.v1",
        "database_head": "0056",
        "execution_authority": False,
        "admission": "DENY",
        "control_writes": 0,
        "order_writes": 0,
    }
    try:
        observed = asyncio.run(
            read_configured_account_control(**vars(parser.parse_args(argv)))
        )
    except (Exception, KeyboardInterrupt, asyncio.CancelledError):  # noqa: BLE001 -- redact account and connection data
        print(canonical({**envelope, "status": "CONTROL_UNCONFIRMED"}).decode("ascii"))
        return 1
    print(
        canonical(
            {**observed, **envelope, "status": "ACCOUNT_CONTROL_OBSERVED"}
        ).decode("ascii")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
