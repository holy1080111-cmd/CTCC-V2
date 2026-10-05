"""Private context mechanism fixtures; never durable/native account acceptance.

Tests deliberately populate otherwise unissued private registries. This tests
the consumption fence only, not a proof issuer. Production has no issuer API.
"""

import asyncio
import copy
import os
import pickle
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from threading import get_ident

import pytest

from app.domain.source_primitives import canonical
from app.trade_qualification import account_clock_boundary as boundary
from app.trade_qualification import account_portfolio_components as components
from app.trade_qualification import account_portfolio_runtime as runtime

ISSUED_AT = datetime(2026, 1, 1, tzinfo=UTC)
ISSUED_NS = 1_767_225_600_000_000_000
MONOTONIC_NS = 1_000_000_000


def private_context_fixture(monkeypatch, *, current_delta_ns=0):
    """An explicit test-only registry insertion; no source authenticity claim."""
    invocation = object()
    handle = object.__new__(boundary._AccountClockBoundary)
    owner = object.__new__(runtime.OwnedAccountComponents)
    handoff = runtime.ComponentHandoff(None, None, b"synthetic context fence only")
    receipt_pin = runtime.journal.digest(handoff.receipt_json)
    record = boundary._NativeClockRegistration(
        issuer=boundary._NATIVE_ISSUER,
        invocation=invocation,
        parent_task=asyncio.current_task(),
        loop=asyncio.get_running_loop(),
        pid=os.getpid(),
        thread=get_ident(),
        receipt_sha256=receipt_pin,
        proof_schema=boundary.PROOF_SCHEMA,
        durable_clock_proof_sha256=runtime.journal.digest(b"synthetic proof mechanism"),
        native_issue_stamp_json=canonical(
            {"utc_ns": ISSUED_NS, "monotonic_ns": MONOTONIC_NS}
        ),
        expires_at=ISSUED_AT + timedelta(seconds=30),
        monotonic_deadline_ns=MONOTONIC_NS + 30_000_000_000,
    )
    boundary._BOUNDARIES[handle] = record
    runtime._OWNERS[owner] = runtime._RegisteredComponents(handoff, handle)
    samples = []

    def sampled():
        samples.append(True)
        return {
            "utc_ns": ISSUED_NS + current_delta_ns,
            "monotonic_ns": MONOTONIC_NS + current_delta_ns,
        }

    monkeypatch.setattr(boundary, "native_stamp", sampled)
    return owner, handle, invocation, receipt_pin, samples


def consume(owner, invocation, pin):
    return runtime._consume_owned_components(
        owner, expected_receipt_sha256=pin, _invocation=invocation
    )


def assert_burned(owner, handle, invocation, pin):
    assert owner not in runtime._OWNERS
    assert handle not in boundary._BOUNDARIES
    with pytest.raises(components.PortfolioComponentError, match="missing_or_consumed"):
        consume(owner, invocation, pin)


@pytest.mark.asyncio
@pytest.mark.parametrize("delta_ns", [-1, 0, 29_000_000_000, 30_000_000_000])
async def test_private_mechanism_requires_independent_time_and_is_one_use(
    monkeypatch, delta_ns
):
    owner, handle, invocation, pin, samples = private_context_fixture(
        monkeypatch, current_delta_ns=delta_ns
    )
    if 0 <= delta_ns < 30_000_000_000:
        handoff = consume(owner, invocation, pin)
        assert handoff.receipt_json == b"synthetic context fence only"
    else:
        with pytest.raises(components.PortfolioComponentError, match="native_clock"):
            consume(owner, invocation, pin)
    assert len(samples) == 1
    assert_burned(owner, handle, invocation, pin)


@pytest.mark.asyncio
@pytest.mark.parametrize("elapsed_ns", [30_000_000_000, 30_000_000_001])
async def test_frozen_utc_cannot_hide_actual_monotonic_expiry(monkeypatch, elapsed_ns):
    owner, handle, invocation, pin, _ = private_context_fixture(monkeypatch)
    monkeypatch.setattr(
        boundary,
        "native_stamp",
        lambda: {"utc_ns": ISSUED_NS, "monotonic_ns": MONOTONIC_NS + elapsed_ns},
    )
    with pytest.raises(components.PortfolioComponentError, match="native_clock"):
        consume(owner, invocation, pin)
    assert_burned(owner, handle, invocation, pin)


@pytest.mark.asyncio
async def test_shortened_monotonic_deadline_denies_before_later_utc_expiry(monkeypatch):
    owner, handle, invocation, pin, samples = private_context_fixture(
        monkeypatch, current_delta_ns=3_000_000_000
    )
    boundary._BOUNDARIES[handle] = replace(
        boundary._BOUNDARIES[handle],
        monotonic_deadline_ns=MONOTONIC_NS + 2_000_000_000,
    )
    with pytest.raises(components.PortfolioComponentError, match="native_clock"):
        consume(owner, invocation, pin)
    assert len(samples) == 1
    assert_burned(owner, handle, invocation, pin)


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["invocation", "pid", "thread", "loop"])
async def test_exact_context_mismatch_denied_before_clock_sample(monkeypatch, field):
    owner, handle, invocation, pin, samples = private_context_fixture(monkeypatch)
    record = boundary._BOUNDARIES[handle]
    wrong_loop = None
    if field == "invocation":
        replacement = object()
    elif field == "pid":
        replacement = record.pid + 1
    elif field == "thread":
        replacement = record.thread + 1
    else:
        wrong_loop = asyncio.new_event_loop()
        replacement = wrong_loop
    boundary._BOUNDARIES[handle] = replace(record, **{field: replacement})
    try:
        with pytest.raises(components.PortfolioComponentError, match="native_clock"):
            consume(owner, invocation, pin)
        assert not samples
        assert_burned(owner, handle, invocation, pin)
    finally:
        if wrong_loop is not None:
            wrong_loop.close()


