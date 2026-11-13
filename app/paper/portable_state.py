"""Canonical, read-only Paper state export with replay checks."""

from __future__ import annotations

import re
from uuid import UUID

from app.database.repositories.persistence import state_checksum
from app.domain.paper import PaperRuntimeStateView, PaperStateView
from app.domain.source_primitives import canonical, decode, sha
from app.paper.engine import PaperBroker

MAX_STATE_BYTES = 1024 * 1024
SCHEMA = "ctcc.paper.portable_state.v1"
_DIGEST = re.compile(r"[a-f0-9]{64}", re.ASCII)
_VERSION = re.compile(r"[A-Za-z0-9._+-]{1,80}", re.ASCII)
_NON_AUTHORITY = {
    "environment": "paper",
    "source": "local_authenticated_runtime_readback",
    "database_receipt": False,
    "restore_authority": False,
    "execution_authority": False,
}


class PortablePaperStateError(ValueError):
    """A fixed readback refusal that keeps account, path and token details private."""


def require(condition: bool) -> None:
    if condition is not True:
        raise PortablePaperStateError("portable_paper_state_refused")


def _digest(value: object) -> bool:
    return type(value) is str and _DIGEST.fullmatch(value) is not None


def _version(value: object) -> bool:
    return type(value) is str and _VERSION.fullmatch(value) is not None


def verified_version(value) -> str:
    require(
        type(value) is dict
        and value.get("environment") == "production"
        and value.get("trading_mode") == "analysis_only"
        and value.get("auto_trade") is False
        and value.get("live_trading") is False
        and _version(value.get("version"))
    )
    return value["version"]


def _validate_state(value) -> PaperStateView:
    state = PaperStateView.model_validate_json(
        canonical(value), strict=True, extra="forbid"
    )
    require(len(state.orders) <= 4096 and len(state.positions) <= 4096)
    orders = {row.id: row for row in state.orders}
    clients = {row.client_order_id for row in state.orders}
    filled = {row.id for row in state.orders if row.status == "filled"}
    require(
        len(orders) == len(clients) == len(state.orders)
        and len({row.id for row in state.positions}) == len(state.positions)
        and {row.order_id for row in state.positions} == filled
        and len(filled) == len(state.positions)
    )
    for pos in state.positions:
        order = orders[pos.order_id]
        require(
            (
                pos.symbol,
                pos.side,
                pos.quantity,
                pos.entry_price,
                pos.stop_loss,
                pos.take_profit,
                pos.opened_at,
            )
            == (
                order.symbol,
                order.side,
                order.quantity,
                order.average_fill_price,
                order.stop_loss,
                order.take_profit,
                order.filled_at,
            )
        )
    require(
        all(
            instant is None or instant.utcoffset() is not None
            for row in state.orders
            for instant in (row.created_at, row.filled_at)
        )
        and all(
            instant is None or instant.utcoffset() is not None
            for row in state.positions
            for instant in (row.opened_at, row.closed_at)
        )
    )
    # Keep exactly observed rows. The broker independently re-derives runtime
    # counters and accounting from them, which catches stale or malformed views.
    require(PaperBroker().restore(state).account == state.account)
    require(state.model_dump(mode="json") == value)
    return state


def read_runtime_state(raw: bytes, expected_binding: str) -> dict:
    """Check the persistent same-installation local API response before export."""
    value = decode(raw, MAX_STATE_BYTES)
    require(
        type(value) is dict
        and set(value) == {*PaperRuntimeStateView.model_fields, "installation_binding"}
        and value["installation_binding"] == expected_binding
        and _digest(expected_binding)
        and value["persistent"] is True
        and type(value["recovered"]) is bool
        and type(value["runtime_id"]) is str
    )
    runtime_id = UUID(value["runtime_id"])
    require(runtime_id.version == 4 and str(runtime_id) == value["runtime_id"])
    _validate_state(value["state"])
    return value


def make_export(view: dict, *, software_version: str) -> bytes:
    """Keep the complete observed Paper view without storing the token or binding."""
    require(_version(software_version))
    raw = canonical(
        {
            "schema": SCHEMA,
            **_NON_AUTHORITY,
            "software_version": software_version,
            "runtime_id_sha256": sha(view["runtime_id"].encode("ascii")),
            "installation_binding_sha256": sha(
                view["installation_binding"].encode("ascii")
            ),
            "recovered": view["recovered"],
            "durable_state_sha256": state_checksum(_validate_state(view["state"])),
            "state": view["state"],
        }
    )
    inspect_export(raw, expected_sha256=sha(raw))
    return raw


def inspect_export(raw: bytes, *, expected_sha256: str) -> dict[str, object]:
    """Read immutable bytes against an independent digest and rederive the ledger."""
    require(_digest(expected_sha256) and sha(raw) == expected_sha256)
    value = decode(raw, MAX_STATE_BYTES)
    require(
        type(value) is dict
        and set(value)
        == {
            "schema",
            *_NON_AUTHORITY,
            "software_version",
            "runtime_id_sha256",
            "installation_binding_sha256",
            "recovered",
            "durable_state_sha256",
            "state",
        }
        and value["schema"] == SCHEMA
        and all(
            value[name] is expected
            if type(expected) is bool
            else value[name] == expected
            for name, expected in _NON_AUTHORITY.items()
        )
        and _version(value["software_version"])
        and _digest(value["runtime_id_sha256"])
        and _digest(value["installation_binding_sha256"])
        and type(value["recovered"]) is bool
        and canonical(value) == raw
    )
    state = _validate_state(value["state"])
    require(value["durable_state_sha256"] == state_checksum(state))
    return {
        "status": "PAPER_EXPORT_VERIFIED",
        "export_sha256": expected_sha256,
        "durable_state_sha256": value["durable_state_sha256"],
        "software_version": value["software_version"],
        "order_count": len(state.orders),
        "position_count": len(state.positions),
        "pending_orders": state.account.pending_orders,
        "open_positions": state.account.open_positions,
        "closed_positions": state.account.closed_trades,
        **_NON_AUTHORITY,
    }
