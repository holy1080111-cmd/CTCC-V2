"""Native prospective controlled Demo writer coverage, never order authority.

Only original V4 acquisition, the existing Control owner guards, the actual B5
audit and committed DB0048 readback can issue the private live owner. Saved
rows and measured receipts are audit data. No source deadline is restarted.
"""

from __future__ import annotations

import asyncio
import os
import re
import secrets
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from threading import get_ident
from types import CoroutineType
from typing import Literal
from weakref import WeakKeyDictionary, ref

from app.database.repositories import demo_control as controls
from app.database.repositories import public_runtime_bundle_custody as custody
from app.database.repositories.qualification_ledger import QualificationLedgerRepository
from app.domain import native_clock
from app.domain.source_primitives import (
    canonical,
    decode,
    sha,
    utc_from_ns,
    validate_stamps,
)
from app.exchange.okx.errors import OkxPrivateApiError
from app.okx_live import execution_authority as live_authority
from app.trade_qualification import account_current_source_v4 as account
from app.trade_qualification import account_current_source_verifier as current
from app.trade_qualification import account_native_proof as proof
from app.trade_qualification import account_native_proof_storage as storage
from app.trade_qualification import account_native_runtime as runtime
from app.trade_qualification import demo_control as control
from app.trade_qualification import execution_authority, private_write_authority
from app.trade_qualification.reservations import LedgerScope

_HOOK_KEY = object()
_BINDINGS = WeakKeyDictionary()
_OWNERS = WeakKeyDictionary()
_FINANCIAL = WeakKeyDictionary()
_MAX_OWNERS = 256
_MAX_SOURCE_FILE = 4 * 1024 * 1024
_ROOT = Path(__file__).resolve().parents[2]
_SHA = re.compile(r"[a-f0-9]{64}\Z")
_PRIVATE_NAMES = frozenset(
    {
        "account-route-attempt.v4.plan.json",
        "account-route-attempt.v4.raw",
        "account-route-attempt.v4.json",
        "operational-route-proof.account-v4.json",
    }
)
WRITER_PATHS = (
    "app/exchange/okx/private_rest.py",
    "app/exchange/okx/owned_write_seams_v1.py",
    "app/trade_qualification/private_write_authority.py",
    "app/trade_qualification/execution_authority.py",
    "app/trade_qualification/dispatch_ownership.py",
    "app/api/routers/okx_demo.py",
    "app/api/routers/demo_automation.py",
    "app/api/routers/okx_live.py",
    "app/okx_demo/service.py",
    "app/demo_automation/service.py",
    "app/demo_automation/runtime.py",
    "app/okx_live/service.py",
    "app/okx_live/automation.py",
    "app/okx_live/runtime.py",
    "app/okx_live/execution_authority.py",
    "app/database/repositories/demo_control.py",
    "app/database/repositories/qualification_ledger.py",
    "app/database/repositories/demo_automation.py",
    "app/database/repositories/okx_demo.py",
    "app/database/repositories/okx_live.py",
    "app/database/repositories/okx_live_execution.py",
    "app/trade_qualification/controlled_writer_coverage_v1.py",
    "app/database/repositories/controlled_writer_coverage.py",
    "app/domain/controlled_writer_coverage_catalog_v1.py",
    "scripts/execute_okx_live_micro_order.ps1",
    "scripts/run_okx_live_automation_once.ps1",
    "scripts/start_controlled_demo_execution_soak.ps1",
)
_DENY_SOURCE = {
    "app/exchange/okx/private_rest.py": "3edcc729215665251b22a28d6e6e14b234d03182e0bb9a227fe595d1b692b438",
    "app/trade_qualification/private_write_authority.py": "58b7049b19db5f28f36e03736d6874cceaba98917f3e44122ea965ac65d91738",
    "app/trade_qualification/execution_authority.py": "e4473d95a3ee0375d3ff8d165cce4e8a82b30b5ea659e14066ef8a796e20b0d7",
    "app/okx_live/execution_authority.py": "5c2c5702fea8c305ef0995458f2820a2bd23b37473e5e9c8e5014393d0990b94",
    "app/exchange/okx/owned_write_seams_v1.py": "240abb68cb21c0f2d90f426adc728220198f0a037841f5181daccf7326458f6f",
}
POLICY_BYTES = canonical(
    {
        "version": "ctcc.controlled_demo_writer_coverage.v1",
        "scope": "prospective_controlled_CTCC_demo_deployment_only",
        "source": "original_native_V4_MOVE_and_replay",
        "account_source_policy_sha256": account.POLICY_SHA256,
        "control": "read_acquire_separate_readback_owner_bound_estop_readback",
        "local": "same_UID_all_currency_original_B5_no_claim_revision_fabrication",
        "custody": "DB0048_prepared_active_commit_separate_exact_readback",
        "original_native_anchor_ns": 60_000_000_000,
        "native_drift_ns": 5_000_000,
        "original_current_ns": 30_000_000_000,
        "original_custody_receipt_ns": 5_000_000_000,
        "writer_paths": list(WRITER_PATHS),
        "all_nonGET": "DENY_UNCHANGED",
        "retries": 0,
        "account_complete": False,
        "account_revision_published": False,
        "flat_start_permission": False,
        "event_eligibility": False,
        "execution_authority": False,
    }
)
POLICY_SHA256 = sha(POLICY_BYTES)


