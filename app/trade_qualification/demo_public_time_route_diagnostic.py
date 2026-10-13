"""Independent, DENY-only native diagnostics for reviewed OKX Demo time routes.

This module does not import the public-source owner, a market snapshot, account
credentials, or execution code. A reachable public endpoint cannot identify the
registration site of an account and cannot issue a trading capability.
"""

from __future__ import annotations

import asyncio
import os
import re
import ssl
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import httpcore
import httpx

from app.domain.native_clock import (
    clock_timezone,
    native_os_clock,
    native_stamp,
    validate_os_clock,
)
from app.domain.source_primitives import canonical, decode, sha, validate_stamps
from app.trade_evidence.storage import _posix_root, _root_path, _windows_root
from app.trade_qualification.demo_public_origin import (
    build_demo_public_time_request_v3,
    reviewed_demo_public_route,
    validate_demo_public_time_request_v3,
)

SCHEMA_VERSION = "ctcc.demo_public_time_route_diagnostic.v1"
_REGIONS = ("global", "us_au", "eea")
_MAX_BODY = 4096
_MAX_OBSERVATION_NS = 5_000_000_000
_SAFE_RESPONSE_HEADERS = frozenset(
    {"content-type", "content-length", "content-encoding", "date"}
)
_MILLISECONDS = re.compile(r"[1-9][0-9]{12}")


class DemoPublicTimeDiagnosticError(ValueError):
    """Fixed local failure code; never contains response bytes or network text."""


@dataclass(frozen=True, slots=True)
class DemoPublicTimeRouteDiagnostic:
    root: Path
    summary_sha256: str
    results: tuple[dict, ...]
    coherent_public_time_observation: bool
    admission: Literal["DENY"] = field(default="DENY", init=False)
    registration_region_authenticated: Literal[False] = field(default=False, init=False)
    source_authenticity_verified: Literal[False] = field(default=False, init=False)
    execution_authority: Literal[False] = field(default=False, init=False)


def _root_context(root: Path):
    """Pin this diagnostic's local output without importing a market source."""
    if type(root) is not type(Path()):
        raise DemoPublicTimeDiagnosticError("demo_time_root_invalid")
    return (_windows_root if os.name == "nt" else _posix_root)(_root_path(root))


def _publish(directory, name: str, payload: bytes) -> None:
    """The existing pinned directory publisher uses exclusive, no-clobber writes."""
    directory.publish(name, payload)
    if directory.read(name, max(len(payload), 1)) != payload:
        raise DemoPublicTimeDiagnosticError("demo_time_readback_failed")


def _plan() -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "regions": list(_REGIONS),
        "environment": "demo",
        "method": "GET",
        "endpoint": "/api/v5/public/time",
        "max_requests_per_region": 1,
        "retries": 0,
        "trust_env": False,
        "follow_redirects": False,
        "max_response_bytes": _MAX_BODY,
        "source_authenticity_verified": False,
        "registration_region_authenticated": False,
        "execution_authority": False,
        "admission": "DENY",
    }


def _client() -> httpx.AsyncClient:
    transport = httpx.AsyncHTTPTransport(verify=True, trust_env=False, retries=0)
    return httpx.AsyncClient(
        transport=transport,
        trust_env=False,
        follow_redirects=False,
        auth=None,
        timeout=httpx.Timeout(2),
    )


def _client_context(client: httpx.AsyncClient) -> ssl.SSLContext:
    if (
        type(client) is not httpx.AsyncClient
        or client.is_closed
        or client.trust_env
        or client.auth is not None
        or client.follow_redirects
        or client.params
        or client.cookies
        or any(client.event_hooks.values())
        or type(client._mounts) is not dict
        or any(mount is not None for mount in client._mounts.values())
    ):
        raise DemoPublicTimeDiagnosticError("demo_time_client_not_isolated")
    transport = client._transport
    if (
        type(transport) is not httpx.AsyncHTTPTransport
        or type(transport._pool) is not httpcore.AsyncConnectionPool
        or type(transport._pool._retries) is not int
        or transport._pool._retries != 0
    ):
        raise DemoPublicTimeDiagnosticError("demo_time_transport_not_isolated")
    context = transport._pool._ssl_context
    if (
        type(context) is not ssl.SSLContext
        or context.verify_mode != ssl.CERT_REQUIRED
        or context.check_hostname is not True
    ):
        raise DemoPublicTimeDiagnosticError("demo_time_tls_not_verified")
    return context


