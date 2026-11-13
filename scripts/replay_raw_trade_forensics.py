"""Reconstruct or verify one private retained Demo forensic receipt offline."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

from app.domain.source_primitives import canonical, sha
from app.trade_evidence import post_submit, raw_forensics
from app.trade_evidence.native_private_root_v2 import (
    _windows_private,
    require_private_evidence_base_v2,
)
from app.trade_evidence.storage import _posix_root, _root_path, _windows_root
from app.trade_qualification.account_capture import MAX_PACKET_BYTES
from scripts.evidence_json import read_evidence_json

_SCHEMA = "ctcc.raw_trade_forensics_replay_source_pins.v1"
_PIN_FILE = "source-pins.json"
_PATH_FILE = "holding-path.json"
_RECEIPT_FILE = "forensics-receipt.json"
_DIGEST = re.compile(r"[0-9a-f]{64}", re.ASCII)
_PIN_FIELDS = {
    "submission_report_sha256",
    "account_packet_sha256",
    "account_plan_sha256",
    "attribution_sha256",
    "holding_path_sha256",
}


class ForensicReplayRefused(ValueError):
    """Fixed offline refusal without private account or source details."""


def _require(value: bool) -> None:
    if value is not True:
        raise ForensicReplayRefused("raw_trade_forensics_offline_refused") from None


def _is_digest(value: object) -> bool:
    return type(value) is str and _DIGEST.fullmatch(value) is not None


def _read(directory, name: str, maximum: int, *, expected: str | None = None) -> bytes:
    if os.name == "nt":
        _windows_private(directory.path / name)
    raw = directory.read(name, maximum)
    if expected is not None:
        _require(_is_digest(expected) and sha(raw) == expected)
    return raw


def _sources(directory, pins_sha256: str) -> tuple[bytes, dict]:
    raw = _read(directory, _PIN_FILE, 8192, expected=pins_sha256)
    pins = read_evidence_json(raw)
    _require(
        type(pins) is dict
        and set(pins) == {"schema", *_PIN_FIELDS}
        and pins["schema"] == _SCHEMA
        and canonical(pins) == raw
        and all(
            _is_digest(pins[name]) for name in _PIN_FIELDS - {"holding_path_sha256"}
        )
        and (
            pins["holding_path_sha256"] is None
            or _is_digest(pins["holding_path_sha256"])
        )
    )
    _require(
        (_PATH_FILE in directory.names()) == (pins["holding_path_sha256"] is not None)
    )
    holding_path = (
        _read(
            directory,
            _PATH_FILE,
            raw_forensics.MAX_PATH_BYTES,
            expected=pins["holding_path_sha256"],
        )
        if pins["holding_path_sha256"] is not None
        else None
    )
    submission = _read(
        directory,
        "submission.json",
        post_submit.MAX_REPORT_BYTES,
        expected=pins["submission_report_sha256"],
    )
    account = _read(
        directory,
        "account.json",
        MAX_PACKET_BYTES,
        expected=pins["account_packet_sha256"],
    )
    attribution = _read(
        directory,
        "attribution.json",
        raw_forensics.MAX_ATTRIBUTION_BYTES,
        expected=pins["attribution_sha256"],
    )
    return submission, {
        "expected_submission_sha256": pins["submission_report_sha256"],
        "account_bytes": account,
        "expected_account_sha256": pins["account_packet_sha256"],
        "expected_account_plan_sha256": pins["account_plan_sha256"],
        "attribution_bytes": attribution,
        "expected_attribution_sha256": pins["attribution_sha256"],
        "holding_path_bytes": holding_path,
    }


def replay_raw_trade_forensics(
    source_directory: Path,
    *,
    source_pins_sha256: str,
    receipt_sha256: str | None = None,
) -> str:
    """Publish once or independently verify the existing receipt."""
    try:
        _require(
            _is_digest(source_pins_sha256)
            and (receipt_sha256 is None or _is_digest(receipt_sha256))
        )
        root = _root_path(require_private_evidence_base_v2(str(source_directory)))
        open_root = _windows_root if os.name == "nt" else _posix_root
        if receipt_sha256 is None:
            with open_root(root) as directory:
                _require(_RECEIPT_FILE not in directory.names())
                submission, operands = _sources(directory, source_pins_sha256)
                result = raw_forensics.reconstruct_trade_forensics(
                    submission, **operands
                )
                directory.publish(_RECEIPT_FILE, result.canonical_receipt)
                receipt_sha256 = result.receipt_sha256
        # Reopen the native directory and all original pinned source bytes.
        # File readback alone cannot verify the computation in a prior receipt.
        with open_root(root) as directory:
            submission, operands = _sources(directory, source_pins_sha256)
            saved = _read(
                directory,
                _RECEIPT_FILE,
                raw_forensics.MAX_RECEIPT_BYTES,
                expected=receipt_sha256,
            )
            verified = raw_forensics.verify_raw_forensics_receipt(
                saved, receipt_sha256, submission, **operands
            )
        return verified.receipt_sha256
    except ForensicReplayRefused:
        raise
    except Exception:  # noqa: BLE001 -- redact paths, account data and parser detail.
        raise ForensicReplayRefused("raw_trade_forensics_offline_refused") from None


class _PrivateParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        _require(False)


def main(argv: list[str] | None = None) -> int:
    parser = _PrivateParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--source-directory", type=Path, required=True)
    parser.add_argument("--source-pins-sha256", required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--reconstruct", action="store_true")
    mode.add_argument("--verify-receipt-sha256")
    try:
        args = parser.parse_args(argv)
        if not args.reconstruct:
            _require(_is_digest(args.verify_receipt_sha256))
        digest = replay_raw_trade_forensics(
            args.source_directory,
            source_pins_sha256=args.source_pins_sha256,
            receipt_sha256=args.verify_receipt_sha256,
        )
    except (ForensicReplayRefused, KeyboardInterrupt):
        print('{"admission":"DENY","status":"REFUSED"}', file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "schema": "ctcc.raw_trade_forensics_offline_replay_summary.v1",
                "status": "REPLAY_VERIFIED",
                "receipt_sha256": digest,
                "ingestion_completeness_verified": False,
                "source_authenticity_verified": False,
                "real_trade_sample_verified": False,
                "execution_authority": False,
                "admission": "DENY",
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
