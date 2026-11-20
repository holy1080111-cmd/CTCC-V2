"""Maintain ONE explicitly synthetic anonymous raw-bank regression fixture.

Uses the existing mock-wire capture fixture and ORIGINAL canonical validators.
No credentials, account capture, database, operator Arm or venue order exists.
The resulting DATA must never count as venue or predictive-availability evidence.
This maintenance helper is not imported by runtime product code or unit tests.
"""

import asyncio
import base64
import hashlib
import json
import re
import tempfile
from contextlib import contextmanager
from pathlib import Path

import pytest

from app.trade_qualification import native_live_public_source_v1 as source
from tests.unit.research.test_native_live_public_source_v1 import live_source
from tests.unit.test_qualification_market_bridge import v2_engine_source

_TARGET = Path(__file__).parent / "fixtures" / "global_public_initial_synthetic_v2.json"
_PRIVATE = re.compile(
    rb"OK-ACCESS-(?:KEY|SIGN|PASSPHRASE)|authorization|cookie|"
    rb'"(?:uid|mainUid|account_uid|main_uid)"\s*:',
    re.IGNORECASE,
)


async def _capture(fixture):
    _clock, _parent, _raw, _harness, key, request = fixture
    opaque, _params = request("initial")
    owner = await source.capture_owned_live_public_v1(opaque, key=key)
    try:
        value = source._replay_owned_live_public_v1(owner, key=key)
        assert (
            value.stage == "initial"
            and value.packet.environment == "anonymous_global_public"
        )
        return tuple((item.name, item.raw) for item in value.raw_files)
    finally:
        owner.close()


def main():
    assert (
        Path(source.__file__).resolve().parents[2]
        == Path(__file__).resolve().parents[1]
    )
    # This existing builder performs its own asyncio.run; it must be outside
    # the capture's event loop, exactly as the original synchronous fixture.
    market = v2_engine_source("long")
    with (
        pytest.MonkeyPatch.context() as patch,
        tempfile.TemporaryDirectory(prefix="ctcc-synthetic-global-") as temporary,
        contextmanager(live_source.__wrapped__)(
            patch, market, Path(temporary)
        ) as fixture,
    ):
        files = asyncio.run(_capture(fixture))
    assert all(_PRIVATE.search(raw) is None for _name, raw in files)
    plan = json.loads(dict(files)["plan.json"])
    assert (
        plan["stage"] == "initial_public"
        and plan["environment"] == "anonymous_global_public"
    )
    document = {
        "schema_version": "ctcc.synthetic.anonymous_global_raw_bank.v2",
        "provenance": "synthetic_anonymous_transport_engineering_fixture",
        "generation_method": "existing_mock_wire_capture_and_original_canonical_validator",
        "stage": "initial_public",
        "source_plan_schema": plan["schema_version"],
        "actual_venue_evidence": False,
        "execution_authority": False,
        "predictive_claim_eligible": False,
        "account_data_present": False,
        "source_times_preserved": True,
        "raw_files": [
            {
                "name": name,
                "bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "base64": base64.b64encode(raw).decode("ascii"),
            }
            for name, raw in files
        ],
    }
    raw = (
        json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        + "\n"
    ).encode("ascii")
    _TARGET.parent.mkdir(parents=True, exist_ok=True)
    with _TARGET.open("xb") as output:
        output.write(raw)
    print(
        json.dumps(
            {
                "fixture_sha256": hashlib.sha256(raw).hexdigest(),
                "fixture_bytes": len(raw),
                "raw_files": len(files),
                "scope": "SYNTHETIC_ANONYMOUS_ONLY",
                "private_marker_scan": "PASS",
                "execution_authority": False,
            }
        )
    )


if __name__ == "__main__":
    main()