def _tls(response: httpx.Response, context: ssl.SSLContext, hostname: str) -> dict:
    stream = response.extensions.get("network_stream")
    tls = None if stream is None else stream.get_extra_info("ssl_object")
    if (
        type(tls) is not ssl.SSLObject
        or tls.context is not context
        or tls.server_hostname != hostname
        or tls.version() not in {"TLSv1.2", "TLSv1.3"}
    ):
        raise DemoPublicTimeDiagnosticError("demo_time_tls_not_verified")
    certificate = tls.getpeercert(binary_form=True)
    if type(certificate) is not bytes or not certificate:
        raise DemoPublicTimeDiagnosticError("demo_time_tls_not_verified")
    return {
        "hostname": hostname,
        "version": tls.version(),
        "peer_sha256": sha(certificate),
    }


def _server_time(raw: bytes) -> tuple[str, str]:
    document = decode(raw, _MAX_BODY)
    if (
        type(document) is not dict
        or set(document) != {"code", "msg", "data"}
        or document["code"] != "0"
        or document["msg"] != ""
        or type(document["data"]) is not list
        or len(document["data"]) != 1
        or type(document["data"][0]) is not dict
        or set(document["data"][0]) != {"ts"}
    ):
        raise DemoPublicTimeDiagnosticError("demo_time_body_invalid")
    value = document["data"][0]["ts"]
    if type(value) is not str or _MILLISECONDS.fullmatch(value) is None:
        raise DemoPublicTimeDiagnosticError("demo_time_body_invalid")
    return value, sha(canonical(document))


def _response_headers(response: httpx.Response) -> tuple[list[list[str]], bool]:
    safe = [
        [name, value]
        for name, value in response.headers.multi_items()
        if name in _SAFE_RESPONSE_HEADERS
    ]
    invalid = (
        len(safe) > 16
        or any(len(value) > 1024 for _, value in safe)
        or len({name for name, _ in safe}) != len(safe)
    )
    return [[name, value[:1024]] for name, value in safe[:16]], invalid


def _validate_observation(result: dict, raw: bytes) -> dict:
    """Classify measured endpoint/time coherence only, never account origin."""
    try:
        route = reviewed_demo_public_route(result["region"])
        stamps = tuple(
            result[key]
            for key in (
                "request_start",
                "headers_received",
                "body_complete",
                "validation_complete",
            )
        )
        if (
            result["response_url"] != route.rest_origin + "/api/v5/public/time"
            or result["status_code"] != 200
            or result["redirect_count"] != 0
            or result["tls_hostname"] != route.rest_hostname
            or result["tls_version"] not in ("TLSv1.2", "TLSv1.3")
            or type(result["tls_peer_sha256"]) is not str
            or re.fullmatch("[a-f0-9]{64}", result["tls_peer_sha256"]) is None
            or result["raw_body_sha256"] != sha(raw)
            or result["raw_body_size"] != len(raw)
            or result["body_truncated"] is not False
        ):
            raise ValueError
        headers = result["response_headers"]
        fields = dict(headers)
        if (
            len(fields) != len(headers)
            or fields.get("content-type", "").split(";", 1)[0].strip().lower()
            != "application/json"
            or fields.get("content-encoding", "identity").lower() != "identity"
            or (
                "content-length" in fields
                and (
                    not fields["content-length"].isascii()
                    or not fields["content-length"].isdigit()
                    or int(fields["content-length"]) != len(raw)
                )
            )
        ):
            raise ValueError
        checked = validate_stamps(stamps)
        if checked[-1].monotonic_ns - checked[0].monotonic_ns > _MAX_OBSERVATION_NS:
            raise ValueError
        server_ms, canonical_sha = _server_time(raw)
        if (
            not checked[0].utc_ns <= int(server_ms) * 1_000_000 <= checked[1].utc_ns
            or result["server_time_ms"] != server_ms
            or result["canonical_body_sha256"] != canonical_sha
        ):
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise DemoPublicTimeDiagnosticError("demo_time_observation_rejected") from None
    return result


