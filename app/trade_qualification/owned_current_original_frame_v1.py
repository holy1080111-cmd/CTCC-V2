"""Private current original-input handoff; not candidate or order authority.

Only the existing native V4 issuers can supply these original bytes. Public
DTOs and retained database bundles cannot construct a frame. Existing source,
history, portfolio, G12 and dispatch contracts remain unchanged.
"""

import asyncio
import os
from dataclasses import dataclass, field
from pathlib import Path
from threading import get_ident
from typing import Literal
from weakref import WeakKeyDictionary

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.repositories import public_runtime_bundle_custody as custody
from app.domain.native_clock import native_stamp
from app.domain.source_primitives import canonical, sha, utc_from_ns, validate_stamps
from app.exchange.okx.private_rest import (
    DemoPinnedReadSession,
    OkxDemoPrivateRestClient,
)
from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_current_source_v4 as account
from app.trade_qualification import account_native_runtime as account_native
from app.trade_qualification import data_v2
from app.trade_qualification import demo_market_source_v4 as market
from app.trade_qualification import original_candidate_precursor_v2 as precursor
from app.trade_qualification.account_runtime import ControlledDemoAccountSession

_HOOK_KEY = object()
_CAPTURES = WeakKeyDictionary()
_FRAMES = WeakKeyDictionary()


class CurrentOriginalFrameError(ValueError):
    """Fixed code only; raw source, credential and database errors stay private."""


def _deny():
    raise CurrentOriginalFrameError("current_original_frame_unavailable")


class _Opaque:
    __slots__ = ("__weakref__",)

    def __new__(cls):
        _deny()

    def __copy__(self):
        _deny()

    def __deepcopy__(self, memo):
        _deny()

    def __reduce_ex__(self, protocol):
        _deny()


class CurrentOriginalCaptureV1(_Opaque):
    __slots__ = ()

    def capture(self):
        """Burn synchronously, even if the returned coroutine never starts."""
        state = (
            _CAPTURES.pop(self, None)
            if type(self) is CurrentOriginalCaptureV1
            else None
        )
        if state is None:
            _deny()
        _context(state)
        return _capture(state)


class OwnedCurrentOriginalFrameV1(_Opaque):
    __slots__ = ()

    def close(self):
        """Discard only held local source ownership, never accepted evidence."""
        state = (
            _FRAMES.pop(self, None)
            if type(self) is OwnedCurrentOriginalFrameV1
            else None
        )
        if state is not None:
            _release(state)


@dataclass(frozen=True, slots=True, repr=False)
class CurrentOriginalInputsV1:
    """Internal measured operands; caller construction is not source ownership."""

    public_packet: object
    account_packet: object
    market: object
    quote: object
    reference: object
    g1: object
    precursor: object
    receipt_json: bytes
    account_complete: Literal[False] = field(default=False, init=False)
    account_revision_published: Literal[False] = field(default=False, init=False)
    candidate_qualified: Literal[False] = field(default=False, init=False)
    post_g12_eligible: Literal[False] = field(default=False, init=False)
    execution_authority: Literal[False] = field(default=False, init=False)


def _context(state):
    task = asyncio.current_task()
    if (
        task is None
        or task.cancelling()
        or state["task"] is not task
        or state["loop"] is not asyncio.get_running_loop()
        or state["pid"] != os.getpid()
        or state["thread"] != get_ident()
    ):
        _deny()
    state["read_session"].verify_current()
    if state["read_session"]._owner is not state["private_client"]:
        _deny()


def _database_pins(factory, custody_factory):
    if not account_native._configured_factory(factory):
        _deny()
    if (
        type(custody_factory) is not async_sessionmaker
        or set(vars(custody_factory)) != {"kw", "class_"}
        or custody_factory.class_ is not AsyncSession
        or type(custody_factory.kw) is not dict
        or not set(custody_factory.kw) <= {"bind", "autoflush", "expire_on_commit"}
        or any(
            name in custody_factory.kw and type(custody_factory.kw[name]) is not bool
            for name in ("autoflush", "expire_on_commit")
        )
        or not custody._native_engine(factory.kw["bind"])
        or not custody._native_engine(custody_factory.kw.get("bind"))
    ):
        _deny()
    return (
        factory,
        factory.kw["bind"],
        tuple(sorted(factory.kw.items())),
        custody_factory,
        custody_factory.kw["bind"],
        tuple(sorted(custody_factory.kw.items())),
    )


def _check_database(state):
    current = _database_pins(state["factory"], state["custody_factory"])
    pinned = state["database_pins"]
    if (
        any(current[index] is not pinned[index] for index in (0, 1, 3, 4))
        or current[2] != pinned[2]
        or current[5] != pinned[5]
        or state["repository"]._factory is not state["owned_custody_factory"]
        or tuple(sorted(state["owned_custody_factory"].kw.items())) != pinned[5]
    ):
        _deny()
    custody.RuntimeBundleCustodyRepository._check_factory(state["repository"])


