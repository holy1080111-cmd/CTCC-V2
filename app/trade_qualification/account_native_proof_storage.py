"""No-clobber companion files in the existing native artifact storage domain.

Windows provides file flush/readback, not atomic power-loss directory durability.
A preserved partial/source-only attempt is never accepted or resumed as current.
"""

import os
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from app.domain.native_clock import native_stamp
from app.domain.source_primitives import (
    canonical,
    sha,
    utc_from_ns,
    validate_stamps,
)
from app.trade_evidence.storage import (
    _posix_root,
    _root_path,
    _windows_native_path,
    _windows_root,
)
from app.trade_qualification import account_native_proof as proof

READBACK_SCHEMA = "ctcc.demo_account_native_clock_readback.v2"
V3_READBACK_SCHEMA = "ctcc.demo_account_native_clock_readback.v3"
EXPOSED_V4_READBACK_SCHEMA = "ctcc.demo_account_exposed_native_clock_readback.v4"
MAX_PART = 512 * 1024


def _readback_schema(document):
    if type(document) is not dict:
        raise proof.NativeAccountProofError("native_account_proof_contract_invalid")
    schema = document.get("schema_version")
    if schema == proof.SCHEMA:
        return READBACK_SCHEMA
    if schema == proof.V3_SCHEMA:
        return V3_READBACK_SCHEMA
    if schema == proof.EXPOSED_V4_SCHEMA:
        return EXPOSED_V4_READBACK_SCHEMA
    raise proof.NativeAccountProofError("native_account_proof_contract_invalid")


def _root_context(root):
    if type(root) is not Path:
        raise proof.NativeAccountProofError("native_path_required")
    return (_windows_root if os.name == "nt" else _posix_root)(_root_path(root))


def _root_identity(root):
    info = os.stat(
        _windows_native_path(root) if os.name == "nt" else root,
        follow_symlinks=False,
    )
    return info.st_dev, info.st_ino


@contextmanager
def _companion_attempt(root):
    """Keep partial accepted files after any late failure; never replace them."""
    with _root_context(root) as directory:
        if directory.names():
            raise proof.NativeAccountProofError(
                "native_account_companion_root_not_empty"
            )
        yield directory


def _publish_part(directory, name, raw):
    if (
        type(name) is not str
        or name not in proof.FILES
        or type(raw) is not bytes
        or not 0 < len(raw) <= MAX_PART
    ):
        raise proof.NativeAccountProofError("native_account_companion_part_invalid")
    directory.publish(name, raw)
    if directory.read(name, MAX_PART) != raw:
        raise proof.NativeAccountProofError(
            "native_account_companion_part_readback_failed"
        )


def _seal_companion(directory, raw, *, chain, scope):
    files = {name: directory.read(name, MAX_PART) for name in proof.FILES}
    proof.replay_native_account_proof(
        raw, files=files, chain=chain, scope=scope, expected_proof_sha256=sha(raw)
    )
    if set(directory.names()) != set(proof.FILES):
        raise proof.NativeAccountProofError(
            "native_account_companion_inventory_invalid"
        )
    directory.publish("proof.json", raw)
    if directory.read("proof.json", proof.MAX_PROOF) != raw:
        raise proof.NativeAccountProofError("native_account_companion_readback_failed")
    completed = native_stamp()
    document = proof._canonical_document(raw)
    validate_stamps((document["proof_persist_start"], completed))
    if completed["monotonic_ns"] >= document["monotonic_deadline_ns"] or utc_from_ns(
        completed["utc_ns"]
    ) >= datetime.fromisoformat(document["expires_at"]):
        raise proof.NativeAccountProofError("native_account_companion_readback_expired")
    device, inode = _root_identity(directory.path)
    receipt = canonical(
        {
            "schema_version": _readback_schema(document),
            "proof_sha256": sha(raw),
            "clock_file_sha256": {name: sha(files[name]) for name in proof.FILES},
            "root_device": device,
            "root_inode": inode,
            "readback_complete": completed,
            "execution_authority": False,
        }
    )
    directory.publish("readback.json", receipt)
    if directory.read("readback.json", MAX_PART) != receipt:
        raise proof.NativeAccountProofError("native_account_companion_readback_failed")
    return sha(receipt)


def read_native_account_companion(
    root, *, chain, scope, expected_proof_sha256, expected_readback_sha256
):
    """Separate root handle/readback. Audit replay never creates an owner."""
    try:
        with _root_context(root) as directory:
            if set(directory.names()) != {*proof.FILES, "proof.json", "readback.json"}:
                raise proof.NativeAccountProofError(
                    "native_account_companion_inventory_invalid"
                )
            raw = directory.read("proof.json", proof.MAX_PROOF)
            document = proof._canonical_document(raw)
            files = {name: directory.read(name, MAX_PART) for name in proof.FILES}
            receipt_raw = directory.read("readback.json", MAX_PART)
            receipt = proof._canonical_document(receipt_raw, MAX_PART)
            if (
                type(expected_readback_sha256) is not str
                or sha(receipt_raw) != expected_readback_sha256
                or set(receipt)
                != {
                    "schema_version",
                    "proof_sha256",
                    "clock_file_sha256",
                    "root_device",
                    "root_inode",
                    "readback_complete",
                    "execution_authority",
                }
                or receipt["schema_version"] != _readback_schema(document)
                or receipt["proof_sha256"] != expected_proof_sha256
                or receipt["clock_file_sha256"]
                != {name: sha(files[name]) for name in proof.FILES}
                or type(receipt["root_device"]) is not int
                or type(receipt["root_inode"]) is not int
                or (receipt["root_device"], receipt["root_inode"])
                != _root_identity(directory.path)
                or receipt["execution_authority"] is not False
            ):
                raise proof.NativeAccountProofError(
                    "native_account_companion_readback_binding_invalid"
                )
            result = proof.replay_native_account_proof(
                raw,
                files=files,
                chain=chain,
                scope=scope,
                expected_proof_sha256=expected_proof_sha256,
            )
            validate_stamps(
                (document["proof_persist_start"], receipt["readback_complete"])
            )
            if (
                receipt["readback_complete"]["monotonic_ns"]
                >= result.monotonic_deadline_ns
                or utc_from_ns(receipt["readback_complete"]["utc_ns"])
                >= result.expires_at
            ):
                raise proof.NativeAccountProofError(
                    "native_account_companion_readback_expired"
                )
            return result, receipt_raw
    except proof.NativeAccountProofError:
        raise
    except Exception:  # noqa: BLE001 -- don't expose native path/UID/SQL details
        raise proof.NativeAccountProofError(
            "native_account_companion_readback_failed"
        ) from None
