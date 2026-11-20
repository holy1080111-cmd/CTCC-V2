"""Isolated-hosted test helpers; no production configuration or DB selection."""

from __future__ import annotations

import asyncio
import json
import os
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
    tmp_path, pins, *, owner_dsn_file, ca_file, source_archive_file, role_urls
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
    return completed.returncode, receipt
