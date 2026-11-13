"""Private file custody for one Live account-config observation.

POSIX flushes directory entries. Windows flushes file data and verifies the
entries by reopening them. A later missing or partial file fails readback.
"""

from __future__ import annotations

import os
import re
import stat
from pathlib import Path

from app.demo_automation.native_private_root_v2 import (
    _windows_private,
    mkdir_private_evidence_child_v2,
    require_private_evidence_base_v2,
)
from app.domain.source_primitives import ClockStamp, canonical, decode, sha
from app.exchange.okx.live_account_config_observation_v1 import (
    MAX_BODY,
    MAX_HEADER_BYTES,
    MAX_HEADERS,
    LiveAccountConfigObservationV1,
    _checked_response_headers,
    _receipt_document,
    _stamp,
    _validate_clock,
    _validate_signed_time,
    observe_live_account_config_v1,
    verify_live_account_config_readback_v1,
)
from app.exchange.okx.live_read_lease_v1 import (
    _LIVE_HOSTS,
    _UID,
    LivePinnedGetLeaseV1,
    OkxLivePinnedGetClientV1,
)
from app.trade_evidence.native_private_root_v2 import (
    _open_new_windows_private_evidence_file,
)
from app.trade_evidence.storage import (
    _read_fd,
    _regular_file,
    _windows_native_path,
    _WindowsDirectory,
    _write_fd,
)
from app.trade_qualification.account_native_proof_storage import (
    _root_context,
    _root_identity,
)

SCHEMA = "ctcc.live_account_config_private_custody.v1"
_LIMITS = {
    "body.raw": MAX_BODY,
    "headers.raw": MAX_HEADER_BYTES + 8 * MAX_HEADERS,
    "observation.json": 8192,
    "custody.json": 2048,
}
_DATA_FILES = frozenset(_LIMITS) - {"custody.json"}
_ATTEMPT = re.compile(r"[0-9a-f]{32}")
_DIGEST = re.compile(r"[0-9a-f]{64}")
_LIVE_ORIGINS = frozenset("https://" + host for host in _LIVE_HOSTS)


class LiveConfigCustodyError(ValueError):
    """Fixed storage failure without private data or paths."""


def _deny(code: str) -> None:
    raise LiveConfigCustodyError(code) from None


def _check_name(attempt_id: str) -> None:
    if type(attempt_id) is not str or _ATTEMPT.fullmatch(attempt_id) is None:
        _deny("live_config_custody_attempt_invalid")


def _check_directory(directory) -> tuple[int, int]:
    require_private_evidence_base_v2(str(directory.path))
    if type(directory) is _WindowsDirectory:
        return _root_identity(directory.path)
    info = os.fstat(directory.fd)
    if info.st_uid != os.geteuid() or info.st_mode & 0o077:
        _deny("live_config_custody_directory_not_private")
    return info.st_dev, info.st_ino


def _check_file(directory, name, fd) -> None:
    info = os.fstat(fd)
    _regular_file(info)
    if type(directory) is _WindowsDirectory:
        _windows_private(_windows_native_path(directory.path / name))
    elif info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077:
        _deny("live_config_custody_file_not_private")


def _read(directory, name: str, *, body_limit: int = MAX_BODY) -> bytes:
    if type(directory) is _WindowsDirectory:
        fd = directory.api.open_file(directory.path / name)
    else:
        fd = os.open(
            name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory.fd
        )
    try:
        _check_file(directory, name, fd)
        raw = _read_fd(fd, body_limit if name == "body.raw" else _LIMITS[name])
        _check_file(directory, name, fd)
        return raw
    finally:
        os.close(fd)


def _publish(directory, name: str, raw: bytes, *, body_limit: int = MAX_BODY) -> None:
    if not 0 < len(raw) <= (body_limit if name == "body.raw" else _LIMITS[name]):
        _deny("live_config_custody_file_size_invalid")
    if type(directory) is _WindowsDirectory:
        # Check the ACL of the reserved file before any private bytes are written.
        fd = _open_new_windows_private_evidence_file(directory, name)
        try:
            _check_file(directory, name, fd)
            _write_fd(fd, raw)
        finally:
            os.close(fd)
    else:
        directory.publish(name, raw)


