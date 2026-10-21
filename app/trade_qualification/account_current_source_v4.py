"""Native current Demo account acquisition; no portfolio or order authority.

The versioned owner is issued only by the original native B1 acquisition and
restricted DB0046 exact-byte readback in this invocation. Saved bundles are
private audit evidence, never current capabilities. Existing diagnostic/native
proof contracts, including the V7 flat-use revocation, remain unchanged.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from threading import get_ident
from typing import Literal
from weakref import WeakKeyDictionary

from app.database.repositories import public_runtime_bundle_custody as custody
from app.domain.native_clock import native_stamp
from app.domain.source_primitives import (
    canonical,
    decode,
    sha,
    utc_from_ns,
    validate_stamps,
)
from app.public_market_source.public_runtime_custody_v4 import (
    replay_operational_proof_v4,
)
from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_capture_journal as journal
from app.trade_qualification import account_current_source_verifier as current
from app.trade_qualification import account_native_clock as native
from app.trade_qualification import account_native_proof as proof
from app.trade_qualification import account_native_proof_storage as storage
from app.trade_qualification import account_native_runtime as runtime
from app.trade_qualification import account_observation_index as observed
from app.trade_qualification import demo_operational_route_v4 as operational
from app.trade_qualification import public_source_runtime as public_storage
from app.trade_qualification.account_runtime import ControlledDemoAccountSession
from app.trade_qualification.demo_market_source_v4 import _route_proof
from app.trade_qualification.reservations import LedgerScope

SCHEMA = "ctcc.demo_current_account_raw_bundle.v4"
POLICY_BYTES = canonical(
    {
        "version": "ctcc.demo_current_account_source.v4",
        "source": "same_invocation_native_V7_B1_and_9549_companion",
        "current_policy_sha256": current.V7_POLICY_SHA256,
        "native_proof_policy_sha256": proof.V7_9549_DIAGNOSTIC_POLICY_SHA256,
        "custody": "restricted_application_role_DB0046_commit_separate_transaction_exact_readback",
        "current_page_chains": "all_V7_unfiltered_current_streams_with_explicit_terminal_pages",
        "exchange_snapshot": "non_atomic_measured_interval",
        "historical_native_flat_use": "remains_revoked",
        "registration_site": "UNKNOWN",
        "post_g12_eligible": False,
        "history_HWM_streak_and_portfolio": "separate_unknown_not_issued",
        "execution_authority": False,
    }
)
POLICY_SHA256 = sha(POLICY_BYTES)
_CURRENT = WeakKeyDictionary()
_INITIAL = WeakKeyDictionary()
_MAX_NATIVE_WALL_SECONDS = 30


class _InitialAccountScopeV4:
    __slots__ = ("__weakref__",)


class CurrentAccountSourceV4Error(ValueError):
    """Fixed codes only; private source, account and transport stay private."""


def _deny(code="account_v4_source_unavailable"):
    raise CurrentAccountSourceV4Error(code)


class OwnedCurrentDemoAccountSourceV4:
    __slots__ = ("__weakref__",)

    def __new__(cls):
        _deny("account_v4_source_not_constructible")

    def __copy__(self):
        _deny("account_v4_source_not_transferable")

    def __deepcopy__(self, memo):
        _deny("account_v4_source_not_transferable")

    def __reduce_ex__(self, protocol):
        _deny("account_v4_source_not_transferable")


@dataclass(frozen=True, slots=True, repr=False)
class RecordedCurrentAccountBundleV4:
    """Replay result has no source authority or runtime owner."""

    packet: object
    reference: object
    current_receipt_json: bytes
    proof_envelope_sha256: str
    native_proof_sha256: str
    native_readback_sha256: str
    expires_at: datetime
    monotonic_deadline_ns: int
    source_authenticity_verified: Literal[False] = field(default=False, init=False)
    account_complete: Literal[False] = field(default=False, init=False)
    post_g12_eligible: Literal[False] = field(default=False, init=False)
    execution_authority: Literal[False] = field(default=False, init=False)


@dataclass(frozen=True, slots=True, repr=False)
class CurrentAccountOperandsV4:
    """Private measured operands. This DTO cannot issue a portfolio/revision."""

    packet: object
    reference: object
    current_receipt_json: bytes
    receipt_json: bytes
    source_authenticity_verified: Literal[True] = field(default=True, init=False)
    durable_raw_readback_verified: Literal[True] = field(default=True, init=False)
    current_page_chains_complete: Literal[True] = field(default=True, init=False)
    account_complete: Literal[False] = field(default=False, init=False)
    account_atomic_revision_verified: Literal[False] = field(default=False, init=False)
    historical_availability_verified: Literal[False] = field(default=False, init=False)
    execution_authority: Literal[False] = field(default=False, init=False)
    registration_site: Literal["UNKNOWN"] = field(default="UNKNOWN", init=False)
    post_g12_eligible: Literal[False] = field(default=False, init=False)

    @property
    def receipt_sha256(self):
        return sha(self.receipt_json)


def _time(value):
    if type(value) is not str:
        _deny("account_v4_bundle_invalid")
    at = capture._utc(datetime.fromisoformat(value))
    if at.isoformat() != value:
        _deny("account_v4_bundle_invalid")
    return at


def _unhex(value, maximum):
    if type(value) is not str or len(value) > maximum * 2:
        _deny("account_v4_bundle_invalid")
    raw = bytes.fromhex(value)
    if raw.hex() != value or not raw:
        _deny("account_v4_bundle_invalid")
    return raw


def _chain_document(chain):
    return [
        {
            "event_hex": item.event.event_json.hex(),
            "raw_hex": None
            if item.event.raw_body is None
            else item.event.raw_body.hex(),
            "packet_hex": None
            if item.event.packet_payload is None
            else item.event.packet_payload.hex(),
            "db_recorded_at": item.db_recorded_at.isoformat(),
            "readback_at": item.readback_at.isoformat(),
        }
        for item in chain
    ]


def _chain_replay(rows):
    if type(rows) is not list or not 1 <= len(rows) <= journal.MAX_EVENTS:
        _deny("account_v4_bundle_invalid")
    result = []
    for row in rows:
        if type(row) is not dict or set(row) != {
            "event_hex",
            "raw_hex",
            "packet_hex",
            "db_recorded_at",
            "readback_at",
        }:
            _deny("account_v4_bundle_invalid")
        result.append(
            journal.JournalReadback(
                journal._JournalEvent(
                    journal._ISSUER,
                    _unhex(row["event_hex"], 262144),
                    None
                    if row["raw_hex"] is None
                    else _unhex(row["raw_hex"], journal.PAGE_BYTES),
                    None
                    if row["packet_hex"] is None
                    else _unhex(row["packet_hex"], capture.MAX_PACKET_BYTES),
                ),
                _time(row["db_recorded_at"]),
                _time(row["readback_at"]),
            )
        )
    return tuple(result)


def replay_current_account_bundle_v4(raw, *, expected_sha256):
    """Exact original-byte replay; deliberately cannot remint a native owner."""
    try:
        if (
            type(raw) is not bytes
            or not 0 < len(raw) <= custody.MAX_BUNDLE_BYTES
            or sha(raw) != expected_sha256
        ):
            _deny("account_v4_bundle_invalid")
        document = decode(raw, custody.MAX_BUNDLE_BYTES)
        if (
            canonical(document) != raw
            or type(document) is not dict
            or set(document)
            != {
                "schema_version",
                "policy_sha256",
                "route_proof",
                "native_proof_hex",
                "native_readback_hex",
                "clock_files",
                "chain",
                "proof_envelope",
            }
            or document["schema_version"] != SCHEMA
            or document["policy_sha256"] != POLICY_SHA256
        ):
            _deny("account_v4_bundle_invalid")
        route = document["route_proof"]
        replay_operational_proof_v4(route)
        native_raw = _unhex(document["native_proof_hex"], proof.MAX_PROOF)
        native_doc = proof._canonical_document(native_raw)
        if (
            native_doc["schema_version"] != proof.V7_9549_DIAGNOSTIC_SCHEMA
            or native_doc["policy_sha256"] != proof.V7_9549_DIAGNOSTIC_POLICY_SHA256
        ):
            _deny("account_v4_bundle_invalid")
        chain = _chain_replay(document["chain"])
        reference = observed.source_reference(chain)
        identity = route["account_identity"]
        scope = LedgerScope(account_id=identity["uid"], settlement_currency="USDT")
        files = document["clock_files"]
        if type(files) is not dict or set(files) != set(proof.FILES):
            _deny("account_v4_bundle_invalid")
        files = {name: _unhex(value, storage.MAX_PART) for name, value in files.items()}
        replay = proof.replay_native_account_proof(
            native_raw,
            files=files,
            chain=chain,
            scope=scope,
            expected_proof_sha256=sha(native_raw),
        )
        packet = replay.packet
        first_config = decode(
            next(
                page
                for page in packet.observations
                if page.request.stream == "config_before"
            )
            .rows[0]
            .canonical_json.encode(),
            262144,
        )
        if (
            type(packet.plan) is not capture.CurrentDemoAccountCapturePlanV7
            or route["account_plan_sha256"] != reference.plan_sha256
            or route["rest_origin"] != packet.plan.origin
            or identity != {name: first_config[name] for name in identity}
            or route["credential_session_sha256"]
            != sha(
                canonical(
                    {
                        "session_binding_id": packet.plan.session_binding_id,
                        "account_plan_sha256": reference.plan_sha256,
                    }
                )
            )
        ):
            _deny("account_v4_bundle_invalid")
        validate_stamps((route["consumed"], native_doc["stage"]["started"]))
        if (
            route["consumed"]["monotonic_ns"]
            >= native_doc["stage"]["started"]["monotonic_ns"]
            or route["consumed"]["utc_ns"] >= native_doc["stage"]["started"]["utc_ns"]
        ):
            _deny("account_v4_bundle_invalid")
        readback_raw = _unhex(document["native_readback_hex"], storage.MAX_PART)
        readback = proof._canonical_document(readback_raw, storage.MAX_PART)
        if (
            set(readback)
            != {
                "schema_version",
                "proof_sha256",
                "clock_file_sha256",
                "root_device",
                "root_inode",
                "readback_complete",
                "execution_authority",
            }
            or readback["schema_version"] != storage.V7_9549_DIAGNOSTIC_READBACK_SCHEMA
            or readback["proof_sha256"] != sha(native_raw)
            or readback["clock_file_sha256"]
            != {name: sha(value) for name, value in files.items()}
            or readback["execution_authority"] is not False
            or type(readback["root_device"]) is not int
            or type(readback["root_inode"]) is not int
        ):
            _deny("account_v4_bundle_invalid")
        validate_stamps(
            (native_doc["proof_persist_start"], readback["readback_complete"])
        )
        if (
            readback["readback_complete"]["monotonic_ns"]
            >= replay.monotonic_deadline_ns
            or utc_from_ns(readback["readback_complete"]["utc_ns"]) >= replay.expires_at
        ):
            _deny("account_v4_bundle_invalid")
        envelope = {
            "policy_sha256": POLICY_SHA256,
            "route_proof_sha256": sha(canonical(route)),
            "native_proof_sha256": sha(native_raw),
            "native_readback_sha256": sha(readback_raw),
            "source_reference": observed.reference_document(reference),
            "original_chain_sha256": sha(canonical(document["chain"])),
        }
        if document["proof_envelope"] != envelope:
            _deny("account_v4_bundle_invalid")
        return RecordedCurrentAccountBundleV4(
            packet,
            reference,
            replay.current_source_receipt_json,
            sha(canonical(envelope)),
            sha(native_raw),
            sha(readback_raw),
            replay.expires_at,
            replay.monotonic_deadline_ns,
        )
    except Exception:  # noqa: BLE001 -- private raw/parser failures stay private
        raise CurrentAccountSourceV4Error("account_v4_bundle_invalid") from None


def _freeze_bundle(route_proof, chain, directory):
    native_raw = directory.read("proof.json", proof.MAX_PROOF)
    readback_raw = directory.read("readback.json", storage.MAX_PART)
    reference = observed.source_reference(chain)
    rows = _chain_document(chain)
    envelope = {
        "policy_sha256": POLICY_SHA256,
        "route_proof_sha256": sha(canonical(route_proof)),
        "native_proof_sha256": sha(native_raw),
        "native_readback_sha256": sha(readback_raw),
        "source_reference": observed.reference_document(reference),
        "original_chain_sha256": sha(canonical(rows)),
    }
    raw = canonical(
        {
            "schema_version": SCHEMA,
            "policy_sha256": POLICY_SHA256,
            "route_proof": route_proof,
            "native_proof_hex": native_raw.hex(),
            "native_readback_hex": readback_raw.hex(),
            "clock_files": {
                name: directory.read(name, storage.MAX_PART).hex()
                for name in proof.FILES
            },
            "chain": rows,
            "proof_envelope": envelope,
        }
    )
    return raw, replay_current_account_bundle_v4(raw, expected_sha256=sha(raw))


def _check_context(state):
    task = asyncio.current_task()
    if (
        task is None
        or task.cancelling()
        or task is not state["parent"]
        or asyncio.get_running_loop() is not state["loop"]
        or os.getpid() != state["pid"]
        or get_ident() != state["thread"]
    ):
        _deny()


def _check_owner(state):
    _check_context(state)
    session = state["session"]
    if (
        session._used is not True
        or session._plan is not state["plan"]
        or session._pin != state["plan_sha256"]
        or capture.plan_sha256(session._plan) != state["plan_sha256"]
        or native._checked_credential_pin(session) != state["credential_pin"]
        or session._checked_read_session(state["plan"]) is not state["read_session"]
    ):
        _deny()
    state["read_session"].verify_current()


async def acquire_initial_demo_account_source_v4(
    *,
    account_session,
    private_client,
    session_factory,
    private_proof_root,
    native_proof_root,
    custody_repository,
):
    """Native initial current GETs only. No receipt/packet/clock/URL is accepted.

    Both roots are empty private directories. The route lease is consumed before
    the account session is marked used; no existing route guard is relaxed.
    """
    try:
        if (
            type(account_session) is not ControlledDemoAccountSession
            or not runtime._configured_factory(session_factory)
            or not custody._native_engine(session_factory.kw["bind"])
            or type(custody_repository) is not custody.RuntimeBundleCustodyRepository
        ):
            _deny("account_v4_inputs_invalid")
        if custody_repository._poisoned is not False:
            _deny("account_v4_custody_state_invalid")
        # The exact repository is slot-backed; no instance callback is accepted.
        custody.RuntimeBundleCustodyRepository._check_factory(custody_repository)
        if (
            any(
                type(root) is not type(Path()) or not root.is_absolute()
                for root in (private_proof_root, native_proof_root)
            )
            or private_proof_root == native_proof_root
            or private_proof_root in native_proof_root.parents
            or native_proof_root in private_proof_root.parents
        ):
            _deny("account_v4_distinct_private_roots_required")
        plan, _ = operational._checked_objects(account_session, private_client)
        read_session = account_session._checked_read_session(plan)
        if (
            type(plan) is not capture.CurrentDemoAccountCapturePlanV7
            or plan.registration_region != "global"
            or plan.settlement_currency != "USDT"
            or read_session is None
        ):
            _deny("account_v4_pinned_v7_session_required")
        with storage._root_context(native_proof_root) as directory:
            if directory.names():
                _deny("account_v4_native_root_not_empty")
        async with asyncio.timeout(5):
            await custody.RuntimeBundleCustodyRepository.verify_deployment(
                custody_repository
            )
        with public_storage._native_recheck_root(private_proof_root) as directory:
            if directory.names():
                _deny("account_v4_private_root_not_empty")
            observation = (
                await operational._probe_initial_demo_operational_route_retained_v4(
                    account_session, private_client, proof_directory=directory
                )
            )
            # Burn route authority before _claim_initial_session sets _used=True.
            route_state = operational._consume_initial_route_state_v4(
                observation, account_session, private_client
            )
            route_proof = _route_proof(route_state)
            encoded = canonical(route_proof)
            directory.publish("operational-route-proof.account-v4.json", encoded)
            if (
                directory.read("operational-route-proof.account-v4.json", 32768)
                != encoded
            ):
                _deny("account_v4_route_readback_changed")
        initial = _InitialAccountScopeV4()
        _INITIAL[initial] = {
            "parent": asyncio.current_task(),
            "loop": asyncio.get_running_loop(),
            "pid": os.getpid(),
            "thread": get_ident(),
            "session": account_session,
            "private_client": private_client,
            "factory": session_factory,
            "root": native_proof_root,
            "repository": custody_repository,
            "route_proof": route_proof,
            "read_session": read_session,
        }
        try:
            async with asyncio.timeout(_MAX_NATIVE_WALL_SECONDS) as budget:
                _INITIAL[initial]["budget"] = budget
                return await _acquire_native(initial)
        finally:
            _INITIAL.pop(initial, None)
    except asyncio.CancelledError:
        raise
    except CurrentAccountSourceV4Error:
        raise
    except Exception:  # noqa: BLE001 -- native/SQL/credentials never escape
        raise CurrentAccountSourceV4Error("account_v4_acquisition_failed") from None


async def _acquire_native(initial):
    owned = (
        _INITIAL.pop(initial, None) if type(initial) is _InitialAccountScopeV4 else None
    )
    if owned is None:
        _deny("account_v4_native_scope_unavailable")
    _check_context(owned)
    session, private_client, factory, root, repository, route_proof, read_session = (
        owned[name]
        for name in (
            "session",
            "private_client",
            "factory",
            "root",
            "repository",
            "route_proof",
            "read_session",
        )
    )
    plan = capture._checked_plan(session._plan, session._pin)
    scope = LedgerScope(
        account_id=plan.expected_uid, settlement_currency=plan.settlement_currency
    )
    with (
        native._claim_initial_session(session),
        native._initial_stage(
            plan_sha256=session._pin,
            scope_sha256=proof.scope_sha256(scope),
            _claimed_session=session,
        ) as stage,
    ):
        state = native._state(stage)
        sink = []
        result = await runtime._capture_initial_current(
            stage, session, factory, root, _v7_diagnostic_sink=sink
        )
        _constrain_budget(owned["budget"], state, native._sample(stage))
        owner = state.get("journal")
        if (
            type(result) is not runtime.InitialNativeAccountDiagnostic
            or len(sink) != 1
            or sink[0] is not result
            or type(owner) is not journal._OwnedAccountJournal
            or not all(
                (
                    state["closed"],
                    owner.finished,
                    owner.acquisition_ok,
                    owner.closed_safe,
                    owner.finalization_complete,
                )
            )
            or state["session_claim"]["bootstrap_used"] is not True
        ):
            _deny("account_v4_original_native_capture_required")
        chain = await owner.repository.read_chain(scope, owner.capture_id)
        reference = observed.source_reference(chain)
        if (
            reference.head_sha256 != owner.previous
            or reference.packet_sha256 != owner.owned_packet_sha256
            or reference.plan_sha256 != session._pin
            or len(chain) != owner.sequence
        ):
            _deny("account_v4_original_chain_changed")
        with storage._root_context(root) as directory:
            raw, replay = _freeze_bundle(route_proof, chain, directory)
        diagnostic = decode(result.receipt_json, 65536)
        if (
            diagnostic["native_proof_sha256"] != replay.native_proof_sha256
            or diagnostic["native_readback_sha256"] != replay.native_readback_sha256
            or diagnostic["original_current_chain_head_sha256"] != reference.head_sha256
            or diagnostic["current_source_receipt_sha256"]
            != sha(replay.current_receipt_json)
        ):
            _deny("account_v4_native_proof_changed")
        runtime._secret_checked(raw, owner.tokens)
        issue = native._sample(stage)
        remaining = (replay.expires_at - utc_from_ns(issue["utc_ns"])).total_seconds()
        if remaining <= 0 or issue["monotonic_ns"] >= replay.monotonic_deadline_ns:
            _deny("account_v4_source_expired")
        async with asyncio.timeout(min(5, remaining)):
            receipt = await custody.RuntimeBundleCustodyRepository.seal_bundle(
                repository,
                bundle_raw=raw,
                expected_sha256=sha(raw),
                proof_envelope_sha256=replay.proof_envelope_sha256,
            )
        retained = custody._consume_runtime_bundle_custody_receipt(
            receipt,
            expected_bundle_sha256=sha(raw),
            expected_proof_envelope_sha256=replay.proof_envelope_sha256,
        )
        if retained.bundle_raw != raw:
            _deny("account_v4_custody_readback_changed")
        replay_current_account_bundle_v4(retained.bundle_raw, expected_sha256=sha(raw))
        await runtime._recheck_original_db_chain(
            owner.repository,
            scope,
            chain,
            reference,
            replay.packet,
            proof_schema=proof.V7_9549_DIAGNOSTIC_SCHEMA,
        )
        storage.read_native_account_companion(
            root,
            chain=chain,
            scope=scope,
            expected_proof_sha256=replay.native_proof_sha256,
            expected_readback_sha256=replay.native_readback_sha256,
        )
        issued = native._sample(stage)
        context = {
            "parent": asyncio.current_task(),
            "loop": asyncio.get_running_loop(),
            "pid": os.getpid(),
            "thread": get_ident(),
            "session": session,
            "private_client": private_client,
            "plan": session._plan,
            "plan_sha256": session._pin,
            "credential_pin": native._checked_credential_pin(session),
            "read_session": read_session,
            "bundle_raw": raw,
            "bundle_sha256": sha(raw),
            "custody_id": retained.bundle_id,
            "last": issued,
        }
        _check_owner(context)
        _check_current(replay, issued)
        source = object.__new__(OwnedCurrentDemoAccountSourceV4)
        _CURRENT[source] = context
        return source


def _constrain_budget(budget, state, stamp):
    """Shorten the same actual monotonic timeout to original source freshness."""
    remaining = min(
        (state["current_deadline"] - stamp["monotonic_ns"]) / 1_000_000_000,
        (state["current_expiry"] - utc_from_ns(stamp["utc_ns"])).total_seconds(),
    )
    if remaining <= 0:
        _deny("account_v4_source_expired")
    budget.reschedule(min(budget.when(), asyncio.get_running_loop().time() + remaining))


def _check_current(replay, stamp):
    at = utc_from_ns(stamp["utc_ns"])
    if at >= replay.expires_at or stamp["monotonic_ns"] >= replay.monotonic_deadline_ns:
        _deny("account_v4_source_expired")
    # Recompute current EOF freshness at this actual clock, never replay cutoff.
    document = decode(replay.current_receipt_json, capture.MAX_PACKET_BYTES)
    earliest = _time(document["observation_interval"]["earliest_body_exhausted_at"])
    if not earliest <= at or (at - earliest).total_seconds() >= 30:
        _deny("account_v4_source_expired")


def consume_owned_current_demo_account_v4(source):
    state = (
        _CURRENT.pop(source, None)
        if type(source) is OwnedCurrentDemoAccountSourceV4
        else None
    )
    if state is None:
        _deny()
    try:
        _check_owner(state)
        now = native_stamp()
        validate_stamps((state["last"], now))
        replay = replay_current_account_bundle_v4(
            state["bundle_raw"], expected_sha256=state["bundle_sha256"]
        )
        _check_current(replay, now)
        receipt = canonical(
            {
                "schema_version": "ctcc.demo_current_account_operands.v4",
                "policy_sha256": POLICY_SHA256,
                "bundle_sha256": state["bundle_sha256"],
                "custody_id": state["custody_id"],
                "source_reference": observed.reference_document(replay.reference),
                "proof_envelope_sha256": replay.proof_envelope_sha256,
                "current_receipt_sha256": sha(replay.current_receipt_json),
                "observed_at": utc_from_ns(now["utc_ns"]).isoformat(),
                "source_authenticity_verified": True,
                "durable_raw_readback_verified": True,
                "current_page_chains_complete": True,
                "exchange_capture_atomic": False,
                "registration_site": "UNKNOWN",
                "post_g12_eligible": False,
                "account_complete": False,
                "history_complete": None,
                "loss_streak_seed": None,
                "historical_HWM": None,
                "snapshot": None,
                "account_revision_published": False,
                "flat_start_permission": False,
                "execution_authority": False,
            }
        )
        return CurrentAccountOperandsV4(
            replay.packet, replay.reference, replay.current_receipt_json, receipt
        )
    except Exception:  # noqa: BLE001 -- burned owner stays burned on any failure
        raise CurrentAccountSourceV4Error("account_v4_consumption_failed") from None
