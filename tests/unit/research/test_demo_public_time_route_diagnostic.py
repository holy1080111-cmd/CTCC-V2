"""Synthetic transport tests for a diagnostic that never grants source authority."""

import json
from collections import Counter

import httpx
import pytest

from app.trade_qualification import demo_public_time_route_diagnostic as diagnostic


class _Clock:
    def __init__(self):
        self.counter = 0
        self.base = 1_760_000_000_000_000_000

    def stamp(self):
        self.counter += 1
        offset = self.counter * 100_000_000
        return {"utc_ns": self.base + offset, "monotonic_ns": self.base + offset}

    def os_clock(self):
        return {
            "schema_version": "ctcc.windows_clock_observation.v2",
            "sample": self.stamp(),
        }


class _Stream(httpx.AsyncByteStream):
    def __init__(self, server_ms):
        self.body = json.dumps(
            {"code": "0", "msg": "", "data": [{"ts": str(server_ms)}]}
        ).encode()

    async def __aiter__(self):
        yield self.body

    async def aclose(self):
        pass


@pytest.fixture
def fake_native(monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(diagnostic, "native_stamp", clock.stamp)
    monkeypatch.setattr(diagnostic, "native_os_clock", clock.os_clock)
    monkeypatch.setattr(diagnostic, "validate_os_clock", lambda value: value)
    monkeypatch.setattr(
        diagnostic, "clock_timezone", lambda value: ("Taipei Standard Time", 480)
    )
    monkeypatch.setattr(diagnostic, "_client_context", lambda value: object())
    monkeypatch.setattr(
        diagnostic,
        "_tls",
        lambda response, context, hostname: {
            "hostname": hostname,
            "version": "TLSv1.3",
            "peer_sha256": "a" * 64,
        },
    )
    return clock


@pytest.mark.asyncio
async def test_three_exact_gets_remain_deny_and_read_back(
    tmp_path, monkeypatch, fake_native
):
    requests = Counter()

    def handler(request):
        requests[request.url.host] += 1
        assert request.method == "GET"
        assert request.url.path == "/api/v5/public/time"
        assert not request.url.query
        assert request.headers["x-simulated-trading"] == "1"
        assert not any(
            name in request.headers
            for name in ("authorization", "cookie", "ok-access-key", "ok-access-sign")
        )
        server_ms = (
            fake_native.base + fake_native.counter * 100_000_000 + 10_000_000
        ) // 1_000_000
        return httpx.Response(
            200,
            stream=_Stream(server_ms),
            headers={"content-type": "application/json"},
        )

    def client():
        return httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            trust_env=False,
            follow_redirects=False,
        )

    monkeypatch.setattr(diagnostic, "_client", client)
    captured = await diagnostic.probe_reviewed_demo_public_time_routes(tmp_path)
    assert requests == {
        "openapi.okx.com": 1,
        "us.okx.com": 1,
        "eea.okx.com": 1,
    }
    assert captured.admission == "DENY"
    assert captured.registration_region_authenticated is False
    assert captured.source_authenticity_verified is False
    assert captured.execution_authority is False
    assert captured.coherent_public_time_observation is True
    assert [item["endpoint_observed"] for item in captured.results] == [True] * 3
    replay = diagnostic.read_demo_public_time_route_diagnostic(
        tmp_path, expected_summary_sha256=captured.summary_sha256
    )
    assert replay.results == captured.results
    assert replay.coherent_public_time_observation is True
    with pytest.raises(
        diagnostic.DemoPublicTimeDiagnosticError, match="demo_time_root_not_empty"
    ):
        await diagnostic.probe_reviewed_demo_public_time_routes(tmp_path)
    assert sum(requests.values()) == 3
    (tmp_path / "global-start.json").write_bytes(b"tampered")
    with pytest.raises(
        diagnostic.DemoPublicTimeDiagnosticError, match="demo_time_readback_invalid"
    ):
        diagnostic.read_demo_public_time_route_diagnostic(
            tmp_path, expected_summary_sha256=captured.summary_sha256
        )


