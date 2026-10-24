"""Private historical acquisition phases; no portable record issues an observer.

The B1 observer keeps its original inventory. Positions have a separate trace
sharing the original B1 native anchor, task and clock. No clock is set here.
"""

import asyncio
import hashlib
import json
from contextlib import contextmanager
from weakref import WeakKeyDictionary

import httpx

from app.domain.source_primitives import canonical, sha, utc_from_ns, validate_stamps
from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_collector as collector
from app.trade_qualification import account_native_clock as native

_OBSERVERS = WeakKeyDictionary()
MAX_POSITION_PAGES = 18
MAX_POSITION_RAW_BYTES = 4 * 1024 * 1024 + 2 * 65536
MAX_POSITION_EVENTS = 4096


class HistoryNativeStageError(ValueError):
    """Fixed codes only; bodies and private exceptions never escape."""


class _PositionObserver:
    __slots__ = ("__weakref__",)

    def __new__(cls):
        raise HistoryNativeStageError("history_observer_unavailable")

    def __copy__(self):
        raise HistoryNativeStageError("history_observer_unavailable")

    def __deepcopy__(self, memo):
        raise HistoryNativeStageError("history_observer_unavailable")

    def __reduce_ex__(self, protocol):
        raise HistoryNativeStageError("history_observer_unavailable")


def _state(observer, *, cleanup=False):
    value = _OBSERVERS.get(observer) if type(observer) is _PositionObserver else None
    if value is None:
        raise HistoryNativeStageError("history_observer_unavailable")
    parent = native._state(value["stage"], _durable_cleanup=cleanup)
    if parent["clock"] is not value["clock"] or not parent["closed"]:
        raise HistoryNativeStageError("history_observer_binding_changed")
    return value


@contextmanager
def _positions_observer(stage, *, directory, tokens):
    parent = native._state(stage)
    if not parent["closed"] or parent["clock_results"] != {"before": "accepted"}:
        raise HistoryNativeStageError("history_b1_not_closed")
    observer = object.__new__(_PositionObserver)
    _OBSERVERS[observer] = {
        "stage": stage,
        "clock": parent["clock"],
        "directory": directory,
        # The closed B1 owner freezes its tokens as a tuple. Copy that full
        # set once, then keep this one list through every later signature.
        "tokens": list(tokens),
        "events": [],
        "pages": [],
        "current": None,
        "completed": False,
        "total_bytes": 0,
    }
    try:
        yield observer
    finally:
        _OBSERVERS.pop(observer, None)


def _clock(observer):
    return _state(observer)["clock"]


def _known_tokens(observer):
    return _state(observer)["tokens"]


def _transport(observer, client):
    _state(observer)
    collector._client_guard(client)
    # The old diagnostic collector permits MockTransport. This path does not.
    if type(client._transport) is not httpx.AsyncHTTPTransport:
        raise HistoryNativeStageError("history_native_tls_required")


def _event(observer, phase, **fields):
    value = _state(observer)
    stamp = native._sample(value["stage"])
    events = value["events"]
    if len(events) >= MAX_POSITION_EVENTS:
        raise HistoryNativeStageError("history_phase_limit")
    record = {
        "index": len(events),
        "phase": phase,
        "request_index": len(value["pages"]),
        "stamp": stamp,
        "previous_sha256": None if not events else sha(canonical(events[-1])),
        **fields,
    }
    events.append(record)
    raw = canonical(record)
    name = f"position-phase-{len(events) - 1:05d}.json"
    value["directory"].publish(name, raw)
    if value["directory"].read(name, 4096) != raw:
        raise HistoryNativeStageError("history_phase_readback_changed")
    return utc_from_ns(stamp["utc_ns"])


def _begin(observer, spec, maximum, tokens):
    value = _state(observer)
    if (
        value["current"] is not None
        or value["completed"]
        or len(value["pages"]) >= MAX_POSITION_PAGES
    ):
        raise HistoryNativeStageError("history_request_inventory_invalid")
    if tokens is not value["tokens"]:
        raise HistoryNativeStageError("history_secret_inventory_changed")
    value["current"] = {
        "body": bytearray(),
        "maximum": maximum,
        "phase": "start",
        "hasher": hashlib.sha256(),
        "empty_chunks": 0,
    }
    return _event(
        observer,
        "request_start",
        request_sha256=sha(canonical([spec.origin, spec.endpoint, spec.parameters])),
    )


def _phase(observer, phase, *, certificate=None):
    value = _state(observer)
    current = value["current"]
    expected = {
        "request_dispatch": {"start"},
        "headers_received": {"request_dispatch"},
        "tls_verified": {"headers_received"},
        "body_exhausted": {"tls_verified", "body_chunk"},
        "response_closed": {"body_exhausted"},
        "validated": {"response_closed"},
    }
    if (
        current is None
        or phase not in expected
        or current["phase"] not in expected[phase]
    ):
        raise HistoryNativeStageError("history_phase_inventory_invalid")
    if phase == "tls_verified":
        if type(certificate) is not str or len(certificate) != 64:
            raise HistoryNativeStageError("history_native_tls_required")
        current["certificate_sha256"] = certificate
    current["phase"] = phase
    return _event(observer, phase, certificate_sha256=certificate)


