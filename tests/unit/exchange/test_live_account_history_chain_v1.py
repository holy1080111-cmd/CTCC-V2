from __future__ import annotations

import json

import pytest

from app.exchange.okx import live_account_history_chain_v1 as chain
from app.exchange.okx.live_account_history_page_v1 import (
    select_live_account_history_page_v1 as select,
)
from app.exchange.okx.live_read_lease_v1 import OkxLivePinnedGetClientV1
from tests.unit.exchange.test_live_account_config_custody_v1 import _private_root_path
from tests.unit.exchange.test_live_account_config_observation_v1 import _http, _settings
from tests.unit.exchange.test_live_account_current_chain_v1 import (
    CurrentAccount,
    _response,
)

ATTEMPT = "a" * 32


@pytest.fixture
def private_root(tmp_path):
    return _private_root_path(tmp_path)


def _now_ms() -> int:
    return chain._stamp().utc_ns // chain._NS_PER_MS


def _window(kind="fills_history", *, cutoff=None):
    end = _now_ms() - 10_000 if cutoff is None else cutoff
    options = {} if kind.startswith("bills_") else {"inst_type": "SWAP"}
    return select(kind, begin_ms=str(end - 60_000), end_ms=str(end), **options)


class HistoryAccount:
    def __init__(self, selector, rows, *, repeat=False, config_drift=False):
        self.config = CurrentAccount(config_drift=config_drift)
        self.selector = selector
        self.rows = rows
        self.repeat = repeat
        self.queries = []

    def __call__(self, request):
        if request.url.path == "/api/v5/account/config":
            return self.config(request)
        assert request.url.path == self.selector.path
        assert request.url.host == "openapi.okx.com"
        assert request.method == "GET"
        assert "x-simulated-trading" not in request.headers
        query = dict(request.url.params)
        self.queries.append(query)
        return _response(self.rows if self.repeat or "after" not in query else [])


async def _collect(handler, private_root, selector):
    async with _http(handler) as http:
        client = OkxLivePinnedGetClientV1(http, settings=_settings())
        with client.pinned_get_lease() as lease:
            return await chain.collect_live_account_history_v1(
                client, lease, selector, private_root=private_root, attempt_id=ATTEMPT
            )


def _read(private_root, receipt):
    return chain.read_live_account_history_v1(
        private_root,
        attempt_id=ATTEMPT,
        expected_custody_sha256=receipt["custody_sha256"],
    )


@pytest.mark.asyncio
async def test_history_retains_large_raw_page_and_requires_explicit_empty_terminal(
    private_root,
):
    selector = _window()
    rows = [
        {
            "instType": "SWAP",
            "billId": cursor,
            "tradeId": trade_id,
            "ordId": "1",
            "ts": str(int(selector.end_ms) - 1),
            "fillTime": str(int(selector.begin_ms) - 2),
            "opaqueSourceField": "z" * 35_000,
        }
        for cursor, trade_id in (("907", "1001"), ("904", "1002"))
    ]
    handler = HistoryAccount(selector, rows)
    receipt = await _collect(handler, private_root, selector)
    assert receipt["page_count"] == 2 and receipt["row_count"] == 2
    assert receipt["explicit_terminal_page_observed"] is True
    assert receipt["synthetic_mock"] is True
    assert all(
        receipt[key] is False
        for key in (
            "history_complete",
            "account_complete",
            "source_authenticity_verified",
            "execution_authority",
        )
    )
    assert handler.config.configs == 2
    assert handler.queries == [
        dict(selector.query),
        {**dict(selector.query), "after": "904"},
    ]
    first = private_root / ATTEMPT / "p-000000"
    raw = first / "body.raw"
    assert len(raw.read_bytes()) > 65_536
    assert json.loads(raw.read_bytes())["data"] == rows
    metadata = json.loads((first / "observation.json").read_bytes())
    assert metadata["first_row_id"] == "907" and metadata["last_row_id"] == "904"
    assert metadata["previous_page_sha256"] == receipt["plan_sha256"]
    second = private_root / ATTEMPT / "p-000001" / "observation.json"
    assert dict(json.loads(second.read_bytes())["request_query"]) == handler.queries[1]
    assert _read(private_root, receipt) == receipt
    assert all(
        identifier not in str(receipt) for identifier in ("1234567", "192.0.2.1")
    )
    raw.write_bytes(raw.read_bytes().replace(b"1001", b"1003"))
    with pytest.raises(chain.LiveAccountHistoryChainError):
        _read(private_root, receipt)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["bills_recent", "orders_archive", "algo_history"])