class ControlledWriterCoverageError(ValueError):
    """Fixed codes only; no source/account/SQL/credential exception text."""


def _deny(code="controlled_writer_coverage_unavailable"):
    raise ControlledWriterCoverageError(code) from None


class _Opaque:
    __slots__ = ("__weakref__",)

    def __new__(cls):
        _deny("controlled_writer_coverage_not_constructible")

    def __copy__(self):
        _deny("controlled_writer_coverage_not_transferable")

    def __deepcopy__(self, memo):
        _deny("controlled_writer_coverage_not_transferable")

    def __reduce_ex__(self, protocol):
        _deny("controlled_writer_coverage_not_transferable")


class _ActivationBindingV1(_Opaque):
    __slots__ = ()


class OwnedControlledDemoWriterCoverageV1(_Opaque):
    __slots__ = ()


@dataclass(frozen=True, slots=True, repr=False)
class ControlledDemoWriterCoverageFactsV1:
    """Measured private operands; constructing this DTO issues no capability."""

    receipt_json: bytes
    controlled_ctcc_coverage_verified: Literal[True] = field(default=True, init=False)
    account_complete: Literal[False] = field(default=False, init=False)
    account_revision_published: Literal[False] = field(default=False, init=False)
    flat_start_permission: Literal[False] = field(default=False, init=False)
    event_eligibility: Literal[False] = field(default=False, init=False)
    execution_authority: Literal[False] = field(default=False, init=False)


def _context():
    task = asyncio.current_task()
    if task is None or task.cancelling():
        _deny("controlled_writer_coverage_context_invalid")
    return os.getpid(), get_ident(), asyncio.get_running_loop(), task


def _read_file(path, maximum):
    if type(path) is not type(Path()) or not path.is_absolute():
        _deny("controlled_writer_coverage_native_path_required")
    before = os.stat(path, follow_symlinks=False)
    with storage._root_context(path.parent) as directory:
        root_identity = storage._root_identity(path.parent)
        raw = directory.read(path.name, maximum)
    after = os.stat(path, follow_symlinks=False)
    signature = lambda value: (
        value.st_dev,
        value.st_ino,
        value.st_size,
        value.st_mtime_ns,
    )
    if type(raw) is not bytes or not raw or signature(before) != signature(after):
        _deny("controlled_writer_coverage_file_changed")
    return raw, (root_identity, signature(after), sha(raw))


def _guard_denials():
    """Call only existing synchronous pure classifiers, never a client/send."""
    _check_function_pins()
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        try:
            private_write_authority.require_owned_private_write_authority(
                method, "/api/v5/trade/order", owner=None, slot=None
            )
        except OkxPrivateApiError as error:
            if error.code != "private_write_authority_unavailable":
                _deny("controlled_writer_coverage_default_deny_changed")
        else:
            _deny("controlled_writer_coverage_default_deny_changed")
    for check in (
        execution_authority.enforce_demo_final_dispatch,
        live_authority.enforce_live_final_dispatch,
    ):
        for path in (
            "/api/v5/trade/order",
            "/api/v5/trade/cancel-order",
            "/api/v5/trade/close-position",
            "/api/v5/trade/cancel-all-after",
            "/api/v5/unknown-write",
        ):
            try:
                check("POST", path)
            except OkxPrivateApiError:
                continue
            _deny("controlled_writer_coverage_default_deny_changed")


