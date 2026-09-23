"""Owned write-ahead public response bytes, including rejected/partial attempts.

This journal never supplies measured availability. Only a separate complete,
clock-admitted capture may reference a completed attempt. Missing final markers
are preserved and rejected on resume; no current timestamp repairs old state.
"""

from __future__ import annotations

import asyncio
import re
import uuid
from contextlib import contextmanager

import httpx

from app.public_market_source.public_market_receipts import (
    ENDPOINT,
    MAX_RAW,
    TIME_ENDPOINT,
    ClockStamp,
    PublicReceiptError,
    canonical,
    decode,
    sha,
    validate_stamps,
)

MAX_ATTEMPTS = 1024
MAX_CHUNKS = 256
_ISSUER = object()
_ERROR_CODES = {
    "none",
    "cancelled",
    "timeout",
    "source_or_policy_rejected",
    "filesystem_or_transport_error",
    "acquisition_error",
}


def _digest(value):
    return type(value) is str and re.fullmatch(r"[a-f0-9]{64}", value) is not None


def failure_code(exc):
    # Do not serialize exception text/args/headers, including custom subclasses.
    if isinstance(exc, asyncio.CancelledError):
        return "cancelled"
    if isinstance(exc, (TimeoutError, httpx.TimeoutException)):
        return "timeout"
    if type(exc) is PublicReceiptError:
        return "source_or_policy_rejected"
    if isinstance(exc, OSError):
        return "filesystem_or_transport_error"
    return "acquisition_error"


def _read(directory, name, limit=8 * MAX_RAW):
    raw = directory.read(name, limit)
    value = decode(raw, limit)
    if canonical(value) != raw:
        raise PublicReceiptError("attempt_record_noncanonical")
    return value, raw


def _stamp(value):
    return ClockStamp.model_validate(value).model_dump()


