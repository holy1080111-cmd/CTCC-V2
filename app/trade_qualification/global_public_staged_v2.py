"""One native post-publication attempt, OHLC first and mutable sources last.

The open journal is never a complete market packet or a transferable source.
Only the original native Live public producer can resume its same-task stage.
"""

from __future__ import annotations

import asyncio
import os
import sys
from contextlib import ExitStack, asynccontextmanager
from datetime import timedelta
from threading import get_ident
from uuid import uuid4
from weakref import WeakKeyDictionary

from app.domain.native_clock import native_stamp
from app.domain.source_primitives import ClockStamp, canonical, sha, utc_from_ns
from app.public_market_source import public_runtime_journal as journal
from app.trade_qualification import candle_collector as candles
from app.trade_qualification import global_public_runtime_v1 as production
from app.trade_qualification import market_aux_collector as auxiliary
from app.trade_qualification import public_market_collector as public
from app.trade_qualification import public_source_runtime as runtime
from app.trade_qualification import ws_collector as websocket

_PRODUCER = "app.trade_qualification.native_live_public_source_v1"
_STAGES = WeakKeyDictionary()


class StagedGlobalPublicError(ValueError):
    """Fixed incomplete-source refusal."""


class _Stage:
    __slots__ = ("__weakref__",)

    def __new__(cls):
        raise StagedGlobalPublicError("global_public_stage_not_constructible")

    def __copy__(self):
        raise StagedGlobalPublicError("global_public_stage_not_transferable")

    def __deepcopy__(self, memo):
        raise StagedGlobalPublicError("global_public_stage_not_transferable")

    def __reduce_ex__(self, protocol):
        raise StagedGlobalPublicError("global_public_stage_not_transferable")


def _key(key):
    module = sys.modules.get(_PRODUCER)
    if (
        module is None
        or key is None
        or key is not getattr(module, "_STAGED_RUNTIME_KEY_V2", None)
    ):
        raise StagedGlobalPublicError("global_public_stage_owner_required")


def _state(stage, key):
    _key(key)
    state = _STAGES.get(stage) if type(stage) is _Stage else None
    task = asyncio.current_task()
    if state is None or task is not state["task"] or task.cancelling():
        raise StagedGlobalPublicError("global_public_stage_owner_required")
    runtime._sample(state["source"])
    return state


def _prefix(state):
    head, events = journal._replay_open_v3_prefix(state["attempt"])
    if head != state["prefix"] or events[-1]["kind"] != "ohlc_stage_complete":
        raise StagedGlobalPublicError("global_public_stage_prefix_changed")
    return head