def open_current_original_capture_v1(
    *,
    read_session,
    private_client,
    public_account_plan,
    current_account_plan,
    public_root,
    public_account_proof_root,
    account_proof_root,
    account_native_root,
    instrument_id,
    strategy,
    market_policy,
    session_factory,
    custody_session_factory,
):
    """Configure one capture without accepting any source or gate result.

    Two independent one-use sessions are created from one still-current runtime
    read lease. The account factory remains the configured exact factory; this
    API does not broaden the existing account V4 factory policy.
    """
    try:
        task = asyncio.current_task()
        if (
            task is None
            or task.cancelling()
            or type(read_session) is not DemoPinnedReadSession
            or type(private_client) is not OkxDemoPrivateRestClient
            or read_session._owner is not private_client
            or type(public_account_plan) is not capture.CurrentDemoAccountCapturePlanV7
            or type(current_account_plan) is not capture.CurrentDemoAccountCapturePlanV7
            or type(instrument_id) is not str
            or instrument_id not in market.REVIEWED_DEMO_INSTRUMENT_IDS
            or type(strategy) is not str
            or strategy not in precursor.STRATEGY_CATALOG
        ):
            _deny()
        read_session.verify_current()
        plans = tuple(
            capture._checked_plan(plan, capture.plan_sha256(plan))
            for plan in (public_account_plan, current_account_plan)
        )
        names = (
            "expected_uid",
            "expected_main_uid",
            "settlement_currency",
            "origin",
            "registration_region",
            "session_binding_id",
        )
        if (
            any(getattr(plans[0], name) != getattr(plans[1], name) for name in names)
            or plans[0].plan_id == plans[1].plan_id
            or plans[0].session_binding_id != read_session.binding_id
            or plans[0].registration_region != "global"
            or plans[0].settlement_currency != "USDT"
            or any(instrument_id not in plan.leverage_instrument_ids for plan in plans)
        ):
            _deny()
        roots = (
            public_root,
            public_account_proof_root,
            account_proof_root,
            account_native_root,
        )
        if (
            any(
                type(root) is not type(Path()) or not root.is_absolute()
                for root in roots
            )
            or len(set(roots)) != 4
            or any(a in b.parents for a in roots for b in roots if a != b)
        ):
            _deny()
        database_pins = _database_pins(session_factory, custody_session_factory)
        owned_factory = async_sessionmaker(
            class_=AsyncSession, **dict(database_pins[5])
        )
        repository = custody.RuntimeBundleCustodyRepository(owned_factory)
        sessions = tuple(
            ControlledDemoAccountSession.from_pinned_read_session(
                read_session=read_session,
                plan=plan,
                expected_plan_sha256=capture.plan_sha256(plan),
            )
            for plan in plans
        )
        selected = market.public._policy_copy(market_policy)
        token = object.__new__(CurrentOriginalCaptureV1)
        state = {
            "task": task,
            "loop": asyncio.get_running_loop(),
            "pid": os.getpid(),
            "thread": get_ident(),
            "read_session": read_session,
            "private_client": private_client,
            "sessions": sessions,
            "plans": plans,
            "plan_pins": tuple(capture.plan_sha256(plan) for plan in plans),
            "roots": roots,
            "instrument_id": instrument_id,
            "strategy": strategy,
            "policy": selected,
            "factory": session_factory,
            "custody_factory": custody_session_factory,
            "owned_custody_factory": owned_factory,
            "repository": repository,
            "database_pins": database_pins,
        }
        _check_database(state)
        _CAPTURES[token] = state
        return token
    except (CurrentOriginalFrameError, asyncio.CancelledError):
        raise
    except Exception:  # noqa: BLE001 -- no private config, source or DB details
        raise CurrentOriginalFrameError("current_original_frame_unavailable") from None


def _market_pins(public):
    return (
        public["bundle_raw"],
        public["bundle_sha256"],
        public["journal_sha256"],
        public["proof_sha256"],
        public["expires_at"],
        sha(canonical(public["plan"])),
    )


def _source_pins(state):
    private = state["account_state"]
    return (
        *_market_pins(state["market_state"]),
        private["bundle_raw"],
        private["bundle_sha256"],
        private["custody_id"],
        private["plan_sha256"],
        private["credential_pin"],
    )


def _release(state):
    held = state.get("market_state")
    if held is not None:
        market.operational._discard_owned_route_v4(held["v4_route_claim"])