class _OwnedAttempt:
    __slots__ = (
        "active",
        "clock_started",
        "clocks",
        "directory",
        "head",
        "plan",
        "requests",
        "started",
        "summary",
        "version",
    )

    def __init__(self, issuer, directory, plan, started, version=1):
        if issuer is not _ISSUER:
            raise PublicReceiptError("owned_attempt_required")
        self.directory, self.plan, self.started = directory, plan, started
        self.requests, self.active, self.summary, self.head = [], None, None, None
        if type(version) is not int or version not in {1, 2}:
            raise PublicReceiptError("attempt_version_invalid")
        self.version, self.clocks = version, []
        self.clock_started = set()

    def claim_clock_stage(self, stage):
        self.check_clock_stage(stage)
        if stage in self.clock_started:
            raise PublicReceiptError("attempt_clock_already_started")
        self.clock_started.add(stage)

    def check_clock_stage(self, stage):
        if (
            self.version != 2
            or self.summary is not None
            or self.active is not None
            or type(stage) is not str
            or len(self.clocks) >= 2
            or stage != ("before", "after")[len(self.clocks)]
            or (stage == "before" and self.requests)
            or (stage == "after" and self.clocks[0]["outcome"] != "accepted")
        ):
            raise PublicReceiptError("attempt_clock_order_invalid")

    def record_clock(self, stage, owned):
        from app.public_market_source.public_clock import (
            MAX_CLOCK_PAYLOAD,
            _owned_clock_payload,
            replay_clock_observation,
        )

        self.check_clock_stage(stage)
        payload = _owned_clock_payload(owned, self, stage)
        observation = replay_clock_observation(payload)
        binding = {
            "schema_version": "ctcc.public.attempt_clock_binding.v1",
            "attempt_id": self.started["attempt_id"],
            "plan_sha256": self.plan.canonical_sha256(),
            "stage": stage,
            "outcome": observation["outcome"],
            "payload_sha256": sha(payload),
            "payload_bytes": len(payload),
        }
        self.directory.publish(f"clock-{stage}.json", payload)
        if self.directory.read(f"clock-{stage}.json", MAX_CLOCK_PAYLOAD) != payload:
            raise PublicReceiptError("attempt_clock_readback_failed")
        raw = canonical(binding)
        self.directory.publish(f"clock-{stage}-binding.json", raw)
        if self.directory.read(f"clock-{stage}-binding.json", MAX_RAW) != raw:
            raise PublicReceiptError("attempt_clock_readback_failed")
        self.clocks.append(binding)
        return observation

    def begin_request(self, *, endpoint, query, stamp):
        if (
            self.active is not None
            or self.summary is not None
            or len(self.requests) >= 32
        ):
            raise PublicReceiptError("attempt_request_inventory_invalid")
        index = len(self.requests)
        self.directory.mkdir(f"request-{index:03d}")
        request = {
            "index": index,
            "endpoint": endpoint,
            "query": query,
            "request_start": _stamp(stamp),
            "plan_sha256": self.plan.canonical_sha256(),
        }
        with self.directory.child(f"request-{index:03d}") as child:
            child.publish("request.json", canonical(request))
        self.requests.append(
            {
                "request": request,
                "headers": None,
                "chunks": [],
                "body_complete": None,
                "validation_complete": None,
                "cleanup": "not_returned",
                "result": "incomplete",
                "error_code": "not_returned",
                "observed_bytes": 0,
                "stored_bytes": 0,
                "truncated": False,
            }
        )
        self.active = index

    def headers(self, *, stamp, status, headers, tls, truncated=False):
        record = self.requests[self.active]
        value = {
            "headers_received": _stamp(stamp),
            "http_status": status,
            "safe_headers": headers,
            "tls": tls,
            "headers_truncated": truncated,
        }
        with self.directory.child(f"request-{self.active:03d}") as child:
            child.publish("headers.json", canonical(value))
        record["headers"] = value

    def chunk(self, payload, stamp):
        if type(payload) is not bytes:
            raise PublicReceiptError("attempt_chunk_type_invalid")
        record = self.requests[self.active]
        if not payload:
            return
        ordinal = len(record["chunks"])
        remaining = self.plan.max_response_bytes - record["stored_bytes"]
        if ordinal >= MAX_CHUNKS:
            record["observed_bytes"] += len(payload)
            record["truncated"] = True
            with self.directory.child(f"request-{self.active:03d}") as child:
                child.publish(
                    "limit.json",
                    canonical(
                        {
                            "reason": "chunk_limit",
                            "observed_chunk_bytes": len(payload),
                            "observed_chunk_sha256": sha(payload),
                            "received": _stamp(stamp),
                        }
                    ),
                )
            raise PublicReceiptError("attempt_chunk_limit")
        retained = payload[:remaining]
        chunk = {
            "ordinal": ordinal,
            "stored_bytes": len(retained),
            "observed_bytes": len(payload),
            "stored_sha256": sha(retained),
            "observed_sha256": sha(payload),
            "received": _stamp(stamp),
        }
        with self.directory.child(f"request-{self.active:03d}") as child:
            if retained:
                child.publish(f"chunk-{ordinal:03d}.raw", retained)
            child.publish(f"chunk-{ordinal:03d}.json", canonical(chunk))
        record["chunks"].append(chunk)
        record["observed_bytes"] += len(payload)
        record["stored_bytes"] += len(retained)
        if len(retained) != len(payload):
            record["truncated"] = True
            raise PublicReceiptError("public_body_limit")

    def body_complete(self, stamp):
        record = self.requests[self.active]
        if record["truncated"]:
            raise PublicReceiptError("truncated_body_cannot_complete")
        value = {
            "body_complete": _stamp(stamp),
            "observed_bytes": record["observed_bytes"],
            "stored_bytes": record["stored_bytes"],
        }
        with self.directory.child(f"request-{self.active:03d}") as child:
            child.publish("body-complete.json", canonical(value))
        record["body_complete"] = value

    def finish_request(
        self, *, accepted, validation_complete=None, error_code="none", cleanup="closed"
    ):
        if self.active is None:
            return
        record = self.requests[self.active]
        record["validation_complete"] = (
            None if validation_complete is None else _stamp(validation_complete)
        )
        record["cleanup"] = cleanup
        record["result"] = (
            "accepted"
            if accepted
            else ("rejected" if record["body_complete"] is not None else "incomplete")
        )
        record["error_code"] = error_code
        with self.directory.child(f"request-{self.active:03d}") as child:
            child.publish("result.json", canonical(record))
        self.active = None

    def seal(self, *, disposition, code, stamp, receipt_sha256=None):
        if self.active is not None or self.summary is not None:
            raise PublicReceiptError("attempt_not_sealable")
        if disposition == "completed_collection" and (
            not self.requests
            or any(item["result"] != "accepted" for item in self.requests)
            or self.version == 2
            and (
                len(self.clocks) != 2
                or any(item["outcome"] != "accepted" for item in self.clocks)
            )
        ):
            raise PublicReceiptError("attempt_incomplete")
        if stamp is None and (
            self.version != 2 or disposition == "completed_collection"
        ):
            raise PublicReceiptError("attempt_terminal_clock_required")
        value = {
            "schema_version": f"ctcc.public.attempt.v{self.version}",
            "attempt_id": self.started["attempt_id"],
            "plan_sha256": self.plan.canonical_sha256(),
            "started_sha256": sha(canonical(self.started)),
            "disposition": disposition,
            "error_code": code,
            "completed": None if stamp is None else _stamp(stamp),
            "request_count": len(self.requests),
            "request_sha256s": tuple(sha(canonical(item)) for item in self.requests),
            "intended_receipt_sha256": receipt_sha256,
            "execution_authority": False,
            "measured_availability_eligible": False,
        }
        if self.version == 2:
            value.update(
                clock_records=tuple(self.clocks),
                terminal_clock="unavailable" if stamp is None else "observed",
            )
        raw = canonical(value)
        self.directory.publish("summary.json", raw)
        if self.directory.read("summary.json", 8 * MAX_RAW) != raw:
            raise PublicReceiptError("attempt_readback_failed")
        self.summary = value
        return sha(raw)


