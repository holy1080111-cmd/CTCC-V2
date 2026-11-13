"""Collect a configured Demo CURRENT-index history bundle at DB0056."""

from __future__ import annotations

import asyncio
from pathlib import Path

from app.demo_automation import native_server_selectors_v2 as selectors
from app.demo_automation.native_current_source_0056_v1 import (
    _attempt,
    _need,
    _observe_control,
    _readback_control,
)
from app.demo_automation.native_source_only_v1 import (
    _dispose,
    _history_bundle_hash,
    _retain_incomplete,
    _settings,
)
from app.domain.source_primitives import canonical, sha
from app.exchange.okx.private_rest import OkxDemoPrivateRestClient
from app.trade_evidence.native_private_root_v2 import require_private_evidence_base_v2
from app.trade_qualification import account_history_source_v1 as history
from app.trade_qualification import account_native_proof_storage as storage
from app.trade_qualification.demo_control import ControlScope

_PARTS = ("history-raw", "history-companion")
_RECEIPT = "db0056-current-history-observation.json"


class NativeHistorySource0056Error(ValueError):
    """Stable refusal; source rows and private deployment settings stay local."""


async def _capture(
    settings, instrument_id: str, root: Path, *, bootstrap_only: bool = False
) -> bytes:
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
            role_evidence = selection.source_roles_0056
            scope = ControlScope("demo", selection.scope_config.expected_uid)
            before = await _observe_control(factories, scope, role_evidence)
            if bootstrap_only:
                _need(
                    before.control.state.emergency_stop is True,
                    "native_history_0056_bootstrap_stop_required",
                )
            digest = await history.acquire_configured_history_current_source_0056_v1(
                selection=selection,
                read_session=lease,
                private_client=client,
                raw_root=root / _PARTS[0],
                companion_root=root / _PARTS[1],
                bootstrap_only=bootstrap_only,
            )
            after = await _observe_control(factories, scope, role_evidence)
            _readback_control(before, after)
            if bootstrap_only:
                from app.trade_qualification.native_demo_control_owner_v2 import (
                    _read_genesis,
                )
                from app.trade_qualification.reservations import LedgerScope

                genesis = await _read_genesis(
                    factories.writer_factory,
                    LedgerScope(
                        account_id=scope.account_id, settlement_currency="USDT"
                    ),
                )
                final = await _observe_control(factories, scope, role_evidence)
                _readback_control(after, final)
                after = final
            _need(
                selection.factories is factories
                and selection.source_roles_0056 is role_evidence
                and _history_bundle_hash(root / _PARTS[0]) == digest,
                "native_history_source_0056_readback_changed",
            )
            lease.verify_current()
        return canonical(
            {
                "schema": "ctcc.demo.db0056.current_history_observation.v1",
                "database_head": "0056",
                "admission": "DENY",
                "history_bundle_sha256": digest,
                **(
                    {"measured_genesis_sha256": genesis.source_sha256}
                    if bootstrap_only
                    else {}
                ),
                "capture_contract": "ctcc.demo_account_plan.v5",
                "current_v7_page_chains_verified": False,
                "control_revision": after.control.state.revision,
                "control_event_sha256": after.control.event_sha256,
                "control_observation_semantics": "ENDPOINTS_ONLY",
                "server_incarnation_sha256": after.server_incarnation_sha256,
                "role_route_sha256": after.role_route_sha256,
                "native_definitions_sha256": after.writer_catalog.native_definitions_sha256,
                "registration_site": "UNKNOWN",
                "history_tail_closed": False,
                "account_complete": False,
                "post_g12_eligible": False,
                "execution_authority": False,
                "order_writes": 0,
            }
        )
    finally:
        await _dispose(selection, factories)


async def observe_configured_current_history_0056_v1(
    instrument_id: str, *, bootstrap_only: bool = False
) -> str:
    """Return only the hash of the privately retained diagnostic receipt."""
    root = None
    try:
        _need(
            type(bootstrap_only) is bool, "native_history_0056_source_purpose_required"
        )
        settings = _settings(instrument_id)
        require_private_evidence_base_v2(settings.okx_demo_native_evidence_root)
        root = _attempt(settings, leaf_prefix="db0056-history-", parts=_PARTS)
        async with asyncio.timeout(120):
            receipt = await _capture(
                settings, instrument_id, root, bootstrap_only=bootstrap_only
            )
        with storage._root_context(root) as directory:
            directory.publish(_RECEIPT, receipt)
            _need(
                directory.read(_RECEIPT, len(receipt)) == receipt,
                "native_history_source_0056_receipt_changed",
            )
        return sha(receipt)
    except asyncio.CancelledError:
        if root is not None:
            _retain_incomplete(root)
        raise
    except Exception:  # noqa: BLE001 -- never echo a path, account or source row
        if root is not None:
            try:
                _retain_incomplete(root)
            except Exception:  # noqa: BLE001 -- storage may have failed after publication
                raise NativeHistorySource0056Error(
                    "db0056_current_history_retention_uncertain"
                ) from None
        raise NativeHistorySource0056Error("db0056_current_history_refused") from None
