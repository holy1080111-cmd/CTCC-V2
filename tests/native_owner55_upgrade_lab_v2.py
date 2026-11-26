"""Isolated-hosted test helpers; no production configuration or DB selection."""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.database.models.demo_automation import DemoAutomationState
from app.database.repositories import native_legacy_control_genesis_0056_v1 as genesis
from app.database.repositories.demo_control import DemoControlRepository
from app.trade_qualification.demo_control import ControlScope
from scripts import native_owner_upgrade_0056_v1 as subject


async def seed_stopped_retained_owner55_v2(owner):
    factory = async_sessionmaker(owner, autoflush=False, expire_on_commit=False)
    seed_at = datetime.now(UTC) - timedelta(seconds=2)
    await DemoControlRepository(factory, clock=lambda: seed_at).latch_stop(
        ControlScope("demo", "730055"), command_id="a" * 64
    )
    values = genesis._stopped_values(seed_at, "f" * 64)
    values.update(
        control_revision=7,
        daily_pnl=Decimal("-12.5"),
        trades_today=13,
        consecutive_losses=3,
        peak_equity=Decimal(1234),
        realized_pnl_events=[{"net_pnl": "-12.5", "closed_at": seed_at.isoformat()}],
    )
    async with factory.begin() as session:
        session.add(DemoAutomationState(**values))


async def run_actual_owner55_cli_v2(
    tmp_path,
    pins,
    *,
    owner_dsn_file,
    ca_file,
    source_archive_file,
    role_urls,
    fixed_trace=None,
):
    """A fresh CLI with actual reviewed source/private files and configured3 TLS DSNs.

    Passwords are lab-only; the isolated PG harness may use trust auth, so this
    deliberately makes no password-authentication or host-backup-lineage claim.
    """
    value = asdict(pins) | {
        "schema": "ctcc.native_owner_0056.reviewed_upgrade_inputs.v2"
    }
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("ascii")
    pin_file = tmp_path / "reviewed-owner55-v2.json"
    pin_file.write_bytes(raw)
    pin_file.chmod(0o600)
    environment = {
        name: os.environ[name]
        for name in ("SYSTEMROOT", "WINDIR", "PATH", "HOME", "TMPDIR", "TEMP", "TMP")
        if name in os.environ
    }
    environment.update(
        PYTHONDONTWRITEBYTECODE="1",
        PYTHONHASHSEED="0",
        ENVIRONMENT="test",
        DATABASE_URL=role_urls[0].render_as_string(hide_password=False),
        OKX_DEMO_NATIVE_CUSTODY_DATABASE_URL=role_urls[1].render_as_string(
            hide_password=False
        ),
        OKX_DEMO_NATIVE_FRAME_READER_DATABASE_URL=role_urls[2].render_as_string(
            hide_password=False
        ),
        CTCC_NATIVE_PG_TLS_CA_FILE=str(ca_file),
        CTCC_NATIVE_PG_TLS_CA_SHA256=pins.route.ca_sha256,
    )
    command = [
        sys.executable,
        "-m",
        "scripts.native_owner_upgrade_0056_v1",
        "--upgrade-reviewed-native-owner-0055-to-0056",
        "--reviewed-pins-file",
        str(pin_file),
        "--reviewed-pins-sha256",
        subject._sha(raw),
        "--owner-dsn-file",
        str(owner_dsn_file),
        "--ca-file",
        str(ca_file),
        "--source-archive-file",
        str(source_archive_file),
    ]
    if fixed_trace is not None:
        command = [sys.executable, "-B", "-c", _FIXED_TRACE_SOURCE, *command[3:]]
    completed = await asyncio.to_thread(
        subprocess.run,
        command,
        cwd=subject._SOURCE,
        env=environment,
        capture_output=True,
        text=True,
        timeout=240,
        check=False,
    )
    # Never expose environment, private filenames, DSNs or driver stderr in
    # failure diagnostics. Only the tool's fixed redacted JSON is retained.
    try:
        receipt = json.loads(completed.stdout.strip())
    except (ValueError, TypeError):
        raise AssertionError("isolated_owner55_cli_missing_fixed_json") from None
    if fixed_trace is not None:
        lines = [
            line.removeprefix("CTCC_NONSUPER_FIXED_TRACE ")
            for line in completed.stderr.splitlines()
            if line.startswith("CTCC_NONSUPER_FIXED_TRACE ")
        ]
        assert len(lines) == 1 and len(lines[0]) <= 40000
        decoded = json.loads(lines[0])
        assert type(decoded) is list and 0 < len(decoded) <= 128
        for record in decoded:
            assert type(record) is list and len(record) == 5
            event, file, phase, number, kind = record
            assert event in ("entered", "raised")
            assert re.fullmatch(r"(?:scripts|app/core|app/domain)/[a-z0-9_]+\.py", file)
            assert re.fullmatch(r"[A-Za-z0-9_<>]{1,90}", phase)
            assert type(number) is int and 0 < number <= 10000
            assert (
                kind is None
                and event == "entered"
                or event == "raised"
                and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,79}", kind)
            )
        fixed_trace.extend(decoded)
    return completed.returncode, receipt


