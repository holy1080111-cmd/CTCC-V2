"""Opt-in anonymous Global-public component clock diagnostic, always DENY.

This is deliberately separate from the account-bound Demo source runtime. It
observes only fixed public GETs and one public ticker subscription; its raw
application bytes and measured times cannot establish an account's region or
qualify a candidate. In particular, candle ``ts`` is the bar *opening* time,
not the HTTP response time.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import ssl
from pathlib import Path

from websockets.asyncio.client import connect

from app.domain.native_clock import native_os_clock
from app.domain.source_primitives import validate_stamps
from scripts import diagnose_public_timestamp as base

WS_ENDPOINT = "wss://ws.okx.com:443/ws/v5/public"
WS_HOSTNAME = "ws.okx.com"
WS_SUBSCRIPTION = base._json(
    {
        "id": "ctccpublicdiagnostic1",
        "op": "subscribe",
        "args": [{"channel": "tickers", "instId": base.INSTRUMENT}],
    }
)
REQUESTS = (
    ("time_pre", "/api/v5/public/time", ()),
    *(
        (
            f"candle_{bar.lower()}",
            "/api/v5/market/candles",
            (("instId", base.INSTRUMENT), ("bar", bar), ("limit", "2")),
        )
        for bar in ("4H", "1H", "15m", "5m")
    ),
    ("ticker", "/api/v5/market/ticker", (("instId", base.INSTRUMENT),)),
    (
        "mark",
        "/api/v5/public/mark-price",
        (("instType", "SWAP"), ("instId", base.INSTRUMENT)),
    ),
    *base.REQUESTS[1:4],
    ("time_post", "/api/v5/public/time", ()),
)
BAR_MILLISECONDS = {
    "candle_4h": 14_400_000,
    "candle_1h": 3_600_000,
    "candle_15m": 900_000,
    "candle_5m": 300_000,
}


class _NoRedirectConnect(connect):
    # websockets 17 follows handshake redirects by default.
    def process_redirect(self, exc):
        return exc


def _reject_non_json_constant(_value: str) -> None:
    raise ValueError("source_json_invalid")


def _canonical_json(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("source_json_invalid") from exc


def _decode(raw: bytes) -> object:
    if type(raw) is not bytes or not 0 < len(raw) <= base.MAX_BODY:
        raise ValueError("diagnostic_body_size_invalid")
    try:
        payload = json.loads(
            raw,
            object_pairs_hook=base._strict_object,
            parse_constant=_reject_non_json_constant,
        )
        _canonical_json(payload)  # Reject overflowed finite-looking numbers too.
        return payload
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError("source_json_invalid") from exc


def _timestamp(row: dict) -> tuple[str, int]:
    ts = row.get("ts")
    if type(ts) is not str or len(ts) != 13 or not ts.isascii() or not ts.isdigit():
        raise ValueError("source_timestamp_invalid")
    return ts, int(ts) * 1_000_000


def inspect_rest(role: str, raw: bytes, stamps: tuple[dict, dict, dict, dict]) -> dict:
    """Check shape and original time meaning without promoting source authority."""
    validate_stamps(stamps)
    if role not in {item[0] for item in REQUESTS}:
        raise ValueError("diagnostic_role_invalid")
    payload = _decode(raw)
    if (
        type(payload) is not dict
        or payload.get("code") != "0"
        or payload.get("msg") != ""
        or type(payload.get("data")) is not list
    ):
        raise ValueError("source_shape_invalid")
    data = payload["data"]
    if role in BAR_MILLISECONDS:
        if len(data) != 2 or any(
            type(row) is not list
            or len(row) != 9
            or any(type(field) is not str for field in row)
            or row[8] not in {"0", "1"}
            for row in data
        ):
            raise ValueError("source_candle_shape_invalid")
        rows = [{"ts": row[0], "confirm": row[8]} for row in data]
        first_raw, first_ns = _timestamp(rows[0])
        _, second_ns = _timestamp(rows[1])
        if (
            first_ns <= second_ns
            or first_ns - second_ns != BAR_MILLISECONDS[role] * 1_000_000
        ):
            raise ValueError("source_candle_order_invalid")
        semantics = "bar_open_not_http_response_time"
        extra = {
            "bar_milliseconds": BAR_MILLISECONDS[role],
            "latest_confirm": rows[0]["confirm"],
            "previous_confirm": rows[1]["confirm"],
            "latest_bar_closed_by_headers": (
                first_ns + BAR_MILLISECONDS[role] * 1_000_000 <= stamps[1]["utc_ns"]
            ),
        }
        verified_instrument = False  # Candles have no instId in the body.
    else:
        if len(data) != 1 or type(data[0]) is not dict:
            raise ValueError("source_shape_invalid")
        row = data[0]
        if (
            role in {"ticker", "mark", "open_interest", "funding"}
            and row.get("instId") != base.INSTRUMENT
        ):
            raise ValueError("source_instrument_mismatch")
        first_raw, first_ns = _timestamp(row)
        semantics = {
            "time_pre": "server_response_time",
            "time_post": "server_response_time",
            "ticker": "ticker_data_timestamp",
            "mark": "mark_price_data_timestamp",
            "books5": "book_generation",
            "open_interest": "exchange_data_return",
            "funding": "exchange_data_return",
        }[role]
        extra = {"response_instrument_id": row.get("instId")}
        verified_instrument = role in {"ticker", "mark", "open_interest", "funding"}
    return {
        "role": role,
        "requested_instrument_id": None
        if role.startswith("time_")
        else base.INSTRUMENT,
        "response_instrument_verified": verified_instrument,
        "timestamp_semantics": semantics,
        "source_ts_raw": first_raw,
        "source_timestamp_ns": first_ns,
        "source_minus_request_start_ns": first_ns - stamps[0]["utc_ns"],
        "source_minus_headers_received_ns": first_ns - stamps[1]["utc_ns"],
        "source_not_future_at_headers": first_ns <= stamps[1]["utc_ns"],
        "source_within_request_interval": stamps[0]["utc_ns"]
        <= first_ns
        <= stamps[2]["utc_ns"],
        "raw_body_sha256": base._sha(raw),
        "canonical_json_sha256": base._sha(_canonical_json(payload)),
        "validation_after_body": stamps[3]["monotonic_ns"] >= stamps[2]["monotonic_ns"],
        **extra,
    }


def inspect_ws(raw: bytes, *, kind: str, received: dict) -> dict:
    """Parse one exact public application message; never attest wire frames."""
    payload = _decode(raw)
    if type(payload) is not dict or type(payload.get("arg")) is not dict:
        raise ValueError("ws_source_shape_invalid")
    arg = payload["arg"]
    if arg.get("channel") != "tickers" or arg.get("instId") != base.INSTRUMENT:
        raise ValueError("ws_source_scope_invalid")
    result = {
        "kind": kind,
        "raw_body_sha256": base._sha(raw),
        "canonical_json_sha256": base._sha(_canonical_json(payload)),
    }
    if kind == "ack":
        if (
            set(payload) != {"id", "event", "arg", "connId"}
            or set(arg) != {"channel", "instId"}
            or payload.get("event") != "subscribe"
            or payload.get("id") != "ctccpublicdiagnostic1"
            or type(payload.get("connId")) is not str
            or not payload["connId"]
        ):
            raise ValueError("ws_ack_invalid")
        return result
    if (
        kind != "ticker"
        or type(payload.get("data")) is not list
        or len(payload["data"]) != 1
    ):
        raise ValueError("ws_ticker_shape_invalid")
    row = payload["data"][0]
    if type(row) is not dict or row.get("instId") != base.INSTRUMENT:
        raise ValueError("ws_source_scope_invalid")
    ts_raw, source_ns = _timestamp(row)
    return {
        **result,
        "source_ts_raw": ts_raw,
        "source_timestamp_ns": source_ns,
        "source_minus_received_ns": source_ns - received["utc_ns"],
        "source_not_future_at_receive": source_ns <= received["utc_ns"],
        "timestamp_semantics": "ticker_data_timestamp",
    }


async def _capture_ws() -> tuple[dict, tuple[bytes, bytes, bytes]]:
    """One connection, one subscribe, two bounded receives, no retry/login."""
    started = base.native_stamp()
    connected = subscribe_started = subscribe_complete = ack_received = (
        ticker_received
    ) = closed = None
    ack = ticker = b""
    tls_peer_sha256 = tls_version = None
    failure_class = None
    socket = None
    context = ssl.create_default_context()
    try:
        async with asyncio.timeout(10):
            socket = await _NoRedirectConnect(
                WS_ENDPOINT,
                ssl=context,
                server_hostname=WS_HOSTNAME,
                proxy=None,
                compression=None,
                additional_headers=None,
                user_agent_header="CTCC-public-component-diagnostic/1",
                open_timeout=5,
                close_timeout=1,
                ping_interval=None,
                max_size=base.MAX_BODY,
                max_queue=1,
            )
            connected = base.native_stamp()
            tls = socket.transport.get_extra_info("ssl_object")
            if (
                type(tls) is ssl.SSLObject
                and tls.context is context
                and tls.server_hostname == WS_HOSTNAME
                and tls.version() in {"TLSv1.2", "TLSv1.3"}
            ):
                cert = tls.getpeercert(binary_form=True)
                if type(cert) is bytes and cert:
                    tls_peer_sha256, tls_version = base._sha(cert), tls.version()
            subscribe_started = base.native_stamp()
            await socket.send(WS_SUBSCRIPTION.decode("ascii"))
            subscribe_complete = base.native_stamp()
            for kind in ("ack", "ticker"):
                frame = await socket.recv()
                if type(frame) is not str:
                    raise ValueError("ws_binary_message_rejected")
                data = frame.encode("utf-8")
                if not 0 < len(data) <= base.MAX_BODY:
                    raise ValueError("ws_message_size_invalid")
                if kind == "ack":
                    ack, ack_received = data, base.native_stamp()
                else:
                    ticker, ticker_received = data, base.native_stamp()
    except Exception as exc:  # noqa: BLE001 -- diagnostic retains partial messages
        failure_class = type(exc).__name__
    finally:
        if socket is not None:
            try:
                async with asyncio.timeout(1):
                    await socket.close()
            except Exception:  # noqa: BLE001 -- no admission even if close fails
                failure_class = "CloseFailed"
            closed = base.native_stamp()
    record = {
        "endpoint": WS_ENDPOINT,
        "environment": "anonymous_global_production_public_diagnostic",
        "connection_started": started,
        "connected": connected,
        "subscribe_started": subscribe_started,
        "subscribe_complete": subscribe_complete,
        "ack_received": ack_received,
        "ticker_received": ticker_received,
        "closed": closed,
        "subscription_sha256": base._sha(WS_SUBSCRIPTION),
        "ack_sha256": base._sha(ack),
        "ticker_sha256": base._sha(ticker),
        "tls_peer_sha256": tls_peer_sha256,
        "tls_version": tls_version,
        "tls_binding_verified": tls_peer_sha256 is not None,
        "application_message_only": True,
        "redirects": "disabled",
        "proxy": "disabled",
        "retries": 0,
        "login": False,
        "failure_class": failure_class,
        "admission": "DENY",
        "execution_authority": False,
    }
    return record, (WS_SUBSCRIPTION, ack, ticker)


async def run(output: Path) -> dict:
    if not output.is_absolute():
        raise ValueError("absolute_output_required")
    output.mkdir(exist_ok=False)
    source = base._source_identity()
    source["probe_sha256"] = base._sha(Path(__file__).read_bytes())
    source["base_probe_sha256"] = base._sha(Path(base.__file__).read_bytes())
    summary = {
        "schema_version": "ctcc.public_component_clock_diagnostic.v1",
        "source_identity": source,
        "environment": "anonymous_global_production_public_diagnostic",
        "origin": base.ORIGIN,
        "ws_endpoint": WS_ENDPOINT,
        "requested_instrument_id": base.INSTRUMENT,
        "route_status": "registration_region_unproved_diagnostic_only",
        "admission": "DENY",
        "source_authenticity_verified": False,
        "execution_authority": False,
        "account_requests": 0,
        "order_writes": 0,
        "request_count_maximum": len(REQUESTS),
        "request_count_attempted": 0,
        "observations": [],
        "ws_attempted": False,
        "ws_code": "unobserved",
        "code": "incomplete",
    }
    client = None
    try:
        summary["native_clock_sha256"] = base._put_new(
            output / "native-clock.json", base._json(native_os_clock())
        )
        for index, (role, path, query) in enumerate(REQUESTS):
            client = base._client()  # fresh TLS/session, never inherit cookies
            summary["request_count_attempted"] += 1
            receipt, raw = await base._capture_one(client, role, path, query)
            prefix = f"{index:02d}-{role}"
            receipt["raw_body_sha256"] = base._put_new(output / f"{prefix}.raw", raw)
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
                stamps = (
                    receipt["request_start"],
                    receipt["headers_received"],
                    receipt["body_complete"],
                    receipt["validation_time"],
                )
                receipt["interpretation"] = inspect_rest(role, raw, stamps)
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
                        "source_candle_shape_invalid",
                        "source_candle_order_invalid",
                        "source_instrument_mismatch",
                        "source_timestamp_invalid",
                        "exchange_time_outside_request",
                    }
                    else "observation_rejected"
                )
            summary["observations"].append(
                {
                    "role": role,
                    "code": receipt["code"],
                    "raw_sha256": receipt["raw_body_sha256"],
                    "receipt_sha256": base._put_new(
                        output / f"{prefix}.json", base._json(receipt)
                    ),
                    "source_minus_headers_received_ns": receipt.get(
                        "interpretation", {}
                    ).get("source_minus_headers_received_ns"),
                    "source_not_future_at_headers": receipt.get(
                        "interpretation", {}
                    ).get("source_not_future_at_headers"),
                }
            )
            await client.aclose()
            client = None
        summary["ws_attempted"] = True
        ws_receipt, (subscription, ack, ticker) = await _capture_ws()
        for name, raw in (
            ("subscription", subscription),
            ("ack", ack),
            ("ticker", ticker),
        ):
            base._put_new(output / f"11-ws-{name}.raw", raw)
        try:
            if (
                ws_receipt["failure_class"] is not None
                or not ws_receipt["tls_binding_verified"]
            ):
                raise ValueError("ws_response_incomplete")
            stamps = (
                ws_receipt["connection_started"],
                ws_receipt["connected"],
                ws_receipt["subscribe_started"],
                ws_receipt["subscribe_complete"],
                ws_receipt["ack_received"],
                ws_receipt["ticker_received"],
                ws_receipt["closed"],
            )
            validate_stamps(stamps)
            ws_receipt["ack_interpretation"] = inspect_ws(
                ack, kind="ack", received=ws_receipt["ack_received"]
            )
            ws_receipt["ticker_interpretation"] = inspect_ws(
                ticker, kind="ticker", received=ws_receipt["ticker_received"]
            )
            ws_receipt["code"] = "observed"
        except (ValueError, TypeError) as exc:
            ws_receipt["code"] = (
                str(exc)
                if str(exc)
                in {
                    "ws_response_incomplete",
                    "ws_source_shape_invalid",
                    "ws_source_scope_invalid",
                    "ws_ack_invalid",
                    "ws_ticker_shape_invalid",
                    "source_timestamp_invalid",
                    "source_json_invalid",
                }
                else "observation_rejected"
            )
        summary["ws_code"] = ws_receipt["code"]
        summary["ws_receipt_sha256"] = base._put_new(
            output / "11-ws.json", base._json(ws_receipt)
        )
        summary["future_component_roles"] = [
            item["role"]
            for item in summary["observations"]
            if item["source_not_future_at_headers"] is False
        ]
        if (
            ws_receipt.get("ticker_interpretation", {}).get(
                "source_not_future_at_receive"
            )
            is False
        ):
            summary["future_component_roles"].append("ws_ticker")
        summary["code"] = (
            (
                "future_source_observed"
                if summary["future_component_roles"]
                else "components_observed"
            )
            if (
                all(item["code"] == "observed" for item in summary["observations"])
                and summary["ws_code"] == "observed"
            )
            else "incomplete"
        )
    except Exception as exc:  # noqa: BLE001 -- never grant authority from a probe
        summary["failure_class"] = type(exc).__name__
    finally:
        if client is not None:
            try:
                await client.aclose()
            except Exception:  # noqa: BLE001
                summary["client_close_failed"] = True
        summary["summary_sha256"] = base._put_new(
            output / "summary.json", base._json(summary)
        )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--capture",
        action="store_true",
        help="Opt in to fixed anonymous public requests",
    )
    parser.add_argument(
        "--output", type=Path, required=True, help="New absolute evidence directory"
    )
    args = parser.parse_args()
    if not args.capture:
        parser.error(
            "--capture required; importing this module performs no network I/O"
        )
    result = asyncio.run(run(args.output))
    print(
        json.dumps(
            {
                key: result[key]
                for key in (
                    "code",
                    "admission",
                    "request_count_attempted",
                    "ws_code",
                    "summary_sha256",
                )
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