def _package_inventory():
    _guard_denials()
    manifest, manifest_pin = _read_file(_ROOT / "MANIFEST.sha256", 2 * 1024 * 1024)
    rows = {}
    for line in manifest.decode("ascii").splitlines():
        digest, separator, path = line.partition("  ")
        if (
            not separator
            or _SHA.fullmatch(digest) is None
            or path in rows
            or not path
            or ".." in Path(path).parts
        ):
            _deny("controlled_writer_coverage_manifest_invalid")
        rows[path] = digest
    files = {}
    for relative in WRITER_PATHS:
        raw, pin = _read_file(_ROOT / relative, _MAX_SOURCE_FILE)
        if rows.get(relative) != sha(raw) or (
            relative in _DENY_SOURCE and _DENY_SOURCE[relative] != sha(raw)
        ):
            _deny("controlled_writer_coverage_packaged_source_changed")
        files[relative] = pin
    return {
        "manifest": manifest_pin,
        "files": files,
        "manifest_sha256": sha(manifest),
        "writer_sha256": sha(canonical({p: v[2] for p, v in files.items()})),
    }


def _factory_pins(factory, repository):
    if (
        not runtime._configured_factory(factory)
        or not custody._native_engine(factory.kw["bind"])
        or type(repository) is not custody.RuntimeBundleCustodyRepository
    ):
        _deny("controlled_writer_coverage_native_factory_required")
    custody.RuntimeBundleCustodyRepository._check_factory(repository)
    other = repository._factory
    return (
        factory,
        factory.kw["bind"],
        tuple(sorted(factory.kw.items())),
        repository,
        other,
        other.kw["bind"],
        tuple(sorted(other.kw.items())),
    )


def _source_files(root, names, maximum):
    with storage._root_context(root) as directory:
        if set(directory.names()) != set(names):
            _deny("controlled_writer_coverage_source_inventory_changed")
    pins = {name: _read_file(root / name, maximum)[1] for name in sorted(names)}
    return storage._root_identity(root), pins


def _source_material(held, replay):
    bundle = decode(held["bundle_raw"], custody.MAX_BUNDLE_BYTES)
    native = proof._canonical_document(
        account._unhex(bundle["native_proof_hex"], proof.MAX_PROOF)
    )
    receipt = decode(replay.current_receipt_json, account.capture.MAX_PACKET_BYTES)
    identity = receipt["identity"]
    counts = receipt["inventory_row_counts"]
    if (
        receipt["policy_sha256"] != current.V7_POLICY_SHA256
        or receipt["blocking_reasons"] != []
        or receipt["observed_inventory_state"] != "observed_empty_supported_queries"
        or receipt["observed_flat"] is not True
        or type(counts) is not dict
        or set(counts) != set(current.V7_INVENTORY_STREAMS)
        or any(type(number) is not int or number != 0 for number in counts.values())
        or identity["environment"] != "demo"
        or identity["settlement_currency"] != "USDT"
        or identity["uid"] != replay.packet.plan.expected_uid
        or identity["main_uid"] != replay.packet.plan.expected_main_uid
    ):
        _deny("controlled_writer_coverage_current_inventory_incomplete")
    return bundle, native["stage"]["started"], identity


def _check_binding(binding):
    state = _BINDINGS.get(binding) if type(binding) is _ActivationBindingV1 else None
    if state is None or state.get("failed"):
        _deny("controlled_writer_coverage_binding_unavailable")
    actual = _context()
    context = state["context"]
    if actual[:3] != context[:3] or actual[3] is not context[3]():
        _deny("controlled_writer_coverage_context_changed")
    _check_function_pins()
    pins = _factory_pins(state["factory"], state["custody"])
    expected = state["factory_pins"]
    if (
        any(pins[n] is not expected[n] for n in (0, 1, 3, 4, 5))
        or pins[2] != expected[2]
        or pins[6] != expected[6]
        or _package_inventory() != state["inventory"]
    ):
        _deny("controlled_writer_coverage_native_identity_changed")
    repository = state.get("repository")
    if repository is not None and (
        type(repository) is not state["repository_type"]
        or repository._factory is not state["factory"]
        or repository._poisoned is not False
        or any(
            getattr(type(repository), name) is not function
            for name, function in state["repository_functions"]
        )
    ):
        _deny("controlled_writer_coverage_repository_changed")
    held = state.get("account")
    if held is not None:
        replay, stamp = account._replay_controlled_writer_coverage_v1(
            held, key=_HOOK_KEY
        )
        validate_stamps((state["anchor"], state["last"], stamp))
        if (
            sha(replay.current_receipt_json)
            != state["template"]["source_current_receipt_sha256"]
            or replay.reference.head_sha256
            != state["template"]["source_chain_head_sha256"]
        ):
            _deny("controlled_writer_coverage_source_changed")
    else:
        stamp = native_clock.native_stamp()
        validate_stamps((state["last"], stamp))
    state["last"] = stamp
    if state.get("control") is not None:
        at = utc_from_ns(stamp["utc_ns"])
        bound = state["control"].state
        if (
            at < bound.updated_at
            or at >= bound.lease_until
            or bound.arm_request_id is not None
        ):
            _deny("controlled_writer_coverage_control_expired")
    for root_key, names, maximum in (
        ("private_root", _PRIVATE_NAMES, _MAX_SOURCE_FILE),
        ("native_root", (*proof.FILES, "proof.json", "readback.json"), proof.MAX_PROOF),
    ):
        if root_key in state.get("source_files", {}) and (
            _source_files(state[root_key], names, maximum)
            != state["source_files"][root_key]
        ):
            _deny("controlled_writer_coverage_source_files_changed")
    completed = native_clock.native_stamp()
    if held is not None:
        validate_stamps((state["anchor"], stamp, completed))
        account._check_current(replay, completed)
    else:
        validate_stamps((stamp, completed))
    stamp = completed
    state["last"] = stamp
    if state.get("control") is not None and (
        utc_from_ns(stamp["utc_ns"]) >= state["control"].state.lease_until
    ):
        _deny("controlled_writer_coverage_control_expired")
    return state, stamp


