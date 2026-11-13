"""Acquire one native CURRENT page chain through DB0056 and observe Control endpoints."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
from uuid import uuid4

from app.core.native_locked_demo_control_0056_v1 import (
    NativeLockedDemoControl0056V1,
    read_locked_demo_control_0056_v1,
)
from app.database.repositories.public_runtime_bundle_custody import (
    RuntimeBundleCustodyRepository,
)
from app.demo_automation import native_server_selectors_v2 as selectors
from app.demo_automation.native_source_only_v1 import _dispose, _settings
from app.domain.source_primitives import canonical, sha
from app.exchange.okx.private_rest import OkxDemoPrivateRestClient
from app.trade_evidence.native_private_root_v2 import (
    mkdir_private_evidence_child_v2,
    require_private_evidence_base_v2,
)
from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_current_source_v4 as current
from app.trade_qualification import account_native_proof_storage as storage
from app.trade_qualification.account_runtime import ControlledDemoAccountSession
from app.trade_qualification.demo_control import ControlScope

_RECEIPT = "db0056-current-source-observation.json"
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")


class NativeCurrentSource0056Error(ValueError):
    """Fixed failure code with no SQL, source rows, account IDs or secrets."""


@dataclass(frozen=True, slots=True)
class NativeCurrentSource0056ObservationV1:
    receipt_sha256: str
    database_head: Literal["0056"] = field(default="0056", init=False)
    admission: Literal["DENY"] = field(default="DENY", init=False)
    account_complete: Literal[False] = field(default=False, init=False)
    execution_authority: Literal[False] = field(default=False, init=False)


def _need(condition: bool, code: str) -> None:
    if condition is not True:
        raise NativeCurrentSource0056Error(code) from None


def _attempt(
    settings, *, leaf_prefix="db0056-current-", parts=("route-proof", "native-proof")
):
    base = require_private_evidence_base_v2(settings.okx_demo_native_evidence_root)
    leaf = leaf_prefix + uuid4().hex
    with storage._root_context(base) as directory:
        mkdir_private_evidence_child_v2(directory, leaf)
        with directory.child(leaf) as attempt:
            for name in parts:
                mkdir_private_evidence_child_v2(attempt, name)
    result = base / leaf
    require_private_evidence_base_v2(str(result))
    for name in parts:
        require_private_evidence_base_v2(str(result / name))
    return result


async def _observe_control(factories, scope, roles):
    # Never hold the UID lock over a provider request. Another process can
    # stop or disarm during collection; a changed endpoint burns this result.
    async with factories.writer_factory() as session, session.begin():
        result = await read_locked_demo_control_0056_v1(factories, session, scope)
        _need(
            type(result) is NativeLockedDemoControl0056V1
            and result.database_head == "0056"
            and result.control.state.scope == scope
            and result.control.state.arm_request_id is None
            and result.issuance_authority is result.execution_authority is False
            and result.server_incarnation_sha256 == roles.server_incarnation_sha256
            and result.role_route_sha256 == roles.role_route_sha256
            and result.writer_catalog.native_definitions_sha256
            == roles.observation.inherited_native_definitions_sha256,
            "native_current_0056_control_refused",
        )
        result.control.__post_init__()
    return result


def _readback_control(before, after):
    _need(
        before.control.state == after.control.state
        and before.control.event_sha256 == after.control.event_sha256
        and before.writer_catalog == after.writer_catalog
        and after.control.observed_at >= before.control.observed_at,
        "native_current_0056_control_changed",
    )


def _source_document(operands, plan, plan_sha, before, after):
    _need(
        type(operands) is current.CurrentAccountOperandsV4
        and type(operands.packet) is capture.DemoAccountPacket
        and operands.packet.plan == plan
        and operands.packet.plan_sha256 == operands.reference.plan_sha256 == plan_sha
        and operands.reference.session_binding_sha256
        == sha(plan.session_binding_id.encode("ascii"))
        and operands.source_authenticity_verified is True
        and operands.durable_raw_readback_verified is True
        and operands.current_page_chains_complete is True
        and operands.account_complete is operands.execution_authority is False,
        "native_current_0056_source_readback_refused",
    )
    frozen = capture.freeze_demo_account_packet(
        operands.packet, expected_plan_sha256=plan_sha
    )
    _need(
        frozen.sha256 == operands.reference.packet_sha256,
        "native_current_0056_packet_changed",
    )
    return {
        "schema": "ctcc.demo.db0056.current_page_chain_observation.v1",
        "database_head": "0056",
        "admission": "DENY",
        "control_observation_semantics": "ENDPOINTS_ONLY",
        "control_revision": after.control.state.revision,
        "control_event_sha256": after.control.event_sha256,
        "control_first_observed_at": before.control.observed_at.isoformat(),
        "control_last_observed_at": after.control.observed_at.isoformat(),
        "server_incarnation_sha256": after.server_incarnation_sha256,
        "role_route_sha256": after.role_route_sha256,
        "native_definitions_sha256": after.writer_catalog.native_definitions_sha256,
        "plan_sha256": plan_sha,
        "packet_sha256": operands.reference.packet_sha256,
        "original_chain_sha256": operands.reference.head_sha256,
        "current_source_receipt_sha256": sha(operands.current_receipt_json),
        "source_custody_receipt_sha256": operands.receipt_sha256,
        "current_page_chains_complete": True,
        "account_complete": False,
        "post_g12_eligible": False,
        "execution_authority": False,
        "registration_site": "UNKNOWN",
        "order_writes": 0,
    }


async def _capture(settings, instrument_id: str):
    selection = factories = None
    client = OkxDemoPrivateRestClient(settings=settings)
    try:
        with client.pinned_read_session() as lease:
            selection = await selectors.select_configured_demo_source_identity_0056_v1(
                private_client=client,
                read_session=lease,
                instrument_ids=(instrument_id,),
            )
            factories = selection.factories
            roles = selection.source_roles_0056
            plan = selection.current_plan
            scope = ControlScope("demo", plan.expected_uid)
            before = await _observe_control(factories, scope, roles)
            root = _attempt(settings)
            plan_sha = capture.plan_sha256(plan)
            account = ControlledDemoAccountSession.from_pinned_read_session(
                read_session=lease, plan=plan, expected_plan_sha256=plan_sha
            )
            owned = await current.acquire_initial_demo_account_source_v4(
                account_session=account,
                private_client=client,
                session_factory=factories.writer_factory,
                private_proof_root=root / "route-proof",
                native_proof_root=root / "native-proof",
                custody_repository=RuntimeBundleCustodyRepository(
                    factories.custody_factory
                ),
            )
            try:
                after = await _observe_control(factories, scope, roles)
                _readback_control(before, after)
                _need(
                    selection.current_plan == plan and selection.factories is factories,
                    "native_current_0056_selection_changed",
                )
                lease.verify_current()
                operands = current.consume_owned_current_demo_account_v4(owned)
            finally:
                # Errors between acquisition and consumption retire the local
                # capability while retaining its already-committed audit data.
                current._CURRENT.pop(owned, None)
            return root, _source_document(operands, plan, plan_sha, before, after)
    finally:
        await _dispose(selection, factories)


def _publish(root: Path, document: dict) -> NativeCurrentSource0056ObservationV1:
    payload = canonical(document)
    with storage._root_context(root) as directory:
        directory.publish(_RECEIPT, payload)
        _need(
            directory.read(_RECEIPT, 32768) == payload,
            "native_current_0056_receipt_changed",
        )
    return NativeCurrentSource0056ObservationV1(sha(payload))


async def observe_configured_current_account_0056_v1(
    instrument_id: str,
) -> NativeCurrentSource0056ObservationV1:
    """One explicit native read. Positive result is current-data evidence only."""
    try:
        settings = _settings(instrument_id)
        require_private_evidence_base_v2(settings.okx_demo_native_evidence_root)
        # Role/catalog preflight is separately bounded by 55 seconds. The
        # signed selector and original page-chain retain their own deadlines.
        async with asyncio.timeout(120):
            root, document = await _capture(settings, instrument_id)
        _need(
            all(
                type(value) is str and _DIGEST.fullmatch(value) is not None
                for key, value in document.items()
                if key.endswith("_sha256")
            ),
            "native_current_0056_digest_refused",
        )
        return _publish(root, document)
    except (asyncio.CancelledError, NativeCurrentSource0056Error):
        raise
    except Exception:  # noqa: BLE001 -- do not expose credentials, SQL, or account IDs.
        raise NativeCurrentSource0056Error(
            "native_current_0056_observation_unavailable"
        ) from None