def _coherent_public_time(
    before: dict, after: dict, results: list[dict], same_clock: bool
) -> bool:
    """A route-local observation is insufficient when the full clock path fails."""
    if not same_clock or any(row["endpoint_observed"] is not True for row in results):
        return False
    sequence = [before["sample"]]
    for row in results:
        sequence.extend(
            row[key]
            for key in (
                "request_start",
                "headers_received",
                "body_complete",
                "validation_complete",
            )
        )
    sequence.append(after["sample"])
    try:
        validate_stamps(tuple(sequence))
    except Exception:  # noqa: BLE001 -- the entire path stays diagnostic
        return False
    return True


async def _probe_one(region: str, directory) -> dict:
    route = reviewed_demo_public_route(region)
    request = build_demo_public_time_request_v3(route, "time_pre")
    validate_demo_public_time_request_v3(route, "time_pre", request)
    client = _client()
    response = None
    started = None
    raw = bytearray()
    stamps: dict[str, dict | None] = {
        "headers_received": None,
        "body_complete": None,
        "validation_complete": None,
    }
    detail = {
        "response_url": None,
        "status_code": None,
        "redirect_count": None,
        "response_headers": [],
        "tls_hostname": None,
        "tls_version": None,
        "tls_peer_sha256": None,
        "body_truncated": False,
        "server_time_ms": None,
        "canonical_body_sha256": None,
    }
    code = "demo_time_transport_rejected"
    try:
        context = _client_context(client)
        started = native_stamp()
        _publish(
            directory,
            f"{region}-start.json",
            canonical(
                {
                    "region": region,
                    "method": "GET",
                    "url": str(request.url),
                    "request_headers": sorted(
                        [name.lower(), value] for name, value in request.headers.items()
                    ),
                    "request_start": started,
                    "max_requests": 1,
                }
            ),
        )
        # The start record is durable before the only network send for this route.
        async with asyncio.timeout(5):
            response = await client.send(
                request, auth=None, follow_redirects=False, stream=True
            )
            stamps["headers_received"] = native_stamp()
            detail["response_url"] = str(response.url)
            detail["status_code"] = response.status_code
            detail["redirect_count"] = len(response.history)
            detail["response_headers"], bad_headers = _response_headers(response)
            if bad_headers:
                code = "demo_time_headers_rejected"
            try:
                proof = _tls(response, context, route.rest_hostname)
                detail["tls_hostname"] = proof["hostname"]
                detail["tls_version"] = proof["version"]
                detail["tls_peer_sha256"] = proof["peer_sha256"]
            except DemoPublicTimeDiagnosticError:
                code = "demo_time_tls_not_verified"
            async for chunk in response.aiter_raw(chunk_size=_MAX_BODY + 1):
                remaining = _MAX_BODY - len(raw)
                raw.extend(chunk[:remaining])
                if len(chunk) > remaining:
                    detail["body_truncated"] = True
                    code = "demo_time_body_truncated"
                    break
            if not detail["body_truncated"]:
                stamps["body_complete"] = native_stamp()
        if stamps["body_complete"] is not None:
            server_ms, canonical_sha = _server_time(bytes(raw))
            detail["server_time_ms"] = server_ms
            detail["canonical_body_sha256"] = canonical_sha
            if code == "demo_time_transport_rejected":
                code = "demo_time_observation_rejected"
    except asyncio.CancelledError:
        code = "demo_time_cancelled"
        raise
    except Exception:  # noqa: BLE001 -- never retain network exception text
        if code == "demo_time_transport_rejected" and response is not None:
            code = "demo_time_observation_rejected"
    finally:
        if response is not None:
            try:
                await response.aclose()
            except Exception:  # noqa: BLE001 -- preserve raw; still DENY
                code = "demo_time_close_rejected"
        try:
            await client.aclose()
        except Exception:  # noqa: BLE001 -- preserve raw; still DENY
            code = "demo_time_close_rejected"
        try:
            stamps["validation_complete"] = native_stamp()
        except Exception:  # noqa: BLE001 -- missing time stays missing
            stamps["validation_complete"] = None
        observed = {
            "schema_version": SCHEMA_VERSION,
            "region": region,
            "environment": "demo",
            "request_start": started,
            **stamps,
            **detail,
            "raw_body_sha256": sha(bytes(raw)) if raw else None,
            "raw_body_size": len(raw),
            "failure_code": code,
            "endpoint_observed": False,
            "registration_region_authenticated": False,
            "source_authenticity_verified": False,
            "execution_authority": False,
            "admission": "DENY",
        }
        if raw:
            _publish(directory, f"{region}.raw", bytes(raw))
        if (
            code in {"demo_time_transport_rejected", "demo_time_observation_rejected"}
            and all(stamps.values())
            and observed["request_start"] is not None
        ):
            try:
                observed["endpoint_observed"] = True
                _validate_observation(observed, bytes(raw))
            except DemoPublicTimeDiagnosticError:
                observed["endpoint_observed"] = False
            else:
                observed["failure_code"] = None
        _publish(directory, f"{region}.json", canonical(observed))
    return observed


