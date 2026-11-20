"""Private immutable lifecycle HMAC key custody; no free-payload signing API.

The owner installer creates one independent random32-byte secret per exact UID.
Runtime can load only its original private descriptor and later use the key
through the original lifecycle's source-owned signing boundary.
"""

import asyncio
import hashlib
import hmac
import os
import re
import stat
from pathlib import Path
from weakref import WeakKeyDictionary

from app.database.native_pg_tls_v1 import _read_bounded_regular_file_v1
from app.domain.source_primitives import canonical, decode, sha
from app.trade_evidence.native_private_root_v2 import (
    _windows_private,
    require_private_evidence_base_v2,
)

SCHEMA = "ctcc.native_live.lifecycle_key.v1"
_KEY_HANDLE = object()
_KEYS = WeakKeyDictionary()
_HEX = re.compile(r"[a-f0-9]{64}\Z")
_UID = re.compile(r"[1-9][0-9]{0,39}\Z")
_FIELDS = {
    "schema",
    "environment",
    "account_uid",
    "main_uid",
    "database_oid",
    "owner_oid",
    "reconciler_oid",
    "origin",
    "key_id",
    "key_hex",
}


class LiveLifecycleKeyError(ValueError):
    pass


def _need(condition):
    if condition is not True:
        raise LiveLifecycleKeyError("native_live_lifecycle_key_unavailable") from None


class OwnedLiveLifecycleKeyV1:
    __slots__ = ("__weakref__",)

    def __new__(cls):
        _need(False)

    def __copy__(self):
        _need(False)

    def __deepcopy__(self, memo):
        _need(False)

    def __reduce_ex__(self, protocol):
        _need(False)


def _private_file(path):
    _need(
        type(path) is type(Path())
        and path.is_absolute()
        and path.resolve(strict=True) == path
    )
    require_private_evidence_base_v2(str(path.parent))
    value = path.lstat()
    _need(
        stat.S_ISREG(value.st_mode)
        and value.st_nlink == 1
        and not getattr(value, "st_file_attributes", 0) & 1024
    )
    if os.name == "nt":
        _windows_private(path)
    else:
        _need(value.st_uid == os.geteuid() and not value.st_mode & 0o077)


def key_identity_v1(document):
    """Owner setup's deterministic public fingerprint, not key material."""
    _need(type(document) is dict and set(document) == _FIELDS)
    _need(document["schema"] == SCHEMA and document["environment"] == "live")
    _need(
        all(
            type(document[name]) is str and _UID.fullmatch(document[name]) is not None
            for name in ("account_uid", "main_uid")
        )
    )
    _need(
        all(
            type(document[name]) is int and 0 < document[name] < 2**32
            for name in ("database_oid", "owner_oid", "reconciler_oid")
        )
    )
    _need(
        document["reconciler_oid"] != document["owner_oid"]
        and document["origin"] in ("https://openapi.okx.com", "https://eea.okx.com")
    )
    _need(
        type(document["key_hex"]) is str
        and _HEX.fullmatch(document["key_hex"]) is not None
    )
    return sha(
        canonical(
            [
                SCHEMA,
                "live",
                document["account_uid"],
                document["main_uid"],
                document["database_oid"],
                document["owner_oid"],
                document["reconciler_oid"],
                document["origin"],
                sha(bytes.fromhex(document["key_hex"])),
            ]
        )
    )


def _runtime_state(owner):
    from app.okx_live import native_runtime_v1 as runtime

    return runtime._replay_live_runtime_v1(owner, key=runtime._KEY)


def load_owned_live_lifecycle_key_v1(runtime_owner):
    """Load original runtime's descriptor; no caller path or raw secret result."""
    try:
        state = _runtime_state(runtime_owner)
        descriptor = state["journal_key_descriptor"]
        _need(type(descriptor) is dict and set(descriptor) == {"path", "sha256"})
        _need(
            type(descriptor["path"]) is str
            and type(descriptor["sha256"]) is str
            and _HEX.fullmatch(descriptor["sha256"]) is not None
        )
        path = Path(descriptor["path"])
        _private_file(path)
        before = path.lstat()
        raw = _read_bounded_regular_file_v1(path, 4096)
        _need(sha(raw) == descriptor["sha256"])
        document = decode(raw, maximum=4096)
        _need(
            canonical(document) == raw
            and key_identity_v1(document) == document["key_id"]
        )
        pins = state["reconciler_pins"]
        _need(
            (
                document["database_oid"],
                document["owner_oid"],
                document["reconciler_oid"],
            )
            == (pins.database_oid, pins.owner_oid, pins.reconciler_oid)
        )
        _need(
            document["origin"]
            == {"global": "https://openapi.okx.com", "eea": "https://eea.okx.com"}[
                state["region"]
            ]
        )
        _private_file(path)
        after = path.lstat()
        _need(
            (
                before.st_dev,
                before.st_ino,
                before.st_mtime_ns,
                before.st_ctime_ns,
                before.st_nlink,
            )
            == (
                after.st_dev,
                after.st_ino,
                after.st_mtime_ns,
                after.st_ctime_ns,
                after.st_nlink,
            )
        )
        _need(
            _runtime_state(runtime_owner) is state
            and _read_bounded_regular_file_v1(path, 4096) == raw
        )
        result = object.__new__(OwnedLiveLifecycleKeyV1)
        _KEYS[result] = {
            "runtime_owner": runtime_owner,
            "state": state,
            "path": path,
            "raw": raw,
            "document": document,
            "task": asyncio.current_task(),
            "pid": os.getpid(),
        }
        return result
    except LiveLifecycleKeyError:
        raise
    except Exception:  # noqa: BLE001 -- secret bytes, descriptors and private paths never escape.
        _need(False)


