"""Synthetic owners exercise actual R7 denial control flow and local storage."""

import asyncio
import os
from decimal import Decimal
from types import SimpleNamespace as Data

import pytest

from app.domain.source_primitives import canonical, decode, sha, utc_from_ns
from app.trade_evidence import storage
from app.trade_qualification import native_postpublication_recheck_v2 as subject
from app.trade_qualification.economics import EconomicsPolicy

_INVOCATION = "a" * 32
_REPORT = "synthetic-g12"
_ORIGIN = 1_767_225_600_000_000_000


def _clock(seconds):
    return {
        "utc_ns": _ORIGIN + seconds * 1_000_000_000,
        "monotonic_ns": (seconds + 1) * 1_000_000_000,
    }


def _at(seconds):
    return utc_from_ns(_clock(seconds)["utc_ns"])


def _ticker(bid, ask, second):
    return Data(
        bid=Decimal(bid),
        ask=Decimal(ask),
        request_started_at=_at(second),
        source_generated_at=_at(second),
        body_completed_at=_at(second + 1),
        raw_body_sha256=sha(canonical([bid, ask, second])),
    )


def _fixture(tmp_path, monkeypatch, *, direction="long"):
    native = subject.publication.custody.public_source_runtime
    root = tmp_path / "evidence"
    root.mkdir()
    original_files = root / _REPORT
    original_files.mkdir()
    for filename in storage.FILE_NAMES:
        (original_files / filename).write_bytes(filename.encode())
    geometry = Data(
        direction=direction,
        candidate_entry=Decimal(100),
        stop_loss=Decimal("95" if direction == "long" else "105"),
        take_profit=Decimal("115" if direction == "long" else "85"),
    )
    facts = Data(
        original=Data(
            result=geometry,
            candidate_sha256="a" * 64,
            original_event_key="synthetic-event",
            original_policy_sha256="b" * 64,
        ),
        snapshot=Data(
            quote=Data(ticker=_ticker("99.99", "100.01", 0)), prepared_at=_at(1)
        ),
        receipt=Data(completed_at=_at(2), report_sha256="c" * 64),
        publication=Data(publication_sha256="d" * 64),
        evaluation_sha256="e" * 64,
        claim=Data(
            invocation_id=_INVOCATION,
            values={
                "evidence_root_sha256": sha(
                    canonical([str(root), *native._native_recheck_root_identity(root)])
                )
            },
        ),
    )
    state = {
        "published": {"root": root, "facts": facts},
        "task": asyncio.current_task(),
        "barrier": _clock(3),
        "deadline": _at(15),
        "public_state": {"bundle_sha256": "f" * 64},
        "account_binding": object(),
    }
    scanned = []

    def scan(binding, raw, *, key):
        assert binding is state["account_binding"]
        assert key is subject._ACCOUNT_KEY_V2
        scanned.append(decode(raw))

    # Source/owner authentication is tested by its own suite. These synthetic
    # seams cannot create any production source, account, or execution owner.
    monkeypatch.setattr(
        subject._native_guard, "check_saved_native_guard_v1", lambda _state: None
    )
    monkeypatch.setattr(
        subject.publication, "_require_actual_publication", lambda _published: None
    )
    monkeypatch.setattr(subject.account, "scan_issued_metadata_v1", scan)
    monkeypatch.setattr(subject, "native_stamp", lambda: _clock(7))
    packet = Data(bundle_sha256="1" * 64)
    g1 = Data(
        passed=False,
        gate=Data(code="stale_market_data"),
        evaluation_sha256="2" * 64,
        policy_sha256="3" * 64,
    )
    issued = Data(issuer_sha256="4" * 64, issued_row_sha256="5" * 64)
    return state, facts, packet, g1, issued, scanned


def _read_decision(root):
    directory = root / f"r7-denial-{_INVOCATION}"
    raw = (directory / "decision.json").read_bytes()
    marker = directory / "readback.json"
    readback = decode(marker.read_bytes()) if marker.exists() else None
    return directory, raw, decode(raw), readback


