"""Paced MockTransport/native clocks and real private custody; no source authority."""

import asyncio
import json
import os
from pathlib import Path

import pytest

from app.domain.source_primitives import canonical, decode, sha
from app.exchange.okx import live_account_current_chain_v1 as current
from app.exchange.okx import live_account_history_chain_v1 as history
from app.exchange.okx.live_account_config_observation_v1 import _stamp
from app.exchange.okx.live_account_history_page_v1 import (
    select_live_account_history_page_v1,
)
from app.exchange.okx.live_read_lease_v1 import OkxLivePinnedGetClientV1
from app.exchange.okx.live_trade_read_page_v1 import (
    observe_live_trade_read_page_v1,
    select_live_trade_read_v1,
)
from app.okx_live import controlled_close_recovery_v1 as subject
from app.okx_live import owned_lifecycle_v1 as lifecycle
from app.okx_live.postsubmit_capture_v1 import _current_original_pages, _metadata
from tests import native_live_postsubmit_fixtures_v1 as raw
from tests.unit.exchange.test_live_account_config_observation_v1 import (
    _http,
    _raw_response,
    _settings,
)
from tests.unit.test_native_live_controlled_close_recovery_v1 import scenario
from tests.unit.test_native_live_lifecycle_custody_v1 import _private_root_path
from tests.unit.test_native_live_zero_close_oco_recovery_v1 import zero_oco_scenario


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["filled_close", "zero_then_original_oco"])
async def test_complete_lower_get_sequence_preserves_synthetic_flags_and_native_freshness(
    tmp_path, kind
):
    """Measure exact ordered lower GET/readback sequence, not a private signer."""
    zero_mode = kind == "zero_then_original_oco"
    intent, template, args = zero_oco_scenario() if zero_mode else scenario()
    root = _private_root_path(tmp_path)
    requests = []

    async def handler(request):
        await asyncio.sleep(0.005)
        assert request.method == "GET" and request.url.host == "openapi.okx.com"
        assert "x-simulated-trading" not in request.headers
        path, query = request.url.path, dict(request.url.params)
        requests.append((path, query))
        close_order = template.zero_order if zero_mode else template.close_order
        close_fills = template.zero_fills if zero_mode else template.close_fills
        entry_id, close_id = (
            template.order.selector.order_id,
            close_order.selector.order_id,
        )
        if path == "/api/v5/account/config":
            rows = decode(
                raw.config(0, perm="read_only,trade", ip="192.0.2.1").raw_body
            )["data"]
        elif path == "/api/v5/trade/orders-history-archive":
            rows = [] if "after" in query else decode(close_order.raw_body)["data"]
        elif path == "/api/v5/trade/order":
            pages = {entry_id: template.order, close_id: close_order}
            if zero_mode:
                pages[template.exit_order.selector.order_id] = template.exit_order
            rows = decode(pages[query["ordId"]].raw_body)["data"]
        elif path == "/api/v5/trade/fills-history":
            pages = {entry_id: template.fills, close_id: close_fills}
            if zero_mode:
                pages[template.exit_order.selector.order_id] = template.exit_fills
            selected = pages[query["ordId"]]
            rows = [] if "after" in query else decode(selected[0].raw_body)["data"]
        elif path == "/api/v5/trade/order-algo":
            rows = decode(template.protection.raw_body)["data"]
        elif path == "/api/v5/account/account-position-risk":
            rows = [{"balData": [], "posData": []}]
        elif path == "/api/v5/account/balance":
            rows = [{"totalEq": "10.20", "details": []}]
        elif path in (
            "/api/v5/account/positions",
            "/api/v5/trade/orders-pending",
            "/api/v5/trade/orders-algo-pending",
        ):
            rows = []
        elif path == "/api/v5/account/instruments":
            rows = [
                {
                    "instType": "SWAP",
                    "instId": intent.instrument_id,
                    "settleCcy": "USDT",
                    "ctType": "linear",
                    "ctValCcy": "ETH",
                    "state": "live",
                    "ctVal": "0.01",
                    "ctMult": "1",
                    "tickSz": "0.01",
                    "lotSz": "1",
                    "minSz": "1",
                    "lever": "20",
                }
            ]
        elif path == "/api/v5/account/leverage-info":
            rows = [
                {
                    "instId": intent.instrument_id,
                    "posSide": "net",
                    "mgnMode": query["mgnMode"],
                    "lever": "1",
                }
            ]
        else:
            pytest.fail("unexpected synthetic GET")
        return _raw_response(canonical({"code": "0", "msg": "", "data": rows}))

    settings = _settings(
        okx_live_read_max_retries=0,
        okx_live_allowed_symbols=intent.instrument_id,
        okx_live_scan_symbols=intent.instrument_id,
        okx_live_expected_uid=intent.account_uid,
        okx_live_expected_main_uid=intent.main_uid,
    )
    barrier = _stamp()
    async with _http(handler) as http:
        client = OkxLivePinnedGetClientV1(http, settings=settings)
        with client.pinned_get_lease() as lease:
            cutoff = _stamp()
            selected = select_live_account_history_page_v1(
                "orders_archive",
                begin_ms=str(subject._ms(args["records"].close.recorded_at)),
                end_ms=str(cutoff.utc_ns // 1_000_000 - 1),
                inst_type="SWAP",
            )
            receipt = await history.collect_live_account_history_v1(
                client, lease, selected, private_root=root, attempt_id="4" * 32
            )
            before_archive, after_archive, archive = subject._archive_readback(
                root, "4" * 32, receipt, selected, lease
            )

            async def get(kind, **values):
                return await observe_live_trade_read_page_v1(
                    client,
                    lease,
                    select_live_trade_read_v1(
                        kind, instrument_id=intent.instrument_id, **values
                    ),
                )

            async def fills(identity):
                first = await get("fills_history", order_id=identity)
                rows = decode(first.raw_body)["data"]
                return (
                    (first,)
                    if not rows
                    else (
                        first,
                        await get(
                            "fills_history",
                            order_id=identity,
                            after_bill_id=rows[-1]["billId"],
                        ),
                    )
                )

            close_id = (
                template.zero_order if zero_mode else template.close_order
            ).selector.order_id
            zero = zero_fills = zero_after = zero_validated = None
            if zero_mode:
                zero = await get("order", order_id=close_id)
                zero_fills = await fills(close_id)
                zero_after = await get("order", order_id=close_id)
                zero_validated = _stamp()
                subject._verify_zero_effect_v1(
                    intent,
                    args["records"],
                    args["journal"],
                    order=zero,
                    fills=zero_fills,
                    after=zero_after,
                    validated=zero_validated,
                    archive_pages=archive,
                    archive_before=before_archive,
                    archive_after=after_archive,
                    cutoff=cutoff,
                    barrier=barrier,
                )
            entry = await get("order", order_id=template.order.selector.order_id)
            entry_fills = await fills(template.order.selector.order_id)
            algo = await get("algo", algo_id=template.protection.selector.algo_id)
            exit_id = template.exit_order.selector.order_id if zero_mode else close_id
            exiting = await get("order", order_id=exit_id)
            exit_fills = await fills(exit_id)
            entry_after = await get("order", order_id=template.order.selector.order_id)
            exit_after = await get("order", order_id=exit_id)
            algo_after = await get("algo", algo_id=template.protection.selector.algo_id)
            current_receipt = await current.collect_live_current_account_v1(
                client, lease, private_root=root, attempt_id="5" * 32
            )
            before, after, pages = _current_original_pages(
                root, "5" * 32, current_receipt, lease
            )
            validated = _stamp()
            base = (
                entry,
                entry_fills,
                algo,
                before,
                after,
                pages,
                current_receipt["custody_sha256"],
                sha(
                    canonical(
                        [
                            page.receipt_sha256
                            for page in (
                                entry,
                                *entry_fills,
                                algo,
                                exiting,
                                *exit_fills,
                                entry_after,
                                exit_after,
                                algo_after,
                            )
                        ]
                    )
                ),
                validated,
                *_metadata(pages, intent.instrument_id),
            )
            extra = {
                "archive_pages": archive,
                "archive_config_before": before_archive,
                "archive_config_after": after_archive,
                "archive_custody_sha256": receipt["custody_sha256"],
                "archive_attempt_id": "4" * 32,
                "archive_cutoff": cutoff,
                "entry_order_after": entry_after,
                "protection_after": algo_after,
            }
            capture = (
                subject.ControlledZeroCloseOcoCaptureV1(
                    *base,
                    **extra,
                    exit_order=exiting,
                    exit_order_after=exit_after,
                    exit_fills=exit_fills,
                    zero_order=zero,
                    zero_fills=zero_fills,
                    zero_order_after=zero_after,
                    zero_validated_at=zero_validated,
                )
                if zero_mode
                else subject.ControlledCloseRecoveryCaptureV1(
                    *base,
                    **extra,
                    close_order=exiting,
                    close_order_after=exit_after,
                    close_fills=exit_fills,
                )
            )
            assessor = (
                subject.assess_zero_effect_original_oco_v1
                if zero_mode
                else subject.assess_controlled_close_recovery_v1
            )
            observed = assessor(intent, capture, **{**args, "barrier": barrier})
            assert observed.outcome == "RECONCILED_FLAT"
            guard_start = _stamp()
            lifecycle._require_lifecycle_source_modules_v1()
            finished = _stamp()
            assert all(
                page.synthetic_mock is True
                for page in (
                    entry,
                    *entry_fills,
                    algo,
                    exiting,
                    *exit_fills,
                    *pages,
                    *archive,
                )
            )
            assert (
                validated.monotonic_ns - entry.stamps[-1].monotonic_ns < 5_000_000_000
            )
            assert finished.monotonic_ns - validated.monotonic_ns < 5_000_000_000
            result = {
                "kind": kind,
                "synthetic_transport": True,
                "provider_acceptance": False,
                "private_owner_signing_exercised": False,
                "execution_authority": False,
                "capture_seconds": (validated.monotonic_ns - barrier.monotonic_ns)
                / 1e9,
                "terminal_first_page_age_seconds": (
                    validated.monotonic_ns - entry.stamps[-1].monotonic_ns
                )
                / 1e9,
                "source_guard_seconds": (
                    finished.monotonic_ns - guard_start.monotonic_ns
                )
                / 1e9,
                "get_requests": len(requests),
                "source_sha256": sha(Path(subject.__file__).read_bytes()),
            }
            destination = os.environ.get("CTCC_CONTROLLED_RECOVERY_NATIVE_CLOCK_REPORT")
            if destination:
                Path(destination + "." + kind + ".json").write_text(
                    json.dumps(result, sort_keys=True), encoding="utf8"
                )