@pytest.mark.asyncio
async def test_future_server_time_rejects_without_replacing_raw(
    tmp_path, monkeypatch, fake_native
):
    requests = Counter()

    def handler(request):
        requests[request.url.host] += 1
        future_ms = (fake_native.base + 60_000_000_000) // 1_000_000
        return httpx.Response(
            200,
            stream=_Stream(future_ms),
            headers={"content-type": "application/json"},
        )

    monkeypatch.setattr(
        diagnostic,
        "_client",
        lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(handler), trust_env=False
        ),
    )
    captured = await diagnostic.probe_reviewed_demo_public_time_routes(tmp_path)
    assert sum(requests.values()) == 3
    assert all(item["endpoint_observed"] is False for item in captured.results)
    assert all(
        item["failure_code"] == "demo_time_observation_rejected"
        for item in captured.results
    )
    assert all((tmp_path / f"{region}.raw").is_file() for region in diagnostic._REGIONS)
    diagnostic.read_demo_public_time_route_diagnostic(
        tmp_path, expected_summary_sha256=captured.summary_sha256
    )
    (tmp_path / "global.raw").write_bytes(b"tampered")
    with pytest.raises(
        diagnostic.DemoPublicTimeDiagnosticError, match="demo_time_readback_invalid"
    ):
        diagnostic.read_demo_public_time_route_diagnostic(
            tmp_path, expected_summary_sha256=captured.summary_sha256
        )


@pytest.mark.asyncio
async def test_unhealthy_native_clock_starts_no_request(tmp_path, monkeypatch):
    monkeypatch.setattr(
        diagnostic,
        "native_os_clock",
        lambda: (_ for _ in ()).throw(ValueError("synthetic_clock_failure")),
    )
    monkeypatch.setattr(
        diagnostic,
        "_client",
        lambda: (_ for _ in ()).throw(AssertionError("network_must_not_start")),
    )
    with pytest.raises(
        diagnostic.DemoPublicTimeDiagnosticError,
        match="demo_time_native_clock_rejected",
    ):
        await diagnostic.probe_reviewed_demo_public_time_routes(tmp_path)
    assert (tmp_path / "clock-before.json").is_file()
    assert not (tmp_path / "global-start.json").exists()


@pytest.mark.asyncio
async def test_changed_native_clock_marks_overall_false_without_rewriting_routes(
    tmp_path, monkeypatch, fake_native
):
    clock_calls = 0

    def os_clock():
        nonlocal clock_calls
        clock_calls += 1
        value = fake_native.os_clock()
        if clock_calls == 2:
            value["schema_version"] = "ctcc.changed_clock_observation.v2"
        return value

    def handler(request):
        server_ms = (
            fake_native.base + fake_native.counter * 100_000_000 + 10_000_000
        ) // 1_000_000
        return httpx.Response(
            200,
            stream=_Stream(server_ms),
            headers={"content-type": "application/json"},
        )

    monkeypatch.setattr(diagnostic, "native_os_clock", os_clock)
    monkeypatch.setattr(
        diagnostic,
        "_client",
        lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(handler), trust_env=False
        ),
    )
    captured = await diagnostic.probe_reviewed_demo_public_time_routes(tmp_path)
    assert [row["endpoint_observed"] for row in captured.results] == [True] * 3
    assert captured.coherent_public_time_observation is False
    summary = json.loads((tmp_path / "summary.json").read_bytes())
    assert summary["native_clock_before_after_valid"] is False
    assert summary["coherent_public_time_observation"] is False
    assert (
        diagnostic.read_demo_public_time_route_diagnostic(
            tmp_path, expected_summary_sha256=captured.summary_sha256
        ).coherent_public_time_observation
        is False
    )


@pytest.mark.asyncio
async def test_redirect_response_does_not_follow_or_prove_route(
    tmp_path, monkeypatch, fake_native
):
    requests = Counter()

    def handler(request):
        requests[request.url.host] += 1
        return httpx.Response(302, headers={"location": "https://example.org"})

    monkeypatch.setattr(
        diagnostic,
        "_client",
        lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            trust_env=False,
            follow_redirects=False,
        ),
    )
    captured = await diagnostic.probe_reviewed_demo_public_time_routes(tmp_path)
    assert sum(requests.values()) == 3
    assert all(row["endpoint_observed"] is False for row in captured.results)
    assert captured.coherent_public_time_observation is False
