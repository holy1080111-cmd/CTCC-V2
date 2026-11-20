"""Private Live lifecycle ownership, separate from entry/Arm authority.

Only the fixed spent qualification and its genuine physical response can start
a postsubmit capture. Constructors, saved receipts and pure fact results never
own a credential lease, signer, SQL resolution or controlled close permission.
"""

import asyncio
import hashlib
import hmac
import os
import threading
from contextvars import ContextVar
from copy import deepcopy
from pathlib import Path
from types import FunctionType
from weakref import WeakKeyDictionary

from app.domain.source_primitives import canonical, decode, sha

_POSTSUBMIT_HANDOFF_KEY_V1 = object()
_SOURCE_LEASE_KEY_V1 = object()
_OWNER_KEY_V1 = object()
_OWNERS = WeakKeyDictionary()
_ACTIVE_CAPTURE = ContextVar("ctcc_owned_live_lifecycle_capture_v1", default=None)
_RECEIPTS = WeakKeyDictionary()
_PUBLIC_KEYS = frozenset(
    (
        "schema",
        "status",
        "reservation_id",
        "report_id",
        "risk_hold_retained",
        "protection_verified",
        "emergency_stop",
        "disarmed",
        "execution_authority",
    )
)


class NativeLiveLifecycleError(ValueError):
    pass


def _need(condition, code="native_live_lifecycle_unavailable"):
    if condition is not True:
        raise NativeLiveLifecycleError(code) from None


class OwnedLiveLifecycleV1:
    __slots__ = ("__weakref__",)

    def __init__(self, key=None):
        _need(key is _OWNER_KEY_V1)

    @property
    def execution_authority(self):
        return False


def _record(owner):
    _need(type(owner) is OwnedLiveLifecycleV1)
    state = _OWNERS.get(owner)
    _need(
        type(state) is dict
        and state["pid"] == os.getpid()
        and state["thread"] == threading.get_ident()
        and state["task"] is asyncio.current_task()
        and state["loop"] is asyncio.get_running_loop()
    )
    return state


def _require_active_source_context_v1(source_owner, *, key):
    """Negative-only bridge check; returns no lease, credential or authority."""
    _need(key is _SOURCE_LEASE_KEY_V1)
    state = _record(_ACTIVE_CAPTURE.get())
    _need(state["source_owner"] is source_owner and state["phase"] == "capturing")


def _runtime_repository(runtime_owner):
    from app.database.repositories.native_live_lifecycle_v1 import (
        NativeLiveLifecycleRepositoryV1,
    )
    from app.okx_live import lifecycle_key_v1 as keys
    from app.okx_live import native_runtime_v1 as runtime

    cfg = runtime._replay_live_runtime_v1(runtime_owner, key=runtime._KEY)
    key_owner = keys.load_owned_live_lifecycle_key_v1(runtime_owner)
    repository = NativeLiveLifecycleRepositoryV1(
        cfg["reconciler_factory"], cfg["reconciler_pins"]
    )
    return cfg, key_owner, repository


def _require_complete_entry_flow_v1():
    """An additive SQL foundation alone cannot unlock a positive Live Arm."""
    path = Path(__file__).resolve()
    source = path.read_bytes()
    functions = []
    for name in (
        "reconcile_owned_live_postsubmit_v1",
        "reconcile_pending_owned_live_intent_v1",
        "_capture_owned_live_postsubmit_v1",
        "_verify_source_for_signing_v1",
    ):
        function = globals().get(name)
        _need(
            type(function) is FunctionType and function.__module__ == __name__,
            "native_live_lifecycle_entry_flow_unavailable",
        )
        functions.append(function)
    from app.trade_qualification.native_continuation_guard_v1 import _source_code

    for function in functions:
        _source_code(function, source, str(path))


async def preflight_owned_live_lifecycle_runtime_v1(runtime_owner):
    """No venue calls or Arm: verify original runtime, key and dedicated actor."""
    _require_complete_entry_flow_v1()
    _, key_owner, repository = _runtime_repository(runtime_owner)
    return await repository.preflight(key_owner)


