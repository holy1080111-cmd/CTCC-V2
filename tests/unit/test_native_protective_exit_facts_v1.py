"""Declared synthetic OCO DATA; no venue, source admission or SQL permission."""

import copy
from dataclasses import replace
from datetime import timedelta
from urllib.parse import urlencode, urlsplit

import pytest

from app.domain import native_protective_hold_schema_v58 as schema
from app.domain.source_primitives import canonical, decode
from app.exchange.okx import native_reconciliation_observation_v1 as read
from app.trade_qualification import native_close_contracts_v1 as clock
from app.trade_qualification import native_protective_exit_facts_v1 as exit_facts
from app.trade_qualification import native_reconciliation_facts_v1 as facts
from tests.unit.test_native_close_final_facts_v1 import terminal
from tests.unit.test_native_ledger_contracts_v2 import (
    synthetic as synthetic,  # noqa: PLC0414 -- pytest fixture reexport.
)
from tests.unit.test_native_reconciliation_facts_v1 import (
    target as target,  # noqa: PLC0414 -- pytest fixture reexport.
)


def protective_terminal(target, *, original=None):
    original, _, _, old = terminal(target, original=original)
    entry = next(
        facts.rows(page)[0] for role, page in old if role == "entry_order_before"
    )
    child = next(
        facts.rows(page)[0] for role, page in old if role == "close_order_before"
    )
    entry_time = min(
        int(row["fillTime"])
        for role, page in original
        if role in {"fills_recent", "fills_history"}
        for row in facts.rows(page)
    )
    base = old[0][1].started_at
    end = str(clock._milliseconds(base))
    sequence = []
    for role, page in old:
        rows = copy.deepcopy(facts.rows(page))
        path, query = page.path, dict(page.parameters)
        if role == "original_protection_detail":
            for row in rows:
                row.update(
                    state="effective",
                    actualSide="tp",
                    failCode="",
                    ordIdList=[child["ordId"]],
                    ordId=child["ordId"],
                    subAlgoIdList=[],
                    actualPx=child["avgPx"],
                    actualSz=child["sz"],
                    triggerTime=str(int(child["cTime"]) - 1),
                )
            sequence.append(("protection_before", path, query, rows))
            continue
        if role == "bills":
            sequence.extend(
                (
                    name,
                    route,
                    {
                        "begin": str(entry_time),
                        "end": end,
                        "limit": "100",
                    },
                    [],
                )
                for name, route in (
                    ("exit_bills_recent", "/api/v5/account/bills"),
                    ("exit_bills_archive", "/api/v5/account/bills-archive"),
                )
            )
            continue
        role = (
            role.replace("close_order", "exit_order")
            .replace("close_fills", "exit_fills")
            .replace("final_fills_recent_all", "exit_all_fills_recent")
            .replace("final_fills_history_all", "exit_all_fills_history")
        )
        if role.startswith("exit_order"):
            query = {"instId": entry["instId"], "ordId": child["ordId"]}
            for row in rows:
                row["algoId"] = "8001"
        if role.startswith("exit_all_fills"):
            query = {key: value for key, value in query.items() if key != "instId"}
            query.update(begin=str(entry_time), end=end)
        sequence.append((role, path, query, rows))
    protection = next(item for item in sequence if item[0] == "protection_before")
    sequence.insert(len(sequence) - 1, ("protection_after", *protection[1:]))
    configuration = next(item for item in sequence if item[0] == "config_before")
    sequence[0:0] = [
        ("protected_selector_config", *configuration[1:]),
        ("protected_selector_algo", *protection[1:]),
    ]
    result = []
    for index, (role, path, query, rows) in enumerate(sequence):
        started, mono = (
            base + timedelta(milliseconds=index * 10),
            old[0][1].started_monotonic_ns + index * 10_000_000,
        )
        result.append(
            (
                role,
                read.DemoReconciliationObservationV1(
                    path,
                    read.checked_query(path, query),
                    target.origin + path + ("?" + urlencode(query) if query else ""),
                    "f" * 64,
                    "d" * 64,
                    started,
                    mono,
                    started + timedelta(milliseconds=1),
                    mono + 1_000_000,
                    started + timedelta(milliseconds=2),
                    mono + 2_000_000,
                    200,
                    (),
                    canonical({"code": "0", "data": rows}),
                    "b" * 64,
                    urlsplit(target.origin).hostname,
                ),
            )
        )
    return original, tuple(result)


def mutate(observations, role, key, value):
    result = []
    for current, page in observations:
        if current == role:
            body = decode(page.raw_body)
            for row in body["data"]:
                row[key] = value
            page = replace(page, raw_body=canonical(body))
        result.append((current, page))
    return tuple(result)


def test_original_oco_child_fill_is_data_and_preserves_unknown_metrics(target):
    original, observed = protective_terminal(target)
    result = exit_facts.assess_original_protective_exit_v1(target, original, observed)
    document = decode(result.receipt_json)
    assert document["classification"] == "original_oco_full_exit_flat"
    assert (
        document["metrics_complete"] is False
        and document["mfe"] is document["mae"] is None
    )
    assert (
        result.execution_authority
        is result.order_retry_authority
        is result.release_authority
        is False
    )


@pytest.mark.parametrize(
    "role,key,value",
    [
        ("protection_before", "algoId", "9999"),
        ("protection_before", "state", "canceled"),
        ("protection_before", "actualSide", ""),
        ("protection_before", "tpTriggerPxType", "last"),
        ("protection_before", "ordIdList", ["10002", "10003"]),
        ("protection_before", "subAlgoIdList", ["20001"]),
        ("exit_order_before", "algoId", "9999"),
        ("exit_order_before", "posSide", "foreign"),
        ("exit_order_before", "state", "partially_filled"),
        ("exit_fills_recent", "fillSz", "0.001"),
        ("exit_fills_recent", "feeCcy", "BTC"),
        ("exit_all_fills_history", "ordId", "10099"),
    ],
)
def test_changed_split_partial_or_foreign_exit_keeps_unknown(target, role, key, value):
    original, observed = protective_terminal(target)
    with pytest.raises(facts.NativeReconciliationFactsError):
        exit_facts.assess_original_protective_exit_v1(
            target, original, mutate(observed, role, key, value)
        )


def test_missing_funding_chain_and_missing_full_account_are_not_empty(target):
    original, observed = protective_terminal(target)
    for omitted in ("exit_bills_archive", "positions", "exit_all_fills_recent"):
        with pytest.raises(facts.NativeReconciliationFactsError):
            exit_facts.assess_original_protective_exit_v1(
                target, original, tuple(row for row in observed if row[0] != omitted)
            )


def test_additive_schema_keeps_exact_ancestor_and_no_own_close_substitute():
    assert (
        schema.CURRENT_SQL.replace(schema._BRANCH, "", 1) == schema.ancestor.CURRENT_SQL
    )
    assert len(schema.statements()) == 3
    assert "ctcc.native_controlled_close_intent.v1" in schema.PREDICATE_SQL
    assert "EXCEPTION WHEN OTHERS THEN RETURN false" in schema.PREDICATE_SQL
    assert (
        "FOR q IN SELECT * FROM public.qualification_submission_outcomes"
        in schema.PREDICATE_SQL
    )
    assert "qualification_native_manifest_v55" in schema.PREDICATE_SQL
    assert "exit_all_fills_history" in schema.PREDICATE_SQL
    with pytest.raises(ValueError):
        schema.trigger_sql(schema.ancestor.CURRENT_SQL + " ")
