"""Bounded native barrier/clock/secret replay, no legacy owner bootstrap.

The finite journal scanner below is copied exactly from the existing source
helper, without executing/importing its obsolete initial source-hash bootstrap.
It keeps every V4 byte/schema/size/fragment/body/secret rule unchanged.
"""

from app.domain.source_primitives import canonical, decode, sha, validate_stamps
from app.public_market_source import public_clock
from app.public_market_source import public_runtime_custody_v4 as public_custody
from app.trade_qualification import account_current_source_v4 as account
from app.trade_qualification import public_source_runtime as runtime


class NativePostpublicationSupportError(ValueError):
    """Fixed codes only."""


def _deny(code):
    raise NativePostpublicationSupportError(code) from None


def _after(barrier, stamp):
    validate_stamps((barrier, stamp))
    if any(stamp[name] <= barrier[name] for name in ("utc_ns", "monotonic_ns")):
        _deny("postpublication_request_not_after_barrier")


def _clock_after(barrier, raw):
    result = public_clock.replay_clock_observation(raw)
    if result["outcome"] != "accepted":
        _deny("postpublication_clock_incomplete")
    observation = result["observation"]
    if observation["schema_version"] != "ctcc.windows_clock_observation.v2":
        _deny("postpublication_clock_incomplete")
    _after(barrier, observation["diagnostic"]["host_before"]["request_start"])