def _repository_binding_v1(binding, *, key):
    if key is not _HOOK_KEY:
        _deny("controlled_writer_coverage_private_binding_required")
    state, _stamp = _check_binding(binding)
    if (
        state.get("control") is None
        or state["control"].state.emergency_stop is not True
        or state.get("ledger") is None
        or not state.get("template")
    ):
        _deny("controlled_writer_coverage_latched_binding_required")
    return {
        "session_factory": state["factory"],
        "control": state["control"],
        "ledger": state["ledger"],
        "ledger_scope": state["ledger_scope"],
        "state_template": decode(canonical(state["template"]), 65536),
        "clock": state["clock"],
    }


async def _repository_replay_v1(binding, *, key):
    if key is not _HOOK_KEY:
        _deny("controlled_writer_coverage_private_binding_required")
    return _check_binding(binding)[1]


async def _checked_await(binding, operation):
    try:
        state, stamp = _check_binding(binding)
    except BaseException:
        if type(operation) is CoroutineType:
            operation.close()
        raise
    remaining = 30.0
    if state.get("account") is not None:
        remaining = min(
            (state["anchor"]["monotonic_ns"] + 60_000_000_000 - stamp["monotonic_ns"])
            / 1e9,
            (
                state["template"]["source_current_monotonic_deadline_ns"]
                - stamp["monotonic_ns"]
            )
            / 1e9,
            (
                datetime.fromisoformat(state["template"]["source_current_expires_at"])
                - utc_from_ns(stamp["utc_ns"])
            ).total_seconds(),
        )
    if state.get("control") is not None:
        remaining = min(
            remaining,
            (
                state["control"].state.lease_until - utc_from_ns(stamp["utc_ns"])
            ).total_seconds(),
        )
    if remaining <= 0:
        if type(operation) is CoroutineType:
            operation.close()
        _deny("controlled_writer_coverage_source_expired")
    async with asyncio.timeout(remaining):
        result = await operation
    _check_binding(binding)
    return result


async def _read_control(repository, scope):
    try:
        return await repository.read(scope)
    except control.DemoControlError as error:
        if (
            type(error) is control.DemoControlError
            and str(error) == "control_scope_missing"
        ):
            return None
        raise


def _control_pins(state, bundle, identity):
    session_sha = bundle["route_proof"]["credential_session_sha256"]
    return control.ControlPins(
        config_sha256=sha(
            canonical(
                {
                    "schema_version": "ctcc.controlled_writer_coverage_config.v1",
                    "identity": identity,
                    "source_plan_sha256": state["template"]["source_plan_sha256"],
                    "credential_session_sha256": session_sha,
                    "policy_sha256": POLICY_SHA256,
                    "writer_inventory_sha256": state["inventory"]["writer_sha256"],
                }
            )
        ),
        policy_sha256=POLICY_SHA256,
        credential_session_sha256=session_sha,
    )


