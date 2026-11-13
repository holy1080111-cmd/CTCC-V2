"""Explicit DB0056 restricted startup observation; always diagnostic DENY.

This command reads only the configured PostgreSQL roles. Its caller-supplied
reference digest does not authenticate a deployment, so a matching observation
cannot admit an API lifespan or grant execution authority.

Exit 2 means caller pins matched a real read-only observation but admission is
still DENY. Exit 1 means even that observation was refused. No path returns 0.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import stat
import sys
from pathlib import Path

from app.core.native_startup_candidate_0056_v1 import (
    NativeStartupCandidate0056PinsV1,
    observe_selected_native_startup_candidate_0056_v1,
)

_SCHEMA = "ctcc.native_only.startup0056_cli_diagnostic.v1"
_PIN_SCHEMA = "ctcc.native_only.startup0056_caller_pins.v1"
_HEX = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_PIN_FIELDS = tuple(NativeStartupCandidate0056PinsV1.__dataclass_fields__)
_SOURCE = Path(__file__).resolve().parents[1]


class DiagnosticRefusal(ValueError):
    """Fixed refusal whose text never includes settings or database content."""


def _need(condition: bool) -> None:
    if condition is not True:
        raise DiagnosticRefusal("native_startup0056_diagnostic_refused")


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    value = {}
    for key, item in pairs:
        _need(key not in value)
        value[key] = item
    return value


def _parse_pins(raw: bytes) -> NativeStartupCandidate0056PinsV1:
    _need(type(raw) is bytes and 0 < len(raw) <= 4096)
    value = json.loads(raw.decode("ascii"), object_pairs_hook=_unique_object)
    _need(type(value) is dict and set(value) == {"schema", *_PIN_FIELDS})
    _need(value["schema"] == _PIN_SCHEMA)
    _need(
        all(
            type(value[field]) is str and _HEX.fullmatch(value[field]) is not None
            for field in _PIN_FIELDS
        )
    )
    return NativeStartupCandidate0056PinsV1(
        **{field: value[field] for field in _PIN_FIELDS}
    )


def _read_private_pins(path_raw: str, expected_sha256: str):
    """Bound a private regular file by an externally supplied exact byte hash."""
    _need(
        os.name == "posix"
        and type(path_raw) is str
        and 0 < len(path_raw) <= 4096
        and type(expected_sha256) is str
        and _HEX.fullmatch(expected_sha256) is not None
    )
    path = Path(path_raw)
    _need(path.is_absolute() and path.resolve(strict=True) == path)
    parent = path.parent
    parent_info = parent.lstat()
    _need(
        stat.S_ISDIR(parent_info.st_mode)
        and parent_info.st_uid == os.geteuid()
        and stat.S_IMODE(parent_info.st_mode) == 0o700
    )
    before = path.lstat()
    _need(
        stat.S_ISREG(before.st_mode)
        and before.st_uid == os.geteuid()
        and stat.S_IMODE(before.st_mode) == 0o600
        and before.st_nlink == 1
        and 0 < before.st_size <= 4096
    )
    descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        held = os.fstat(descriptor)
        _need(
            (held.st_dev, held.st_ino, held.st_size)
            == (before.st_dev, before.st_ino, before.st_size)
        )
        raw = os.read(descriptor, 4097)
        _need(len(raw) == held.st_size and os.read(descriptor, 1) == b"")
    finally:
        os.close(descriptor)
    after = path.lstat()
    _need(
        (after.st_dev, after.st_ino, after.st_size)
        == (before.st_dev, before.st_ino, before.st_size)
        and hashlib.sha256(raw).hexdigest() == expected_sha256
    )
    return _parse_pins(raw)


async def _observe_configured(pins: NativeStartupCandidate0056PinsV1) -> None:
    """Use the actual configured writer and dispose both restricted companions."""
    from app.config.settings import get_settings
    from app.core.native_only_lifespan import require_native_only_settings
    from app.database.session import AsyncSessionFactory
    from app.demo_automation.native_server_selectors_v2 import (
        select_configured_native_demo_factories_v2,
    )

    settings = get_settings()
    _need(settings.ctcc_native_only_lifespan_v1 is True)
    require_native_only_settings(settings)
    selected = None
    disposal_failed = False
    try:
        selected = select_configured_native_demo_factories_v2()
        _need(selected.writer_factory is AsyncSessionFactory)
        async with asyncio.timeout(190):
            observed = await observe_selected_native_startup_candidate_0056_v1(
                selected, source=_SOURCE, pins=pins
            )
        _need(
            observed.admission == "DENY"
            and observed.independent_reference_authenticated is False
            and observed.startup_admission is False
            and observed.execution_authority is False
            and type(observed.control_revision) is int
            and observed.control_revision >= 1
        )
    finally:
        if selected is not None:
            for engine in (selected.custody_engine, selected.frame_reader_engine):
                try:
                    await engine.dispose()
                except BaseException:  # noqa: BLE001 -- no private engine error escapes
                    disposal_failed = True
    _need(not disposal_failed)


def _output(status: str) -> dict:
    return {
        "schema": _SCHEMA,
        "status": status,
        "caller_pins_matched": status == "OBSERVED_DENY",
        "independent_reference_authenticated": False,
        "startup_admission": False,
        "execution_authority": False,
        "admission": "DENY",
    }


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    try:
        _need(
            type(args) is list
            and len(args) == 5
            and args[0] == "--inspect-configured"
            and args[1] == "--pins-file"
            and args[3] == "--pins-sha256"
        )
        pins = _read_private_pins(args[2], args[4])
        asyncio.run(_observe_configured(pins))
        output = _output("OBSERVED_DENY")
        # A diagnostic match must never be usable as a successful shell gate.
        code = 2
    except BaseException:  # noqa: BLE001 -- fixed output, including cancellation
        output = _output("REFUSED")
        code = 1
    print(json.dumps(output, sort_keys=True, separators=(",", ":")))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