def _secret_public_journal(directory, events, tokens):
    """Scan an already replayed captured V4 journal without parsing fragments."""
    if (
        type(directory) is not public_custody.RuntimeBundleDirectoryV4
        or type(tokens) is not tuple
        or len(tokens) < 6
        or any(
            type(token) is not str or not token or not token.isascii()
            for token in tokens
        )
        or type(events) is not tuple
        or not 0 < len(events) <= public_custody.journal.MAX_EVENTS
    ):
        _deny("postpublication_public_secret_journal_incomplete")
    plan_raw = directory.read("plan.json", runtime.MAX_RAW)
    summary_raw = directory.read("summary.json", runtime.MAX_RAW)
    account.runtime._secret_checked(plan_raw, tokens)
    account.runtime._secret_checked(summary_raw, tokens)
    plan, summary = (
        decode(plan_raw, runtime.MAX_RAW),
        decode(summary_raw, runtime.MAX_RAW),
    )
    if (
        type(plan) is not dict
        or type(summary) is not dict
        or canonical(plan) != plan_raw
        or canonical(summary) != summary_raw
        or set(summary)
        != {
            "schema_version",
            "plan_sha256",
            "event_count",
            "head_sha256",
            "disposition",
            "completed",
            "packet_sha256",
            "execution_authority",
            "measured_availability_eligible",
            "original_source_verified",
            "account_complete",
        }
        or summary["schema_version"] != "ctcc.public.runtime_summary.v1"
        or any(
            summary[name] is not False
            for name in (
                "execution_authority",
                "measured_availability_eligible",
                "original_source_verified",
                "account_complete",
            )
        )
        or plan["schema_version"] != "ctcc.public.initial_runtime_plan.v4"
        or summary["disposition"] != "captured"
        or type(summary["event_count"]) is not int
        or summary["event_count"] != len(events)
    ):
        _deny("postpublication_public_secret_journal_incomplete")
    plan_pin = sha(plan_raw)
    previous = plan_pin
    total = len(plan_raw) + len(summary_raw)
    expected = {"plan.json", "summary.json"}
    requests = {}
    json_raw_kinds = {
        "clock_before",
        "clock_after",
        "ws_subscribe",
        "ws_ack",
        "ws_ticker",
        "packet",
    }
    for index, event in enumerate(events):
        if (
            type(event) is not dict
            or set(event)
            != {
                "schema_version",
                "index",
                "kind",
                "plan_sha256",
                "previous_sha256",
                "metadata",
                "raw_sha256",
                "raw_bytes",
            }
            or event["schema_version"] != "ctcc.public.runtime_event.v1"
            or type(event.get("index")) is not int
            or event["index"] != index
            or type(event.get("kind")) is not str
            or event["kind"] not in public_custody.journal._KINDS
            or event["kind"] in {"request_failed", "component_failed"}
            or event.get("plan_sha256") != plan_pin
            or event.get("previous_sha256") != previous
            or type(event.get("metadata")) is not dict
        ):
            _deny("postpublication_public_secret_journal_incomplete")
        name = f"event-{index:04d}.json"
        encoded = directory.read(name, runtime.MAX_RAW)
        if encoded != canonical(event):
            _deny("postpublication_public_secret_journal_incomplete")
        account.runtime._secret_checked(encoded, tokens)
        expected.add(name)
        total += len(encoded)
        raw = None
        if event["raw_bytes"] is not None:
            if (
                type(event["raw_bytes"]) is not int
                or not 0 <= event["raw_bytes"] <= runtime.MAX_RAW
            ):
                _deny("postpublication_public_secret_journal_incomplete")
            name = f"event-{index:04d}.raw"
            raw = directory.read(name, runtime.MAX_RAW)
            if len(raw) != event["raw_bytes"] or sha(raw) != event["raw_sha256"]:
                _deny("postpublication_public_secret_journal_incomplete")
            # Every retained fragment is scanned as bytes. A fragment is not
            # a complete JSON document, even when it happens to parse alone.
            account.runtime.collector._no_secrets(raw, tokens)
            expected.add(name)
            total += len(raw)
        elif event["raw_sha256"] is not None:
            _deny("postpublication_public_secret_journal_incomplete")
        if total > public_custody.journal.MAX_TOTAL:
            _deny("postpublication_public_secret_journal_incomplete")
        kind, meta = event["kind"], event["metadata"]
        if kind == "request":
            if (
                raw is not None
                or set(meta)
                != {
                    "id",
                    "role",
                    "method",
                    "origin",
                    "endpoint",
                    "query",
                    "started",
                    "request_headers",
                }
                or type(meta.get("id")) is not int
                or meta["id"] != len(requests)
                or len(requests) >= 64
            ):
                _deny("postpublication_public_secret_journal_incomplete")
            requests[meta["id"]] = {
                "body": bytearray(),
                "chunks": 0,
                "headers": False,
                "content_length": None,
                "complete": False,
                "closed": False,
            }
        elif kind in {"headers", "chunk", "body_complete", "response_closed"}:
            if type(meta.get("id")) is not int or meta["id"] not in requests:
                _deny("postpublication_public_secret_journal_incomplete")
            request = requests[meta["id"]]
            if request["closed"]:
                _deny("postpublication_public_secret_journal_incomplete")
            if kind == "headers":
                if (
                    request["headers"]
                    or set(meta)
                    != {"id", "received", "status", "tls", "safe_headers", "truncated"}
                    or raw is not None
                    or type(meta.get("status")) is not int
                    or meta["status"] != 200
                    or meta.get("truncated") is not False
                ):
                    _deny("postpublication_public_secret_journal_incomplete")
                headers = meta["safe_headers"]
                if (
                    type(headers) is not list
                    or len(headers) > 16
                    or any(
                        type(pair) is not list
                        or len(pair) != 2
                        or type(pair[0]) is not str
                        or pair[0]
                        not in {
                            "content-type",
                            "content-length",
                            "content-encoding",
                            "date",
                        }
                        or type(pair[1]) is not str
                        or len(pair[1]) > 1024
                        for pair in headers
                    )
                    or len(dict(headers)) != len(headers)
                ):
                    _deny("postpublication_public_secret_journal_incomplete")
                fields = dict(headers)
                length = fields.get("content-length")
                if length is not None:
                    if not length.isascii() or not length.isdigit() or len(length) > 10:
                        _deny("postpublication_public_secret_journal_incomplete")
                    request["content_length"] = int(length)
                request["headers"] = True
            elif kind == "chunk":
                if (
                    not request["headers"]
                    or set(meta)
                    != {
                        "id",
                        "received",
                        "observed_bytes",
                        "observed_sha256",
                        "truncated",
                    }
                    or request["complete"]
                    or raw is None
                    or type(meta.get("observed_bytes")) is not int
                    or meta["observed_bytes"] != len(raw)
                    or meta.get("observed_sha256") != sha(raw)
                    or meta.get("truncated") is not False
                    or len(request["body"]) + len(raw) > runtime.MAX_RAW
                ):
                    _deny("postpublication_public_secret_journal_incomplete")
                request["body"].extend(raw)
                request["chunks"] += 1
            elif kind == "body_complete":
                if (
                    not request["headers"]
                    or set(meta) != {"id", "completed", "body_bytes", "body_sha256"}
                    or request["complete"]
                    or not request["chunks"]
                    or raw is not None
                    or type(meta.get("body_bytes")) is not int
                    or meta["body_bytes"] != len(request["body"])
                    or meta.get("body_sha256") != sha(bytes(request["body"]))
                    or (
                        request["content_length"] is not None
                        and request["content_length"] != len(request["body"])
                    )
                ):
                    _deny("postpublication_public_secret_journal_incomplete")
                # The original event order within this exact request ID is the
                # only assembly order. Both byte and decoded-JSON union scans
                # run on the complete body; no sorting, repair or substitution.
                account.runtime._secret_checked(bytes(request["body"]), tokens)
                request["complete"] = True
            else:
                if (
                    not request["complete"]
                    or set(meta) != {"id", "completed"}
                    or raw is not None
                ):
                    _deny("postpublication_public_secret_journal_incomplete")
                request["closed"] = True
        elif kind in json_raw_kinds:
            if raw is None:
                _deny("postpublication_public_secret_journal_incomplete")
            account.runtime._secret_checked(raw, tokens)
        elif raw is not None:
            _deny("postpublication_public_secret_journal_incomplete")
        previous = sha(encoded)
    names = directory.names()
    if (
        type(names) is not tuple
        or len(names) != len(expected)
        or len(names) != len(set(names))
        or set(names) != expected
        or summary["plan_sha256"] != plan_pin
        or summary["head_sha256"] != previous
        or events[-1]["kind"] != "packet"
        or summary["packet_sha256"] != events[-1]["raw_sha256"]
        or not requests
        or any(not request["closed"] for request in requests.values())
    ):
        _deny("postpublication_public_secret_journal_incomplete")