def _template(state, held, replay, bundle, identity):
    return {
        "schema_version": "ctcc.demo_controlled_writer_coverage_state.v1",
        "environment": "demo",
        "account_id": identity["uid"],
        "process_domain_nonce": state["process_nonce"],
        "writer_inventory_sha256": state["inventory"]["writer_sha256"],
        "deployed_manifest_sha256": state["inventory"]["manifest_sha256"],
        "policy_sha256": POLICY_SHA256,
        "identity_sha256": sha(canonical(identity)),
        "credential_session_sha256": bundle["route_proof"]["credential_session_sha256"],
        "source_plan_sha256": held["plan_sha256"],
        "source_chain_head_sha256": replay.reference.head_sha256,
        "source_bundle_sha256": held["bundle_sha256"],
        "source_proof_envelope_sha256": replay.proof_envelope_sha256,
        "source_current_receipt_sha256": sha(replay.current_receipt_json),
        "source_native_proof_sha256": replay.native_proof_sha256,
        "source_native_readback_sha256": replay.native_readback_sha256,
        "source_custody_id": held["custody_id"],
        "source_current_expires_at": replay.expires_at.isoformat(),
        "source_current_monotonic_deadline_ns": replay.monotonic_deadline_ns,
        "original_native_anchor": dict(state["anchor"]),
        "account_revision_published": False,
        "account_complete": False,
        "flat_start_permission": False,
        "event_eligibility": False,
        "execution_authority": False,
    }


def _bind_control(state, observation):
    if (
        type(observation) is not controls.ControlObservation
        or observation.state.emergency_stop is not True
        or observation.state.arm_request_id is not None
        or observation.state.owner_sha256 != control.owner_binding(state["owner_token"])
        or observation.state.scope != state["control_scope"]
        or observation.state.pins != state["control_pins"]
        or observation.state.owner_epoch != state["control"].state.owner_epoch
        or observation.state.revision != state["control"].state.revision + 1
        or observation.state.lease_until != state["control"].state.lease_until
    ):
        _deny("controlled_writer_coverage_owned_estop_required")
    bound = observation.state
    state["control"] = observation
    state["template"].update(
        {
            "control_owner_epoch": bound.owner_epoch,
            "control_revision": bound.revision,
            "control_event_sha256": observation.event_sha256,
            "control_state_sha256": control.digest(
                control.canonical(control.document(bound))
            ),
            "control_pins": {
                name: getattr(bound.pins, name)
                for name in (
                    "config_sha256",
                    "policy_sha256",
                    "credential_session_sha256",
                )
            },
            "control_lease_until": bound.lease_until.isoformat(),
            "control_emergency_stop": True,
            "control_arm_request_id": None,
        }
    )


def _observation(binding, value, *, expected_state, previous=None):
    from app.database.repositories.controlled_writer_coverage import (
        CoverageObservationV1,
    )

    if type(value) is not CoverageObservationV1 or value.state != expected_state:
        _deny("controlled_writer_coverage_readback_invalid")
    state, _stamp = _check_binding(binding)
    wire = value.state_document
    if (
        canonical({name: wire[name] for name in state["template"]})
        != canonical(state["template"])
        or wire["state"] != expected_state
        or value.state_sha256 != sha(value.state_json.encode("ascii"))
        or value.audit_sha256 != sha(value.audit_json.encode("ascii"))
        or wire["audit_sha256"] != value.audit_sha256
        or wire["generation"] != value.generation
        or wire["epoch_id"] != value.epoch_id
        or wire["journal_sequence"] != value.journal_sequence
    ):
        _deny("controlled_writer_coverage_source_binding_changed")
    if previous is not None and (
        value.state_json != previous.state_json
        or value.journal_sha256 != previous.journal_sha256
        or value.audit_json != previous.audit_json
        or value.records_json != previous.records_json
    ):
        _deny("controlled_writer_coverage_readback_changed")
    return value


