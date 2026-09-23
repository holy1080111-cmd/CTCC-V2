"""Owned public GET acquisition. No account credential, order or settings import."""

from __future__ import annotations

import asyncio
import ssl
import uuid
from dataclasses import dataclass

import httpcore
import httpx

from app.public_market_source.public_clock import (
    clock_timezone,
    native_os_clock,
    native_stamp,
    validate_os_clock,
)
from app.public_market_source.public_market_receipts import (
    ENDPOINT,
    MINUTE_NS,
    TIME_ENDPOINT,
    MeasuredPublicMinuteReceiptV1,
    PublicMinuteCapturePlanV1,
    PublicRawReceiptV1,
    PublicReceiptError,
    canonical,
    checked,
    decode,
    parsed_rows,
    request_query,
    row_content_sha,
    row_identity,
    sha,
    validate_stamps,
    verify_raw,
)

_ISSUER = object()


@dataclass(frozen=True, slots=True, init=False, repr=False)
class _OwnedPublicCapture:
    receipt: MeasuredPublicMinuteReceiptV1
    raw_files: dict[str, bytes]

    def __init__(self, issuer, receipt, raw_files):
        if issuer is not _ISSUER:
            raise PublicReceiptError("owned_capture_required")
        object.__setattr__(self, "receipt", receipt)
        object.__setattr__(self, "raw_files", dict(raw_files))


def _new_client():
    return httpx.AsyncClient(
        transport=httpx.AsyncHTTPTransport(verify=True, trust_env=False, retries=0),
        trust_env=False,
        follow_redirects=False,
        auth=None,
    )


def _client_guard(client, *, first=False):
    if (
        type(client) is not httpx.AsyncClient
        or client.is_closed
        or client.trust_env
        or client.auth is not None
        or client.follow_redirects
        or client.params
        or (first and client.cookies)
        or any(client.event_hooks.values())
        or any(
            name not in {"accept", "accept-encoding", "connection", "user-agent"}
            for name in client.headers
        )
        or type(client._mounts) is not dict
        or any(value is not None for value in client._mounts.values())
    ):
        raise PublicReceiptError("public_client_not_isolated")
    transport = client._transport
    if type(transport) is httpx.MockTransport:
        return False
    if (
        type(transport) is not httpx.AsyncHTTPTransport
        or type(transport._pool) is not httpcore.AsyncConnectionPool
        or type(transport._pool._retries) is not int
        or transport._pool._retries != 0
    ):
        raise PublicReceiptError("public_transport_not_isolated")
    context = transport._pool._ssl_context
    if (
        type(context) is not ssl.SSLContext
        or context.verify_mode != ssl.CERT_REQUIRED
        or context.check_hostname is not True
    ):
        raise PublicReceiptError("verified_tls_required")
    return True


async def _close(resource, *, cancelled=False):
    async def close_once():
        async with asyncio.timeout(2):
            await resource.aclose()

    task = asyncio.create_task(close_once())
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
        except Exception:  # noqa: BLE001 -- fixed diagnostic, no HTTP detail
            break
    failed = False
    try:
        task.result()
    except (Exception, asyncio.CancelledError):  # noqa: BLE001 -- bounded cleanup
        failed = True
    if cancelled:
        raise asyncio.CancelledError
    if failed:
        raise PublicReceiptError("public_cleanup_failed")