def replay_attempt(directory, *, expected_plan=None):
    from app.public_market_source.public_market_receipts import (
        PublicMinuteCapturePlanV1,
    )

    plan_data, plan_raw = _read(directory, "plan.json")
    plan = PublicMinuteCapturePlanV1.model_validate(plan_data)
    if expected_plan is not None and plan.canonical_sha256() != expected_plan:
        raise PublicReceiptError("attempt_plan_mismatch")
    started, started_raw = _read(directory, "started.json")
    if (
        set(started) != {"schema_version", "attempt_id", "plan_sha256", "started"}
        or started["schema_version"]
        not in {
            "ctcc.public.attempt_start.v1",
            "ctcc.public.attempt_start.v2",
        }
        or started["plan_sha256"] != sha(plan_raw)
    ):
        raise PublicReceiptError("attempt_start_invalid")
    _stamp(started["started"])
    if (
        type(started["attempt_id"]) is not str
        or re.fullmatch(r"[a-f0-9]{32}", started["attempt_id"]) is None
    ):
        raise PublicReceiptError("attempt_start_invalid")
    summary, summary_raw = _read(directory, "summary.json")
    v2 = started["schema_version"] == "ctcc.public.attempt_start.v2"
    if (
        set(summary)
        != {
            "schema_version",
            "attempt_id",
            "plan_sha256",
            "started_sha256",
            "disposition",
            "error_code",
            "completed",
            "request_count",
            "request_sha256s",
            "intended_receipt_sha256",
            "execution_authority",
            "measured_availability_eligible",
        }
        | ({"clock_records", "terminal_clock"} if v2 else set())
        or summary["schema_version"]
        != ("ctcc.public.attempt.v2" if v2 else "ctcc.public.attempt.v1")
        or summary["attempt_id"] != started["attempt_id"]
        or summary["started_sha256"] != sha(started_raw)
        or summary["plan_sha256"] != plan.canonical_sha256()
        or summary["execution_authority"] is not False
        or summary["measured_availability_eligible"] is not False
    ):
        raise PublicReceiptError("attempt_summary_invalid")
    count = summary["request_count"]
    if (
        type(count) is not int
        or not 0 <= count <= 32
        or type(summary["request_sha256s"]) is not list
        or len(summary["request_sha256s"]) != count
    ):
        raise PublicReceiptError("attempt_request_inventory_invalid")
    if v2 and summary["completed"] is None:
        if (
            summary["terminal_clock"] != "unavailable"
            or summary["disposition"] == "completed_collection"
        ):
            raise PublicReceiptError("attempt_terminal_clock_required")
    else:
        _stamp(summary["completed"])
        if v2 and summary["terminal_clock"] != "observed":
            raise PublicReceiptError("attempt_terminal_clock_invalid")
    if (
        summary["error_code"] not in _ERROR_CODES
        or any(not _digest(value) for value in summary["request_sha256s"])
        or (
            summary["intended_receipt_sha256"] is not None
            and not _digest(summary["intended_receipt_sha256"])
        )
    ):
        raise PublicReceiptError("attempt_summary_invalid")
    clock_files, clock_bytes = (
        _replay_clocks(directory, started, summary) if v2 else (set(), 0)
    )
    if (
        set(directory.names())
        != {
            "plan.json",
            "started.json",
            "summary.json",
            *(f"request-{i:03d}" for i in range(count)),
        }
        | clock_files
    ):
        raise PublicReceiptError("attempt_file_inventory_invalid")
    requests, total = (
        [],
        len(plan_raw) + len(started_raw) + len(summary_raw) + clock_bytes,
    )
    for index in range(count):
        with directory.child(f"request-{index:03d}") as child:
            result, result_raw = _read(child, "result.json")
            if sha(result_raw) != summary["request_sha256s"][index] or set(result) != {
                "request",
                "headers",
                "chunks",
                "body_complete",
                "validation_complete",
                "cleanup",
                "result",
                "error_code",
                "observed_bytes",
                "stored_bytes",
                "truncated",
            }:
                raise PublicReceiptError("attempt_request_result_invalid")
            request, request_raw = _read(child, "request.json")
            if (
                request != result["request"]
                or set(request)
                != {"index", "endpoint", "query", "request_start", "plan_sha256"}
                or request["index"] != index
                or request["endpoint"] not in {ENDPOINT, TIME_ENDPOINT}
                or request["plan_sha256"] != plan.canonical_sha256()
            ):
                raise PublicReceiptError("attempt_request_identity_invalid")
            _stamp(request["request_start"])
            if (
                type(request["index"]) is not int
                or type(request["query"]) is not list
                or len(request["query"]) > 4
                or any(
                    type(pair) is not list
                    or len(pair) != 2
                    or any(type(value) is not str or len(value) > 128 for value in pair)
                    for pair in request["query"]
                )
            ):
                raise PublicReceiptError("attempt_request_identity_invalid")
            if (
                result["cleanup"] not in {"closed", "failed", "not_returned"}
                or result["error_code"] not in _ERROR_CODES
                or type(result["observed_bytes"]) is not int
                or type(result["stored_bytes"]) is not int
            ):
                raise PublicReceiptError("attempt_request_result_invalid")
            expected = {"request.json", "result.json"}
            if result["headers"] is not None:
                expected.add("headers.json")
                headers, headers_raw = _read(child, "headers.json")
                if headers != result["headers"]:
                    raise PublicReceiptError("attempt_headers_mismatch")
                if (
                    set(headers)
                    != {
                        "headers_received",
                        "http_status",
                        "safe_headers",
                        "tls",
                        "headers_truncated",
                    }
                    or type(headers["http_status"]) is not int
                    or not 100 <= headers["http_status"] <= 999
                    or type(headers["headers_truncated"]) is not bool
                    or type(headers["safe_headers"]) is not list
                    or len(headers["safe_headers"]) > 16
                    or any(
                        type(pair) is not list
                        or len(pair) != 2
                        or pair[0]
                        not in {
                            "content-type",
                            "content-length",
                            "content-encoding",
                            "date",
                        }
                        or type(pair[1]) is not str
                        or len(pair[1]) > 1024
                        for pair in headers["safe_headers"]
                    )
                ):
                    raise PublicReceiptError("attempt_headers_invalid")
                proof = headers["tls"]
                if (
                    type(proof) is not dict
                    or set(proof)
                    != {"classification", "peer_sha256", "hostname", "version"}
                    or proof["classification"]
                    not in {"synthetic_test", "unverified", "owned_native_tls"}
                    or proof["hostname"] != plan.origin.removeprefix("https://")
                ):
                    raise PublicReceiptError("attempt_tls_invalid")
                if proof["classification"] == "owned_native_tls":
                    if not _digest(proof["peer_sha256"]) or proof["version"] not in {
                        "TLSv1.2",
                        "TLSv1.3",
                    }:
                        raise PublicReceiptError("attempt_tls_invalid")
                elif proof["peer_sha256"] is not None or proof["version"] is not None:
                    raise PublicReceiptError("attempt_tls_invalid")
                _stamp(headers["headers_received"])
                total += len(headers_raw)
            chunks = result["chunks"]
            if type(chunks) is not list or len(chunks) > MAX_CHUNKS:
                raise PublicReceiptError("attempt_chunk_inventory_invalid")
            body, observed = bytearray(), 0
            for ordinal, chunk in enumerate(chunks):
                expected.add(f"chunk-{ordinal:03d}.json")
                value, metadata_raw = _read(child, f"chunk-{ordinal:03d}.json")
                if (
                    value != chunk
                    or set(chunk)
                    != {
                        "ordinal",
                        "stored_bytes",
                        "observed_bytes",
                        "stored_sha256",
                        "observed_sha256",
                        "received",
                    }
                    or chunk["ordinal"] != ordinal
                    or type(chunk["stored_bytes"]) is not int
                    or type(chunk["observed_bytes"]) is not int
                    or not 0 <= chunk["stored_bytes"] <= chunk["observed_bytes"]
                ):
                    raise PublicReceiptError("attempt_chunk_invalid")
                _stamp(chunk["received"])
                if (
                    type(chunk["ordinal"]) is not int
                    or not _digest(chunk["observed_sha256"])
                    or not _digest(chunk["stored_sha256"])
                ):
                    raise PublicReceiptError("attempt_chunk_invalid")
                raw = b""
                if chunk["stored_bytes"]:
                    name = f"chunk-{ordinal:03d}.raw"
                    expected.add(name)
                    raw = child.read(name, plan.max_response_bytes)
                if (
                    len(raw) != chunk["stored_bytes"]
                    or sha(raw) != chunk["stored_sha256"]
                    or (
                        chunk["stored_bytes"] == chunk["observed_bytes"]
                        and sha(raw) != chunk["observed_sha256"]
                    )
                ):
                    raise PublicReceiptError("attempt_chunk_identity_mismatch")
                body.extend(raw)
                observed += chunk["observed_bytes"]
                total += len(raw) + len(metadata_raw)
            truncated = len(body) != observed
            if "limit.json" in child.names():
                expected.add("limit.json")
                limit, limit_raw = _read(child, "limit.json")
                if (
                    len(chunks) != MAX_CHUNKS
                    or set(limit)
                    != {
                        "reason",
                        "observed_chunk_bytes",
                        "observed_chunk_sha256",
                        "received",
                    }
                    or limit["reason"] != "chunk_limit"
                    or type(limit["observed_chunk_bytes"]) is not int
                    or limit["observed_chunk_bytes"] <= 0
                ):
                    raise PublicReceiptError("attempt_limit_invalid")
                _stamp(limit["received"])
                if not _digest(limit["observed_chunk_sha256"]):
                    raise PublicReceiptError("attempt_limit_invalid")
                observed += limit["observed_chunk_bytes"]
                total += len(limit_raw)
                truncated = True
            if (
                len(body) > plan.max_response_bytes
                or result["stored_bytes"] != len(body)
                or result["observed_bytes"] != observed
                or type(result["truncated"]) is not bool
                or result["truncated"] != truncated
            ):
                raise PublicReceiptError("attempt_body_size_mismatch")
            if result["body_complete"] is not None:
                expected.add("body-complete.json")
                completed, completed_raw = _read(child, "body-complete.json")
                if (
                    set(completed)
                    != {"body_complete", "observed_bytes", "stored_bytes"}
                    or type(completed["stored_bytes"]) is not int
                    or type(completed["observed_bytes"]) is not int
                ):
                    raise PublicReceiptError("attempt_completion_mismatch")
                if (
                    completed != result["body_complete"]
                    or truncated
                    or completed["stored_bytes"] != len(body)
                    or completed["observed_bytes"] != observed
                ):
                    raise PublicReceiptError("attempt_completion_mismatch")
                _stamp(completed["body_complete"])
                total += len(completed_raw)
            if result["result"] == "accepted":
                if (
                    result["headers"] is None
                    or result["body_complete"] is None
                    or result["validation_complete"] is None
                    or result["cleanup"] != "closed"
                    or result["error_code"] != "none"
                    or truncated
                ):
                    raise PublicReceiptError("attempt_acceptance_invalid")
                _stamp(result["validation_complete"])
                if (
                    result["headers"]["http_status"] != 200
                    or result["headers"]["headers_truncated"]
                    or result["headers"]["tls"]["classification"] == "unverified"
                ):
                    raise PublicReceiptError("attempt_acceptance_invalid")
                headers_dict = dict(result["headers"]["safe_headers"])
                if (
                    len(headers_dict) != len(result["headers"]["safe_headers"])
                    or headers_dict.get("content-type", "")
                    .split(";", 1)[0]
                    .strip()
                    .lower()
                    != "application/json"
                    or headers_dict.get("content-encoding", "identity").lower()
                    != "identity"
                ):
                    raise PublicReceiptError("attempt_acceptance_invalid")
                validate_stamps(
                    (
                        request["request_start"],
                        result["headers"]["headers_received"],
                        *(chunk["received"] for chunk in chunks),
                        result["body_complete"]["body_complete"],
                        result["validation_complete"],
                    )
                )
            elif (
                result["result"] not in {"rejected", "incomplete"}
                or result["error_code"] == "none"
            ):
                raise PublicReceiptError("attempt_rejection_invalid")
            if (
                (result["result"] == "rejected" and result["body_complete"] is None)
                or (
                    result["result"] == "incomplete"
                    and result["body_complete"] is not None
                )
                or (chunks and result["headers"] is None)
            ):
                raise PublicReceiptError("attempt_rejection_invalid")
            if set(child.names()) != expected:
                raise PublicReceiptError("attempt_request_file_inventory_invalid")
            requests.append((result, bytes(body)))
            total += len(result_raw) + len(request_raw)
    disposition = summary["disposition"]
    if disposition == "completed_collection":
        if (
            not requests
            or summary["error_code"] != "none"
            or any(item[0]["result"] != "accepted" for item in requests)
        ):
            raise PublicReceiptError("attempt_incomplete")
        if not _digest(summary["intended_receipt_sha256"]):
            raise PublicReceiptError("attempt_receipt_pin_missing")
    elif (
        disposition not in {"rejected", "incomplete"}
        or summary["error_code"] == "none"
        or summary["intended_receipt_sha256"] is not None
    ):
        raise PublicReceiptError("attempt_rejection_invalid")
    return summary, tuple(requests), total, sha(summary_raw)