@asynccontextmanager
async def begin_global_public_ohlc_v2(
    root,
    *,
    instrument_id,
    market_policy,
    original_publication,
    expires_at,
    expires_monotonic_ns,
    key,
):
    _key(key)
    policy = public._policy_copy(market_policy)
    task = asyncio.current_task()
    if task is None or task.cancelling():
        raise asyncio.CancelledError
    first = native_stamp()
    expiration = min(expires_at, utc_from_ns(first["utc_ns"]) + timedelta(seconds=30))
    remaining = min(30, (expires_monotonic_ns - first["monotonic_ns"]) / 1_000_000_000)
    if remaining <= 0:
        raise StagedGlobalPublicError("global_public_stage_expired")
    invocation_id = uuid4().hex
    plan = {
        "schema_version": journal.GLOBAL_STAGED_PLAN,
        "stage": "post_g12_staged_public",
        "invocation_id": invocation_id,
        "environment": journal.GLOBAL_ENVIRONMENT,
        "report_id": "initial-" + invocation_id,
        "instrument_id": instrument_id,
        "invocation_started": first,
        "expires_at": expiration.isoformat(),
        "rest_origin": journal.GLOBAL_REST_ORIGIN,
        "ws_origin": journal.GLOBAL_WS_ORIGIN,
        "global_route_sha256": journal.GLOBAL_ROUTE_SHA256,
        "policy_sha256": public._digest(policy),
        "original_publication": original_publication,
    }
    journal._validate_global_staged_plan(plan)
    source = runtime._Source(runtime._ISSUER)
    transport_state = {
        "invocation": object(),
        "pid": os.getpid(),
        "thread": get_ident(),
        "loop": asyncio.get_running_loop(),
        "parent": task,
        "last": first,
        "not_before": first,
        "clients": {},
        "client_roles": set(),
        "request_count": 0,
        "responses": [],
        "ws_claimed": False,
        "ws_context": None,
        "socket": None,
        "ws_records": [],
        "report_id": plan["report_id"],
        "instrument_id": instrument_id,
        "invocation_started": first,
        "publication_completed_at": None,
        "expires_at": expiration,
        "plan": plan,
        "policy": policy,
        "deadline": asyncio.get_running_loop().time() + remaining,
    }
    transport_state["clock"] = lambda: runtime._sample(source)
    runtime._SOURCES[source] = transport_state
    stage = object.__new__(_Stage)
    try:
        async with asyncio.timeout(remaining):
            with ExitStack() as journal_stack:
                attempt = journal_stack.enter_context(
                    journal._runtime_attempt(root, plan)
                )
                transport_state["attempt"] = attempt
                try:
                    observed = runtime._record_clock(
                        attempt,
                        "before",
                        runtime._observe_owned_runtime_clock(attempt, "before"),
                    )
                    if observed["outcome"] != "accepted":
                        raise StagedGlobalPublicError(
                            "global_public_stage_clock_rejected"
                        )
                    runtime._accept_clock_sample(transport_state, observed)
                    await runtime._fetch_owned_public_time_v3(source, "time_pre")
                    started_at = transport_state["clock"]()
                    collected = await public._http_capture(
                        candles.collect_candles,
                        policy.client_close_timeout_seconds,
                        _source=source,
                        _role="candles",
                        policy=policy.candles,
                        clock=transport_state["clock"],
                        report_id=plan["report_id"],
                        instrument_id=instrument_id,
                        barrier_completed_at=None,
                    )
                    encoded = canonical(
                        collected.model_dump(mode="json", round_trip=True)
                    )
                    runtime._sample(source)
                    completed = dict(transport_state["last"])
                    head = journal._append(
                        attempt,
                        "ohlc_stage_complete",
                        {"completed": completed, "candles_sha256": sha(encoded)},
                        encoded,
                    )
                    state = {
                        "task": task,
                        "source": source,
                        "attempt": attempt,
                        "plan": plan,
                        "prefix": head,
                        "candles": encoded,
                        "completed": completed,
                        "started_at": started_at,
                        "finished": False,
                        "journal_stack": journal_stack,
                    }
                    _STAGES[stage] = state
                    _prefix(state)
                    yield stage
                    if state["finished"] is not True:
                        raise StagedGlobalPublicError("global_public_stage_incomplete")
                except BaseException:
                    try:
                        journal._seal(attempt, success=False, stamp=native_stamp())
                    except Exception:  # noqa: BLE001, S110 -- keep the original durable tail
                        pass
                    raise
    finally:
        _STAGES.pop(stage, None)
        runtime._SOURCES.pop(source, None)
        runtime._V3_TIME_PHASES.pop(source, None)


def replay_global_public_ohlc_v2(stage, *, key):
    state = _state(stage, key)
    if state["finished"]:
        raise StagedGlobalPublicError("global_public_stage_spent")
    _prefix(state)
    directory = journal._state(state["attempt"])["directory"]
    files = tuple(
        (name, directory.read(name, journal.MAX_RAW))
        for name in sorted(directory.names())
    )
    if sum(len(raw) for _name, raw in files) > journal.MAX_TOTAL:
        raise StagedGlobalPublicError("global_public_stage_inventory_invalid")
    return (
        ClockStamp.model_validate(state["completed"]),
        candles.CollectedCandles.model_validate_json(state["candles"]),
        sha(canonical(state["plan"])),
        state["prefix"],
        files,
    )