async def acquire_controlled_demo_writer_coverage_v1(
    *,
    account_session,
    private_client,
    session_factory,
    private_proof_root,
    native_proof_root,
    custody_repository,
):
    """No DTO, supplied owner/policy/clock/complete flag can activate coverage."""
    from app.database.repositories.controlled_writer_coverage import (
        ControlledWriterCoverageRepository,
    )

    binding = None
    success = False
    try:
        context = _context()
        inventory = _package_inventory()
        pins = _factory_pins(session_factory, custody_repository)
        if len(_BINDINGS) >= _MAX_OWNERS:
            _deny("controlled_writer_coverage_owner_bound")
        binding = object.__new__(_ActivationBindingV1)
        state = {
            "context": (*context[:3], ref(context[3])),
            "inventory": inventory,
            "factory": session_factory,
            "custody": custody_repository,
            "factory_pins": pins,
            "private_root": private_proof_root,
            "native_root": native_proof_root,
            "source_files": {},
            "account": None,
            "last": native_clock.native_stamp(),
            "control": None,
            "failed": False,
            "process_nonce": secrets.token_hex(16),
            "owner_token": secrets.token_bytes(32),
            "template": {},
        }
        _BINDINGS[binding] = state
        weak_binding = ref(binding)

        def clock():
            held_binding = weak_binding()
            if held_binding is None:
                _deny()
            return utc_from_ns(_check_binding(held_binding)[1]["utc_ns"])

        state["clock"] = clock
        repository = ControlledWriterCoverageRepository(session_factory)
        state["repository"] = repository
        state["repository_type"] = type(repository)
        state["repository_functions"] = tuple(
            (name, getattr(type(repository), name))
            for name in (
                "verify_deployment",
                "_prepare_native",
                "_activate_native",
                "_readback_native",
            )
        )
        await _checked_await(binding, repository.verify_deployment())
        original = await _checked_await(
            binding,
            account.acquire_initial_demo_account_source_v4(
                account_session=account_session,
                private_client=private_client,
                session_factory=session_factory,
                private_proof_root=private_proof_root,
                native_proof_root=native_proof_root,
                custody_repository=custody_repository,
            ),
        )
        held = account._take_controlled_writer_coverage_v1(original, key=_HOOK_KEY)
        replay, stamp = account._replay_controlled_writer_coverage_v1(
            held, key=_HOOK_KEY
        )
        bundle, anchor, identity = _source_material(held, replay)
        state.update(account=held, anchor=anchor, last=stamp)
        state["template"] = _template(state, held, replay, bundle, identity)
        state["source_files"] = {
            "private_root": _source_files(
                private_proof_root, _PRIVATE_NAMES, _MAX_SOURCE_FILE
            ),
            "native_root": _source_files(
                native_proof_root,
                (*proof.FILES, "proof.json", "readback.json"),
                proof.MAX_PROOF,
            ),
        }
        scope = control.ControlScope(environment="demo", account_id=identity["uid"])
        state["control_scope"] = scope
        state["ledger_scope"] = LedgerScope(
            account_id=identity["uid"], settlement_currency="USDT"
        )
        state["ledger"] = QualificationLedgerRepository(session_factory, clock=clock)
        control_repository = controls.DemoControlRepository(
            session_factory, clock=clock
        )
        before = await _checked_await(binding, _read_control(control_repository, scope))
        at = clock()
        if before is not None and (
            before.state.arm_request_id is not None or before.state.lease_until > at
        ):
            _deny("controlled_writer_coverage_control_conflict")
        _check_binding(binding)
        state["control_pins"] = _control_pins(state, bundle, identity)
        acquired = await _checked_await(
            binding,
            control_repository.acquire(
                scope,
                owner_token=state["owner_token"],
                pins=state["control_pins"],
                command_id=secrets.token_hex(32),
            ),
        )
        if (
            type(acquired) is not controls.ControlObservation
            or acquired.state.scope != scope
            or acquired.state.pins != state["control_pins"]
            or acquired.state.owner_sha256
            != control.owner_binding(state["owner_token"])
            or acquired.state.arm_request_id is not None
        ):
            _deny("controlled_writer_coverage_control_readback_invalid")
        state["control"] = acquired
        latched = await _checked_await(
            binding,
            control_repository.change(
                scope,
                owner_token=state["owner_token"],
                owner_epoch=acquired.state.owner_epoch,
                expected_revision=acquired.state.revision,
                action="estop",
                command_id=secrets.token_hex(32),
            ),
        )
        _bind_control(state, latched)
        prepared = _observation(
            binding,
            await _checked_await(
                binding, repository._prepare_native(binding, key=_HOOK_KEY)
            ),
            expected_state="prepared",
        )
        active = _observation(
            binding,
            await _checked_await(
                binding, repository._activate_native(binding, prepared, key=_HOOK_KEY)
            ),
            expected_state="active",
        )
        if (
            active.generation != prepared.generation
            or active.epoch_id != prepared.epoch_id
            or active.journal_sequence != prepared.journal_sequence + 1
            or active.audit_json != prepared.audit_json
        ):
            _deny("controlled_writer_coverage_active_lineage_changed")
        _observation(
            binding,
            await _checked_await(
                binding, repository._readback_native(binding, active, key=_HOOK_KEY)
            ),
            expected_state="active",
            previous=active,
        )
        state["active"] = active
        state["activation_complete"] = dict(_check_binding(binding)[1])
        owner = object.__new__(OwnedControlledDemoWriterCoverageV1)
        _OWNERS[owner] = binding
        success = True
        return owner
    except asyncio.CancelledError:
        raise
    except ControlledWriterCoverageError:
        raise
    except Exception:  # noqa: BLE001 - Redact native failures and deny acquisition.
        _deny("controlled_writer_coverage_acquisition_failed")
    finally:
        if binding is not None and not success:
            state = _BINDINGS.pop(binding, None)
            if state is not None:
                state.clear()