_FIXED_TRACE_SOURCE = '\nimport json\nimport os\nimport re\nimport runpy\nimport sys\n\nROOT = os.getcwd() + os.sep\nMARKER = "CTCC_NONSUPER_FIXED_TRACE "\nMODULE = "scripts.native_owner_upgrade_0056_v1"\nFILES = frozenset({\n    "scripts/native_owner_upgrade_0056_v1.py",\n    "scripts/native_owner_granted_catalog_v1.py",\n    "scripts/native_owner_route_preflight_0056_v1.py",\n    "scripts/native_owner_upgrade_0057_v1.py",\n    "scripts/native_owner_retained_rows_v2.py",\n    "scripts/current_contract_source_scopes_v1.py",\n    "scripts/current0056_fitted_delta_catalog_v1.py",\n    "app/core/native_role_identity_0056_v2.py",\n    "app/core/native_role_preflight_v1.py",\n    "app/core/native_role_witness_0056_v1.py",\n    "app/domain/collector_current_0055_catalog_v1.py",\n    "app/domain/collector_current_0056_inherited_catalog_v1.py",\n})\nPHASES = frozenset({\n    "upgrade_owner_0055_to_0056_v1", "_load_pins_v2",\n    "_require_positive_private_file_v2", "_prepared", "_verify_reviewed_archive",\n    "_configured_resources_v2", "preflight_owner_route_0056_v1",\n    "_checked_preflight", "_role_observation_v2", "_readonly_inventory_v2",\n    "_granted_maintainer_oid", "_catalog_v2", "observe_granted_owner_structure",\n    "_commit_once", "_upgrade_exact_0055", "_preserved_v2", "_independent_readback",\n})\nignored = {"StopIteration", "StopAsyncIteration", "GeneratorExit"}\nrecords, seen = [], set()\n\ndef trace(frame, event, argument):\n    file = frame.f_code.co_filename\n    if not file.startswith(ROOT):\n        return None\n    name = file.removeprefix(ROOT).replace(os.sep, "/")\n    if name not in FILES:\n        return None\n    phase = frame.f_code.co_name\n    if event == "call" and phase in PHASES:\n        item = ("entered", name, phase, frame.f_code.co_firstlineno, None)\n    elif event == "exception":\n        kind = argument[0].__name__\n        if kind in ignored or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,79}", kind) is None:\n            return trace\n        item = ("raised", name, phase, frame.f_lineno, kind)\n    else:\n        return trace\n    if item not in seen and len(records) < 128:\n        seen.add(item)\n        records.append(item)\n    return trace\n\ntry:\n    sys.argv = [MODULE, *sys.argv[1:]]\n    sys.settrace(trace)\n    runpy.run_module(MODULE, run_name="__main__", alter_sys=True)\nfinally:\n    sys.settrace(None)\n    print(MARKER + json.dumps(records, separators=(",", ":")), file=sys.stderr, flush=True)\n'
