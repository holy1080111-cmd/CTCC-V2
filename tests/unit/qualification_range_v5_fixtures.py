"""Declared synthetic range sources, never exchange/source acceptance."""

from decimal import Decimal

from app.trade_qualification.history_engine import HistoryPreEvidencePolicyV5
from app.trade_qualification.history_prefix import HistoryQualificationPrefixPolicyV5
from tests.unit.history_qualification_fixtures import history_inputs
from tests.unit.qualification_range_fixtures import range_source


def range_v5_source(direction="long"):
    source = range_source(direction)
    # Build a wider historical range before event detection/qualification. The
    # edge reclaim and all subsequent selectors operate on these raw candles.
    for frame, offset, field, price in (
        ("15m", -6, "high", "100.5"),
        ("15m", -20, "high", "105"),
        ("1H", -20, "low", "99.0"),
    ):
        value = Decimal(price)
        if direction == "short":
            field = "low" if field == "high" else "high"
            value = Decimal(200) - value
        rows = source.market.candles[frame]
        rows[offset] = rows[offset].model_copy(update={field: value})
    return source


def v5_inputs(source):
    args = history_inputs(source)
    old = args["policy"]
    prefix = old.prefix.model_dump(mode="python", round_trip=True)
    prefix.update(
        contract_version="ctcc-history-qualification-prefix-v5",
        range_anchor_policy="ctcc-original-range-anchor-v1",
        range_protection_policy="ctcc-neutral-range-protection-v1",
    )
    args["policy"] = HistoryPreEvidencePolicyV5(
        contract_version="ctcc-history-pre-evidence-v5",
        policy_id="synthetic-neutral-range-v5",
        prefix=HistoryQualificationPrefixPolicyV5(**prefix),
        protection=old.protection,
        economics=old.economics,
        portfolio=old.portfolio,
    )
    return args


def memory_published(source, inputs, monkeypatch):
    """Actual rendering/publisher/hash/readback on a declared memory backend.

    No Windows filesystem, native no-clobber, durable IO or real source claim.
    """
    from contextlib import contextmanager
    from pathlib import Path

    from app.trade_evidence import storage
    from app.trade_evidence.gates import publish_qualification_evidence
    from app.trade_qualification.history_engine import evaluate_history_pre_evidence_v5
    from tests.unit.research.test_public_journal_contracts import MemoryDirectory
    from tests.unit.test_qualification_evidence_gate import Clock

    content = {}

    @contextmanager
    def memory_root(_):
        yield MemoryDirectory(content)

    monkeypatch.setattr(storage, "_windows_root", memory_root)
    monkeypatch.setattr(storage, "_posix_root", memory_root)
    run = evaluate_history_pre_evidence_v5(source.market, **inputs)
    assert run.pre_evidence_complete, run.result.fail_codes
    evidence = publish_qualification_evidence(
        Path.cwd() / "synthetic-memory-range-v5",
        source.market,
        run=run,
        **inputs,
        purpose="synthetic_test",
        clock=Clock(source.evaluated_at),
    )
    assert evidence.result.gates[-1].passed, evidence.error_detail
    return evidence, content


def range_v5_ledger_fixture(direction, monkeypatch, *, account_id=None):
    import asyncio
    from types import SimpleNamespace

    from app.trade_qualification import reservations as r
    from app.trade_qualification.engine import PortfolioInputs
    from app.trade_qualification.recheck_models import freeze_recheck_origin
    from app.trade_qualification.service import _plain
    from tests.unit.qualification_execution_binding_fixtures import execution_binding
    from tests.unit.qualification_ledger_fixtures import SYNTHETIC_UID, LedgerFixture
    from tests.unit.test_history_expansion_v2 import current_inputs

    account_id = SYNTHETIC_UID if account_id is None else account_id
    source = range_v5_source(direction)
    inputs = v5_inputs(source)
    raw = _plain(inputs["risk_inputs"])
    raw["account"]["account_id"] = account_id
    for name in r.STAMP_FIELDS:
        raw["account"][name]["account_id"] = account_id
    raw["authority"]["stamp"]["account_id"] = account_id
    inputs["risk_inputs"] = PortfolioInputs.model_validate(raw, strict=True)
    evidence, content = memory_published(source, inputs, monkeypatch)
    origin = freeze_recheck_origin(evidence)
    market, current = asyncio.run(current_inputs(source, inputs, origin))
    risk = current["current_risk_inputs"]
    scope = r.LedgerScope(account_id=account_id, settlement_currency="USDT")
    claims = r.AccountLedgerClaims(
        scope=scope,
        account=risk.account,
        authority=risk.authority,
        reconciliation_id="a" * 64,
    )
    # Temporary typed input to fixture construction only. This bare request
    # cannot pass the production V5 reservation boundary.
    bare = r.ReservationRequest(
        scope=scope,
        origin=origin,
        quote=current["quote"].quote,
        risk_inputs=risk,
        expected_account_revision=1,
        expected_ledger_revision=1,
    )
    chain = SimpleNamespace(
        original_source=source,
        original_inputs=inputs,
        latest_source=SimpleNamespace(
            market=market,
            quote=current["quote"],
            reference=current["reference"],
            evaluated_at=current["observed_at"],
        ),
    )
    temporary = LedgerFixture(bare, claims, chain)
    binding = execution_binding(temporary)
    replay = r.ReservationReplayBindingV3(
        contract_version="ctcc-reservation-replay-v3",
        **{
            name: getattr(binding, name)
            for name in r.ReservationReplayBindingV3.model_fields
            if name not in r.LedgerModel.model_fields and name != "contract_version"
        },
    )
    request = r.ReservationRequestV3(
        **_plain(bare),
        contract_version="ctcc-reservation-request-v3",
        replay_binding=replay,
    )
    return LedgerFixture(request, claims, chain), binding, content