async def _fetch(
    client, plan, *, endpoint, query=(), index=0, previous=None, attempt=None
):
    from app.public_market_source.public_attempt_journal import failure_code

    native = _client_guard(client)
    request = httpx.Request(
        "GET",
        plan.origin + endpoint,
        params=query,
        headers={
            "Accept": "application/json",
            "Accept-Encoding": "identity",
            "User-Agent": "CTCC-owned-minute-receipts/1",
        },
        extensions={"timeout": httpx.Timeout(5).as_dict()},
    )
    body = bytearray()
    started = native_stamp()
    if started["utc_ns"] < plan.created_ns:
        raise PublicReceiptError("plan_not_yet_created")
    if attempt is not None:
        attempt.begin_request(endpoint=endpoint, query=query, stamp=started)
    response, receipt, validated_at = None, None, None
    accepted, cleanup, error, interrupted = False, "not_returned", None, False
    try:
        async with asyncio.timeout(5):
            response = await client.send(
                request, auth=None, follow_redirects=False, stream=True
            )
            if type(response) is not httpx.Response:
                response = None  # never call a foreign object's cleanup callback
                raise PublicReceiptError("public_response_rejected")
            headers_at = native_stamp()
            rejection = None
            if (
                response.status_code != 200
                or response.url != request.url
                or response.history
                or response.is_closed
                or response.is_stream_consumed
            ):
                rejection = "public_response_rejected"
            peer, version = "0" * 64, "TLSv1.3"
            proof = {
                "classification": "synthetic_test",
                "peer_sha256": None,
                "hostname": request.url.host,
                "version": None,
            }
            if native:
                proof["classification"] = "unverified"
                try:
                    stream = response.extensions.get("network_stream")
                    tls = (
                        None if stream is None else stream.get_extra_info("ssl_object")
                    )
                    if (
                        type(tls) is not ssl.SSLObject
                        or tls.context is not client._transport._pool._ssl_context
                        or tls.server_hostname != request.url.host
                        or tls.version() not in {"TLSv1.2", "TLSv1.3"}
                    ):
                        raise PublicReceiptError("public_tls_peer_missing")
                    cert = tls.getpeercert(binary_form=True)
                    if type(cert) is not bytes or not cert:
                        raise PublicReceiptError("public_tls_peer_missing")
                    peer, version = sha(cert), tls.version()
                    proof = {
                        "classification": "owned_native_tls",
                        "peer_sha256": peer,
                        "hostname": request.url.host,
                        "version": version,
                    }
                except (ValueError, OSError, AttributeError):
                    rejection = rejection or "public_tls_peer_missing"
            safe_headers = tuple(
                (name, value)
                for name, value in response.headers.multi_items()
                if name
                in {"content-type", "content-length", "content-encoding", "date"}
            )
            oversized_headers = len(safe_headers) > 16 or any(
                len(value) > 1024 for _, value in safe_headers
            )
            if attempt is not None:
                attempt.headers(
                    stamp=headers_at,
                    status=response.status_code,
                    headers=tuple(
                        (name, value[:1024]) for name, value in safe_headers[:16]
                    ),
                    tls=proof,
                    truncated=oversized_headers,
                )
            if (
                len({name for name, _ in safe_headers}) != len(safe_headers)
                or oversized_headers
            ):
                rejection = rejection or "public_headers_invalid"
            if (
                response.headers.get("content-type", "")
                .split(";", 1)[0]
                .strip()
                .lower()
                != "application/json"
                or response.headers.get("content-encoding", "identity").lower()
                != "identity"
            ):
                rejection = rejection or "public_encoding_rejected"
            length = response.headers.get("content-length")
            if length is not None and (
                not length.isascii()
                or not length.isdigit()
                or len(length) > 8
                or int(length) > plan.max_response_bytes
            ):
                rejection = rejection or "public_body_limit"
            # Even a rejected status/header retains only the bounded bytes actually
            # received. It never follows Location or performs another request.
            if response.is_closed or response.is_stream_consumed:
                raise PublicReceiptError(rejection or "public_response_rejected")
            async for chunk in response.stream:
                if type(chunk) is not bytes:
                    raise PublicReceiptError("public_body_chunk_invalid")
                if attempt is not None:
                    attempt.chunk(chunk, native_stamp())
                if len(body) + len(chunk) > plan.max_response_bytes:
                    raise PublicReceiptError("public_body_limit")
                body.extend(chunk)
            body_at = native_stamp()
            if attempt is not None:
                attempt.body_complete(body_at)
            if rejection is not None:
                raise PublicReceiptError(rejection)
            if length is not None and int(length) != len(body):
                raise PublicReceiptError("public_body_length_mismatch")
            raw = bytes(body)
            canonical_body = canonical(decode(raw))
            rows = None
            if endpoint == ENDPOINT:
                rows, _ = parsed_rows(raw)
            validated_at = native_stamp()
            receipt = PublicRawReceiptV1(
                plan_sha256=plan.canonical_sha256(),
                endpoint=endpoint,
                query=query,
                page_index=index,
                previous_page_sha256=previous,
                cursor_type=None if rows is None else "candle_open_ms",
                first_row_identity=None
                if rows is None
                else row_identity(plan, rows[0]),
                last_row_identity=None
                if rows is None
                else row_identity(plan, rows[-1]),
                timestamp_semantics="server_response_time"
                if rows is None
                else "confirmed_candle_open",
                body_sha256=sha(raw),
                body_size=len(raw),
                canonical_body_sha256=sha(canonical_body),
                request_start=started,
                headers_received=headers_at,
                body_complete=body_at,
                validation_complete=validated_at,
                tls_peer_sha256=peer,
                tls_hostname=request.url.host,
                tls_version=version,
                response_headers=safe_headers,
                transport_origin="owned_native_tls" if native else "synthetic_test",
            )
            verify_raw(plan, receipt, raw)
            accepted = True
    except (Exception, asyncio.CancelledError) as exc:  # noqa: BLE001 -- preserve raw then re-raise
        error = exc
        interrupted = isinstance(exc, asyncio.CancelledError)
    finally:
        if response is not None:
            try:
                await _close(response, cancelled=interrupted)
                cleanup = "closed"
            except (Exception, asyncio.CancelledError) as exc:  # noqa: BLE001 -- preserve raw then re-raise
                error = error or exc
                cleanup = "failed"
        if attempt is not None:
            attempt.finish_request(
                accepted=accepted and error is None,
                validation_complete=validated_at if accepted else None,
                error_code="none" if error is None else failure_code(error),
                cleanup=cleanup,
            )
    if error is not None:
        raise error
    return receipt, bytes(body)