async def test_history_uses_endpoint_specific_cursor_and_timestamp_fields(
    private_root, kind
):
    if kind == "algo_history":
        selector = select(kind, ord_type="conditional", state="effective")
        now = _now_ms() - 1000
        rows = [
            {
                "algoId": "20",
                "ordType": "conditional",
                "state": "effective",
                "cTime": str(now - 1),
                "uTime": str(now),
                "triggerTime": str(now),
            }
        ]
    else:
        selector = _window(kind)
        rows = (
            [{"billId": "20", "ts": str(selector.end_ms), "type": "8"}]
            if kind.startswith("bills_")
            else [
                {
                    "instType": "SWAP",
                    "ordId": "20",
                    "cTime": str(selector.end_ms),
                    "uTime": str(int(selector.end_ms) + 1),
                }
            ]
        )
    handler = HistoryAccount(selector, rows)
    receipt = await _collect(handler, private_root, selector)
    assert receipt["row_count"] == 1 and receipt["history_complete"] is False
    assert handler.queries[1] == {**dict(selector.query), "after": "20"}
    assert _read(private_root, receipt) == receipt


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", ["future", "fill_after_generation", "window", "repeat", "cap", "config"]
)
async def test_invalid_history_never_seals_as_an_empty_or_complete_partition(
    private_root, monkeypatch, failure
):
    selector = _window()
    row = {
        "instType": "SWAP",
        "billId": "20",
        "ts": str(selector.end_ms),
        "fillTime": str(selector.end_ms),
    }
    if failure == "future":
        row["fillTime"] = str(_now_ms() + 100_000)
    elif failure == "fill_after_generation":
        row["fillTime"] = str(int(selector.end_ms) + 1)
    elif failure == "window":
        row["ts"] = row["fillTime"] = str(int(selector.begin_ms) - 1)
    elif failure == "cap":
        monkeypatch.setattr(chain, "_PAGE_CAP", 1)
    handler = HistoryAccount(
        selector, [row], repeat=failure == "repeat", config_drift=failure == "config"
    )
    with pytest.raises(chain.LiveAccountHistoryChainError):
        await _collect(handler, private_root, selector)
    attempt = private_root / ATTEMPT
    assert (attempt / "p-000000" / "body.raw").is_file()
    assert not (attempt / "custody.json").exists()
    assert len(handler.queries) == (2 if failure in {"repeat", "config"} else 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("cutoff_offset", [60_000, -4 * 86_400_000])
async def test_history_refuses_future_or_expired_windows_before_any_get(
    private_root, cutoff_offset
):
    selector = _window("fills_recent", cutoff=_now_ms() + cutoff_offset)
    handler = HistoryAccount(selector, [])
    with pytest.raises(
        chain.LiveAccountHistoryChainError, match="outside_retention_or_cutoff"
    ):
        await _collect(handler, private_root, selector)
    assert not handler.config.configs and not handler.queries
    assert not (private_root / ATTEMPT).exists()


@pytest.mark.asyncio
async def test_history_no_clobber_and_missing_terminal_readback(private_root):
    selector = _window("bills_archive")
    handler = HistoryAccount(selector, [{"billId": "20", "ts": str(selector.end_ms)}])
    receipt = await _collect(handler, private_root, selector)
    with pytest.raises(chain.LiveAccountHistoryChainError):
        await _collect(handler, private_root, selector)
    assert handler.config.configs == 2 and len(handler.queries) == 2
    with pytest.raises(
        chain.LiveAccountHistoryChainError, match="expected_digest_required"
    ):
        _read(private_root, {"custody_sha256": "wrong"})
    terminal = private_root / ATTEMPT / "p-000001"
    terminal.rename(terminal.with_name("not-terminal"))
    with pytest.raises(chain.LiveAccountHistoryChainError, match="inventory_invalid"):
        _read(private_root, receipt)
