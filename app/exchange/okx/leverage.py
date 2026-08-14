from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Iterable


def leverage_response_matches(
    rows: Iterable[dict[str, Any]],
    *,
    instrument_id: str,
    margin_mode: str,
    leverage: int,
    position_side: str,
) -> bool:
    """Validate the effective fields returned by OKX set-leverage.

    A successful top-level response alone is insufficient for an execution
    boundary: the response row must identify the same instrument, margin mode,
    position side, and leverage that the caller requested.  In net mode OKX
    may serialize ``posSide`` as either ``net`` or an empty value.
    """

    expected_leverage = Decimal(leverage)
    for row in rows:
        if str(row.get("instId") or "") != instrument_id:
            continue
        if str(row.get("mgnMode") or "") != margin_mode:
            continue
        actual_side = str(row.get("posSide") or "")
        if position_side == "net":
            if actual_side not in {"", "net"}:
                continue
        elif actual_side != position_side:
            continue
        try:
            actual_leverage = Decimal(str(row.get("lever") or ""))
        except InvalidOperation:
            continue
        if actual_leverage == expected_leverage:
            return True
    return False