def _record(owner):
    _need(type(owner) is OwnedLiveLifecycleKeyV1)
    value = _KEYS.get(owner)
    _need(
        type(value) is dict
        and value["pid"] == os.getpid()
        and value["task"] is asyncio.current_task()
    )
    _need(_runtime_state(value["runtime_owner"]) is value["state"])
    _private_file(value["path"])
    _need(_read_bounded_regular_file_v1(value["path"], 4096) == value["raw"])
    return value


def lifecycle_key_metadata_v1(owner):
    document = _record(owner)["document"]
    return {
        **{
            key: document[key]
            for key in (
                "environment",
                "account_uid",
                "main_uid",
                "database_oid",
                "owner_oid",
                "reconciler_oid",
                "origin",
                "key_id",
            )
        },
        "key_material_sha256": sha(bytes.fromhex(document["key_hex"])),
    }


def _material_for_owned_lifecycle_v1(key_owner, lifecycle_owner):
    from app.okx_live import owned_lifecycle_v1 as lifecycle

    record = _record(key_owner)
    lifecycle._verify_source_for_signing_v1(lifecycle_owner, key_owner=key_owner)
    _need(
        lifecycle._record(lifecycle_owner)["runtime_owner"] is record["runtime_owner"]
    )
    return bytes.fromhex(record["document"]["key_hex"])


def _verify_retained_lifecycle_signature_v1(key_owner, raw, signature):
    """Verify stored bytes without exposing a key or a message-signing oracle."""
    from app.domain.native_live_lifecycle_schema_v1 import DOMAIN

    record = _record(key_owner)
    _need(
        type(raw) is bytes
        and 0 < len(raw) <= 65536
        and raw.isascii()
        and type(signature) is str
        and _HEX.fullmatch(signature) is not None
    )
    expected = hmac.digest(
        bytes.fromhex(record["document"]["key_hex"]), DOMAIN + raw, hashlib.sha256
    )
    _need(hmac.compare_digest(expected, bytes.fromhex(signature)))
    return True


def _verify_retained_controlled_close_intent_signature_v1(key_owner, raw, signature):
    """Verify one stored close intent; never sign or accept a caller domain."""
    from app.domain.native_live_controlled_close_schema_v1 import INTENT_DOMAIN

    _need(INTENT_DOMAIN == b"ctcc-native-live-controlled-close-intent-v1\x00")
    record = _record(key_owner)
    _need(
        type(raw) is bytes
        and 0 < len(raw) <= 65536
        and raw.isascii()
        and type(signature) is str
        and _HEX.fullmatch(signature) is not None
    )
    expected = hmac.digest(
        bytes.fromhex(record["document"]["key_hex"]),
        INTENT_DOMAIN + raw,
        hashlib.sha256,
    )
    _need(hmac.compare_digest(expected, bytes.fromhex(signature)))
    return True


def _material_for_owned_controlled_close_v1(key_owner, close_owner):
    """Only original measured close/claim/terminal bytes can reach the key."""
    from app.okx_live import owned_close_v1 as close

    record = _record(key_owner)
    close._verify_source_for_signing_v1(close_owner, key_owner=key_owner)
    _need(close._record(close_owner)["runtime_owner"] is record["runtime_owner"])
    return bytes.fromhex(record["document"]["key_hex"])


def _verify_retained_controlled_close_claim_signature_v1(key_owner, raw, signature):
    """Verify one stored action claim under its separate, fixed domain."""
    from app.domain.native_live_controlled_close_schema_v1 import CLAIM_DOMAIN

    _need(CLAIM_DOMAIN == b"ctcc-native-live-controlled-close-claim-v1\x00")
    record = _record(key_owner)
    _need(
        type(raw) is bytes
        and 0 < len(raw) <= 32768
        and raw.isascii()
        and type(signature) is str
        and _HEX.fullmatch(signature) is not None
    )
    expected = hmac.digest(
        bytes.fromhex(record["document"]["key_hex"]),
        CLAIM_DOMAIN + raw,
        hashlib.sha256,
    )
    _need(hmac.compare_digest(expected, bytes.fromhex(signature)))
    return True
