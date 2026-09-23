from __future__ import annotations

import copy

import pytest

from app.research.public_clock import validate_os_clock
from app.research.public_market_capture import replay_public_capture
from app.research.public_market_receipts import (
    PublicMinuteCapturePlanV1,
    PublicReceiptError,
    canonical,
    checked,
    parsed_rows,
    sha,
    validate_stamps,
)
from tests.unit.research.public_receipt_fixtures import (
    SyntheticCapture,
    os_evidence,
    plan_for,
)


def test_complete_three_page_minute_capture_is_computational_only(monkeypatch):
    fixture = SyntheticCapture(monkeypatch)
    packet, files = fixture.collect()
    plan, replay = replay_public_capture(packet, files)
    assert replay == packet
    assert plan.volume_unit == "contracts"
    assert len(packet.rows) == 240
    assert len(packet.pages) == 3
    assert packet.transport_origin == "synthetic_test"
    assert packet.execution_authority is False
    assert packet.predictive_oos_eligible is False
    assert all(client.is_closed for client in fixture.clients)
    assert len(fixture.requests) == 5
    assert all("cookie" not in request.headers for request in fixture.requests)
    assert all(
        "last-modified" not in dict(page["response_headers"]) for page in packet.pages
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("volume_unit", "base_currency"),
        ("instrument_id", "BTC-USD-SWAP"),
        ("origin", "https://www.okx.com"),
        ("start_ns", plan_for().start_ns + 1),
        ("expected_rows", 239),
        ("execution_authority", True),
        ("execution_authority", 0),
        ("runtime_consumers", False),
    ],
)
def test_plan_drift_and_authority_rejected(field, value):
    data = plan_for().model_dump()
    data[field] = value
    with pytest.raises(ValueError):
        PublicMinuteCapturePlanV1.model_validate(data)


def test_before_validator_rejects_scalar_subclasses_without_callbacks():
    called = []

    class Hostile(str):
        def __str__(self):
            called.append(1)
            raise AssertionError

    data = plan_for().model_dump()
    data["origin"] = Hostile(data["origin"])
    with pytest.raises(ValueError, match="plain_scalar"):
        PublicMinuteCapturePlanV1.model_validate(data)
    assert not called
    dirty = plan_for().model_copy(update={"origin": Hostile("whatever")})
    with pytest.raises(ValueError, match="plain_scalar"):
        checked(dirty, PublicMinuteCapturePlanV1)
    assert not called


@pytest.mark.parametrize(
    "mutation",
    [
        "raw",
        "hash",
        "locator",
        "page",
        "cursor",
        "clock",
        "future_time",
        "mixed",
        "path",
    ],
)
def test_rehashed_or_direct_capture_tampering_rejected(monkeypatch, mutation):
    packet, files = SyntheticCapture(monkeypatch, rows=2).collect()
    data = copy.deepcopy(packet.model_dump())
    if mutation == "raw":
        files["page-000.raw"] += b" "
    elif mutation == "hash":
        data["pages"][0]["body_sha256"] = "a" * 64
    elif mutation == "locator":
        data["rows"][0]["row_ordinal"] = 1
    elif mutation == "page":
        data["pages"][0]["page_index"] = 1
    elif mutation == "cursor":
        data["pages"][0]["query"] = (("after", "0"),)
    elif mutation == "clock":
        data["validation_complete"]["utc_ns"] += 6_000_000
    elif mutation == "future_time":
        data["time_before"]["body_complete"]["utc_ns"] = data["time_before"][
            "request_start"
        ]["utc_ns"]
    elif mutation == "mixed":
        data["pages"][0]["transport_origin"] = "owned_native_tls"
    elif mutation == "path":
        data["raw_files"] = (
            ("../outside", data["raw_files"][0][1]),
            *data["raw_files"][1:],
        )
    with pytest.raises(ValueError):
        replay_public_capture(type(packet).model_validate(data), files)


@pytest.mark.parametrize(
    "change",
    [
        "duplicate",
        "gap",
        "reverse",
        "unknown_col",
        "missing",
        "unconfirmed",
        "negative",
        "exponent",
        "nan",
        "ohlc",
    ],
)
def test_source_rows_are_never_repaired(monkeypatch, change):
    def mutation(request, body):
        if not request.url.path.endswith("candles"):
            return body
        rows = body["data"]
        if change == "duplicate":
            rows[1] = list(rows[0])
        elif change == "gap":
            rows[1][0] = str(int(rows[1][0]) - 60000)
        elif change == "reverse":
            rows.reverse()
        elif change == "unknown_col":
            rows[0].append("0")
        elif change == "missing":
            rows[0][5] = ""
        elif change == "unconfirmed":
            rows[0][8] = "0"
        elif change == "negative":
            rows[0][5] = "-1"
        elif change == "exponent":
            rows[0][5] = "1e3"
        elif change == "nan":
            rows[0][5] = "NaN"
        elif change == "ohlc":
            rows[0][2] = "99"
        return body

    fixture = SyntheticCapture(monkeypatch, rows=2, mutate_response=mutation)
    with pytest.raises(ValueError):
        fixture.collect()
    assert len(fixture.requests) == 2


def test_duplicate_json_keys_rejected():
    with pytest.raises(PublicReceiptError):
        parsed_rows(b'{"code":"0","msg":"","data":[],"data":[]}')


@pytest.mark.parametrize(
    "change",
    [
        "stopped",
        "manual",
        "unsync",
        "error",
        "stale",
        "locale",
        "local_source",
        "unknown_zone",
        "tamper",
    ],
)
def test_os_clock_requires_actual_supported_healthy_diagnostic(change):
    evidence = os_evidence({"utc_ns": 100, "monotonic_ns": 100})
    data = evidence["diagnostic"]
    if change == "stopped":
        data["service_state"] = 1
    elif change == "manual":
        data["service_start_type"] = 3
    elif change == "unsync":
        data["status_text"] = data["status_text"].replace(
            "0(no warning)", "3(not synchronized)"
        )
    elif change == "error":
        data["status_exit_code"] = 1
    elif change == "stale":
        data["status_text"] = data["status_text"].replace("10.125s", "900.001s")
    elif change == "locale":
        data["status_text"] = "unrecognized diagnostic"
    elif change == "local_source":
        data["status_text"] = data["status_text"].replace(
            "time.windows.com,0x8", "Local CMOS Clock"
        )
    elif change == "unknown_zone":
        data["timezone_id"] = ""
    if change != "tamper":
        evidence["diagnostic_sha256"] = sha(canonical(data))
    else:
        evidence["diagnostic_sha256"] = "a" * 64
    with pytest.raises(ValueError):
        validate_os_clock(evidence)


@pytest.mark.parametrize(
    "delta_wall,delta_mono",
    [(-1, 1), (1, -1), (6_000_001, 1), (60_000_000_001, 60_000_000_001)],
)
def test_clock_reversal_jump_or_lease_expiry(delta_wall, delta_mono):
    with pytest.raises(ValueError):
        validate_stamps(
            (
                {"utc_ns": 100, "monotonic_ns": 100},
                {"utc_ns": 100 + delta_wall, "monotonic_ns": 100 + delta_mono},
            )
        )