def _replay(state, *, initial=False):
    _context(state)
    _check_database(state)
    if _market_pins(state["market_state"]) != state["market_pins"]:
        _deny()
    if any(
        capture.plan_sha256(session._plan) != pin
        for session, pin in zip(state["sessions"], state["plan_pins"], strict=True)
    ):
        _deny()
    if not initial and _source_pins(state) != state["source_pins"]:
        _deny()
    packet, context, public_now = market._replay_original_frame_v1(
        state["market_state"], key=_HOOK_KEY
    )
    account_replay, account_now = account._replay_original_frame_v1(
        state["account_state"], key=_HOOK_KEY
    )
    now = native_stamp()
    validate_stamps((public_now, account_now, now))
    at = utc_from_ns(now["utc_ns"])
    # A final sample cannot extend either original source deadline. Replay both
    # freshness predicates at this same later clock, without any additional IO.
    if at >= state["market_state"]["expires_at"]:
        _deny()
    market.public_market_context_v2(
        packet, expected_bundle_sha256=packet.bundle_sha256, evaluated_at=at
    )
    account._check_current(account_replay, now)
    if (
        account_replay.packet.plan_sha256 != state["plan_pins"][1]
        or account_replay.packet.plan.expected_uid != state["plans"][0].expected_uid
        or context.market.instrument_id != state["instrument_id"]
    ):
        _deny()
    deadline = min(state["market_state"]["expires_at"], account_replay.expires_at)
    created = at if initial else state["created_at"]
    if at >= deadline:
        _deny()
    policy = market.fixed_native_original_g1_policy()
    if sha(market.data._canonical(policy)) != market.DATA_POLICY_SHA256:
        _deny()
    args = {
        "strategy": state["strategy"],
        "expected_public_bundle_sha256": packet.bundle_sha256,
        "expected_account_plan_sha256": state["plan_pins"][1],
        "expected_account_packet_sha256": account_replay.reference.packet_sha256,
        "data_policy": policy,
        "expected_data_policy_sha256": market.DATA_POLICY_SHA256,
        "created_at": created,
        "service_deadline": deadline,
    }
    if initial:
        g1 = data_v2.evaluate_public_market_data_v2(
            packet,
            expected_bundle_sha256=packet.bundle_sha256,
            policy=policy,
            evaluated_at=created,
        )
        g1 = data_v2.verify_public_market_data_v2(
            g1,
            packet,
            expected_bundle_sha256=packet.bundle_sha256,
            policy=policy,
            evaluated_at=created,
        )
        replayed = precursor.derive_original_candidate_precursor_v2(
            packet, account_replay.packet, **args
        )
    else:
        g1 = state["g1"]
        if (
            type(g1) is not data_v2.DataQualificationResultV2
            or market.data._canonical(g1) != state["g1_raw"]
        ):
            _deny()
        # This independent raw replay itself re-evaluates and verifies G1.
        # Never refresh original created_at/event/deadline at handoff time.
        replayed = precursor.verify_original_candidate_precursor_v2(
            state["precursor"], packet, account_replay.packet, **args
        )
    if (
        market.decode(replayed.receipt_json)["g1"]["evaluation_sha256"]
        != g1.evaluation_sha256
    ):
        _deny()
    if not initial and (
        created != state["created_at"]
        or deadline != state["deadline"]
        or replayed.receipt_json != state["precursor_raw"]
    ):
        _deny()
    final = native_stamp()
    validate_stamps((now, final))
    final_at = utc_from_ns(final["utc_ns"])
    if final_at >= deadline:
        _deny()
    market.public_market_context_v2(
        packet, expected_bundle_sha256=packet.bundle_sha256, evaluated_at=final_at
    )
    account._check_current(account_replay, final)
    state["market_state"]["last"] = final
    state["account_state"]["last"] = final
    _context(state)
    _check_database(state)
    return packet, account_replay, context, g1, replayed, created, deadline


async def _capture(state):
    issued = False
    try:
        _context(state)
        _check_database(state)
        public_owner = await market.acquire_initial_demo_market_source_v4(
            state["roots"][0],
            account_proof_root=state["roots"][1],
            instrument_id=state["instrument_id"],
            market_policy=state["policy"],
            account_session=state["sessions"][0],
            private_client=state["private_client"],
            custody_repository=state["repository"],
        )
        # Move first, before a phase check can fail and orphan an issued owner.
        state["market_state"] = market._take_original_frame_v1(
            public_owner, key=_HOOK_KEY
        )
        state["market_pins"] = _market_pins(state["market_state"])
        _context(state)
        _check_database(state)
        private_owner = await account.acquire_initial_demo_account_source_v4(
            account_session=state["sessions"][1],
            private_client=state["private_client"],
            session_factory=state["factory"],
            private_proof_root=state["roots"][2],
            native_proof_root=state["roots"][3],
            custody_repository=state["repository"],
        )
        state["account_state"] = account._take_original_frame_v1(
            private_owner, key=_HOOK_KEY
        )
        state["source_pins"] = _source_pins(state)
        *_, g1, result, created, deadline = _replay(state, initial=True)
        state.update(
            created_at=created,
            deadline=deadline,
            precursor_raw=result.receipt_json,
            precursor=result,
            g1=g1,
            g1_raw=market.data._canonical(g1),
        )
        frame = object.__new__(OwnedCurrentOriginalFrameV1)
        _FRAMES[frame] = state
        issued = True
        return frame
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 -- no private source or native/DB error text
        raise CurrentOriginalFrameError("current_original_frame_unavailable") from None
    finally:
        if not issued:
            _release(state)


