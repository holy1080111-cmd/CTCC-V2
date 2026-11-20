"""Fresh DATA-only original OCO monitoring; never submit, close, retry or Arm."""

import sys

from app.database.repositories import native_postsubmit_v1 as journal
from app.database.repositories import native_protective_hold_v1 as repository
from app.trade_qualification import native_continuation_guard_v1 as guard
from app.trade_qualification import native_postsubmit_reconciliation_v1 as post
from app.trade_qualification import native_protective_exit_facts_v1 as facts


async def _collect_original_protective_terminal_v1(state, history):
    target = state["target"]
    expected, _, old_algo = facts.original_protection(
        target.facts, history.original_observations
    )
    entry_query = {"instId": expected["instId"], "clOrdId": expected["clOrdId"]}
    algo_query = {"algoId": old_algo["algoId"]}
    await post._get(state, "config_before", "/api/v5/account/config", {})
    entry = await post._get(
        state, "entry_order_before", "/api/v5/trade/order", entry_query
    )
    post._need(len(entry) == 1)
    algo = await post._get(
        state, "protection_before", "/api/v5/trade/order-algo", algo_query
    )
    post._need(len(algo) == 1 and algo[0].get("state") == "effective")
    children = algo[0].get("ordIdList")
    post._need(
        type(children) is list
        and len(children) == 1
        and type(children[0]) is str
        and children[0].isdigit()
        and 0 < len(children[0]) <= 40
        and int(children[0]) > 0
    )
    child_query = {"instId": expected["instId"], "ordId": children[0]}
    child = await post._get(
        state, "exit_order_before", "/api/v5/trade/order", child_query
    )
    post._need(len(child) == 1)
    for prefix, order_id in (("entry", entry[0]["ordId"]), ("exit", children[0])):
        query = {"instType": "SWAP", "instId": expected["instId"], "ordId": order_id}
        for suffix, path in (
            ("recent", "/api/v5/trade/fills"),
            ("history", "/api/v5/trade/fills-history"),
        ):
            await post._pages(state, prefix + "_fills_" + suffix, path, query, "billId")
    for role, path in (
        ("balance", "/api/v5/account/balance"),
        ("positions", "/api/v5/account/positions"),
        ("account_risk", "/api/v5/account/account-position-risk"),
    ):
        await post._get(state, role, path, {})
    await post._pages(
        state, "pending_orders", "/api/v5/trade/orders-pending", {}, "ordId"
    )
    for kind in post.read.ALGO_TYPES:
        await post._pages(
            state,
            "algo_" + kind,
            "/api/v5/trade/orders-algo-pending",
            {"ordType": kind},
            "algoId",
        )
    await post._get(
        state,
        "leverage",
        "/api/v5/account/leverage-info",
        {"instId": expected["instId"], "mgnMode": "isolated"},
    )
    entry_time = min(
        int(row["fillTime"])
        for role, page in history.original_observations
        if role in ("fills_recent", "fills_history")
        for row in post.facts.rows(page)
    )
    cutoff = facts.chronology._milliseconds(state["observations"][0][1].started_at)
    window = {"begin": str(entry_time), "end": str(cutoff)}
    for suffix, path in (
        ("recent", "/api/v5/trade/fills"),
        ("history", "/api/v5/trade/fills-history"),
    ):
        await post._pages(
            state,
            "exit_all_fills_" + suffix,
            path,
            {"instType": "SWAP", **window},
            "billId",
        )
    for suffix, path in (
        ("recent", "/api/v5/account/bills"),
        ("archive", "/api/v5/account/bills-archive"),
    ):
        await post._pages(state, "exit_bills_" + suffix, path, window, "billId")
    await post._get(state, "entry_order_after", "/api/v5/trade/order", entry_query)
    await post._get(state, "exit_order_after", "/api/v5/trade/order", child_query)
    await post._get(state, "protection_after", "/api/v5/trade/order-algo", algo_query)
    await post._get(state, "config_after", "/api/v5/account/config", {})


async def reconcile_owned_protected_history_v1(state):
    """Called only by the original LIVE native recovery source and SQL history."""
    _NATIVE_GUARD()
    post._source(state)
    post._need(
        state["kind"] == "recovery" and post._LIVE.get(state["binding"]) is state
    )
    history = state["normal_protected_history"]
    post._need(type(history) is repository.NativeProtectedHistoryV1)
    expected, _, original_algo = facts.original_protection(
        state["target"].facts, history.original_observations
    )
    with post._new_read_context(state):
        state["phase"] = "fresh_original_protection_selector"
        await post._get(
            state, "protected_selector_config", "/api/v5/account/config", {}
        )
        selected = await post._get(
            state,
            "protected_selector_algo",
            "/api/v5/trade/order-algo",
            {"algoId": original_algo["algoId"]},
        )
        post._need(
            len(selected) == 1
            and selected[0].get("algoId") == original_algo["algoId"]
            and selected[0].get("instId") == expected["instId"]
        )
        if selected[0].get("state") == "live":
            state["phase"] = "fresh_original_protected_open_inventory"
            post._need(state["target"].current_state == "uncertain")
            await post._collect(state, state["target"])
        else:
            post._need(selected[0].get("state") == "effective")
            state["phase"] = "fresh_original_protective_exit_inventory"
            await _collect_original_protective_terminal_v1(state, history)
    state["phase"] = "durable_original_protection_raw_pages"
    await journal.retain_native_readback_v1(state["binding"])
    if selected[0]["state"] == "live":
        return await repository.append_owned_protected_open_readback_v1(
            state["binding"]
        )
    state["protective_assessment"] = facts.assess_original_protective_exit_v1(
        state["target"].facts,
        history.original_observations,
        state["durable_observations"],
    )
    await repository.append_owned_protective_assessment_v1(state["binding"])
    if state["target"].current_state == "reconciled_flat":
        result = await repository.replay_owned_protective_exit_v1(state["binding"])
    else:
        result = await repository.finish_owned_protective_exit_v1(state["binding"])
        terminal = state["protective_durable_assessment"]
        state["verified_normal_protected_recovery"] = (
            result,
            terminal[1],
            terminal[2],
            terminal[3],
        )
    from app.database.repositories import (
        native_protective_reporting_owner_v58 as reporting,
    )

    try:
        await reporting.prepare_owned_protective_report_writer_v58(
            state, result=result, key=post._PROTECTIVE_REPORT_KEY_V58
        )
    except Exception:  # noqa: BLE001 -- a report failure cannot undo a committed protective exit or prevent read-resource retirement.
        state["protective_report_status"] = "pending"
    return result


_NATIVE_GUARD = guard.install_native_guards_v1(
    sys.modules[__name__],
    ("reconcile_owned_protected_history_v1",),
    dependencies=(journal, repository, post, facts),
)