def replay_public_capture(receipt, raw_files):
    """Fully replay supplied bytes. This pure function grants no owned provenance."""
    receipt = checked(receipt, MeasuredPublicMinuteReceiptV1)
    plan = PublicMinuteCapturePlanV1.model_validate(receipt.plan)
    if plan.canonical_sha256() != receipt.plan_sha256:
        raise PublicReceiptError("capture_plan_mismatch")
    if type(raw_files) is not dict or any(
        type(k) is not str or type(v) is not bytes for k, v in raw_files.items()
    ):
        raise PublicReceiptError("raw_files_invalid")
    if len(raw_files) > 32 or any(
        not 0 < len(raw) <= plan.max_response_bytes for raw in raw_files.values()
    ):
        raise PublicReceiptError("raw_size_invalid")
    names = (
        "time-before.raw",
        *(f"page-{i:03d}.raw" for i in range(len(receipt.pages))),
        "time-after.raw",
    )
    if (
        tuple(name for name, _ in receipt.raw_files) != names
        or set(raw_files) != set(names)
        or any(sha(raw_files[name]) != digest for name, digest in receipt.raw_files)
    ):
        raise PublicReceiptError("raw_file_inventory_mismatch")
    before = PublicRawReceiptV1.model_validate_json(canonical(receipt.time_before))
    after = PublicRawReceiptV1.model_validate_json(canonical(receipt.time_after))
    if before.endpoint != TIME_ENDPOINT or after.endpoint != TIME_ENDPOINT:
        raise PublicReceiptError("time_probe_endpoint_mismatch")
    first_server = verify_raw(plan, before, raw_files[names[0]])
    last_server = verify_raw(plan, after, raw_files[names[-1]])
    if last_server < first_server:
        raise PublicReceiptError("exchange_clock_reversed")
    validate_os_clock(receipt.os_clock_before)
    validate_os_clock(receipt.os_clock_after)
    if (
        receipt.os_clock_before["schema_version"]
        != receipt.os_clock_after["schema_version"]
    ):
        raise PublicReceiptError("clock_domain_changed_during_capture")
    if clock_timezone(receipt.os_clock_before) != clock_timezone(
        receipt.os_clock_after
    ):
        raise PublicReceiptError("timezone_changed_during_capture")
    stamps = [
        receipt.os_clock_before["sample"],
        before.request_start,
        before.headers_received,
        before.body_complete,
        before.validation_complete,
    ]
    native_origins = {before.transport_origin, after.transport_origin}
    expected_open = plan.end_ns - MINUTE_NS
    remaining, previous, cursor, locators = plan.expected_rows, None, plan.end_ns, []
    if len(receipt.pages) > plan.max_pages:
        raise PublicReceiptError("capture_page_budget")
    for index, data in enumerate(receipt.pages):
        page = PublicRawReceiptV1.model_validate_json(canonical(data))
        if (
            not remaining
            or page.endpoint != ENDPOINT
            or page.page_index != index
            or page.previous_page_sha256 != previous
            or page.query != request_query(plan, cursor, remaining)
        ):
            raise PublicReceiptError("page_chain_invalid")
        native_origins.add(page.transport_origin)
        rows = verify_raw(plan, page, raw_files[names[index + 1]])
        if len(rows) > min(plan.page_size, remaining):
            raise PublicReceiptError("page_rows_exceed_request")
        for ordinal, row in enumerate(rows):
            if int(row[0]) * 1_000_000 != expected_open:
                raise PublicReceiptError("minute_gap_duplicate_or_reordered")
            locators.append(
                {
                    "identity": row_identity(plan, row),
                    "content_sha256": row_content_sha(plan, row),
                    "page_index": index,
                    "row_ordinal": ordinal,
                    "raw_sha256": page.body_sha256,
                }
            )
            expected_open -= MINUTE_NS
            remaining -= 1
        cursor = int(rows[-1][0]) * 1_000_000
        previous = page.canonical_sha256()
        stamps.extend(
            (
                page.request_start,
                page.headers_received,
                page.body_complete,
                page.validation_complete,
            )
        )
    if (
        remaining
        or expected_open != plan.start_ns - MINUTE_NS
        or tuple(locators) != receipt.rows
    ):
        raise PublicReceiptError("capture_coverage_or_locator_mismatch")
    stamps.extend(
        (
            after.request_start,
            after.headers_received,
            after.body_complete,
            after.validation_complete,
            receipt.os_clock_after["sample"],
            receipt.validation_complete,
        )
    )
    validate_stamps(stamps)
    if native_origins != {receipt.transport_origin}:
        raise PublicReceiptError("mixed_transport_provenance")
    if before.request_start["utc_ns"] < plan.created_ns:
        raise PublicReceiptError("plan_not_yet_created")
    return plan, receipt