def _chunk(observer, chunk):
    value = _state(observer)
    current = value["current"]
    if (
        current is None
        or current["phase"] not in {"tls_verified", "body_chunk"}
        or type(chunk) is not bytes
        or len(current["body"]) + len(chunk) > current["maximum"]
        or value["total_bytes"] + len(chunk) > MAX_POSITION_RAW_BYTES
    ):
        raise HistoryNativeStageError("history_body_limit")
    if not chunk:
        # No unbounded empty-chunk trace. Still measure its actual time, and
        # reject the ninth empty chunk independently of elapsed wall time.
        native._sample(value["stage"])
        current["empty_chunks"] += 1
        if current["empty_chunks"] > 8:
            raise HistoryNativeStageError("history_empty_chunk_limit")
        return
    current["body"].extend(chunk)
    current["hasher"].update(chunk)
    value["total_bytes"] += len(chunk)
    current["phase"] = "body_chunk"
    _event(
        observer,
        "body_chunk",
        observed_bytes=len(current["body"]),
        prefix_sha256=current["hasher"].hexdigest(),
    )


def _response_closed(observer):
    """Called only after the fixed collector finally actually closed response."""
    value = _state(observer, cleanup=True)
    current = value["current"]
    if (
        current is not None
        and current["phase"] == "body_exhausted"
        and not asyncio.current_task().cancelling()
    ):
        return _phase(observer, "response_closed")
    # Cancellation/early body failures cannot use success _phase/_sample. This
    # append is solely failed cleanup evidence, never accepted by pure replay.
    parent = native._state(value["stage"], _durable_cleanup=True)
    stamp = None
    try:
        actual = native.clock.native_stamp()
        validate_stamps((parent["started"], parent["last"], actual))
        stamp = actual
        parent["last"] = actual
    except Exception:  # noqa: BLE001 -- retain failure even if its clock fails
        stamp = None
    events = value["events"]
    if len(events) >= MAX_POSITION_EVENTS:
        # The fixed failure footer in _abort remains available, even when no
        # more primary phase records may be allocated.
        return
    record = {
        "index": len(events),
        "phase": "failed_response_closed",
        "request_index": len(value["pages"]),
        "stamp": stamp,
        "previous_sha256": None if not events else sha(canonical(events[-1])),
        "execution_authority": False,
    }
    events.append(record)
    raw = canonical(record)
    name = f"position-phase-{len(events) - 1:05d}.json"
    value["directory"].publish(name, raw)
    if value["directory"].read(name, 4096) != raw:
        raise HistoryNativeStageError("history_phase_readback_changed")


def _retain(observer, *, failed=False):
    """Buffer success until signatures stop; flush safe partials on failure."""
    value = _state(observer, cleanup=True)
    current = value["current"]
    if current is None:
        return
    complete = not failed and current["phase"] == "validated"
    value["pages"].append(
        {
            "body": bytes(current["body"]),
            "maximum": current["maximum"],
            "certificate_sha256": current.get("certificate_sha256"),
            "complete": complete,
        }
    )
    value["current"] = None
    if failed:
        _flush(value)


def _flush(value):
    for index, current in enumerate(value["pages"]):
        if "body" in current:
            value["pages"][index] = _publish_retained(value, index, current)


def _publish_retained(value, index, current):
    body = current["body"]
    retention = "not_received"
    if body:
        try:
            collector._no_secrets(body, value["tokens"])
            # Malformed partial JSON may hide an escaped credential. Withhold
            # it rather than publishing bytes whose decoded strings are unknown.
            _, decoded = capture._decode_json(body, limit=current["maximum"], wire=True)
            collector._no_secret_json(decoded, value["tokens"])
            retention = "durable_secret_checked"
        except collector.AccountCollectionError:
            retention = "withheld_secret"
        except capture.AccountCaptureError as exc:
            retention = (
                "withheld_secret"
                if type(exc) is capture.AccountCaptureError
                and exc.args == ("secret_field_forbidden",)
                else "withheld_unverifiable_partial"
            )
        except (ValueError, UnicodeError, RecursionError):
            retention = "withheld_unverifiable_partial"
    marker = {
        "schema_version": "ctcc.history_positions_retention.v1",
        "request_index": index,
        "observed_bytes": len(body),
        "raw_sha256": sha(body),
        "retention": retention,
        "complete": current["complete"] and retention == "durable_secret_checked",
        "certificate_sha256": current.get("certificate_sha256"),
        "execution_authority": False,
    }
    name = f"positions-{index:02d}"
    if retention == "durable_secret_checked":
        value["directory"].publish(name + ".raw", body)
        if value["directory"].read(name + ".raw", current["maximum"]) != body:
            raise HistoryNativeStageError("history_partial_readback_changed")
    raw = canonical(marker)
    value["directory"].publish(name + ".json", raw)
    if value["directory"].read(name + ".json", 4096) != raw:
        raise HistoryNativeStageError("history_partial_readback_changed")
    return marker


def _finished(observer):
    value = _state(observer)
    _flush(value)
    if (
        value["current"] is not None
        or not value["pages"]
        or not all(page["complete"] is True for page in value["pages"])
    ):
        raise HistoryNativeStageError("history_positions_incomplete")
    _event(observer, "positions_closed")
    value["completed"] = True
    return json.loads(canonical({"events": value["events"], "pages": value["pages"]}))


def _abort(observer):
    """Fixed collector cleanup; cannot mark unfinished bytes as successful."""
    value = _state(observer, cleanup=True)
    if value["current"] is not None:
        _retain(observer, failed=True)
    _flush(value)
    if not value["completed"]:
        raw = canonical(
            {
                "schema_version": "ctcc.history_positions_failure.v1",
                "phase_count": len(value["events"]),
                "observed_bytes": value["total_bytes"],
                "complete": False,
                "execution_authority": False,
            }
        )
        value["directory"].publish("positions-failure.json", raw)
        if value["directory"].read("positions-failure.json", 4096) != raw:
            raise HistoryNativeStageError("history_failure_readback_changed")