async def complete_global_public_v2(
    stage, *, account_completed, source_revision_sha256, key
):
    state = _state(stage, key)
    _prefix(state)
    # Burn before starting any mutable source, including a failed/cancelled one.
    _STAGES.pop(stage, None)
    source, attempt = state["source"], state["attempt"]
    transport_state = runtime._state(source)
    journal._append(
        attempt,
        "account_stage_ready",
        {
            "completed": dict(account_completed),
            "source_revision_sha256": source_revision_sha256,
            "ohlc_prefix_sha256": state["prefix"],
        },
    )
    journal._replay_open_v3_prefix(attempt)
    transport_state["not_before"] = dict(account_completed)
    runtime._sample(source)
    policy, tasks = transport_state["policy"], {}
    common = {
        "clock": transport_state["clock"],
        "report_id": transport_state["report_id"],
        "instrument_id": transport_state["instrument_id"],
        "barrier_completed_at": None,
    }
    try:
        for name, collector in (
            ("quote", production._collect_global_quote),
            ("market_aux", auxiliary.collect_market_aux),
        ):
            tasks[name] = asyncio.create_task(
                public._http_capture(
                    collector,
                    policy.client_close_timeout_seconds,
                    _source=source,
                    _role=name,
                    policy=getattr(policy, name),
                    **common,
                )
            )
        tasks["ws"] = asyncio.create_task(
            websocket.collect_ws_reference(policy=policy.ws, _source=source, **common)
        )
    except BaseException:
        await public._join_component_tasks(tasks, _source=source, already_failed=True)
        raise
    components = await public._join_component_tasks(tasks, _source=source)
    packet = production.GlobalPublicComponentPacketV1(
        report_id=transport_state["report_id"],
        instrument_id=transport_state["instrument_id"],
        started_at=state["started_at"],
        completed_at=transport_state["clock"](),
        policy=policy,
        policy_sha256=transport_state["plan"]["policy_sha256"],
        candles=candles.CollectedCandles.model_validate_json(state["candles"]),
        **components,
    )
    packet = runtime._complete_owned_public_market_v3(
        source, packet, _owner=runtime._ISSUER
    )
    await runtime._fetch_owned_public_time_v3(source, "time_post")
    observed = runtime._record_clock(
        attempt, "after", runtime._observe_owned_runtime_clock(attempt, "after")
    )
    if observed["outcome"] != "accepted":
        raise StagedGlobalPublicError("global_public_stage_clock_rejected")
    runtime._accept_clock_sample(transport_state, observed)
    packet = runtime._check_bracket_packet_binding(
        transport_state,
        packet,
        expected_roles=("quote", "candles", "market_aux", "time_pre", "time_post"),
    )
    runtime._sample(source)
    encoded = packet.packet_json
    journal._append(attempt, "packet", {"bundle_sha256": sha(encoded)}, encoded)
    pin = journal._seal(
        attempt, success=True, stamp=transport_state["last"], packet_sha256=sha(encoded)
    )
    plan_raw, summary_raw, packet_raw = journal._sealed_readback(attempt)
    replayed = runtime._replay_captured_packet(plan_raw, summary_raw, packet_raw)
    if (
        packet_raw != encoded
        or replayed.packet_json != encoded
        or sha(summary_raw) != pin
    ):
        raise StagedGlobalPublicError("global_public_stage_readback_changed")
    state["finished"] = True
    # Retire the publisher lease before the outer producer independently reopens
    # this immutable complete journal. This never releases risk/entry authority.
    state["journal_stack"].close()
    return production.GlobalPublicCaptureV1(
        "initial_global_public_captured", sha(plan_raw), pin, replayed
    )