def consume_current_original_inputs_v1(frame):
    """One-use internal source handoff; any failure burns without retry."""
    state = (
        _FRAMES.pop(frame, None) if type(frame) is OwnedCurrentOriginalFrameV1 else None
    )
    if state is None:
        _deny()
    try:
        packet, private, context, g1, result, created, deadline = _replay(state)
        raw = canonical(
            {
                "schema_version": "ctcc.owned_current_original_inputs.v1",
                "stage": "current_original_inputs_ready",
                "public_bundle_sha256": state["market_state"]["bundle_sha256"],
                "public_packet_sha256": packet.bundle_sha256,
                "account_bundle_sha256": state["account_state"]["bundle_sha256"],
                "account_packet_sha256": private.reference.packet_sha256,
                "g1_sha256": g1.evaluation_sha256,
                "precursor_sha256": sha(result.receipt_json),
                "created_at": created.isoformat(),
                "deadline": deadline.isoformat(),
                "account_complete": False,
                "account_revision_published": False,
                "candidate_qualified": False,
                "g12_published": False,
                "post_g12_eligible": False,
                "execution_authority": False,
            }
        )
        return CurrentOriginalInputsV1(
            packet,
            private.packet,
            context.market,
            context.quote,
            context.reference,
            g1,
            result,
            raw,
        )
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 -- a burned frame cannot be reconstituted
        raise CurrentOriginalFrameError("current_original_frame_unavailable") from None
    finally:
        _release(state)


def _base_prefix_key_v1(key):
    from app.trade_qualification.owned_current_original_prefix_v1 import _FRAME_KEY

    if key is not _FRAME_KEY:
        _deny()


def _take_original_base_prefix_v1(frame, *, key):
    """Move, never copy, the original frame into its fixed private continuation."""
    _base_prefix_key_v1(key)
    state = (
        _FRAMES.pop(frame, None) if type(frame) is OwnedCurrentOriginalFrameV1 else None
    )
    if state is None:
        _deny()
    try:
        return state, _replay(state)
    except BaseException:
        _release(state)
        raise


def _replay_original_base_prefix_v1(state, *, key):
    _base_prefix_key_v1(key)
    return _replay(state)


def _check_original_base_prefix_v1(state, replay, *, key):
    """Check current native ownership/freshness after math, without refreshing IO.

    The raw inputs were independently replayed by the preceding private hook.
    Compare the retained original bytes/pins and recheck the original owners at
    the later native stamp. This does not repeat identical G1/precursor math.
    """
    _base_prefix_key_v1(key)
    _context(state)
    _check_database(state)
    if _source_pins(state) != state["source_pins"] or any(
        capture.plan_sha256(session._plan) != pin
        for session, pin in zip(state["sessions"], state["plan_pins"], strict=True)
    ):
        _deny()
    public, private, _, g1, original, created, deadline = replay
    if (
        created != state["created_at"]
        or deadline != state["deadline"]
        or market.data._canonical(g1) != state["g1_raw"]
        or original.receipt_json != state["precursor_raw"]
        or original.intent is None
    ):
        _deny()
    market._check_owner(state["market_state"])
    market.operational._owned_route_state_v4(
        state["market_state"]["v4_route_claim"],
        invocation=state["market_state"]["invocation"],
        owner_task=state["market_state"]["parent"],
    )
    account._check_owner(state["account_state"])
    now = native_stamp()
    validate_stamps(
        (state["market_state"]["last"], state["account_state"]["last"], now)
    )
    at = utc_from_ns(now["utc_ns"])
    if at >= min(deadline, original.intent.expires_at):
        _deny()
    market.public_market_context_v2(
        public, expected_bundle_sha256=public.bundle_sha256, evaluated_at=at
    )
    account._check_current(private, now)
    state["market_state"]["last"] = now
    state["account_state"]["last"] = now
    _context(state)
    _check_database(state)


def _release_original_base_prefix_v1(state, *, key):
    _base_prefix_key_v1(key)
    _release(state)