@pytest.mark.asyncio
async def test_original_token_borrowed_by_actual_other_task_is_denied(monkeypatch):
    owner, handle, invocation, pin, samples = private_context_fixture(monkeypatch)

    async def borrowed():
        with pytest.raises(components.PortfolioComponentError, match="native_clock"):
            consume(owner, invocation, pin)

    await asyncio.create_task(borrowed())
    assert not samples
    assert_burned(owner, handle, invocation, pin)


@pytest.mark.asyncio
async def test_actual_other_thread_and_event_loop_cannot_borrow_original_token(
    monkeypatch,
):
    owner, handle, invocation, pin, samples = private_context_fixture(monkeypatch)

    async def borrowed():
        with pytest.raises(components.PortfolioComponentError, match="native_clock"):
            consume(owner, invocation, pin)

    await asyncio.to_thread(lambda: asyncio.run(borrowed()))
    assert not samples
    assert_burned(owner, handle, invocation, pin)


@pytest.mark.asyncio
async def test_canceling_original_parent_cannot_consume(monkeypatch):
    async def canceled_parent():
        owner, handle, invocation, pin, samples = private_context_fixture(monkeypatch)
        task = asyncio.current_task()
        task.cancel()
        try:
            with pytest.raises(
                components.PortfolioComponentError, match="native_clock"
            ):
                consume(owner, invocation, pin)
            assert not samples
            assert_burned(owner, handle, invocation, pin)
        finally:
            try:
                await asyncio.sleep(0)
            except asyncio.CancelledError:
                task.uncancel()

    await asyncio.create_task(canceled_parent())


@pytest.mark.asyncio
@pytest.mark.parametrize("wrong_pin", ["0" * 64, None])
async def test_wrong_digest_burns_owner_and_boundary_without_sampling(
    monkeypatch, wrong_pin
):
    owner, handle, invocation, pin, samples = private_context_fixture(monkeypatch)
    with pytest.raises(components.PortfolioComponentError, match="missing_or_consumed"):
        consume(owner, invocation, wrong_pin)
    assert not samples
    assert_burned(owner, handle, invocation, pin)


@pytest.mark.asyncio
async def test_foreign_pin_callback_never_runs_and_both_capabilities_are_burned(
    monkeypatch,
):
    owner, handle, invocation, pin, samples = private_context_fixture(monkeypatch)
    comparisons = []

    class ForeignPin:
        def __eq__(self, other):
            comparisons.append("eq")
            return True

        def __ne__(self, other):
            comparisons.append("ne")
            return False

    with pytest.raises(components.PortfolioComponentError, match="missing_or_consumed"):
        consume(owner, invocation, ForeignPin())
    assert not comparisons and not samples
    assert_burned(owner, handle, invocation, pin)


@pytest.mark.asyncio
async def test_native_sampler_failure_denies_and_does_not_expose_os_details(
    monkeypatch,
):
    owner, handle, invocation, pin, _ = private_context_fixture(monkeypatch)

    def unavailable():
        raise OSError("private native OS diagnostic")

    monkeypatch.setattr(boundary, "native_stamp", unavailable)
    with pytest.raises(
        components.PortfolioComponentError, match="native_clock"
    ) as error:
        consume(owner, invocation, pin)
    assert "private native OS diagnostic" not in str(error.value)
    assert_burned(owner, handle, invocation, pin)


@pytest.mark.asyncio
@pytest.mark.parametrize("untrusted", [{"native": True}, lambda: ISSUED_AT, None])
async def test_caller_attestations_never_register_native_boundary(
    monkeypatch, untrusted
):
    samples = []
    monkeypatch.setattr(boundary, "native_stamp", lambda: samples.append(True))
    with pytest.raises(boundary.AccountClockBoundaryError, match="required"):
        boundary._consume_boundary(
            untrusted, invocation=object(), receipt_sha256="a" * 64
        )
    forged = object.__new__(boundary._AccountClockBoundary)
    with pytest.raises(boundary.AccountClockBoundaryError, match="issuer_unavailable"):
        boundary._consume_boundary(forged, invocation=object(), receipt_sha256="a" * 64)
    assert not samples


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["issuer", "proof_schema", "durable_pin"])
async def test_missing_trusted_issuer_or_proof_binding_is_denied(monkeypatch, change):
    owner, handle, invocation, pin, samples = private_context_fixture(monkeypatch)
    record = boundary._BOUNDARIES[handle]
    substitutions = {
        "issuer": {"issuer": object()},
        "proof_schema": {"proof_schema": "ctcc.legacy_tls_only.v1"},
        "durable_pin": {"durable_clock_proof_sha256": "caller says passed"},
    }
    boundary._BOUNDARIES[handle] = replace(record, **substitutions[change])
    with pytest.raises(components.PortfolioComponentError, match="native_clock"):
        consume(owner, invocation, pin)
    assert not samples
    assert_burned(owner, handle, invocation, pin)


def test_clock_boundary_and_component_owner_cannot_be_constructed_or_serialized():
    for kind, error in (
        (boundary._AccountClockBoundary, boundary.AccountClockBoundaryError),
        (runtime.OwnedAccountComponents, components.PortfolioComponentError),
    ):
        with pytest.raises(error, match="not_constructible"):
            kind()
        forged = object.__new__(kind)
        for operation in (copy.copy, copy.deepcopy, pickle.dumps):
            with pytest.raises(error, match="not_transferable"):
                operation(forged)
