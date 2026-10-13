"""Opt-in public timestamp diagnostic; never a CTCC source or order authority.

The fixed five GETs are public time, books5, open interest, funding, public
time. There are no credentials, private routes, retries, redirects, proxies,
or fallback origins. Raw bodies are retained before timestamp interpretation.
This probe cannot authenticate a Demo registration region or qualify a trade.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import ssl
import subprocess
from pathlib import Path

import httpcore
import httpx

from app.domain.native_clock import native_os_clock, native_stamp
from app.domain.source_primitives import validate_stamps

ORIGIN = "https://openapi.okx.com"
HOSTNAME = "openapi.okx.com"
INSTRUMENT = "BTC-USDT-SWAP"
REQUESTS = (
    ("time_pre", "/api/v5/public/time", ()),
    ("books5", "/api/v5/market/books", (("instId", INSTRUMENT), ("sz", "5"))),
    (
        "open_interest",
        "/api/v5/public/open-interest",
        (("instType", "SWAP"), ("instId", INSTRUMENT)),
    ),
    ("funding", "/api/v5/public/funding-rate", (("instId", INSTRUMENT),)),
    ("time_post", "/api/v5/public/time", ()),
)
MAX_BODY = 32768
SAFE_RESPONSE_HEADERS = {"content-type", "content-length", "content-encoding", "date"}


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _json(raw: object) -> bytes:
    return json.dumps(
        raw,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _put_new(path: Path, raw: bytes) -> str:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as target:
        target.write(raw)
        target.flush()
        os.fsync(target.fileno())
    return _sha(raw)


def _strict_object(pairs: list[tuple[str, object]]) -> dict:
    result: dict = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def _reject_non_json_constant(_value: str) -> None:
    raise ValueError("source_json_invalid")


def inspect_raw(role: str, raw: bytes, stamps: tuple[dict, dict, dict, dict]) -> dict:
    """Interpret one raw response without changing or repairing its timestamp."""
    validate_stamps(stamps)
    if role not in {item[0] for item in REQUESTS} or not 0 < len(raw) <= MAX_BODY:
        raise ValueError("diagnostic_input_invalid")
    try:
        payload = json.loads(
            raw,
            object_pairs_hook=_strict_object,
            parse_constant=_reject_non_json_constant,
        )
        canonical_json = _json(payload)  # Reject overflowed finite-looking numbers.
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError("source_json_invalid") from exc
    if (
        type(payload) is not dict
        or payload.get("code") != "0"
        or payload.get("msg") != ""
        or type(payload.get("data")) is not list
        or len(payload["data"]) != 1
        or type(payload["data"][0]) is not dict
    ):
        raise ValueError("source_shape_invalid")
    row = payload["data"][0]
    if role in {"open_interest", "funding"} and row.get("instId") != INSTRUMENT:
        raise ValueError("source_instrument_mismatch")
    ts = row.get("ts")
    if type(ts) is not str or len(ts) != 13 or not ts.isascii() or not ts.isdigit():
        raise ValueError("source_timestamp_invalid")
    source_ns = int(ts) * 1_000_000
    request, headers, body, validation = stamps
    semantics = (
        "server_response_time"
        if role.startswith("time_")
        else "book_generation"
        if role == "books5"
        else "exchange_data_return"
    )
    return {
        "role": role,
        "requested_instrument_id": None if role.startswith("time_") else INSTRUMENT,
        "response_instrument_id": row.get("instId"),
        "response_instrument_verified": role in {"open_interest", "funding"},
        "timestamp_semantics": semantics,
        "source_ts_raw": ts,
        "source_timestamp_ns": source_ns,
        "source_minus_request_start_ns": source_ns - request["utc_ns"],
        "source_minus_headers_received_ns": source_ns - headers["utc_ns"],
        "body_complete_minus_source_ns": body["utc_ns"] - source_ns,
        "source_not_future_at_headers": source_ns <= headers["utc_ns"],
        "source_within_request_interval": request["utc_ns"]
        <= source_ns
        <= body["utc_ns"],
        "body_sha256": _sha(raw),
        "canonical_json_sha256": _sha(canonical_json),
        "validation_after_body": validation["monotonic_ns"] >= body["monotonic_ns"],
    }


def _client() -> httpx.AsyncClient:
    transport = httpx.AsyncHTTPTransport(verify=True, trust_env=False, retries=0)
    return httpx.AsyncClient(
        transport=transport,
        trust_env=False,
        follow_redirects=False,
        auth=None,
        timeout=httpx.Timeout(5),
    )


def _assert_isolated(client: httpx.AsyncClient) -> ssl.SSLContext:
    if (
        type(client) is not httpx.AsyncClient
        or client.trust_env
        or client.follow_redirects
        or client.auth is not None
        or client.cookies
        or client.params
        or any(client.event_hooks.values())
        or type(client._transport) is not httpx.AsyncHTTPTransport
        or type(client._transport._pool) is not httpcore.AsyncConnectionPool
        or client._transport._pool._retries != 0
    ):
        raise ValueError("public_transport_not_isolated")
    context = client._transport._pool._ssl_context
    if (
        type(context) is not ssl.SSLContext
        or context.verify_mode != ssl.CERT_REQUIRED
        or context.check_hostname is not True
    ):
        raise ValueError("public_tls_unverified")
    return context


async def _capture_one(
    client: httpx.AsyncClient, role: str, path: str, query: tuple[tuple[str, str], ...]
) -> tuple[dict, bytes]:
    context = _assert_isolated(client)
    request = httpx.Request(
        "GET",
        ORIGIN + path,
        params=query,
        headers={
            "Accept": "application/json",
            "Accept-Encoding": "identity",
            "User-Agent": "CTCC-public-timestamp-diagnostic/1",
        },
        extensions={"timeout": httpx.Timeout(5).as_dict()},
    )
    if request.url.host != HOSTNAME or request.url.path != path or request.content:
        raise ValueError("public_request_scope_invalid")
    started = native_stamp()
    response = None
    raw = bytearray()
    body_complete = False
    headers = completed = None
    tls_peer_sha256 = tls_version = None
    status = None
    transport_failure_class = None
    response_close_failed = False
    response_headers: tuple[tuple[str, str], ...] = ()
    try:
        async with asyncio.timeout(5):
            response = await client.send(
                request, auth=None, follow_redirects=False, stream=True
            )
            headers = native_stamp()
            status = response.status_code
            response_headers = tuple(
                sorted(
                    (name.lower(), value)
                    for name, value in response.headers.items()
                    if name.lower() in SAFE_RESPONSE_HEADERS
                )
            )
            stream = response.extensions.get("network_stream")
            tls = None if stream is None else stream.get_extra_info("ssl_object")
            if (
                type(tls) is ssl.SSLObject
                and tls.context is context
                and tls.server_hostname == HOSTNAME
                and tls.version() in {"TLSv1.2", "TLSv1.3"}
            ):
                certificate = tls.getpeercert(binary_form=True)
                if type(certificate) is bytes and certificate:
                    tls_peer_sha256 = _sha(certificate)
                    tls_version = tls.version()
            async for chunk in response.aiter_raw():
                if len(raw) + len(chunk) > MAX_BODY:
                    raw.extend(chunk[: MAX_BODY - len(raw)])
                    break
                raw.extend(chunk)
            else:
                body_complete = True
            completed = native_stamp()
    except Exception as exc:  # noqa: BLE001 -- retain partial raw diagnostic bytes
        transport_failure_class = type(exc).__name__
        completed = native_stamp()
    finally:
        if response is not None:
            try:
                await response.aclose()
            except Exception:  # noqa: BLE001 -- incomplete diagnostic only
                response_close_failed = True
    validated = native_stamp()
    record = {
        "role": role,
        "method": "GET",
        "origin": ORIGIN,
        "endpoint": path,
        "query": query,
        "http_status": status,
        "response_headers_allowlisted": response_headers,
        "request_start": started,
        "headers_received": headers,
        "body_complete": completed,
        "validation_time": validated,
        "body_complete_observed": body_complete,
        "transport_failure_class": transport_failure_class,
        "response_close_failed": response_close_failed,
        "raw_body_bytes_retained": len(raw),
        "raw_body_sha256": _sha(raw),
        "tls_peer_sha256": tls_peer_sha256,
        "tls_version": tls_version,
        "tls_binding_verified": tls_peer_sha256 is not None,
        "response_url_exact": response is not None and response.url == request.url,
        "redirect_history_empty": response is not None and not response.history,
        "admission": "DENY",
        "execution_authority": False,
    }
    return record, bytes(raw)


def _source_identity() -> dict:
    root = Path(__file__).resolve().parent.parent
    command = ["git", "-c", f"safe.directory={root.as_posix()}"]

    def git(*args: str) -> str:
        return subprocess.run(
            [*command, *args], cwd=root, check=True, capture_output=True, text=True
        ).stdout.strip()

    return {
        "head": git("rev-parse", "HEAD"),
        "tree": git("rev-parse", "HEAD^{tree}"),
        "probe_sha256": _sha(Path(__file__).read_bytes()),
        "working_tree_dirty": bool(git("status", "--porcelain")),
    }


async def run(output: Path) -> dict:
    if not output.is_absolute():
        raise ValueError("absolute_output_required")
    output.mkdir(exist_ok=False)
    source = _source_identity()
    summary = {
        "schema_version": "ctcc.public_timestamp_diagnostic.v2",
        "source_identity": source,
        "origin": ORIGIN,
        "requested_instrument_id": INSTRUMENT,
        "route_status": "registration_region_unproved_diagnostic_only",
        "admission": "DENY",
        "source_authenticity_verified": False,
        "execution_authority": False,
        "account_requests": 0,
        "order_writes": 0,
        "request_count_maximum": len(REQUESTS),
        "request_count_attempted": 0,
        "request_count_observed": 0,
        "observations": [],
        "code": "incomplete",
    }
    client = None
    try:
        clock = native_os_clock()
        summary["native_clock_sha256"] = _put_new(
            output / "native-clock.json", _json(clock)
        )
        for index, (role, path, query) in enumerate(REQUESTS):
            # A public response may set a cookie. A fresh client ensures that
            # no cookie or connection state crosses into the next request.
            client = _client()
            summary["request_count_attempted"] += 1
            receipt, raw = await _capture_one(client, role, path, query)
            prefix = f"{index:02d}-{role}"
            receipt["raw_body_sha256"] = _put_new(output / f"{prefix}.raw", raw)
            stamps = (
                receipt["request_start"],
                receipt["headers_received"],
                receipt["body_complete"],
                receipt["validation_time"],
            )
            try:
                if (
                    receipt["http_status"] != 200
                    or not receipt["response_url_exact"]
                    or not receipt["redirect_history_empty"]
                    or not receipt["body_complete_observed"]
                    or receipt["transport_failure_class"] is not None
                    or receipt["response_close_failed"]
                    or not receipt["tls_binding_verified"]
                ):
                    raise ValueError("public_response_incomplete")
                receipt["interpretation"] = inspect_raw(role, raw, stamps)
                if (
                    role.startswith("time_")
                    and not receipt["interpretation"]["source_within_request_interval"]
                ):
                    raise ValueError("exchange_time_outside_request")
                receipt["code"] = "observed"
            except (ValueError, TypeError) as exc:
                receipt["code"] = (
                    str(exc)
                    if str(exc)
                    in {
                        "public_response_incomplete",
                        "source_json_invalid",
                        "source_shape_invalid",
                        "source_instrument_mismatch",
                        "source_timestamp_invalid",
                        "diagnostic_input_invalid",
                        "exchange_time_outside_request",
                    }
                    else "observation_rejected"
                )
            summary["observations"].append(
                {
                    "role": role,
                    "receipt_sha256": _put_new(
                        output / f"{prefix}.json", _json(receipt)
                    ),
                    "raw_sha256": receipt["raw_body_sha256"],
                    "code": receipt["code"],
                    "source_minus_headers_received_ns": receipt.get(
                        "interpretation", {}
                    ).get("source_minus_headers_received_ns"),
                }
            )
            summary["request_count_observed"] = len(summary["observations"])
            await client.aclose()
            client = None
        complete = all(item["code"] == "observed" for item in summary["observations"])
        market_deltas = [
            item["source_minus_headers_received_ns"]
            for item in summary["observations"]
            if item["role"] in {"books5", "open_interest", "funding"}
        ]
        summary["code"] = (
            "future_source_observed"
            if complete and any(item is not None and item > 0 for item in market_deltas)
            else "five_public_timestamps_observed"
            if complete
            else "incomplete"
        )
    except Exception as exc:  # noqa: BLE001 -- no raw transport exception reaches evidence
        summary["failure_class"] = type(exc).__name__
        summary["code"] = "incomplete"
    finally:
        if client is not None:
            try:
                await client.aclose()
            except Exception:  # noqa: BLE001 -- diagnostic remains denied
                summary["client_close_failed"] = True
                summary["code"] = "incomplete"
        summary["summary_sha256"] = _put_new(output / "summary.json", _json(summary))
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--capture", action="store_true", help="Opt in to five public GETs"
    )
    parser.add_argument(
        "--output", type=Path, required=True, help="New absolute evidence directory"
    )
    args = parser.parse_args()
    if not args.capture:
        parser.error(
            "--capture is required; import or inspection performs no network I/O"
        )
    result = asyncio.run(run(args.output))
    print(
        json.dumps(
            {
                "code": result["code"],
                "admission": "DENY",
                "request_count_attempted": result["request_count_attempted"],
                "request_count_observed": result["request_count_observed"],
                "summary_sha256": result["summary_sha256"],
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