@pytest.mark.parametrize("late_write_failure", [False, True])
@pytest.mark.asyncio
async def test_first_owned_stale_g1_denial_preserves_decision_and_original_error(
    monkeypatch, tmp_path, late_write_failure
):
    state, facts, packet, g1, issued, scanned = _fixture(tmp_path, monkeypatch)
    root = state["published"]["root"]
    # The actual coroutine and evaluation run; only upstream service I/O and
    # source replay are supplied as closed test operands.
    original = {
        "computed": Data(intent=Data(instrument_id="BTC-USDT-SWAP")),
        "prefix": {"frame": {"policy": object(), "repository": object()}},
    }
    issued.plan = Data(model_dump=lambda **_: {})
    issued.read_session, issued.private_client = object(), object()
    original_binding = object()
    state["published"]["candidate"] = {"account_binding": original_binding}
    state["roots"] = Data(
        public_root=tmp_path / "public",
        public_account_proof_root=tmp_path / "public-proof",
        account_roots=object(),
    )
    released = []

    async def acquire(*_args, **_kwargs):
        return object()

    async def acquire_account(binding, *, barrier, roots, key):
        assert binding is original_binding
        assert barrier is state["barrier"]
        assert roots is state["roots"].account_roots
        assert key is subject._ACCOUNT_KEY_V2
        return object()

    monkeypatch.setattr(
        subject, "_check", lambda _: (original, None, issued, _clock(6))
    )
    monkeypatch.setattr(subject, "_fresh_account", lambda _: issued)
    monkeypatch.setattr(
        subject, "_scan_original_with_fresh_terminal_tokens", lambda *_: None
    )
    monkeypatch.setattr(subject, "_public", lambda _: (packet, None))
    monkeypatch.setattr(subject, "_prefix_policy", lambda _: Data(data=object()))
    monkeypatch.setattr(subject, "_release", lambda actual: released.append(actual))
    monkeypatch.setattr(
        subject, "_register_postpublication_public_scan_v2", lambda _: None
    )
    monkeypatch.setattr(
        subject.data_v2, "evaluate_public_market_data_v2", lambda *_, **__: g1
    )
    monkeypatch.setattr(
        subject.data_v2, "verify_public_market_data_v2", lambda *_, **__: g1
    )
    monkeypatch.setattr(
        subject.capture,
        "CurrentDemoAccountCapturePlanV7",
        Data(model_validate=lambda x, **_: x),
    )
    monkeypatch.setattr(subject.capture, "plan_sha256", lambda _: "6" * 64)
    monkeypatch.setattr(
        subject,
        "ControlledDemoAccountSession",
        Data(from_pinned_read_session=lambda **_: object()),
    )
    monkeypatch.setattr(
        subject.market, "acquire_initial_demo_market_source_v4", acquire
    )
    monkeypatch.setattr(
        subject.market,
        "_take_native_postpublication_v2",
        lambda *_, **__: state["public_state"],
    )
    monkeypatch.setattr(
        subject.account, "acquire_postpublication_issued_account_v1", acquire_account
    )
    monkeypatch.setattr(
        subject.account,
        "_take_issued_account_for_r7_v2",
        lambda *_, **__: state["account_binding"],
    )
    if late_write_failure:
        directory_type = (
            storage._WindowsDirectory if os.name == "nt" else storage._PosixDirectory
        )
        publish = directory_type.publish

        def fail_readback_file(self, name, raw):
            if name == "readback.json":
                raise OSError("synthetic late marker write failure")
            return publish(self, name, raw)

        monkeypatch.setattr(directory_type, "publish", fail_readback_file)

    with pytest.raises(subject.NativeR7Error, match="^native_r7_current_g1_rejected$"):
        subject._evaluate(state)
    assert not (root / f"r7-denial-{_INVOCATION}").exists()
    with pytest.raises(subject.NativeR7Error, match="^native_r7_current_g1_rejected$"):
        await subject._capture(state)
    assert released == [state] and not state.get("issued", False)
    directory, raw, decision, marker = _read_decision(root)
    assert decision["category"] == "data_freshness"
    assert decision["current_g1_code"] == "stale_market_data"
    assert decision["costs"] is None and decision["current_ticker"] is None
    assert decision["current_g1_policy_sha256"] == g1.policy_sha256
    assert decision["public_packet_sha256"] == packet.bundle_sha256
    assert decision["g12_report_sha256"] == facts.receipt.report_sha256
    assert decision["admission"] == "DENY" and decision["execution_authority"] is False
    assert scanned[0] == decision and len(scanned) == 2
    if late_write_failure:
        assert marker is None
    else:
        assert marker["decision_sha256"] == sha(raw)
        assert (
            marker["g12_custody_publication_sha256"]
            == facts.publication.publication_sha256
        )
        assert marker["decision_readback_completed"] == _clock(7)

    before = {path.name: path.read_bytes() for path in directory.iterdir()}
    subject._publish_denial_packet(
        root,
        facts,
        _INVOCATION,
        canonical({"conflict": True}),
        barrier=state["barrier"],
        binding=state["account_binding"],
    )
    assert {path.name: path.read_bytes() for path in directory.iterdir()} == before
    assert {p.name: p.read_bytes() for p in (root / _REPORT).iterdir()} == {
        name: name.encode() for name in storage.FILE_NAMES
    }