def _encode_headers(headers: tuple[tuple[bytes, bytes], ...]) -> bytes:
    return b"".join(
        len(part).to_bytes(4, "big") + part for pair in headers for part in pair
    )


def _decode_headers(raw: bytes) -> tuple[tuple[bytes, bytes], ...]:
    parts = []
    cursor = 0
    while cursor < len(raw):
        if len(raw) - cursor < 4 or len(parts) == MAX_HEADERS * 2:
            _deny("live_config_custody_headers_invalid")
        size = int.from_bytes(raw[cursor : cursor + 4], "big")
        cursor += 4
        if size > len(raw) - cursor:
            _deny("live_config_custody_headers_invalid")
        parts.append(raw[cursor : cursor + size])
        cursor += size
    if len(parts) % 2:
        _deny("live_config_custody_headers_invalid")
    return tuple(zip(parts[::2], parts[1::2], strict=True))


def _observation(files: dict[str, bytes]) -> LiveAccountConfigObservationV1:
    raw_meta = files["observation.json"]
    meta = decode(raw_meta, maximum=_LIMITS["observation.json"])
    body = files["body.raw"]
    headers = _decode_headers(files["headers.raw"])
    observed = LiveAccountConfigObservationV1(
        raw_body=body,
        raw_headers=headers,
        signed_at=meta["signed_at"],
        stamps=tuple(ClockStamp.model_validate(stamp) for stamp in meta["stamps"]),
        lease_binding_id=meta["lease_binding_id"],
        origin=meta["origin"],
        raw_sha256=meta["raw_sha256"],
        canonical_sha256=meta["canonical_sha256"],
        headers_sha256=meta["headers_sha256"],
        request_signature_sha256=meta["request_signature_sha256"],
        expected_uid_sha256=meta["expected_uid_sha256"],
        expected_main_uid_sha256=meta["expected_main_uid_sha256"],
        tls_certificate_sha256=meta["tls_certificate_sha256"],
        receipt_sha256=sha(raw_meta),
        synthetic_mock=meta["synthetic_mock"],
    )
    declared = _checked_response_headers(headers, private_tokens=())
    payload = decode(body, maximum=MAX_BODY)
    rows = payload["data"]
    if (
        raw_meta != canonical(_receipt_document(observed))
        or type(observed.synthetic_mock) is not bool
        or observed.origin not in _LIVE_ORIGINS
        or _DIGEST.fullmatch(observed.lease_binding_id) is None
        or len(observed.stamps) != 6
        or (observed.tls_certificate_sha256 is None) != observed.synthetic_mock
        or (
            observed.tls_certificate_sha256 is not None
            and _DIGEST.fullmatch(observed.tls_certificate_sha256) is None
        )
        or _DIGEST.fullmatch(observed.request_signature_sha256) is None
        or (declared is not None and declared != len(body))
        or sha(body) != observed.raw_sha256
        or sha(files["headers.raw"]) != observed.headers_sha256
        or sha(canonical(payload)) != observed.canonical_sha256
        or payload["code"] != "0"
        or type(rows) is not list
        or len(rows) != 1
        or _UID.fullmatch(rows[0]["uid"]) is None
        or _UID.fullmatch(rows[0]["mainUid"]) is None
        or sha(rows[0]["uid"].encode()) != observed.expected_uid_sha256
        or sha(rows[0]["mainUid"].encode()) != observed.expected_main_uid_sha256
    ):
        _deny("live_config_custody_observation_invalid")
    _validate_clock(observed.stamps)
    _validate_signed_time(observed.signed_at, *observed.stamps[:2])
    return observed


def _seal(
    attempt_id, base_identity, attempt_identity, files, readback_complete
) -> bytes:
    return canonical(
        {
            "schema_version": SCHEMA,
            "attempt_id": attempt_id,
            "base_identity": list(base_identity),
            "attempt_identity": list(attempt_identity),
            "files_sha256": {name: sha(raw) for name, raw in files.items()},
            "raw_readback_complete": readback_complete.model_dump(),
            "account_complete": False,
            "source_authenticity_verified": False,
            "execution_authority": False,
        }
    )


def _read_files(directory, names, *, body_limit: int = MAX_BODY) -> dict[str, bytes]:
    if set(directory.names()) != names:
        _deny("live_config_custody_inventory_invalid")
    return {
        name: _read(directory, name, body_limit=body_limit) for name in sorted(names)
    }


