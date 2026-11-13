"""Explicit localhost operator command to reconcile and clear a DB0056 Demo stop."""

from __future__ import annotations

import argparse
import asyncio
import sys
import warnings
from getpass import GetPassWarning, getpass
from uuid import uuid4

from app.api.security import require_ctcc_token
from app.config.settings import get_settings
from app.database.repositories.demo_control import ControlObservation
from app.database.repositories.demo_control_current56_v1 import (
    DemoControlRepository0056V1,
)
from app.demo_automation import native_server_selectors_v2 as selectors
from app.domain.source_primitives import canonical
from app.exchange.okx.private_rest import OkxDemoPrivateRestClient
from app.trade_qualification.demo_control import ControlScope
from app.trade_qualification.demo_control_clear_v2 import CLEAR_CONFIRMATION_V2


def _refuse() -> None:
    raise ValueError("db0056_demo_stop_clear_refused") from None


def _proof_roots(settings):
    from app.trade_evidence.native_private_root_v2 import (
        mkdir_private_evidence_child_v2,
        require_private_evidence_base_v2,
    )
    from app.trade_qualification import account_native_proof_storage as storage

    base = require_private_evidence_base_v2(settings.okx_demo_native_evidence_root)
    invocation = "clear-stop-" + uuid4().hex
    with storage._root_context(base) as directory:
        mkdir_private_evidence_child_v2(directory, invocation)
        with directory.child(invocation) as attempt:
            for name in ("private-proof", "native-proof"):
                mkdir_private_evidence_child_v2(attempt, name)
    paths = tuple(
        require_private_evidence_base_v2(str(base / invocation / name))
        for name in ("private-proof", "native-proof")
    )
    if storage._root_identity(paths[0]) == storage._root_identity(paths[1]):
        _refuse()
    return paths


def _check_disarmed_result(result, scope):
    if (
        type(result) is not ControlObservation
        or result.state.scope != scope
        or result.state.emergency_stop is not False
        or result.state.arm_request_id is not None
        or result.state.arm_expires_at is not None
        or result.execution_authority is not False
    ):
        _refuse()


def _bindings():
    from app.demo_automation.native_source_only_v1 import _dispose, _settings
    from app.trade_qualification import native_demo_control_owner_v2

    return _settings, _dispose, native_demo_control_owner_v2


async def clear_configured_demo_stop_0056(
    *, instrument: str, token: str, confirmation: str
):
    """Only the original configured signed Demo source can reconcile Control."""
    if type(confirmation) is not str or confirmation != CLEAR_CONFIRMATION_V2:
        _refuse()
    await require_ctcc_token(token)

    read_settings, dispose, owner56 = _bindings()
    settings = read_settings(instrument)
    private_root, native_root = _proof_roots(settings)
    selection = factories = owner = None
    try:
        client = OkxDemoPrivateRestClient(settings=settings)
        with client.pinned_read_session() as lease:
            selection = (
                await selectors._select_configured_demo_controlled_identity_0056(
                    private_client=client,
                    read_session=lease,
                    instrument_ids=(instrument,),
                )
            )
            factories = selection.factories
            record, verified_factories, roles = selectors._controlled_identity_0056(
                selection
            )
            if factories is not verified_factories:
                _refuse()
            scope = ControlScope("demo", record.scope_config.expected_uid)
            current = DemoControlRepository0056V1(
                factories, roles=roles, scope=scope, clock=owner56._NOW
            )
            before = await current.read(scope)
            if (
                before.state.scope != scope
                or before.state.emergency_stop is not True
                or before.state.arm_request_id is not None
                or before.state.arm_expires_at is not None
            ):
                _refuse()
            await require_ctcc_token(token)
            if (
                get_settings() is not settings
                or read_settings(instrument) is not settings
            ):
                _refuse()
            try:
                owner = await owner56.prepare_selected_demo_control_owner_0056(
                    selection
                )
                cleared = await owner.clear_stop(
                    confirmation=confirmation,
                    private_proof_root=private_root,
                    native_proof_root=native_root,
                )
                _check_disarmed_result(cleared, scope)
                if not before.state.revision < cleared.state.revision:
                    _refuse()
                if (
                    get_settings() is not settings
                    or read_settings(instrument) is not settings
                ):
                    _refuse()
                lease.verify_current()
                released = await owner.release()
                _check_disarmed_result(released, scope)
                if released.state.revision != cleared.state.revision + 1:
                    _refuse()
                owner = None
                return released
            finally:
                if owner is not None:
                    # Tightening happens while the exact selected lease is still held.
                    await owner.emergency_stop()
    finally:
        await dispose(selection, factories)


def _operator_input():
    if not sys.stdin.isatty() or not sys.stderr.isatty():
        _refuse()
    confirmation = input(
        f"Type {CLEAR_CONFIRMATION_V2} to reconcile and clear Demo stop: "
    )
    if confirmation != CLEAR_CONFIRMATION_V2:
        _refuse()
    with warnings.catch_warnings():
        warnings.simplefilter("error", GetPassWarning)
        return getpass("Local CTCC API token (hidden): "), confirmation


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--instrument", required=True)
    args = parser.parse_args(argv)
    result = {
        "schema": "ctcc.demo.db0056.local_operator_clear_stop.v1",
        "database_head": "0056",
        "admission": "DENY",
        "execution_authority": False,
        "order_writes": 0,
    }
    try:
        token, confirmation = _operator_input()
        released = asyncio.run(
            clear_configured_demo_stop_0056(
                instrument=args.instrument, token=token, confirmation=confirmation
            )
        )
        result.update(
            status="CONTROL_CLEARED_AND_RELEASED_DISARMED",
            control_revision=released.state.revision,
            emergency_stop=False,
            arm_request_recorded=False,
        )
    except (Exception, KeyboardInterrupt, asyncio.CancelledError):  # noqa: BLE001 -- never print private data or assert zero writes on uncertainty.
        print(
            canonical({**result, "status": "CONTROL_CLEAR_UNCONFIRMED"}).decode("ascii")
        )
        return 1
    print(canonical(result).decode("ascii"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