def _replay_clocks(directory, started, summary):
    from app.public_market_source.public_clock import (
        MAX_CLOCK_PAYLOAD,
        replay_clock_observation,
    )

    records = summary["clock_records"]
    if type(records) is not list or len(records) > 2:
        raise PublicReceiptError("attempt_clock_inventory_invalid")
    names, total = set(), 0
    for stage, expected in zip(("before", "after"), records):
        name, payload_name = f"clock-{stage}-binding.json", f"clock-{stage}.json"
        binding, raw = _read(directory, name, MAX_RAW)
        if (
            binding != expected
            or set(binding)
            != {
                "schema_version",
                "attempt_id",
                "plan_sha256",
                "stage",
                "outcome",
                "payload_sha256",
                "payload_bytes",
            }
            or binding["schema_version"] != "ctcc.public.attempt_clock_binding.v1"
            or binding["attempt_id"] != started["attempt_id"]
            or binding["plan_sha256"] != started["plan_sha256"]
            or binding["stage"] != stage
            or type(binding["payload_bytes"]) is not int
        ):
            raise PublicReceiptError("attempt_clock_binding_invalid")
        payload = directory.read(payload_name, MAX_CLOCK_PAYLOAD)
        observation = replay_clock_observation(payload)
        if (
            binding["payload_sha256"] != sha(payload)
            or binding["payload_bytes"] != len(payload)
            or binding["outcome"] != observation["outcome"]
            or stage == "after"
            and records[0]["outcome"] != "accepted"
        ):
            raise PublicReceiptError("attempt_clock_binding_invalid")
        names.update((name, payload_name))
        total += len(raw) + len(payload)
    if summary["disposition"] == "completed_collection" and (
        len(records) != 2 or any(item["outcome"] != "accepted" for item in records)
    ):
        raise PublicReceiptError("attempt_incomplete")
    return names, total