async def probe_reviewed_demo_public_time_routes(
    root: Path,
) -> DemoPublicTimeRouteDiagnostic:
    """Attempt at most one unauthenticated GET per fixed route, sequentially.

    The absolute output directory must pre-exist and be empty. A partially
    written directory is preserved for forensics and is never resumed or retried.
    """
    if type(root) is not type(Path()) or not root.is_absolute():
        raise DemoPublicTimeDiagnosticError("demo_time_root_invalid")
    with _root_context(root) as directory:
        if directory.names():
            raise DemoPublicTimeDiagnosticError("demo_time_root_not_empty")
        plan = _plan()
        _publish(directory, "plan.json", canonical(plan))
        try:
            before = native_os_clock()
            validate_os_clock(before)
        except Exception:  # noqa: BLE001 -- no network when the native clock fails
            _publish(
                directory,
                "clock-before.json",
                canonical({"outcome": "rejected", "admission": "DENY"}),
            )
            raise DemoPublicTimeDiagnosticError(
                "demo_time_native_clock_rejected"
            ) from None
        _publish(directory, "clock-before.json", canonical(before))
        results = []
        for region in _REGIONS:
            results.append(await _probe_one(region, directory))
        try:
            after = native_os_clock()
            validate_os_clock(after)
        except Exception:  # noqa: BLE001 -- completed probe remains only diagnostic
            _publish(
                directory,
                "clock-after.json",
                canonical({"outcome": "rejected", "admission": "DENY"}),
            )
            same_clock = False
        else:
            _publish(directory, "clock-after.json", canonical(after))
            try:
                validate_stamps((before["sample"], after["sample"]))
                same_clock = before["schema_version"] == after[
                    "schema_version"
                ] and clock_timezone(before) == clock_timezone(after)
            except Exception:  # noqa: BLE001 -- keep the real after-clock bytes
                same_clock = False
        coherent = _coherent_public_time(before, after, results, same_clock)
        summary = {
            "schema_version": SCHEMA_VERSION,
            "plan_sha256": sha(directory.read("plan.json", 8192)),
            "clock_before_sha256": sha(directory.read("clock-before.json", 524288)),
            "clock_after_sha256": sha(directory.read("clock-after.json", 524288)),
            "region_results": [
                {
                    "region": value["region"],
                    "endpoint_observed": value["endpoint_observed"],
                    "failure_code": value["failure_code"],
                    "start_sha256": sha(
                        directory.read(f"{value['region']}-start.json", 8192)
                    ),
                    "result_sha256": sha(
                        directory.read(f"{value['region']}.json", 8192)
                    ),
                }
                for value in results
            ],
            "native_clock_before_after_valid": same_clock,
            "coherent_public_time_observation": coherent,
            "registration_region_authenticated": False,
            "source_authenticity_verified": False,
            "execution_authority": False,
            "admission": "DENY",
        }
        raw_summary = canonical(summary)
        _publish(directory, "summary.json", raw_summary)
        return DemoPublicTimeRouteDiagnostic(
            root, sha(raw_summary), tuple(results), coherent
        )