async def _collect_packet(plan, expected_plan_sha256, *, _attempt=None):
    from app.public_market_source.public_attempt_journal import _OwnedAttempt

    if _attempt is not None and type(_attempt) is not _OwnedAttempt:
        raise PublicReceiptError("owned_attempt_required")
    plan = checked(plan, PublicMinuteCapturePlanV1)
    if (
        type(expected_plan_sha256) is not str
        or plan.canonical_sha256() != expected_plan_sha256
    ):
        raise PublicReceiptError("plan_pin_mismatch")
    # Check OS before creating any network client. A stopped service sends no GET.
    os_before = native_os_clock()
    client = _new_client()
    if type(client) is not httpx.AsyncClient:
        raise PublicReceiptError("public_client_not_isolated")
    cancelled = False
    try:
        _client_guard(client, first=True)
        async with asyncio.timeout(50):
            first, first_raw = await _fetch(
                client, plan, endpoint=TIME_ENDPOINT, attempt=_attempt
            )
            files, pages, locators = {"time-before.raw": first_raw}, [], []
            remaining, previous, cursor = plan.expected_rows, None, plan.end_ns
            while remaining:
                index = len(pages)
                if index >= plan.max_pages:
                    raise PublicReceiptError("capture_page_budget")
                page, raw = await _fetch(
                    client,
                    plan,
                    endpoint=ENDPOINT,
                    query=request_query(plan, cursor, remaining),
                    index=index,
                    previous=previous,
                    attempt=_attempt,
                )
                rows = verify_raw(plan, page, raw)
                if len(rows) > min(plan.page_size, remaining):
                    raise PublicReceiptError("page_rows_exceed_request")
                for ordinal, row in enumerate(rows):
                    expected = plan.start_ns + (remaining - 1) * MINUTE_NS
                    if int(row[0]) * 1_000_000 != expected:
                        raise PublicReceiptError("minute_gap_duplicate_or_reordered")
                    locators.append(
                        {
                            "identity": row_identity(plan, row),
                            "content_sha256": row_content_sha(plan, row),
                            "page_index": index,
                            "row_ordinal": ordinal,
                            "raw_sha256": page.body_sha256,
                        }
                    )
                    remaining -= 1
                files[f"page-{index:03d}.raw"] = raw
                pages.append(page.model_dump())
                previous, cursor = page.canonical_sha256(), int(rows[-1][0]) * 1_000_000
            last, last_raw = await _fetch(
                client, plan, endpoint=TIME_ENDPOINT, attempt=_attempt
            )
            files["time-after.raw"] = last_raw
            os_after = native_os_clock()
    except asyncio.CancelledError:
        cancelled = True
        raise
    finally:
        await _close(client, cancelled=cancelled)
    packet = MeasuredPublicMinuteReceiptV1(
        capture_id=uuid.uuid4().hex,
        plan=plan.model_dump(),
        plan_sha256=plan.canonical_sha256(),
        os_clock_before=os_before,
        os_clock_after=os_after,
        time_before=first.model_dump(),
        time_after=last.model_dump(),
        pages=tuple(pages),
        raw_files=tuple((name, sha(raw)) for name, raw in files.items()),
        rows=tuple(locators),
        validation_complete=native_stamp(),
        transport_origin=first.transport_origin,
    )
    replay_public_capture(packet, files)
    # Availability is sampled AFTER complete primary replay, not before parsing
    # or before TLS/client cleanup. Later independent replays preserve this time.
    complete = native_stamp()
    validate_stamps((os_before["sample"], packet.validation_complete, complete))
    packet = MeasuredPublicMinuteReceiptV1.model_validate(
        {**packet.model_dump(), "validation_complete": complete}
    )
    return packet, files