def consume_owned_controlled_demo_writer_coverage_v1(owner):
    """Burn at CALL TIME, including when its fixed awaitable never starts."""
    binding = (
        _OWNERS.pop(owner, None)
        if type(owner) is OwnedControlledDemoWriterCoverageV1
        else None
    )
    if binding is None:
        _deny("controlled_writer_coverage_owner_unavailable")
    try:
        _check_binding(binding)
    except BaseException:  # noqa: BLE001 - Burn the owner for every call-time failure.
        _BINDINGS.pop(binding, None)
        _deny("controlled_writer_coverage_owner_unavailable")

    async def consume():
        try:
            state, _stamp = _check_binding(binding)
            active = state["active"]
            observed = await _checked_await(
                binding,
                state["repository"]._readback_native(binding, active, key=_HOOK_KEY),
            )
            _observation(binding, observed, expected_state="active", previous=active)
            state, stamp = _check_binding(binding)
            receipt = canonical(
                {
                    "schema_version": "ctcc.controlled_demo_writer_coverage_facts.v1",
                    "policy_sha256": POLICY_SHA256,
                    "account_uid_sha256": sha(
                        state["template"]["account_id"].encode("ascii")
                    ),
                    "environment": "demo",
                    "epoch_id": active.epoch_id,
                    "generation": active.generation,
                    "journal_sequence": active.journal_sequence,
                    "journal_sha256": active.journal_sha256,
                    "audit_sha256": active.audit_sha256,
                    "source_bundle_sha256": state["template"]["source_bundle_sha256"],
                    "writer_inventory_sha256": state["inventory"]["writer_sha256"],
                    "activation_readback_complete": state["activation_complete"],
                    "consumed": stamp,
                    "controlled_ctcc_coverage_verified": True,
                    "external_writer_absence": False,
                    "complete_historical_event_coverage": False,
                    "account_complete": False,
                    "account_revision_published": False,
                    "flat_start_permission": False,
                    "event_eligibility": False,
                    "execution_authority": False,
                }
            )
            return ControlledDemoWriterCoverageFactsV1(receipt)
        except asyncio.CancelledError:
            raise
        except ControlledWriterCoverageError:
            raise
        except Exception:  # noqa: BLE001 - Redact native failures and deny consumption.
            _deny("controlled_writer_coverage_consumption_failed")
        finally:
            released = _BINDINGS.pop(binding, None)
            if released is not None:
                released.clear()

    return consume()


def _financial_key_v1(key):
    from app.trade_qualification.native_account_risk_assembly_v1 import _HOOK_KEY

    if key is not _HOOK_KEY:
        _deny("controlled_writer_coverage_financial_assembler_required")


def _take_financial_assembly_v1(owner, *, key):
    """MOVE the existing binding, including its already-MOVEd V4 source."""
    _financial_key_v1(key)
    binding = (
        _OWNERS.pop(owner, None)
        if type(owner) is OwnedControlledDemoWriterCoverageV1
        else None
    )
    if binding is None:
        _deny("controlled_writer_coverage_owner_unavailable")
    try:
        _check_binding(binding)
        _FINANCIAL[binding] = True
        return binding
    except BaseException:
        released = _BINDINGS.pop(binding, None)
        if released is not None:
            released.clear()
        raise


def _release_financial_assembly_v1(binding, *, key):
    _financial_key_v1(key)
    unmoved = type(binding) is OwnedControlledDemoWriterCoverageV1
    if unmoved:
        binding = _OWNERS.pop(binding, None)
    if type(binding) is _ActivationBindingV1 and (
        unmoved or _FINANCIAL.pop(binding, None) is True
    ):
        _FINANCIAL.pop(binding, None)
        released = _BINDINGS.pop(binding, None)
        if released is not None:
            released.clear()