def _read_custodied_live_account_config(
    private_root: Path, *, attempt_id: str, expected_custody_sha256: str
) -> tuple[dict, LiveAccountConfigObservationV1]:
    _check_name(attempt_id)
    if (
        type(expected_custody_sha256) is not str
        or _DIGEST.fullmatch(expected_custody_sha256) is None
    ):
        _deny("live_config_custody_expected_digest_required")
    try:
        with _root_context(private_root) as base, base.child(attempt_id) as attempt:
            base_identity = _check_directory(base)
            attempt_identity = _check_directory(attempt)
            files = _read_files(attempt, set(_LIMITS))
            seal = files.pop("custody.json")
            if sha(seal) != expected_custody_sha256:
                _deny("live_config_custody_digest_mismatch")
            seal_document = decode(seal, maximum=_LIMITS["custody.json"])
            readback_complete = ClockStamp.model_validate(
                seal_document["raw_readback_complete"]
            )
            if seal != _seal(
                attempt_id, base_identity, attempt_identity, files, readback_complete
            ):
                _deny("live_config_custody_digest_mismatch")
            observed = _observation(files)
            _validate_clock((*observed.stamps, readback_complete))
            if set(attempt.names()) != set(_LIMITS):
                _deny("live_config_custody_inventory_invalid")
            return {
                "schema_version": SCHEMA,
                "attempt_id": attempt_id,
                "custody_sha256": expected_custody_sha256,
                "observation_sha256": observed.receipt_sha256,
                "synthetic_mock": observed.synthetic_mock,
                "account_complete": False,
                "source_authenticity_verified": False,
                "execution_authority": False,
            }, observed
    except LiveConfigCustodyError:
        raise
    except Exception:  # noqa: BLE001 - private file, path and parser errors stay private
        _deny("live_config_custody_read_failed")


def read_live_account_config_custody_v1(
    private_root: Path, *, attempt_id: str, expected_custody_sha256: str
) -> dict:
    """Replay a stored attempt against a digest supplied from outside its directory."""
    receipt, _ = _read_custodied_live_account_config(
        private_root,
        attempt_id=attempt_id,
        expected_custody_sha256=expected_custody_sha256,
    )
    return receipt


async def capture_live_account_config_to_private_custody_v1(
    client: OkxLivePinnedGetClientV1,
    lease: LivePinnedGetLeaseV1,
    *,
    private_root: Path,
    attempt_id: str,
) -> dict:
    """Reserve one attempt, capture once, and seal only after separate readback."""
    _check_name(attempt_id)
    try:
        lease.verify_current()
        with _root_context(private_root) as base:
            base_identity = _check_directory(base)
            mkdir_private_evidence_child_v2(base, attempt_id)
            with base.child(attempt_id) as attempt:
                attempt_identity = _check_directory(attempt)
                _read_files(attempt, frozenset())
                observed = await observe_live_account_config_v1(client, lease)
                verify_live_account_config_readback_v1(observed, lease=lease)
                files = {
                    "body.raw": observed.raw_body,
                    "headers.raw": _encode_headers(observed.raw_headers),
                    "observation.json": canonical(_receipt_document(observed)),
                }
                _check_directory(attempt)
                for name, raw in files.items():
                    _publish(attempt, name, raw)
        with _root_context(private_root) as base, base.child(attempt_id) as attempt:
            if (_check_directory(base), _check_directory(attempt)) != (
                base_identity,
                attempt_identity,
            ):
                _deny("live_config_custody_directory_changed")
            saved = _read_files(attempt, _DATA_FILES)
            if saved != files:
                _deny("live_config_custody_readback_changed")
            replayed = _observation(saved)
            verify_live_account_config_readback_v1(replayed, lease=lease)
            readback_complete = _stamp()
            _validate_clock((*replayed.stamps, readback_complete))
            seal = _seal(
                attempt_id, base_identity, attempt_identity, saved, readback_complete
            )
            _publish(attempt, "custody.json", seal)
        return read_live_account_config_custody_v1(
            private_root, attempt_id=attempt_id, expected_custody_sha256=sha(seal)
        )
    except LiveConfigCustodyError:
        raise
    except Exception:  # noqa: BLE001 - failed attempts remain reserved, without private errors
        _deny("live_config_custody_capture_failed")
