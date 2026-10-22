"""Native account-first current Demo market acquisition and one-use G1 handoff.

Positive current-source authenticity requires the actual native account/public
issuers and restricted durable raw custody. Saved bundles are audit/replay data.
Nothing here certifies account completeness, historical availability or orders.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from threading import get_ident
from typing import Literal
from weakref import WeakKeyDictionary

from app.database.repositories.public_runtime_bundle_custody import (
    RuntimeBundleCustodyRepository,
    _consume_runtime_bundle_custody_receipt,
)
from app.domain.native_clock import native_stamp
from app.domain.source_primitives import (
    PUBLIC_V4_CURRENT_SOURCE_POLICY_SHA256,
    canonical,
    decode,
    demo_public_v2_policy_sha256,
    sha,
    utc_from_ns,
    validate_stamps,
)
from app.exchange.okx.symbols import REVIEWED_DEMO_INSTRUMENT_IDS
from app.public_market_source.public_runtime_custody_v4 import (
    PROOF_SCHEMA,
    freeze_runtime_bundle_v4,
    replay_runtime_bundle_v4,
)
from app.trade_qualification import data, data_v2
from app.trade_qualification import demo_operational_route_v4 as operational
from app.trade_qualification import public_market_collector_v2 as public
from app.trade_qualification import public_source_runtime as runtime
from app.trade_qualification.market_bridge_v2 import public_market_context_v2
from app.trade_qualification.native_original_g1_policy_v1 import (
    DATA_POLICY_SHA256,
    fixed_native_original_g1_policy,
)

_SCOPES = WeakKeyDictionary()
_CURRENT = WeakKeyDictionary()


class CurrentMarketSourceV4Error(ValueError):
    """Fixed public-safe codes; account and transport content remain private."""


class _InitialScopeV4:
    __slots__ = ("__weakref__",)


class OwnedCurrentMarketSourceV4:
    """Only the registered issuing invocation can consume this native source."""

    __slots__ = ("__weakref__",)

    def __new__(cls):
        raise CurrentMarketSourceV4Error("public_v4_source_not_constructible")

    def __copy__(self):
        raise CurrentMarketSourceV4Error("public_v4_source_not_transferable")

    def __deepcopy__(self, memo):
        raise CurrentMarketSourceV4Error("public_v4_source_not_transferable")

    def __reduce_ex__(self, protocol):
        raise CurrentMarketSourceV4Error("public_v4_source_not_transferable")


@dataclass(frozen=True, slots=True, repr=False)
class CurrentMarketEvaluationV4:
    """Source-derived domain/G1 result; this DTO alone grants no permission."""

    market: object
    quote: object
    reference: object
    g1: data_v2.DataQualificationResultV2
    receipt_json: bytes
    source_authenticity_verified: Literal[True] = field(default=True, init=False)
    durable_raw_readback_verified: Literal[True] = field(default=True, init=False)
    account_complete: Literal[False] = field(default=False, init=False)
    historical_availability_verified: Literal[False] = field(default=False, init=False)
    execution_authority: Literal[False] = field(default=False, init=False)
    registration_site: Literal["UNKNOWN"] = field(default="UNKNOWN", init=False)

    @property
    def receipt_sha256(self):
        return sha(self.receipt_json)


def _check_owner(state):
    task = asyncio.current_task()
    if (
        task is None
        or task.cancelling()
        or task is not state["parent"]
        or asyncio.get_running_loop() is not state["loop"]
        or os.getpid() != state["pid"]
        or get_ident() != state["thread"]
    ):
        raise CurrentMarketSourceV4Error("public_v4_owner_unavailable")


def _take_initial_scope_v4(scope):
    state = _SCOPES.pop(scope, None) if type(scope) is _InitialScopeV4 else None
    if state is None:
        raise CurrentMarketSourceV4Error("public_v4_scope_unavailable")
    _check_owner(state)
    return state


def _route_proof(state):
    document = decode(state["raw"], 8192)
    row = document["data"][0]
    return {
        "schema_version": PROOF_SCHEMA,
        "account_plan_sha256": state["plan_sha256"],
        "credential_session_sha256": sha(
            canonical(
                {
                    "session_binding_id": state["session_binding_id"],
                    "account_plan_sha256": state["plan_sha256"],
                }
            )
        ),
        "account_identity": {
            name: row[name] for name in ("uid", "mainUid", "acctLv", "posMode")
        },
        "route_region": state["route"].registration_region,
        "rest_origin": state["route"].rest_origin,
        "ws_origin": state["route"].ws_origin,
        "registration_site": "UNKNOWN",
        "config_raw_hex": state["raw"].hex(),
        "config_raw_sha256": sha(state["raw"]),
        "config_canonical_sha256": sha(canonical(document)),
        "tls_peer_sha256": state["peer_sha256"],
        **{
            name: state[name]
            for name in (
                "request_start",
                "headers_received",
                "body_completed",
                "validated",
                "consumed",
            )
        },
    }


async def acquire_initial_demo_market_source_v4(
    public_root,
    *,
    account_proof_root,
    instrument_id,
    market_policy,
    account_session,
    private_client,
    custody_repository,
):
    """Capture exactly once, retain all raw bytes, then issue a current handle.

    No supplied account observation, route, clock, HTTP client, raw packet,
    source-auth flag or receipt can replace either native issuer. Custody role
    preflight happens before credentialed or public exchange I/O.
    """
    if (
        type(public_root) is not type(Path())
        or type(account_proof_root) is not type(Path())
        or not public_root.is_absolute()
        or not account_proof_root.is_absolute()
        or public_root == account_proof_root
        or public_root in account_proof_root.parents
        or account_proof_root in public_root.parents
    ):
        raise CurrentMarketSourceV4Error(
            "public_v4_distinct_private_proof_root_required"
        )
    try:
        with runtime._reopen_runtime_journal(public_root) as public_directory:
            if public_directory.names():
                raise CurrentMarketSourceV4Error("public_v4_public_root_not_empty")
        with runtime._native_recheck_root(account_proof_root) as proof_directory:
            if proof_directory.names():
                raise CurrentMarketSourceV4Error(
                    "public_v4_private_proof_root_not_empty"
                )
            return await _acquire_initial_demo_market_source_v4(
                public_root,
                proof_directory=proof_directory,
                instrument_id=instrument_id,
                market_policy=market_policy,
                account_session=account_session,
                private_client=private_client,
                custody_repository=custody_repository,
            )
    except (CurrentMarketSourceV4Error, asyncio.CancelledError):
        raise
    except Exception:  # noqa: BLE001 -- no private paths or raw account content
        raise CurrentMarketSourceV4Error(
            "public_v4_private_proof_storage_unavailable"
        ) from None


async def _acquire_initial_demo_market_source_v4(
    public_root,
    *,
    proof_directory,
    instrument_id,
    market_policy,
    account_session,
    private_client,
    custody_repository,
):
    if (
        type(public_root) is not type(Path())
        or not public_root.is_absolute()
        or type(instrument_id) is not str
        or instrument_id not in REVIEWED_DEMO_INSTRUMENT_IDS
        or type(custody_repository) is not RuntimeBundleCustodyRepository
    ):
        raise CurrentMarketSourceV4Error("public_v4_inputs_invalid")
    selected = public._policy_copy(market_policy)
    task = asyncio.current_task()
    if task is None or task.cancelling():
        raise asyncio.CancelledError
    route_claim = scope = None
    retained = False
    failure_code = "public_v4_custody_deployment_unavailable"
    try:
        async with asyncio.timeout(5):
            await custody_repository.verify_deployment()
        failure_code = "public_v4_account_route_unavailable"
        observation = (
            await operational._probe_initial_demo_operational_route_retained_v4(
                account_session, private_client, proof_directory=proof_directory
            )
        )
        invocation = object()
        route_claim = operational._claim_initial_route_v4(
            observation,
            account_session,
            private_client,
            invocation=invocation,
        )
        account = operational._owned_route_state_v4(
            route_claim, invocation=invocation, owner_task=task
        )
        proof = _route_proof(account)
        proof_pin = sha(canonical(proof))
        # Preserve this accepted private source if a later market capture or
        # custody operation fails. Its root is distinct from the public log.
        failure_code = "public_v4_private_proof_storage_unavailable"
        proof_raw = canonical(proof)
        proof_directory.publish("operational-route-proof.v4.json", proof_raw)
        if proof_directory.read("operational-route-proof.v4.json", 32768) != proof_raw:
            raise CurrentMarketSourceV4Error("public_v4_private_proof_readback_changed")
        route = account["route"]
        started = native_stamp()
        validate_stamps((account["consumed"], started))
        if (
            started["utc_ns"] <= account["consumed"]["utc_ns"]
            or started["monotonic_ns"] <= account["consumed"]["monotonic_ns"]
        ):
            raise CurrentMarketSourceV4Error("public_v4_initial_chronology_invalid")
        expires = utc_from_ns(started["utc_ns"]) + timedelta(
            seconds=selected.total_timeout_seconds
        )
        invocation_id = uuid.uuid4().hex
        report = "initial-" + invocation_id
        plan = {
            "stage": "initial_public",
            "invocation_id": invocation_id,
            "environment": "demo",
            "report_id": report,
            "instrument_id": instrument_id,
            "invocation_started": started,
            "expires_at": expires.isoformat(),
            "rest_origin": route.rest_origin,
            "ws_origin": route.ws_origin,
            "registration_region": route.registration_region,
            "registration_region_authenticated": False,
            "registration_site": "UNKNOWN",
            "account_plan_sha256": account["plan_sha256"],
            "credential_session_sha256": proof["credential_session_sha256"],
            "operational_route_proof_sha256": proof_pin,
            "current_source_policy_sha256": PUBLIC_V4_CURRENT_SOURCE_POLICY_SHA256,
            "demo_public_origin_policy_sha256": demo_public_v2_policy_sha256(
                route.registration_region
            ),
            "policy_sha256": public._policy_digest(selected, route),
            **public._plan_pins(route),
        }
        owner = {
            "invocation": invocation,
            "parent": task,
            "loop": asyncio.get_running_loop(),
            "pid": os.getpid(),
            "thread": get_ident(),
            "plan": plan,
            "report_id": report,
            "instrument_id": instrument_id,
            "invocation_started": started,
            "publication_completed_at": None,
            "expires_at": expires,
            "v4_route_claim": route_claim,
            "v4_route_proof_sha256": proof_pin,
        }
        scope = _InitialScopeV4()
        _SCOPES[scope] = owner
        failure_code = "public_v4_full_capture_unavailable"
        carrier = await runtime._capture_initial_public_v4(scope, selected, public_root)
        packet, journal_pin, observed = runtime._consume_initial_public_capture_v4(
            carrier, invocation
        )
        with runtime._reopen_runtime_journal(public_root) as directory:
            if sha(directory.read("summary.json", runtime.MAX_RAW)) != journal_pin:
                raise CurrentMarketSourceV4Error("public_v4_native_journal_changed")
            bundle_raw = freeze_runtime_bundle_v4(directory, proof)
        bundle_pin = sha(bundle_raw)
        failure_code = "public_v4_custody_commit_or_readback_unavailable"
        remaining = (expires - utc_from_ns(native_stamp()["utc_ns"])).total_seconds()
        if remaining <= 0:
            raise CurrentMarketSourceV4Error("public_v4_source_expired")
        async with asyncio.timeout(min(5, remaining)):
            receipt = await custody_repository.seal_bundle(
                bundle_raw=bundle_raw,
                expected_sha256=bundle_pin,
                proof_envelope_sha256=proof_pin,
            )
        readback = _consume_runtime_bundle_custody_receipt(
            receipt,
            expected_bundle_sha256=bundle_pin,
            expected_proof_envelope_sha256=proof_pin,
        )
        if readback.bundle_raw != bundle_raw:
            raise CurrentMarketSourceV4Error("public_v4_custody_readback_changed")
        directory, _, _, _ = replay_runtime_bundle_v4(
            readback.bundle_raw, expected_sha256=bundle_pin
        )
        if sha(directory.read("summary.json", runtime.MAX_RAW)) != journal_pin:
            raise CurrentMarketSourceV4Error("public_v4_custody_journal_changed")
        _, _, replayed = runtime.replay_public_runtime(
            directory,
            expected_plan_sha256=sha(directory.read("plan.json", runtime.MAX_RAW)),
        )
        if replayed.packet_json != packet.packet_json:
            raise CurrentMarketSourceV4Error("public_v4_custody_packet_changed")
        finished = native_stamp()
        validate_stamps((observed, finished))
        _check_owner(owner)
        operational._owned_route_state_v4(
            route_claim, invocation=invocation, owner_task=task
        )
        if utc_from_ns(finished["utc_ns"]) >= expires:
            raise CurrentMarketSourceV4Error("public_v4_source_expired")
        # Recheck quote, candle tail, books/OI and WS at the real post-commit
        # clock. Custody latency cannot silently extend freshness.
        public_market_context_v2(
            replayed,
            expected_bundle_sha256=replayed.bundle_sha256,
            evaluated_at=utc_from_ns(finished["utc_ns"]),
        )
        result = object.__new__(OwnedCurrentMarketSourceV4)
        _CURRENT[result] = {
            **owner,
            "bundle_raw": readback.bundle_raw,
            "bundle_sha256": bundle_pin,
            "custody_id": readback.bundle_id,
            "journal_sha256": journal_pin,
            "proof_sha256": proof_pin,
            "last": finished,
        }
        retained = True
        return result
    except asyncio.CancelledError:
        raise
    except CurrentMarketSourceV4Error:
        raise
    except Exception:  # noqa: BLE001 -- no credential, account or SQL detail escapes
        raise CurrentMarketSourceV4Error(failure_code) from None
    finally:
        if scope is not None:
            _SCOPES.pop(scope, None)
        if route_claim is not None and not retained:
            operational._discard_owned_route_v4(route_claim)


def evaluate_owned_current_market_v4(source):
    """Consume native source once into a real MarketSnapshot and shared G1 math.

    The existing fixed G1 engineering inspection policy remains explicitly
    uncalibrated for trading. Its result cannot bypass candidate/risk controls.
    """
    state = (
        _CURRENT.pop(source, None)
        if type(source) is OwnedCurrentMarketSourceV4
        else None
    )
    if state is None:
        raise CurrentMarketSourceV4Error("public_v4_source_unavailable")
    try:
        _check_owner(state)
        operational._owned_route_state_v4(
            state["v4_route_claim"],
            invocation=state["invocation"],
            owner_task=state["parent"],
        )
        now = native_stamp()
        validate_stamps((state["last"], now))
        at = utc_from_ns(now["utc_ns"])
        if at >= state["expires_at"]:
            raise CurrentMarketSourceV4Error("public_v4_source_expired")
        directory, proof, _, _ = replay_runtime_bundle_v4(
            state["bundle_raw"], expected_sha256=state["bundle_sha256"]
        )
        _, _, packet = runtime.replay_public_runtime(
            directory,
            expected_plan_sha256=sha(directory.read("plan.json", runtime.MAX_RAW)),
        )
        context = public_market_context_v2(
            packet, expected_bundle_sha256=packet.bundle_sha256, evaluated_at=at
        )
        policy = fixed_native_original_g1_policy()
        if sha(data._canonical(policy)) != DATA_POLICY_SHA256:
            raise CurrentMarketSourceV4Error("public_v4_g1_policy_changed")
        result = data_v2.evaluate_public_market_data_v2(
            packet,
            expected_bundle_sha256=packet.bundle_sha256,
            policy=policy,
            evaluated_at=at,
        )
        result = data_v2.verify_public_market_data_v2(
            result,
            packet,
            expected_bundle_sha256=packet.bundle_sha256,
            policy=policy,
            evaluated_at=at,
        )
        receipt = canonical(
            {
                "schema_version": "ctcc.current_market_evaluation.v4",
                "current_source_policy_sha256": PUBLIC_V4_CURRENT_SOURCE_POLICY_SHA256,
                "bundle_sha256": state["bundle_sha256"],
                "packet_sha256": packet.bundle_sha256,
                "journal_sha256": state["journal_sha256"],
                "custody_id": str(state["custody_id"]),
                "operational_route_proof_sha256": sha(canonical(proof)),
                "account_plan_sha256": proof["account_plan_sha256"],
                "credential_session_sha256": proof["credential_session_sha256"],
                "evaluated_at": at.isoformat(),
                "g1_evaluation_sha256": result.evaluation_sha256,
                "g1_policy_sha256": DATA_POLICY_SHA256,
                "source_authenticity_verified": True,
                "durable_raw_readback_verified": True,
                "registration_site": "UNKNOWN",
                "account_complete": False,
                "historical_availability_verified": False,
                "execution_authority": False,
            }
        )
        return CurrentMarketEvaluationV4(
            context.market, context.quote, context.reference, result, receipt
        )
    except Exception:  # noqa: BLE001 -- burned source stays burned on every failure
        raise CurrentMarketSourceV4Error("public_v4_evaluation_failed") from None
    finally:
        operational._discard_owned_route_v4(state["v4_route_claim"])


def _take_original_frame_v1(source, *, key):
    """Private move, not a second public source consumer or a saved-bundle API."""
    from app.trade_qualification.owned_current_original_frame_v1 import _HOOK_KEY

    if key is not _HOOK_KEY:
        raise CurrentMarketSourceV4Error("public_v4_original_frame_required")
    state = (
        _CURRENT.pop(source, None)
        if type(source) is OwnedCurrentMarketSourceV4
        else None
    )
    if state is None:
        raise CurrentMarketSourceV4Error("public_v4_source_unavailable")
    try:
        _replay_original_frame_v1(state, key=key)
        return state
    except BaseException:
        operational._discard_owned_route_v4(state["v4_route_claim"])
        raise


def _replay_original_frame_v1(state, *, key):
    """Keep the original route, immutable bytes and native expiry after awaits."""
    from app.trade_qualification.owned_current_original_frame_v1 import _HOOK_KEY

    if key is not _HOOK_KEY:
        raise CurrentMarketSourceV4Error("public_v4_original_frame_required")
    _check_owner(state)
    operational._owned_route_state_v4(
        state["v4_route_claim"],
        invocation=state["invocation"],
        owner_task=state["parent"],
    )
    now = native_stamp()
    validate_stamps((state["last"], now))
    at = utc_from_ns(now["utc_ns"])
    if at >= state["expires_at"]:
        raise CurrentMarketSourceV4Error("public_v4_source_expired")
    directory, route_proof, _, _ = replay_runtime_bundle_v4(
        state["bundle_raw"], expected_sha256=state["bundle_sha256"]
    )
    if (
        sha(canonical(route_proof)) != state["proof_sha256"]
        or sha(directory.read("summary.json", runtime.MAX_RAW))
        != state["journal_sha256"]
    ):
        raise CurrentMarketSourceV4Error("public_v4_custody_journal_changed")
    _, _, packet = runtime.replay_public_runtime(
        directory,
        expected_plan_sha256=sha(directory.read("plan.json", runtime.MAX_RAW)),
    )
    context = public_market_context_v2(
        packet, expected_bundle_sha256=packet.bundle_sha256, evaluated_at=at
    )
    state["last"] = now
    return packet, context, now
