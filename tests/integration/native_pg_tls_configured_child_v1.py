"""No-provider child for the isolated configured PostgreSQL TLS test."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import ssl
from pathlib import Path

from scripts import manifest

_SOURCE = Path(__file__).resolve().parents[2]
_SHA = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_HOST = re.compile(r"ctcc-final-[0-9a-f]{12}-postgres\Z", re.ASCII)
_ROLE = re.compile(r"ctcc_obs56_(writer|custody|frame)_[0-9a-f]{12}\Z", re.ASCII)
_DENIED = '{"status":"FAIL","code":"native_pg_tls_child_denied"}'


def _need(value: bool) -> None:
    if value is not True:
        raise ValueError("native_pg_tls_child_denied")


async def _check() -> dict:
    expected = os.environ.get("CTCC_DEVVAL_EXPECTED_SOURCE_MANIFEST_SHA256")
    raw_manifest = (_SOURCE / "MANIFEST.sha256").read_bytes()
    _need(
        type(expected) is str
        and _SHA.fullmatch(expected) is not None
        and hashlib.sha256(raw_manifest).hexdigest() == expected
        and manifest.read_manifest(_SOURCE / "MANIFEST.sha256")
        == manifest.build_manifest(_SOURCE)
    )
    # No application module is imported until the exact admitted source lock.
    from sqlalchemy import text

    from app.config.settings import get_settings
    from app.database import session as configured
    from app.database.repositories.public_runtime_bundle_custody import _closure
    from app.demo_automation.native_server_selectors_v2 import (
        select_configured_native_demo_factories_v2,
    )

    settings = get_settings()
    context = configured.native_pg_tls_context
    _need(
        settings is configured.settings
        and settings.ctcc_native_only_lifespan_v1 is True
        and settings.environment == "test"
        and settings.trading_mode == "analysis_only"
        and type(context) is ssl.SSLContext
        and context.check_hostname is True
        and context.verify_mode == ssl.CERT_REQUIRED
        and context.minimum_version >= ssl.TLSVersion.TLSv1_2
        and context.keylog_filename is None
        and context._msg_callback is None
        and context.sni_callback is None
        and configured.native_pg_tls_settings_pin
        == (
            settings.database_url,
            settings.ctcc_native_pg_tls_ca_file,
            settings.ctcc_native_pg_tls_ca_sha256,
        )
    )
    selected = select_configured_native_demo_factories_v2()
    engines = (
        configured.engine,
        selected.custody_engine,
        selected.frame_reader_engine,
    )
    try:
        _need(
            selected.writer_factory is configured.AsyncSessionFactory
            and all(
                _closure(engine.sync_engine.pool._creator)["cparams"].get("ssl")
                is context
                for engine in engines
            )
            and len({engine.url.username for engine in engines}) == 3
            and {engine.url.username.split("_")[2] for engine in engines}
            == {"writer", "custody", "frame"}
            and all(
                engine.url.password is None
                and not engine.url.query
                and _HOST.fullmatch(engine.url.host or "") is not None
                and _ROLE.fullmatch(engine.url.username or "") is not None
                for engine in engines
            )
            and len(
                {
                    (engine.url.host, engine.url.port, engine.url.database)
                    for engine in engines
                }
            )
            == 1
        )
        for engine in engines:
            async with engine.connect() as connection:
                row = tuple(
                    (
                        await connection.execute(
                            text("""
                            SELECT current_user::text, current_database()::text,
                                   (SELECT ssl FROM pg_catalog.pg_stat_ssl
                                    WHERE pid=pg_catalog.pg_backend_pid()),
                                   (SELECT version_num::text FROM public.alembic_version)
                            """)
                        )
                    ).one()
                )
            _need(row == (engine.url.username, engine.url.database, True, "0056"))
        return {
            "schema": "ctcc.native_pg_tls.configured_child.diagnostic.v1",
            "status": "PASS",
            "source_manifest_sha256": expected,
            "distinct_restricted_roles": 3,
            "shared_tls_context": True,
            "actual_ssl_readback": True,
            "database_head": "0056",
            "startup_admission": False,
            "execution_authority": False,
        }
    finally:
        await selected.custody_engine.dispose()
        await selected.frame_reader_engine.dispose()
        await configured.engine.dispose()


def main() -> int:
    try:
        result = asyncio.run(_check())
    except Exception:  # noqa: BLE001 -- fixed refusal hides URLs and roles.
        print(_DENIED)
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
