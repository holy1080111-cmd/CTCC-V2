"""Read the stopped account's native and legacy exposure in a fresh source window."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from app.core import native_locked_demo_control_0056_v1 as locked
from app.core.native_account_restart_recovery_0056_v1 import _stopped
from app.core.native_stopped_control_genesis_0056_v1 import _require_role_witness
from app.database.repositories.qualification_ledger import (
    PortfolioLocalCheckpoint,
    QualificationLedgerError,
    QualificationLedgerRepository,
)
from app.demo_automation import native_current_source_0056_v1 as current
from app.domain.source_primitives import canonical, decode, sha
from app.trade_evidence.native_private_root_v2 import require_private_evidence_base_v2
from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_current_source_verifier as verifier
from app.trade_qualification.native_protection_observation_0056_v1 import (
    observe_position_brackets,
)
from app.trade_qualification.reservations import LedgerScope

_NAME = "db0056-restart-inventory-observation.json"


async def _observe_stopped_portfolio(
    factories, scope, roles, settlement, *, initialize_capture=False
):
    current._need(
        type(initialize_capture) is bool, "db0056_restart_inventory_purpose_refused"
    )
    ledger_scope = LedgerScope(
        environment=scope.environment,
        account_id=scope.account_id,
        settlement_currency=settlement,
    )
    repo = QualificationLedgerRepository(
        factories.writer_factory, clock=lambda: datetime.now(UTC)
    )
    async with factories.writer_factory() as session, session.begin():
        async with locked._verified_locked_demo_control_0056(
            factories, session, scope
        ) as verified:
            row, catalog, witness, started_at, held = verified
            _require_role_witness(roles, catalog, witness)
            state, event = await locked.DemoControlRepositoryV2._snapshot(session, row)
            held()
            current._need(
                state.scope == scope and _stopped(state),
                "db0056_restart_inventory_stop_required",
            )
            try:
                # CURRENT already requires a durable zero-revision scope.
                # Do this under the original UID/Control lock before the
                # first checkpoint so our own initialization is not drift.
                selected = await repo._locked(
                    session, ledger_scope, create=initialize_capture
                )
            except QualificationLedgerError as error:
                if str(error) != "ledger_scope_missing":
                    raise
                local = None
            else:
                local = await repo._read_portfolio_checkpoint_locked(
                    session, ledger_scope, selected
                )
            held()
            observed_at = await session.scalar(locked._CLOCK)
            held()
            observed = locked.ControlObservation(state, event, observed_at)
            current._need(
                observed_at >= started_at, "db0056_restart_inventory_clock_refused"
            )
            if initialize_capture:
                await locked.DemoControlRepositoryV2.verify_historical_binding(
                    session, observed.state, observed.event_sha256
                )
                held()
        return locked.NativeLockedDemoControl0056V1(observed, catalog, *witness), local


def _checked_local(point, plan):
    if point is None:
        return None
    current._need(
        type(point) is PortfolioLocalCheckpoint,
        "db0056_restart_inventory_local_readback_refused",
    )
    value = decode(point.document_json, maximum=262144)
    current._need(
        type(value) is dict
        and canonical(value) == point.document_json
        and value["schema_version"] == "ctcc.portfolio_local_checkpoint.v2"
        and value["environment"] == "demo"
        and value["account_id"] == plan.expected_uid
        and value["requested_settlement_currency"] == plan.settlement_currency
        and value["future_writer_exclusion"] is value["execution_authority"] is False
        and type(value["blocking_reasons"]) is list,
        "db0056_restart_inventory_local_readback_refused",
    )
    states = value["currency_states"]
    current._need(
        type(states) is list
        and len(states) >= 1
        and all(
            type(state) is dict
            and type(state["account_revision"]) is int
            and state["account_revision"] >= 0
            for state in states
        ),
        "db0056_restart_inventory_local_readback_refused",
    )
    return value


def _checked_exchange(raw: bytes, plan, source) -> dict:
    value = decode(raw, maximum=capture.MAX_PACKET_BYTES)
    current._need(
        type(value) is dict
        and canonical(value) == raw
        and value["schema_version"] == "ctcc.current_account_source_observation.v6"
        and value["policy_sha256"] == verifier.V7_POLICY_SHA256
        and value["identity"]["uid"] == plan.expected_uid
        and value["identity"]["main_uid"] == plan.expected_main_uid
        and value["identity"]["environment"] == "demo"
        and value["identity"]["settlement_currency"] == plan.settlement_currency
        and value["source_reference"]["packet_sha256"] == source["packet_sha256"]
        and value["recorded_current_chain_head_sha256"]
        == source["original_chain_sha256"]
        and value["algo_type_coverage"]["all_eight_terminal_chains_replayed"] is True
        and sha(raw) == source["current_source_receipt_sha256"],
        "db0056_restart_inventory_exchange_readback_refused",
    )
    counts = value["inventory_row_counts"]
    current._need(
        type(counts) is dict
        and set(counts) == set(verifier.V7_INVENTORY_STREAMS)
        and all(type(count) is int and count >= 0 for count in counts.values()),
        "db0056_restart_inventory_exchange_inventory_refused",
    )
    return value


def _inventory_document(source, operands, plan, local_before, local_after):
    first, second = (
        _checked_local(value, plan) for value in (local_before, local_after)
    )
    current._need(
        first == second
        and (
            local_before is None or local_before.received_at <= local_after.observed_at
        ),
        "db0056_restart_inventory_local_exposure_changed",
    )
    exchange = _checked_exchange(operands.current_receipt_json, plan, source)
    counts = exchange["inventory_row_counts"]
    return {
        **source,
        "schema": "ctcc.demo.db0056.restart_inventory_observation.v1",
        "emergency_stop": True,
        "exchange_observed_positions": counts["positions"],
        "exchange_observed_ordinary_orders": counts["orders_pending"],
        "exchange_observed_algo_order_rows_by_query": {
            key: count for key, count in counts.items() if key.startswith("algo_")
        },
        "exchange_position_bracket_observations": observe_position_brackets(
            operands.packet, exchange
        ),
        "local_checkpoint_sha256": None if second is None else local_after.state_sha256,
        "local_exposure_observation": (
            "UNKNOWN"
            if second is None
            or any(
                state["account_revision"] == 0 for state in second["currency_states"]
            )
            else "INCOMPLETE_OR_EXPOSED"
            if second["blocking_reasons"]
            else "NO_ACTIVE_ROWS_IN_OBSERVED_CHECKPOINT"
        ),
        "local_checkpoint_blocker_count": None
        if second is None
        else len(second["blocking_reasons"]),
        "position_attribution_complete": False,
        "active_protection_verified": False,
        "unresolved_intents_cleared": False,
        "flat_start_permission": False,
    }


async def observe_configured_restart_inventory_0056_v1(instrument_id: str):
    """Read current exchange and local inventory with matching stopped endpoints."""
    try:
        settings = current._settings(instrument_id)
        require_private_evidence_base_v2(settings.okx_demo_native_evidence_root)
        async with asyncio.timeout(120):
            root, document = await current._capture(
                settings, instrument_id, restart_inventory=True
            )
        current._need(isinstance(root, Path), "db0056_restart_inventory_path_refused")
        return current._publish(root, document, name=_NAME)
    except (asyncio.CancelledError, current.NativeCurrentSource0056Error):
        raise
    except Exception:  # noqa: BLE001 -- expose neither account nor SQL facts.
        raise current.NativeCurrentSource0056Error(
            "db0056_restart_inventory_refused"
        ) from None