@pytest.mark.parametrize(
    ("direction", "bid", "ask", "spread_cap", "expected_code", "attributed"),
    [
        ("long", "107.99", "108", "20", "net_rr_below_minimum", True),
        ("short", "92", "92.01", "20", "net_rr_below_minimum", True),
        ("long", "106", "108", "20", "spread_above_limit", False),
    ],
)
@pytest.mark.asyncio
async def test_counterfactual_keeps_cost_operands_fixed(
    monkeypatch, tmp_path, direction, bid, ask, spread_cap, expected_code, attributed
):
    state, facts, packet, g1, issued, _ = _fixture(
        tmp_path, monkeypatch, direction=direction
    )
    state["started"] = True
    g1.gate.code = "passed"
    quote = Data(
        ticker=_ticker(bid, ask, 4),
        mark=Data(exchange_data_return_at=_at(4)),
        funding=Data(exchange_data_return_at=_at(4), forecast=Data(rate=Decimal(0))),
        capture_completed_at=_at(5),
    )
    policy = EconomicsPolicy(
        policy_id="synthetic-costs",
        round_trip_fee_bps=Decimal(2),
        round_trip_slippage_bps=Decimal(1),
        funding_buffer_bps=Decimal(0),
        funding_periods=1,
        minimum_net_rr=Decimal(2),
        maximum_spread_bps=Decimal(spread_cap),
        maximum_funding_bps=Decimal(1),
        maximum_quote_age_seconds=5,
    )
    fixed = facts.original.result
    executable = quote.ticker.ask if direction == "long" else quote.ticker.bid
    scenarios = tuple(
        subject.economics._scenario(
            entry=entry,
            stop=fixed.stop_loss,
            target=fixed.take_profit,
            direction=direction,
            quote=quote,
            policy=policy,
        )
        for entry in (fixed.candidate_entry, executable)
    )
    assert scenarios[1].code == expected_code
    subject._retain_terminal_denial(
        state,
        code="native_r7_fixed_economics_rejected",
        g1=g1,
        packet=packet,
        issued=issued,
        at=_at(6),
        context=Data(quote=quote),
        costs_policy=policy,
        scenarios=scenarios,
    )
    _, _, decision, marker = _read_decision(state["published"]["root"])
    assert marker is not None
    assert decision["category"] == "economics"
    assert decision["movement_during_render_proven"] is False
    assert decision["entry"] == str(fixed.candidate_entry)
    assert decision["stop_loss"] == str(fixed.stop_loss)
    assert decision["take_profit"] == str(fixed.take_profit)
    assert decision["current_ticker"]["request_started_at"] == _at(4).isoformat()
    costs = decision["costs"]
    assert costs["executable"]["code"] == expected_code
    assert costs["adverse_reference_alone_sufficient_for_net_rr_failure"] is attributed
    assert costs["baseline_reference_with_current_costs"]["code"] == (
        "passed" if attributed else "spread_above_limit"
    )