def read_demo_public_time_route_diagnostic(
    root: Path, *, expected_summary_sha256: str
) -> DemoPublicTimeRouteDiagnostic:
    """Read back a completed diagnostic; replay never grants source authority."""
    if (
        type(root) is not type(Path())
        or not root.is_absolute()
        or type(expected_summary_sha256) is not str
        or re.fullmatch("[a-f0-9]{64}", expected_summary_sha256) is None
    ):
        raise DemoPublicTimeDiagnosticError("demo_time_readback_pin_invalid")
    with _root_context(root) as directory:
        plan_raw = directory.read("plan.json", 8192)
        plan = decode(plan_raw, 8192)
        summary_raw = directory.read("summary.json", 8192)
        summary = decode(summary_raw, 8192)
        before_raw = directory.read("clock-before.json", 524288)
        after_raw = directory.read("clock-after.json", 524288)
        if (
            sha(summary_raw) != expected_summary_sha256
            or canonical(plan) != plan_raw
            or canonical(summary) != summary_raw
            or plan != _plan()
            or summary.get("plan_sha256") != sha(plan_raw)
            or summary.get("clock_before_sha256") != sha(before_raw)
            or summary.get("clock_after_sha256") != sha(after_raw)
            or summary.get("admission") != "DENY"
            or summary.get("registration_region_authenticated") is not False
            or summary.get("source_authenticity_verified") is not False
            or summary.get("execution_authority") is not False
            or len(summary.get("region_results", [])) != len(_REGIONS)
        ):
            raise DemoPublicTimeDiagnosticError("demo_time_readback_invalid")
        before = decode(before_raw, 524288)
        after = decode(after_raw, 524288)
        if before.get("outcome") != "rejected":
            validate_os_clock(before)
        if after.get("outcome") != "rejected":
            validate_os_clock(after)
        try:
            validate_stamps((before["sample"], after["sample"]))
            same_clock = before["schema_version"] == after[
                "schema_version"
            ] and clock_timezone(before) == clock_timezone(after)
        except Exception:  # noqa: BLE001 -- a rejected final clock is diagnostic
            same_clock = False
        if summary.get("native_clock_before_after_valid") is not same_clock:
            raise DemoPublicTimeDiagnosticError("demo_time_readback_invalid")
        expected = {
            "plan.json",
            "clock-before.json",
            "clock-after.json",
            "summary.json",
        }
        results = []
        for region, summary_row in zip(
            _REGIONS, summary["region_results"], strict=True
        ):
            start_name, result_name, raw_name = (
                f"{region}-start.json",
                f"{region}.json",
                f"{region}.raw",
            )
            start_raw = directory.read(start_name, 8192)
            result_raw = directory.read(result_name, 8192)
            if (
                type(summary_row) is not dict
                or summary_row.get("start_sha256") != sha(start_raw)
                or summary_row.get("result_sha256") != sha(result_raw)
            ):
                raise DemoPublicTimeDiagnosticError("demo_time_readback_invalid")
            start, result = decode(start_raw, 8192), decode(result_raw, 8192)
            expected_request = build_demo_public_time_request_v3(
                reviewed_demo_public_route(region), "time_pre"
            )
            expected_headers = sorted(
                [name.lower(), value]
                for name, value in expected_request.headers.items()
            )
            expected.update((start_name, result_name))
            raw = b""
            if result["raw_body_size"]:
                raw = directory.read(raw_name, _MAX_BODY)
                expected.add(raw_name)
            if (
                canonical(start) != start_raw
                or canonical(result) != result_raw
                or start.get("region") != region
                or start.get("method") != "GET"
                or start.get("url")
                != reviewed_demo_public_route(region).rest_origin
                + "/api/v5/public/time"
                or start.get("request_headers") != expected_headers
                or start.get("max_requests") != 1
                or result.get("region") != region
                or result.get("request_start") != start.get("request_start")
                or result.get("raw_body_size") != len(raw)
                or result.get("raw_body_sha256") != (sha(raw) if raw else None)
                or result.get("admission") != "DENY"
                or result.get("registration_region_authenticated") is not False
                or result.get("source_authenticity_verified") is not False
                or result.get("execution_authority") is not False
                or (result.get("endpoint_observed") is True)
                != (result.get("failure_code") is None)
                or summary_row
                != {
                    "region": region,
                    "endpoint_observed": result["endpoint_observed"],
                    "failure_code": result["failure_code"],
                    "start_sha256": sha(start_raw),
                    "result_sha256": sha(result_raw),
                }
            ):
                raise DemoPublicTimeDiagnosticError("demo_time_readback_invalid")
            if result["endpoint_observed"]:
                _validate_observation(result, raw)
            results.append(result)
        # Earlier pinned v1 diagnostics predate the explicit overall flag. The
        # flag is derived from immutable, independently pinned result records.
        coherent = _coherent_public_time(before, after, results, same_clock)
        if (
            "coherent_public_time_observation" in summary
            and summary["coherent_public_time_observation"] is not coherent
        ):
            raise DemoPublicTimeDiagnosticError("demo_time_readback_invalid")
        if set(directory.names()) != expected:
            raise DemoPublicTimeDiagnosticError("demo_time_readback_inventory_invalid")
        return DemoPublicTimeRouteDiagnostic(
            root, sha(summary_raw), tuple(results), coherent
        )