async def _replay_financial_assembly_v1(binding, *, key):
    """Bootstrap only: exact active epoch, Control/B5 and original V4 custody."""
    _financial_key_v1(key)
    if type(binding) is not _ActivationBindingV1 or _FINANCIAL.get(binding) is not True:
        _deny("controlled_writer_coverage_financial_binding_unavailable")
    try:
        state, _stamp = _check_binding(binding)
        active = state["active"]
        observed = await _checked_await(
            binding,
            state["repository"]._readback_native(binding, active, key=_HOOK_KEY),
        )
        _observation(binding, observed, expected_state="active", previous=active)
        state, _stamp = _check_binding(binding)
        retained = await _checked_await(
            binding,
            custody.RuntimeBundleCustodyRepository.read_retained_bundle(
                state["custody"],
                bundle_id=state["template"]["source_custody_id"],
                expected_sha256=state["template"]["source_bundle_sha256"],
                proof_envelope_sha256=state["template"]["source_proof_envelope_sha256"],
            ),
        )
        if (
            type(retained) is not custody.RetainedRuntimeBundleReadback
            or retained.bundle_id != state["template"]["source_custody_id"]
            or retained.bundle_sha256 != state["template"]["source_bundle_sha256"]
            or retained.proof_envelope_sha256
            != state["template"]["source_proof_envelope_sha256"]
            or retained.bundle_raw != state["account"]["bundle_raw"]
        ):
            _deny("controlled_writer_coverage_financial_custody_changed")
        state, stamp = _check_binding(binding)
        replay, stamp = account._replay_controlled_writer_coverage_v1(
            state["account"], key=_HOOK_KEY
        )
        _bundle, _anchor, identity = _source_material(state["account"], replay)
        # Recheck Control/B5 after the additional custody await too.
        observed = await _checked_await(
            binding,
            state["repository"]._readback_native(binding, active, key=_HOOK_KEY),
        )
        _observation(binding, observed, expected_state="active", previous=active)
        state, stamp = _check_binding(binding)
        return {
            "current": replay,
            "identity": identity,
            "control": state["control"],
            "scope": state["ledger_scope"],
            "coverage": active,
            "activation_complete": dict(state["activation_complete"]),
            "checked": dict(stamp),
            "custody_id": state["template"]["source_custody_id"],
        }
    except BaseException:
        _release_financial_assembly_v1(binding, key=key)
        raise


_FUNCTION_PINS = (
    (native_clock, "native_stamp", native_clock.native_stamp),
    (runtime, "_configured_factory", runtime._configured_factory),
    (custody, "_native_engine", custody._native_engine),
    (
        custody.RuntimeBundleCustodyRepository,
        "read_retained_bundle",
        custody.RuntimeBundleCustodyRepository.read_retained_bundle,
    ),
    (
        account,
        "acquire_initial_demo_account_source_v4",
        account.acquire_initial_demo_account_source_v4,
    ),
    (
        account,
        "_take_controlled_writer_coverage_v1",
        account._take_controlled_writer_coverage_v1,
    ),
    (
        account,
        "_replay_controlled_writer_coverage_v1",
        account._replay_controlled_writer_coverage_v1,
    ),
    (
        private_write_authority,
        "require_owned_private_write_authority",
        private_write_authority.require_owned_private_write_authority,
    ),
    (
        execution_authority,
        "enforce_demo_final_dispatch",
        execution_authority.enforce_demo_final_dispatch,
    ),
    (
        live_authority,
        "enforce_live_final_dispatch",
        live_authority.enforce_live_final_dispatch,
    ),
    (controls.DemoControlRepository, "read", controls.DemoControlRepository.read),
    (controls.DemoControlRepository, "acquire", controls.DemoControlRepository.acquire),
    (controls.DemoControlRepository, "change", controls.DemoControlRepository.change),
    (
        controls.DemoControlRepository,
        "_snapshot",
        controls.DemoControlRepository._snapshot,
    ),
    (
        controls.DemoControlRepository,
        "_readback",
        controls.DemoControlRepository._readback,
    ),
    (control, "acquire", control.acquire),
    (control, "advance", control.advance),
    (account, "_check_owner", account._check_owner),
    (account, "_check_current", account._check_current),
    (
        account,
        "replay_current_account_bundle_v4",
        account.replay_current_account_bundle_v4,
    ),
    (storage, "_root_context", storage._root_context),
    (storage, "_root_identity", storage._root_identity),
    (
        QualificationLedgerRepository,
        "_read_portfolio_checkpoint_locked",
        QualificationLedgerRepository._read_portfolio_checkpoint_locked,
    ),
)


def _check_function_pins():
    if any(
        getattr(owner, name) is not function for owner, name, function in _FUNCTION_PINS
    ):
        _deny("controlled_writer_coverage_native_function_changed")
