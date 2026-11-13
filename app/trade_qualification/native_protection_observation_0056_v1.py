"""Match fixed-size exchange OCO rows to positions for stopped readback."""

import json
import re
from collections import Counter, defaultdict

from app.domain.source_primitives import sha
from app.trade_qualification.account_current_source_verifier import V7_INVENTORY_STREAMS
from app.trade_qualification.account_materializer import _direction

_EXPOSURE_ONLY = ["current_exposure_requires_protection_and_local_join"]
_MAX_REPORT_POSITIONS = 100
_CLIENT_ID = re.compile(r"[A-Za-z0-9]{1,32}")


def _position_key(row):
    return row["instId"], row["posSide"]


def _number(row, name):
    return next((item.value for item in row.numbers if item.path == name), None)


def _created(row):
    return next((item.value for item in row.source_times if item.path == "cTime"), None)


def _matches(position, position_row, algo, algo_row, identity, client_ids):
    quantity = _number(position_row, "pos")
    mark = _number(position_row, "markPx")
    size = _number(algo_row, "sz")
    stop = _number(algo_row, "slTriggerPx")
    target = _number(algo_row, "tpTriggerPx")
    position_created, algo_created = _created(position_row), _created(algo_row)
    direction = (
        _direction(position, identity["position_mode"], quantity) if quantity else None
    )
    client_id = algo.get("algoClOrdId")
    if (
        direction is None
        or any(value is None or value <= 0 for value in (mark, size, stop, target))
        or position_created is None
        or algo_created is None
        or algo_created < position_created
        or type(client_id) is not str
        or _CLIENT_ID.fullmatch(client_id) is None
        or client_ids[client_id] != 1
    ):
        return False
    return (
        position["instType"] == algo["instType"] == "SWAP"
        and position["ccy"] == algo.get("ccy") == identity["settlement_currency"]
        and position["mgnMode"] == algo.get("tdMode")
        and algo["ordType"] == "oco"
        and algo["state"] == "live"
        and algo["side"] == ("sell" if direction == "long" else "buy")
        and (position["posSide"] != "net" or algo.get("reduceOnly") == "true")
        and algo.get("closeFraction") in (None, "")
        and algo.get("triggerTime") in (None, "")
        and algo.get("actualSide") in (None, "")
        and algo.get("actualSz") in (None, "", "0")
        and algo.get("failCode") in (None, "")
        and algo.get("slTriggerPxType") == algo.get("tpTriggerPxType") == "mark"
        and algo.get("slOrdPx") == algo.get("tpOrdPx") == "-1"
        and size == quantity.copy_abs()
        and (stop < mark < target if direction == "long" else target < mark < stop)
    )


def observe_position_brackets(packet, source):
    """Return redacted evidence from a replayed packet and its V7 current receipt.

    A structural match conveys no attribution to a CTCC intent, no atomic snapshot
    and no authority to clear the stop or change exposure.
    """
    counts = Counter({stream: 0 for stream in V7_INVENTORY_STREAMS})
    positions, algos, locators = [], [], []
    for request_index, page in enumerate(packet.observations):
        stream = page.request.stream
        if stream not in counts:
            continue
        counts[stream] += len(page.rows)
        for ordinal, row in enumerate(page.rows):
            row_sha = sha(row.canonical_json.encode())
            locators.append(
                (stream, request_index, ordinal, page.receipt_sha256, row_sha)
            )
            if stream == "positions" or stream.startswith("algo_"):
                inventory = positions if stream == "positions" else algos
                inventory.append((json.loads(row.canonical_json), row, row_sha))

    receipt_locators = [
        (
            item["stream"],
            item["request_index"],
            item["row_ordinal"],
            item["page_receipt_sha256"],
            item["row_sha256"],
        )
        for item in source["current_rows"]
        if item["stream"] in counts
    ]
    if counts != source["inventory_row_counts"] or locators != receipt_locators:
        raise ValueError("db0056_restart_bracket_source_changed")

    positions_per_key = Counter(_position_key(raw) for raw, _, _ in positions)
    algos_by_key = defaultdict(list)
    client_ids = Counter()
    for raw, row, row_sha in algos:
        algos_by_key[_position_key(raw)].append((raw, row, row_sha))
        if type(raw.get("algoClOrdId")) is str:
            client_ids[raw["algoClOrdId"]] += 1

    observed = []
    match_count = 0
    for position, position_row, position_sha in positions:
        key = _position_key(position)
        candidates = algos_by_key.get(key, ())
        algo_sha = None
        if (
            source["blocking_reasons"] == _EXPOSURE_ONLY
            and positions_per_key[key] == len(candidates) == 1
        ):
            algo, algo_row, candidate_sha = candidates[0]
            if _matches(
                position, position_row, algo, algo_row, source["identity"], client_ids
            ):
                algo_sha = candidate_sha
                match_count += 1
        if len(observed) < _MAX_REPORT_POSITIONS:
            observed.append(
                {
                    "position_row_sha256": position_sha,
                    "pending_oco_row_sha256": algo_sha,
                    "status": "OBSERVED_FULL_BRACKET_UNATTRIBUTED"
                    if algo_sha
                    else "UNKNOWN",
                }
            )
    return {
        "structural_match_count": match_count,
        "position_rows_omitted": len(positions) - len(observed),
        "positions": observed,
    }
