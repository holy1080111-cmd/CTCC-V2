"""This invocation's G12 -> owned fresh capture -> recorded recheck.

No caller publisher, collector, PASS, old receipt, order callback, settings or
database is accepted. This is an explicit, unmounted diagnostic orchestration
entrypoint, not the Demo trading runtime. Account completeness, authenticated
local guards/history, complete intrabar coverage and reservation/submit remain
separate requirements. A result can never authorize an order or a resumed run.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from gc import get_referents
from pathlib import Path, PosixPath, WindowsPath
from types import MappingProxyType
from typing import Literal

from app.domain.market import MarketSnapshot
from app.trade_evidence.gates import (
    EvidenceGateRun,
    publish_qualification_evidence,
    verify_pre_evidence_versioned,
)
from app.trade_evidence.storage import actual_utc
from app.trade_qualification import account_capture as accounts
from app.trade_qualification import account_collector as private_capture
from app.trade_qualification import account_materializer as materializer
from app.trade_qualification import data as data_models
from app.trade_qualification import economics as economics_models
from app.trade_qualification import engine as engine_models
from app.trade_qualification import event_models, location, models, portfolio, timing
from app.trade_qualification import public_market_collector as public_capture
from app.trade_qualification import quote_collector as quotes
from app.trade_qualification import service as prefix_models
from app.trade_qualification.data import (
    WSReferenceObservation,
    _bounded_scalars,
    _market_copy,
)
from app.trade_qualification.engine import (
    PortfolioInputs,
    PreEvidencePolicy,
    PreEvidenceRun,
    _copy,
)
from app.trade_qualification.history_engine import (
    HistoryPreEvidencePolicyV2,
    HistoryPreEvidencePolicyV3,
    HistoryPreEvidencePolicyV4,
    HistoryPreEvidencePolicyV5,
    HistoryPreEvidencePolicyV6,
    HistoryPreEvidenceRunV2,
    HistoryPreEvidenceRunV3,
    HistoryPreEvidenceRunV4,
    HistoryPreEvidenceRunV5,
    HistoryPreEvidenceRunV6,
)
from app.trade_qualification.history_engine import _copy as _history_copy
from app.trade_qualification.history_prefix import (
    HistoryEntryQualificationResultV2,
    HistoryEntryQualificationResultV3,
    HistoryEntryQualificationResultV4,
    HistoryEntryQualificationResultV5,
    HistoryEntryQualificationResultV6,
    HistoryQualificationPrefixPolicyV2,
    HistoryQualificationPrefixPolicyV3,
    HistoryQualificationPrefixPolicyV4,
    HistoryQualificationPrefixPolicyV5,
    HistoryQualificationPrefixPolicyV6,
    HistoryQualificationPrefixRunV2,
    HistoryQualificationPrefixRunV3,
    HistoryQualificationPrefixRunV4,
    HistoryQualificationPrefixRunV5,
    HistoryQualificationPrefixRunV6,
)
from app.trade_qualification.market_bridge import public_market_snapshot
from app.trade_qualification.quote_collector import validate_collected_quote
from app.trade_qualification.recheck import (
    RecordedRecheckAssessment,
    evaluate_recorded_recheck,
)
from app.trade_qualification.recheck_models import freeze_recheck_origin
from app.trade_qualification.regime_admission import RegimeAdmissionResult
from app.trade_qualification.service import (
    QualificationIntent,
    _bounded,
    _event_keys,
    _plain,
)
from app.trade_qualification.sweep_contract import SweepHistoryAdmissionRecord

_ORIGINAL_KEYS = frozenset(
    {
        "intent",
        "quote",
        "reference",
        "policy",
        "risk_inputs",
        "consumed_event_keys",
        "evaluated_at",
    }
)
_INPUT_MODELS = (
    HistoryPreEvidencePolicyV6,
    HistoryPreEvidenceRunV6,
    HistoryQualificationPrefixPolicyV6,
    HistoryQualificationPrefixRunV6,
    HistoryEntryQualificationResultV6,
    SweepHistoryAdmissionRecord,
    HistoryPreEvidencePolicyV3,
    HistoryPreEvidencePolicyV4,
    HistoryPreEvidencePolicyV5,
    HistoryPreEvidenceRunV3,
    HistoryPreEvidenceRunV4,
    HistoryPreEvidenceRunV5,
    HistoryQualificationPrefixPolicyV3,
    HistoryQualificationPrefixPolicyV4,
    HistoryQualificationPrefixPolicyV5,
    HistoryQualificationPrefixRunV3,
    HistoryQualificationPrefixRunV4,
    HistoryQualificationPrefixRunV5,
    HistoryEntryQualificationResultV3,
    HistoryEntryQualificationResultV4,
    HistoryEntryQualificationResultV5,
    HistoryPreEvidencePolicyV2,
    HistoryPreEvidenceRunV2,
    HistoryQualificationPrefixPolicyV2,
    HistoryQualificationPrefixRunV2,
    HistoryEntryQualificationResultV2,
    RegimeAdmissionResult,
    *data_models._MARKET_MODELS,
    data_models.WSReferenceObservation,
    data_models.DataQualificationPolicy,
    data_models.DataQualificationResult,
    engine_models.ProtectionPolicy,
    PreEvidencePolicy,
    PortfolioInputs,
    PreEvidenceRun,
    QualificationIntent,
    prefix_models.QualificationPrefixPolicy,
    prefix_models.QualificationPrefixRun,
    economics_models.EconomicsPolicy,
    economics_models.EconomicsResult,
    event_models.TriggerDetection,
    location.ExecutableQuote,
    location.LocationResult,
    models.EntryZone,
    models.EntryTrigger,
    models.GateAssessment,
    models.EntryQualificationResult,
    portfolio.ContractRiskSpec,
    portfolio.EvidenceStamp,
    portfolio.DemoRiskAuthority,
    portfolio.PositionExposure,
    portfolio.PendingReservation,
    portfolio.RealizedOutcome,
    portfolio.PortfolioRiskSnapshot,
    portfolio.PortfolioRiskPolicy,
    portfolio.PortfolioRiskResult,
    quotes.CollectedQuote,
    quotes.QuoteCollectionPolicy,
    quotes.EndpointObservation,
    timing.TimingPolicy,
    timing.TimingResult,
)


def _guard_original(value, depth=0, budget=None):
    """Exact, bounded raw state before legacy helpers can inspect/serialize it."""
    if budget is None:
        budget = [300000, 64 * 1024 * 1024]
    budget[0] -= 1
    if depth > 24 or budget[0] < 0:
        raise OneShotInputError("one_shot_preflight_invalid")
    kind = type(value)
    if any(kind is allowed for allowed in _INPUT_MODELS):
        fields = object.__getattribute__(value, "__dict__")
        supplied = object.__getattribute__(value, "__pydantic_fields_set__")
        if (
            type(fields) is not dict
            or any(type(key) is not str for key in fields)
            or set(fields) != set(kind.model_fields)
            or object.__getattribute__(value, "__pydantic_extra__") is not None
            or object.__getattribute__(value, "__pydantic_private__") is not None
            or type(supplied) is not set
            or any(type(key) is not str for key in supplied)
            or not supplied <= set(kind.model_fields)
        ):
            raise OneShotInputError("one_shot_preflight_invalid")
        for key, item in fields.items():
            # Caller quality is ignored by the established raw-market replayer.
            if kind is MarketSnapshot and key == "quality":
                continue
            _guard_original(item, depth + 1, budget)
    elif kind is dict or kind is MappingProxyType:
        if kind is MappingProxyType:
            # A native proxy may wrap a foreign Mapping whose len/items/iterator
            # runs caller code. Check its native referent before accessing it.
            referents = get_referents(value)
            if len(referents) != 1 or type(referents[0]) is not dict:
                raise OneShotInputError("one_shot_preflight_invalid")
        if len(value) > 2048 or any(type(key) is not str for key in value):
            raise OneShotInputError("one_shot_preflight_invalid")
        for key, item in value.items():
            _guard_original(key, depth + 1, budget)
            _guard_original(item, depth + 1, budget)
    elif any(kind is allowed for allowed in (list, tuple, frozenset)):
        if len(value) > 10000:
            raise OneShotInputError("one_shot_preflight_invalid")
        for item in value:
            _guard_original(item, depth + 1, budget)
    elif kind is datetime:
        accounts._utc(value)  # refuses foreign timezone callbacks before conversion
    elif kind is Decimal:
        if (
            not value.is_finite()
            or len(value.as_tuple().digits) > 128
            or abs(value.as_tuple().exponent) > 200
        ):
            raise OneShotInputError("one_shot_preflight_invalid")
    elif kind is str or kind is bytes:
        budget[1] -= len(value)
        if len(value) > 8 * 1024 * 1024 or budget[1] < 0:
            raise OneShotInputError("one_shot_preflight_invalid")
    elif (
        any(
            kind is allowed
            for allowed in (
                models.QualificationGate,
                models.QualificationState,
                models.MarketRegime,
            )
        )
        or value is None
        or kind is bool
        or kind is int
        and abs(value) <= 10**40
    ):
        pass
    else:
        raise OneShotInputError("one_shot_preflight_invalid")


class OneShotInputError(ValueError):
    """A fixed local code, never an exception/body from an external service."""


_PREFLIGHT_CODES = frozenset(
    {
        "one_shot_absolute_root_required",
        "one_shot_purpose_invalid",
        "one_shot_runtime_policy_invalid",
        "one_shot_observed_clock_injected",
        "one_shot_original_input_envelope_invalid",
        "one_shot_intent_or_run_invalid",
        "one_shot_intent_invalid",
        "one_shot_preflight_invalid",
        "one_shot_account_session_mismatch",
        "one_shot_unbound_materialization_pin",
        "one_shot_materialization_pin_mismatch",
        "one_shot_materialization_scope_mismatch",
        "one_shot_account_scope_changed",
    }
)


class _Stopped(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class OneShotCaptureResult:
    """In-memory evidence only; raw account data is not included in repr/logging."""

    code: str
    report_id: str
    observed_at: datetime
    evidence: EvidenceGateRun | None = field(default=None, repr=False)
    market_packet: public_capture.CollectedPublicMarket | None = field(
        default=None, repr=False
    )
    account_packet: accounts.DemoAccountPacket | None = field(default=None, repr=False)
    account_payload_sha256: str | None = None
    account_materialization: materializer.AccountMaterializationResult | None = field(
        default=None, repr=False
    )
    recheck: RecordedRecheckAssessment | None = field(default=None, repr=False)
    record_kind: Literal["one_shot_capture_not_execution_permission"] = (
        "one_shot_capture_not_execution_permission"
    )
    execution_authority: Literal[False] = False
    source_authenticity_verified: Literal[False] = False
    account_complete: Literal[False] = False
    atomic_risk_reserved: Literal[False] = False
    order_submitted: Literal[False] = False

    def __post_init__(self):
        if any(
            getattr(self, name) is not False
            for name in (
                "execution_authority",
                "source_authenticity_verified",
                "account_complete",
                "atomic_risk_reserved",
                "order_submitted",
            )
        ):
            raise OneShotInputError("one_shot_cannot_grant_execution")
        if self.record_kind != "one_shot_capture_not_execution_permission":
            raise OneShotInputError("one_shot_record_kind_invalid")


class _Clock:
    def __init__(self, clock, *, earliest, expiry, wall_deadline, loop):
        self.clock = clock
        self.last = earliest
        self.expiry = expiry
        self.wall_deadline = wall_deadline
        self.loop = loop
        self.violation = None

    def __call__(self):
        task = asyncio.current_task()
        if task is not None and task.cancelling():
            raise asyncio.CancelledError
        if self.loop.time() >= self.wall_deadline:
            self.violation = "one_shot_deadline_exceeded"
            raise _Stopped(self.violation)
        try:
            value = accounts._utc(self.clock())
        except Exception:  # noqa: BLE001 - Never retain caller clock exception text.
            self.violation = "one_shot_clock_invalid"
            raise _Stopped(self.violation) from None
        # A synchronous clock callback may itself consume the budget.
        if self.loop.time() >= self.wall_deadline:
            self.violation = "one_shot_deadline_exceeded"
            raise _Stopped(self.violation)
        if value < self.last:
            self.violation = "one_shot_clock_reversed"
            raise _Stopped(self.violation)
        self.last = value
        if value >= self.expiry:
            self.violation = "one_shot_candidate_expired"
            raise _Stopped(self.violation)
        return value


def _original(market, run, values):
    if (
        type(values) is not dict
        or len(values) != 7
        or any(type(k) is not str for k in values)
        or set(values) != _ORIGINAL_KEYS
    ):
        raise OneShotInputError("one_shot_original_input_envelope_invalid")
    if type(values["intent"]) is not QualificationIntent or type(run) not in (
        PreEvidenceRun,
        HistoryPreEvidenceRunV2,
        HistoryPreEvidenceRunV3,
        HistoryPreEvidenceRunV4,
        HistoryPreEvidenceRunV5,
        HistoryPreEvidenceRunV6,
    ):
        raise OneShotInputError("one_shot_intent_or_run_invalid")
    _guard_original(market)
    _guard_original(run)
    _guard_original(values)
    # Detach all caller-owned mutable models before publishing or awaiting.
    market = _market_copy(market)
    _bounded(values["intent"])
    if type(values["intent"]) is not QualificationIntent:
        raise OneShotInputError("one_shot_intent_invalid")
    intent = QualificationIntent.model_validate(_plain(values["intent"]), strict=True)
    copied = {
        "intent": intent,
        "quote": None
        if values["quote"] is None
        else validate_collected_quote(values["quote"]),
        "reference": None
        if values["reference"] is None
        else _bounded_scalars(values["reference"], WSReferenceObservation),
        "policy": _history_copy(values["policy"], HistoryPreEvidencePolicyV6)
        if type(run) is HistoryPreEvidenceRunV6
        else _history_copy(values["policy"], HistoryPreEvidencePolicyV5)
        if type(run) is HistoryPreEvidenceRunV5
        else _history_copy(values["policy"], HistoryPreEvidencePolicyV4)
        if type(run) is HistoryPreEvidenceRunV4
        else _history_copy(values["policy"], HistoryPreEvidencePolicyV3)
        if type(run) is HistoryPreEvidenceRunV3
        else _history_copy(values["policy"], HistoryPreEvidencePolicyV2)
        if type(run) is HistoryPreEvidenceRunV2
        else _copy(values["policy"], PreEvidencePolicy),
        "risk_inputs": _copy(values["risk_inputs"], PortfolioInputs),
        "consumed_event_keys": _event_keys(values["consumed_event_keys"]),
        "evaluated_at": accounts._utc(values["evaluated_at"]),
    }
    checked = verify_pre_evidence_versioned(run, market, **copied)
    return market, checked, copied


async def _captures(clock, origin, policy, credentials, plan, plan_pin):
    """Fixed dependencies; cancel/join siblings through their bounded cleanup."""
    # Explicit owners avoid TaskGroup's internal parent cancellation hiding an
    # external cancel that arrives while a failed sibling is being cleaned up.
    tasks = []
    interrupted = failed = False
    try:
        tasks.append(
            asyncio.create_task(
                public_capture.collect_public_market(
                    clock=clock,
                    report_id=origin.candidate.report_id,
                    instrument_id=origin.evidence.pre_evidence.prefix.intent.instrument_id,
                    policy=policy,
                    barrier_completed_at=origin.publication_completed_at,
                )
            )
        )
        tasks.append(
            asyncio.create_task(
                private_capture.collect_demo_account_records(
                    credentials=credentials,
                    clock=clock,
                    plan=plan,
                    expected_plan_sha256=plan_pin,
                    barrier_completed_at=origin.publication_completed_at,
                )
            )
        )
        pending = set(tasks)
        while pending:
            done, pending = await asyncio.wait(
                pending, return_when=asyncio.FIRST_COMPLETED
            )
            if any(task.cancelled() or task.exception() is not None for task in done):
                failed = True
                break
    except asyncio.CancelledError:
        interrupted = failed = True
    except Exception:  # noqa: BLE001 - Only a local static failure may escape.
        failed = True
    finally:
        if failed:
            for task in tasks:
                if not task.done():
                    task.cancel()
        joined = asyncio.gather(*tasks, return_exceptions=True)
        while not joined.done():
            try:
                await asyncio.shield(joined)
            except asyncio.CancelledError:
                interrupted = True
                for task in tasks:
                    if not task.done():
                        task.cancel()
        outcomes = joined.result()
    if interrupted:
        raise asyncio.CancelledError
    if failed or any(isinstance(item, BaseException) for item in outcomes):
        raise ValueError("one_shot_owned_capture_failed") from None
    return tuple(outcomes)


async def publish_capture_recheck(
    root: Path,
    original_market: MarketSnapshot,
    *,
    run: PreEvidenceRun,
    original_inputs: dict,
    market_policy: public_capture.PublicMarketCollectionPolicy,
    account_plan: accounts.DemoAccountCapturePlan,
    expected_account_plan_sha256: str,
    credentials: private_capture.DemoAccountCredentials,
    purpose: Literal["synthetic_test", "observed"],
    materialization_inputs: materializer.AccountMaterializationInputs | None = None,
    expected_materialization_inputs_sha256: str | None = None,
    clock: Callable[[], datetime] = actual_utc,
    total_timeout_seconds: int = 90,
) -> OneShotCaptureResult:
    """Publish here once, capture through owned GET/socket clients, recheck.

    This explicit API can read Demo account data with supplied credentials, but
    it has no order or reservation dependency. It is not installed in a router,
    scheduler or startup hook. No raw packet is automatically persisted. Failed
    earlier gates perform no filesystem/network operations. Cancellation is
    propagated; a failed/expired packet is never retried under another report ID.
    """
    try:
        if (
            not any(type(root) is allowed for allowed in (PosixPath, WindowsPath))
            or not root.is_absolute()
        ):
            raise OneShotInputError("one_shot_absolute_root_required")
        if type(purpose) is not str or purpose not in {"synthetic_test", "observed"}:
            raise OneShotInputError("one_shot_purpose_invalid")
        if (
            type(total_timeout_seconds) is not int
            or not 1 <= total_timeout_seconds <= 120
            or not callable(clock)
        ):
            raise OneShotInputError("one_shot_runtime_policy_invalid")
        if purpose == "observed" and clock is not actual_utc:
            raise OneShotInputError("one_shot_observed_clock_injected")
        market, pre, inputs = _original(original_market, run, original_inputs)
        selected = public_capture._policy_copy(market_policy)
        plan = accounts._checked_plan(account_plan, expected_account_plan_sha256)
        secret_values = private_capture._credential_values(credentials)
        handle = private_capture.DemoAccountCredentials(*secret_values)
        if handle.session_binding_id != plan.session_binding_id:
            raise OneShotInputError("one_shot_account_session_mismatch")
        if materialization_inputs is None:
            if expected_materialization_inputs_sha256 is not None:
                raise OneShotInputError("one_shot_unbound_materialization_pin")
            supplements = None
        else:
            supplements = materializer.copy_materialization_inputs(
                materialization_inputs
            )
            if (
                type(expected_materialization_inputs_sha256) is not str
                or expected_materialization_inputs_sha256
                != materializer.materialization_inputs_sha256(supplements)
            ):
                raise OneShotInputError("one_shot_materialization_pin_mismatch")
            if (
                supplements.account_id != plan.expected_uid
                or supplements.settlement_currency != plan.settlement_currency
            ):
                raise OneShotInputError("one_shot_materialization_scope_mismatch")
        prior_account = inputs["risk_inputs"].account
        if prior_account is not None and (
            prior_account.account_id != plan.expected_uid
            or prior_account.settlement_currency != plan.settlement_currency
        ):
            raise OneShotInputError("one_shot_account_scope_changed")
        private_capture._no_secret_json(
            accounts._canonical(accounts._json_value(plan)), list(secret_values[:3])
        )
    except OneShotInputError as exc:
        arguments = object.__getattribute__(exc, "args")
        code = "one_shot_preflight_invalid"
        if (
            type(exc) is OneShotInputError
            and type(arguments) is tuple
            and len(arguments) == 1
            and type(arguments[0]) is str
            and arguments[0] in _PREFLIGHT_CODES
        ):
            code = arguments[0]
        raise OneShotInputError(code) from None
    except Exception:  # noqa: BLE001 - Preflight errors may contain secrets/foreign text.
        raise OneShotInputError("one_shot_preflight_invalid") from None

    at = inputs["evaluated_at"]
    report_id = inputs["intent"].report_id
    evidence = public = account = recheck = mapped = None
    account_pin = None

    def finish(code):
        return OneShotCaptureResult(
            code=code,
            report_id=report_id,
            observed_at=observed.last,
            evidence=evidence,
            market_packet=public,
            account_packet=account,
            account_payload_sha256=account_pin,
            account_materialization=mapped,
            recheck=recheck,
        )

    loop = asyncio.get_running_loop()
    deadline = (
        min(
            inputs["intent"].expires_at,
            pre.result.trigger.expires_at,
            pre.result.entry_zone.expires_at,
            pre.prefix.timing.latest_valid_entry_time,
        )
        if pre.pre_evidence_complete
        else inputs["intent"].expires_at
    )
    observed = _Clock(
        clock,
        earliest=at,
        expiry=deadline,
        wall_deadline=loop.time() + total_timeout_seconds,
        loop=loop,
    )
    if not pre.pre_evidence_complete:
        return finish("one_shot_pre_evidence_rejected")
    try:
        observed()
        if plan.created_at > observed.last:
            return finish("one_shot_account_plan_from_future")
        evidence = publish_qualification_evidence(
            root, market, run=pre, **inputs, purpose=purpose, clock=observed
        )
        if not evidence.result.evidence_complete:
            return finish(observed.violation or "one_shot_evidence_rejected")
        observed()
        origin = freeze_recheck_origin(evidence)
        if plan.created_at > observed.last:
            return finish("one_shot_account_plan_from_future")
        async with asyncio.timeout_at(observed.wall_deadline):
            captured_public, captured_account = await _captures(
                observed, origin, selected, handle, plan, expected_account_plan_sha256
            )
        observed()
        # Replay the exact returned bytes, then bind both captures to this G12.
        public = public_capture.validate_collected_public_market(captured_public)
        frozen = accounts.freeze_demo_account_packet(
            captured_account, expected_plan_sha256=expected_account_plan_sha256
        )
        account = accounts.verify_demo_account_packet(
            frozen.payload,
            expected_sha256=frozen.sha256,
            expected_plan_sha256=expected_account_plan_sha256,
        )
        account_pin = frozen.sha256
        if (
            public.barrier_completed_at != origin.publication_completed_at
            or account.barrier_completed_at != origin.publication_completed_at
        ):
            return finish("one_shot_capture_barrier_mismatch")
        if (
            public.report_id != report_id
            or public.instrument_id != inputs["intent"].instrument_id
            or account.plan != plan
        ):
            return finish("one_shot_capture_scope_mismatch")
        current_market = public_market_snapshot(
            public, expected_bundle_sha256=public.bundle_sha256
        )
        ticker = public.ws.ticker
        reference = WSReferenceObservation(
            report_id=report_id,
            instrument_id=public.instrument_id,
            bid=ticker.bid,
            ask=ticker.ask,
            source_time=ticker.source_time,
            received_at=ticker.received_at,
        )
        # Replay supplemental raw metadata/ledger claims against this fresh packet.
        # Their pin binds content, not source trust or authenticated completeness.
        if supplements is not None:
            replay_inputs = {
                "packet": account,
                "expected_plan_sha256": expected_account_plan_sha256,
                "expected_packet_sha256": account_pin,
                "inputs": supplements,
                "expected_inputs_sha256": expected_materialization_inputs_sha256,
            }
            mapped = materializer.materialize_demo_portfolio_snapshot(**replay_inputs)
            mapped = materializer.verify_account_materialization(
                mapped, **replay_inputs
            )
        # Never carry the original G11 account/authority into the current-risk check.
        risk = PortfolioInputs(
            requested_contracts=inputs["risk_inputs"].requested_contracts,
            requested_leverage=inputs["risk_inputs"].requested_leverage,
            instrument=None
            if mapped is None
            else materializer.get_materialized_instrument(
                mapped, inputs["intent"].instrument_id
            ),
            account=None if mapped is None else mapped.snapshot,
            authority=None,
        )
        recheck = evaluate_recorded_recheck(
            market,
            current_market,
            origin=origin,
            original_inputs=inputs,
            quote=public.quote,
            reference=reference,
            current_risk_inputs=risk,
            consumed_event_keys=inputs["consumed_event_keys"],
            observed_at=observed(),
        )
        observed()
        return finish(
            (
                "one_shot_account_materialization_required"
                if mapped is None
                else "one_shot_account_materialization_incomplete"
            )
            if recheck.checks[-1].step == "current_risk"
            else "one_shot_recorded_recheck_rejected"
        )
    except _Stopped:
        return finish(observed.violation or "one_shot_clock_invalid")
    except TimeoutError:
        return finish("one_shot_deadline_exceeded")
    except Exception:  # noqa: BLE001 - Never serialize external errors or ExceptionGroup.
        # No exception repr, account IDs, signer values, headers or provider body.
        return finish(observed.violation or "one_shot_capture_or_replay_rejected")