async def collect_and_publish_public_minutes(*, plan, expected_plan_sha256, journal):
    """No injected clock/client, automatic retry, credentials, OOS or orders."""
    from app.public_market_source.public_receipt_storage import (
        ControlledPublicReceiptJournal,
    )

    if type(journal) is not ControlledPublicReceiptJournal:
        raise PublicReceiptError("controlled_journal_required")
    from app.public_market_source.public_attempt_journal import failure_code

    selected = checked(plan, PublicMinuteCapturePlanV1)
    if (
        type(expected_plan_sha256) is not str
        or selected.canonical_sha256() != expected_plan_sha256
    ):
        raise PublicReceiptError("plan_pin_mismatch")
    try:
        with journal._begin_attempt(selected) as attempt:
            try:
                packet, files = await _collect_packet(
                    selected, expected_plan_sha256, _attempt=attempt
                )
                if packet.transport_origin != "owned_native_tls":
                    raise PublicReceiptError("native_public_capture_required")
            except (Exception, asyncio.CancelledError) as exc:
                disposition = (
                    "incomplete"
                    if any(item["result"] == "incomplete" for item in attempt.requests)
                    else "rejected"
                )
                attempt.seal(
                    disposition=disposition,
                    code=failure_code(exc),
                    stamp=native_stamp(),
                )
                raise
            else:
                attempt_sha = attempt.seal(
                    disposition="completed_collection",
                    code="none",
                    stamp=native_stamp(),
                    receipt_sha256=packet.canonical_sha256(),
                )
        packet = MeasuredPublicMinuteReceiptV1.model_validate(
            {**packet.model_dump(), "attempt_sha256": attempt_sha}
        )
        return journal._publish_owned(_OwnedPublicCapture(_ISSUER, packet, files))
    except (PublicReceiptError, asyncio.CancelledError):
        raise
    except Exception as exc:
        raise PublicReceiptError("public_capture_not_published") from exc