def _verify_source_for_signing_v1(owner, *, key_owner):
    """The HMAC key is available only for one privately captured, retained proof."""
    from app.okx_live.lifecycle_key_v1 import _private_file

    state = _record(owner)
    _need(state["phase"] == "source_verified" and state["key_owner"] is key_owner)
    raw = state["proof_json"]
    _need(type(raw) is bytes and canonical(decode(raw, maximum=65536)) == raw)
    # A real capture retains these exact bytes before the signer can run. The
    # content-addressed, private file is re-read; a caller receipt is insufficient.
    path = state["proof_path"]
    _private_file(path)
    _need(
        type(path) is type(Path())
        and path.is_absolute()
        and path.is_file()
        and path.read_bytes() == raw
        and path.name == sha(raw) + ".json"
    )
    _need(state["source_verified_sha256"] == sha(raw))


def _consume_owned_live_proof_v1(owner, *, key):
    from app.database.repositories import native_live_lifecycle_v1 as repository
    from app.domain.native_live_lifecycle_schema_v1 import DOMAIN
    from app.okx_live import lifecycle_key_v1 as keys

    _need(key is repository._PROOF_CONSUME_KEY_V1)
    state = _record(owner)
    key_owner = state["key_owner"]
    material = keys._material_for_owned_lifecycle_v1(key_owner, owner)
    raw = state["proof_json"]
    signature = hmac.digest(material, DOMAIN + raw, hashlib.sha256).hex()
    state["phase"] = "proof_spent"
    return raw, signature, key_owner


class OwnedLiveLifecycleReceiptV1:
    __slots__ = ("__weakref__",)

    def __new__(cls):
        _need(False)


def _projection(document):
    _need(type(document) is dict and set(document) == _PUBLIC_KEYS)
    _need(
        document["schema"] == "ctcc.native_live.lifecycle_receipt.v1"
        and document["status"]
        in ("ZERO_FILL_FLAT", "PROTECTED_HOLD", "UNCERTAIN", "RECONCILED_FLAT")
        and document["execution_authority"] is False
    )
    import re

    _need(
        type(document["reservation_id"]) is str
        and re.fullmatch(r"[a-f0-9]{64}", document["reservation_id"]) is not None
    )
    _need(
        document["report_id"] is None
        or type(document["report_id"]) is str
        and 0 < len(document["report_id"]) <= 128
    )
    _need(
        all(
            document[name] is None or type(document[name]) is bool
            for name in ("risk_hold_retained", "emergency_stop", "disarmed")
        )
    )
    _need(
        document["protection_verified"] is None
        or document["protection_verified"] is True
    )
    return deepcopy(document)


def _journal_projection(journal, control, *, current_protection=False):
    proof = decode(journal.proofs[-1], maximum=65536) if journal.proofs else None
    terminal = journal.reservation_state == "reconciled"
    return _projection(
        {
            "schema": "ctcc.native_live.lifecycle_receipt.v1",
            "status": proof["outcome"] if proof else "UNCERTAIN",
            "reservation_id": journal.reservation_id,
            "report_id": proof.get("report_id") if proof else None,
            "risk_hold_retained": not terminal,
            "protection_verified": True
            if current_protection is True
            and proof
            and proof["outcome"] == "PROTECTED_HOLD"
            else None,
            "emergency_stop": control["emergency_stop"],
            "disarmed": control["disarmed"],
            "execution_authority": False,
        }
    )


def _new_receipt(journal, control, *, current_protection=False):
    value = _journal_projection(journal, control, current_protection=current_protection)
    receipt = object.__new__(OwnedLiveLifecycleReceiptV1)
    _RECEIPTS[receipt] = canonical(value)
    return receipt


def live_lifecycle_public_projection_v1(receipt):
    _need(type(receipt) is OwnedLiveLifecycleReceiptV1 and receipt in _RECEIPTS)
    return _projection(decode(_RECEIPTS[receipt], maximum=4096))


async def observe_live_lifecycle_tasks_v1():
    """A bounded, complete pending-native snapshot; no legacy intent adoption."""
    from app.okx_live.native_runtime_v1 import configured_live_runtime_v1

    async with configured_live_runtime_v1() as runtime_owner:
        _, key_owner, repository = _runtime_repository(runtime_owner)
        await repository.preflight(key_owner)
        rows = await repository.observe_pending_v1(key_owner)
        journals = tuple(
            [await repository.read_journal_v1(key_owner, row[0]) for row in rows]
        )
        control = await repository.read_control_v1(key_owner)
        return tuple(_journal_projection(item, control) for item in journals)
