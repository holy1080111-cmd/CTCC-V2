"""Persistent Notion cooldown and one reporting-pass lease; no trade authority."""

from __future__ import annotations

import hashlib
import json
import math
from contextlib import contextmanager
from datetime import datetime, timedelta

from app.trade_evidence import outbox, storage

DIRECTORY = ".notion-runtime"


@contextmanager
def reporting_lease(root):
    with outbox._root_context(root) as directory:
        if DIRECTORY not in directory.names():
            directory.mkdir(DIRECTORY)
    # Separate directory lock serializes cooperating runtime processes across
    # awaited HTTP, while the report queue remains usable by durable publishers.
    with outbox._root_context(storage._root_path(root / DIRECTORY)) as directory:
        yield directory


def _wire(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _history(directory):
    names = sorted(directory.names())
    if len(names) > 1024 or names != [
        f"{index:08d}.json" for index in range(1, len(names) + 1)
    ]:
        raise ValueError("notion_cooldown_history_invalid")
    rows, previous = [], "0" * 64
    for name in names:
        raw = directory.read(name, 2048)
        row = json.loads(raw, object_pairs_hook=storage._unique_object)
        if type(row) is not dict or set(row) != {
            "schema",
            "observed_at",
            "retry_after_seconds",
            "previous_sha256",
            "sha256",
        }:
            raise ValueError("notion_cooldown_record_invalid")
        body = {key: value for key, value in row.items() if key != "sha256"}
        if (
            _wire(row) != raw
            or row["schema"] != "ctcc.notion_cooldown.v1"
            or row["previous_sha256"] != previous
            or hashlib.sha256(_wire(body)).hexdigest() != row["sha256"]
            or (
                row["retry_after_seconds"] is not None
                and (
                    type(row["retry_after_seconds"]) is not int
                    or not 1 <= row["retry_after_seconds"] <= 99999
                )
            )
        ):
            raise ValueError("notion_cooldown_record_invalid")
        at = outbox._utc(datetime.fromisoformat(row["observed_at"]))
        if rows and at < rows[-1][1]:
            raise ValueError("notion_cooldown_clock_invalid")
        rows.append((row, at))
        previous = row["sha256"]
    return rows


def retry_delay(directory, now):
    """0 permits IO; positive seconds wait; None requires explicit reconciliation."""
    now = outbox._utc(now)
    delay = 0
    for row, at in _history(directory):
        if now < at:
            raise ValueError("notion_cooldown_clock_invalid")
        seconds = row["retry_after_seconds"]
        if seconds is None:
            return None
        delay = max(
            delay, math.ceil((at + timedelta(seconds=seconds) - now).total_seconds())
        )
    return delay


def record_rate_limit(directory, now, seconds):
    history = _history(directory)
    if len(history) >= 1024:
        raise ValueError("notion_cooldown_capacity")
    row = {
        "schema": "ctcc.notion_cooldown.v1",
        "observed_at": outbox._utc(now).isoformat(),
        "retry_after_seconds": seconds,
        "previous_sha256": history[-1][0]["sha256"] if history else "0" * 64,
    }
    row["sha256"] = hashlib.sha256(_wire(row)).hexdigest()
    name, raw = f"{len(history) + 1:08d}.json", _wire(row)
    directory.publish(name, raw)
    if directory.read(name, 2048) != raw:
        raise ValueError("notion_cooldown_readback_failed")
    _history(directory)