def replay_attempt_chain(directory, *, sequence, expected_head, genesis_sha256):
    if type(sequence) is not int or not 0 <= sequence <= MAX_ATTEMPTS:
        raise PublicReceiptError("attempt_sequence_invalid")
    expected = {f"attempt-{i:08d}" for i in range(1, sequence + 1)}
    if set(directory.names()) != expected:
        raise PublicReceiptError("attempt_checkpoint_inventory_mismatch")
    previous, result, total = genesis_sha256, {}, 0
    for index in range(1, sequence + 1):
        with directory.child(f"attempt-{index:08d}") as child:
            # Chain marker is deliberately excluded from attempt payload replay.
            entry, raw = _read(child, "chain.json")
            if (
                set(entry)
                != {"schema_version", "sequence", "previous_sha256", "summary_sha256"}
                or entry["schema_version"] != "ctcc.public.attempt_chain.v1"
                or entry["sequence"] != index
                or entry["previous_sha256"] != previous
            ):
                raise PublicReceiptError("attempt_chain_invalid")
            summary, requests, size, digest = replay_attempt(_WithoutChain(child))
            if digest != entry["summary_sha256"] or digest in result:
                raise PublicReceiptError("attempt_chain_summary_mismatch")
            result[digest] = (summary, requests)
            previous = sha(raw)
            total += size + len(raw)
    if previous != expected_head:
        raise PublicReceiptError("attempt_checkpoint_head_mismatch")
    return result, total


