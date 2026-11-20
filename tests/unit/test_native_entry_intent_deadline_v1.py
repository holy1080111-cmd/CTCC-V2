"""Actual coordinator/ledger deadline agreement; synthetic operands, no DB."""

from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace as Data

from app.domain.source_primitives import ClockStamp, canonical, sha
from app.okx_live import owned_qualification_v1 as q
from tests.unit.test_native_live_source_ledger_v1 import NOW, _value


def test_intent_shortens_to_exact_account_bound_without_rounding_beyond_it(monkeypatch):
    old = _value()
    owner = object.__new__(q.source.OwnedLiveSourceV1)
    completed = ClockStamp(
        utc_ns=q.source._utc_ns(NOW) + 123, monotonic_ns=10_000_000_000
    )
    view = {
        "uid": old.account_uid,
        "main_uid": old.main_uid,
        "credential_session_sha256": old.credential_session_sha256,
        "source_revision_sha256": old.source_revision_sha256,
        "current_completed": completed,
        "plan": Data(max_current_age_ns=5_000_000_000),
        "current_readback": Data(
            receipt={"no_open_positions_or_pending_orders_observed": True},
            usdt_available_equity=Data(available_equity=Decimal(1000)),
        ),
    }
    q.source._SOURCES[owner] = view
    raw = old.original_candidate_json.encode("ascii")
    state = {
        "deadline": NOW + timedelta(seconds=30),
        "source_owner": owner,
        "original_candidate_json": raw,
        "identity": view,
        "process_sha256": old.process_sha256,
        "policy_sha256": old.policy_sha256,
        "arm_id": old.arm_id,
        "control": {"revision": old.expected_control_revision},
        "publication": Data(receipt=Data(report_sha256=old.report_sha256)),
        "publication_json": b"{}",
        "instrument_id": old.instrument_id,
        "direction": old.direction,
    }
    operands = canonical(
        {
            "candidate_entry": "100",
            "executable_reference": "100.1",
            "round_trip_cost_per_base": "0.1",
            "expires_at": old.deadline.isoformat(),
        }
    )
    calculation = Data(
        risk_operands_json=operands,
        risk_inputs=None,
        intent=Data(candidate_entry=old.entry),
        policy=None,
        result=Data(stop_loss=old.stop_loss, take_profit=old.take_profit),
        source=Data(evaluated_at=NOW),
        evaluation_sha256=old.recheck_sha256,
    )
    monkeypatch.setattr(
        q.math,
        "_sample_risk",
        lambda *_a, **_k: Data(
            passed=True,
            max_loss_amount=Decimal("0.1"),
            required_margin=Decimal(1),
            notional=Decimal(10),
        ),
    )
    monkeypatch.setattr(
        q.source, "_replay_owned_live_source_v1", lambda *_a, **_k: view
    )
    try:
        value = q._intent(
            state,
            calculation,
            Data(contracts=Decimal(1), leverage=1, position_side="net"),
        )
        assert value.deadline == NOW + timedelta(seconds=5)
        assert (
            q.source._utc_ns(value.deadline)
            <= completed.utc_ns + view["plan"].max_current_age_ns
        )
        assert value.deadline < old.deadline
        assert (value.entry, value.stop_loss, value.take_profit) == (
            old.entry,
            old.stop_loss,
            old.take_profit,
        )
        assert value.candidate_sha256 == sha(raw)
        assert q.ledger._source(value, owner) is view
    finally:
        q.source._SOURCES.pop(owner)
