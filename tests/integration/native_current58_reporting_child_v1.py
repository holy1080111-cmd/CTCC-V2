"""Actual isolated TLS SQL→file→Notion-source preparation; no remote HTTP.

Venue data and destination are declared synthetic fixtures. The test runs
unpatched source guards and source/factory/role checks in a fresh process.
"""

import asyncio
import hashlib
import json
import os
import re
from pathlib import Path

from scripts import manifest

ROOT = Path(__file__).resolve().parents[2]
STAGE = ["source"]


def need(value):
    if value is not True:
        raise ValueError("current58_reporting_child_denied")


async def check():
    from sqlalchemy.engine import make_url

    need(os.environ.get("CTCC_CURRENT58_REPORTING_HERMETIC_CHILD") == "1")
    expected = os.environ.get("CTCC_EXPECTED_REPORTING_MANIFEST_SHA256", "")
    need(re.fullmatch(r"[a-f0-9]{64}", expected) is not None)
    need(
        hashlib.sha256((ROOT / "MANIFEST.sha256").read_bytes()).hexdigest() == expected
    )
    need(
        manifest.read_manifest(ROOT / "MANIFEST.sha256")
        == manifest.build_manifest(ROOT)
    )
    urls = tuple(
        make_url(os.environ[name])
        for name in (
            "DATABASE_URL",
            "OKX_DEMO_NATIVE_CUSTODY_DATABASE_URL",
            "OKX_DEMO_NATIVE_FRAME_READER_DATABASE_URL",
        )
    )
    for url in urls:
        need(
            url.drivername == "postgresql+asyncpg"
            and url.password is None
            and not url.query
        )
        need(
            re.fullmatch(r"ctcc-final-[a-f0-9]{12}-postgres", url.host or "")
            is not None
        )
        need(re.fullmatch(r"ctcc_demo58_[a-f0-9]{12}", url.database or "") is not None)
        need(
            re.fullmatch(r"ctcc_demo58_[wcf]_[a-f0-9]{12}", url.username or "")
            is not None
        )
        need(url.username.rsplit("_", 1)[-1] == url.database.rsplit("_", 1)[-1])
    need(len({(url.host, url.port, url.database) for url in urls}) == 1)
    need(len({url.username for url in urls}) == 3)
    STAGE[0] = "settings"
    from app.config.settings import get_settings
    from app.core.native_manual_demo_lifespan_0056_v1 import (
        require_manual_demo_settings_0056_v1,
    )
    from app.trade_evidence import native_current58_reporting_v1 as current
    from app.trade_evidence import native_notion_delivery_source_v55 as source
    from app.trade_evidence.submission_runtime import SubmissionOutboxRuntime

    settings = get_settings()
    require_manual_demo_settings_0056_v1(settings)
    need(
        settings.okx_demo_allow_order_writes
        is settings.okx_demo_auto_execution
        is settings.live_trading
        is False
    )
    need(
        settings.okx_demo_credentials_configured
        is settings.okx_live_credentials_configured
        is False
    )
    STAGE[0] = "actual58_worker_projection"
    worker = SubmissionOutboxRuntime(settings)
    permit = None
    try:
        await worker.tick()
        need(
            worker.status == "idle"
            and worker.last_code == "submission_outbox_pass_complete"
        )
        need(worker._current58_lease is not None)
        selected, role_identity = current.current58_reporting_context_v1(
            worker._current58_lease, key=current._CONTEXT_KEY_V1
        )
        need(role_identity.schema == "ctcc.native_only.restricted_role_identity0058.v2")
        spool_id = os.environ["CTCC_CURRENT58_REPORTING_SPOOL_SHA256"]
        report_id = os.environ["CTCC_CURRENT58_REPORTING_REPORT_ID"]
        readback = await current.reporting.observe_terminal_lineage_0056_v1(
            selected, spool_id=spool_id
        )
        need(
            readback.database_head == "0058"
            and readback.projection_receipt_envelope_sha256 is not None
        )
        envelope = readback.projection_receipt_envelope_sha256
        await current.close_current58_reporting_v1(worker._current58_lease)
        worker._current58_lease = None
        STAGE[0] = "actual58_notion_source_two_committed_readbacks"
        permit = await source.acquire_native_notion_delivery_source_v55(
            root=Path(settings.notion_outbox_root),
            report_id=report_id,
            expected_envelope_sha256=envelope,
        )
        source.require_prepared_native_notion_source_v55(
            permit, root=Path(settings.notion_outbox_root), report_ids=(report_id,)
        )
        state = source._state(permit)
        proof = json.loads(state["proof_json"])
        need(proof["database_head"] == "0058" and proof["spool_id"] == spool_id)
        need(state["metadata"][0][8] != state["metadata"][1][8])
        need(not current._LEASES and not current._UNKNOWN and not source._ISSUING)
        permit.close()
        permit = None
        need(not source._CAPS and not source._UNKNOWN)
        return {
            "schema": "ctcc.current58_reporting.actual_pg_child.v1",
            "status": "PASS",
            "database_head": "0058",
            "source_manifest_sha256": expected,
            "independent_role_tls_engines": 3,
            "projection_and_local_pair_readback": True,
            "notion_source_distinct_committed_readbacks": 2,
            "report_spool_sha256": spool_id,
            "envelope_sha256": envelope,
            "venue_data": "DECLARED_SYNTHETIC",
            "notion_writes": 0,
            "order_writes": 0,
            "execution_authority": False,
        }
    finally:
        if permit is not None:
            permit.close()
        if worker._current58_lease is not None:
            await current.close_current58_reporting_v1(worker._current58_lease)
            worker._current58_lease = None


def main():
    try:
        result = asyncio.run(check())
    except Exception:  # noqa: BLE001 -- print only a fixed stage; private SQL/TLS details never escape.
        print(
            json.dumps(
                {
                    "schema": "ctcc.current58_reporting.actual_pg_child.v1",
                    "status": "FAIL",
                    "failed_stage": STAGE[0],
                    "notion_writes": 0,
                    "order_writes": 0,
                },
                sort_keys=True,
            )
        )
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