def bind_measured_attempt(receipt, raw_files, attempts):
    if receipt.attempt_sha256 not in attempts:
        raise PublicReceiptError("measured_attempt_missing")
    summary, requests = attempts[receipt.attempt_sha256]
    unbound = type(receipt).model_validate(
        {**receipt.model_dump(), "attempt_sha256": None}
    )
    if (
        summary["disposition"] != "completed_collection"
        or summary["plan_sha256"] != receipt.plan_sha256
        or summary["intended_receipt_sha256"] != unbound.canonical_sha256()
    ):
        raise PublicReceiptError("measured_attempt_binding_invalid")
    if summary["schema_version"] == "ctcc.public.attempt.v2":
        from app.public_market_source.public_clock import CLOCK_RESULT_VERSION

        observations = (receipt.os_clock_before, receipt.os_clock_after)
        if len(summary["clock_records"]) != 2 or any(
            binding["payload_sha256"]
            != sha(
                canonical(
                    {
                        "schema_version": CLOCK_RESULT_VERSION,
                        "outcome": "accepted",
                        "observation": observed,
                    }
                )
            )
            for binding, observed in zip(
                summary["clock_records"], observations, strict=True
            )
        ):
            raise PublicReceiptError("measured_attempt_clock_mismatch")
    pages = (receipt.time_before, *receipt.pages, receipt.time_after)
    if len(requests) != len(pages):
        raise PublicReceiptError("measured_attempt_request_count")
    for (result, body), page, (filename, _) in zip(
        requests, pages, receipt.raw_files, strict=True
    ):
        headers = result["headers"]
        if (
            body != raw_files[filename]
            or result["request"]["endpoint"] != page["endpoint"]
            or canonical(result["request"]["query"]) != canonical(page["query"])
            or result["request"]["request_start"] != page["request_start"]
            or headers["headers_received"] != page["headers_received"]
            or result["body_complete"]["body_complete"] != page["body_complete"]
            or result["validation_complete"] != page["validation_complete"]
            or headers["http_status"] != 200
            or headers["headers_truncated"]
            or canonical(headers["safe_headers"]) != canonical(page["response_headers"])
            or headers["tls"]
            != {
                "classification": "owned_native_tls",
                "peer_sha256": page["tls_peer_sha256"],
                "hostname": page["tls_hostname"],
                "version": page["tls_version"],
            }
        ):
            raise PublicReceiptError("measured_attempt_source_mismatch")


class _WithoutChain:
    def __init__(self, directory):
        self.directory = directory

    def names(self):
        return tuple(name for name in self.directory.names() if name != "chain.json")

    def read(self, name, maximum):
        return self.directory.read(name, maximum)

    def child(self, name):
        return self.directory.child(name)


@contextmanager
def owned_attempt(directory, plan, *, stamp, version=1):
    if type(version) is not int or version not in {1, 2}:
        raise PublicReceiptError("attempt_version_invalid")
    started = {
        "schema_version": f"ctcc.public.attempt_start.v{version}",
        "attempt_id": uuid.uuid4().hex,
        "plan_sha256": plan.canonical_sha256(),
        "started": _stamp(stamp),
    }
    directory.publish("plan.json", plan.canonical_bytes())
    directory.publish("started.json", canonical(started))
    yield _OwnedAttempt(_ISSUER, directory, plan, started, version)
