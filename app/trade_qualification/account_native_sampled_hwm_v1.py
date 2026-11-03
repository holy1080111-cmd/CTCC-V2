"""Live owned source MOVE -> sampled values -> existing custody commit/readback.

The only public input is the real V4 history+coverage owner. The fixed native
assembly hook retains that owner's original Task, factories, credentials,
source bytes, native anchors and expiry. No portable observation/claims DTO,
caller clock, passed flag or alternate destination is accepted.
"""

import asyncio
import json
from datetime import UTC, datetime
from types import MappingProxyType

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.repositories import public_runtime_bundle_custody as custody
from app.domain.source_primitives import canonical, decode, sha, utc_from_ns
from app.trade_qualification import account_current_source_v4 as current
from app.trade_qualification import account_history_global_financial_v2 as financial
from app.trade_qualification import account_observation_index as observed
from app.trade_qualification import (
    account_original_balance_measurement_v1 as measurement,
)
from app.trade_qualification.reservations import LedgerScope, checked_bootstrap

_SOURCE_KEY = object()
_UNKNOWN_BINDINGS = []
MAX_RECORD_BYTES = 1048576
SCHEMA = "ctcc.native_owned_sampled_balance_peak_record.v1"
POLICY_BYTES = canonical(
    {
        "version": "ctcc.native_owned_sampled_balance_peak_record.policy.v1",
        "measurement_policy_sha256": measurement.POLICY_SHA256,
        "financial_policy_sha256": financial.POLICY_SHA256,
        "entry": "actual_owned_history_coverage_MOVE_no_portable_input",
        "prefix": "actual_original_PG_genesis_to_exact_head_same_invocation",
        "current": "still_owned_original_CURRENT_V7_packet_native_clock_and_custody",
        "persistence": "existing_restricted_runtime_bundle_custody_commit_and_exact_separate_readback",
        "historical_clock_all_sources": "not_inferred_from_version_or_native_current",
        "initial_peak_cashflow_and_full_historical_peak": "unknown",
        "account_revision_issued": False,
        "execution_authority": False,
    }
)
POLICY_SHA256 = sha(POLICY_BYTES)
ERROR_CODES = frozenset(
    {
        "native_sampled_hwm_unavailable",
        "native_sampled_hwm_original_context_required",
        "native_sampled_hwm_original_clock_changed",
        "native_sampled_hwm_original_observation_required",
        "native_sampled_hwm_original_prefix_changed",
        "native_sampled_hwm_unknown_promoted",
        "native_sampled_hwm_account_identity_changed",
        "native_sampled_hwm_original_deadline_expired",
        "native_sampled_hwm_finalization_unknown",
        "native_sampled_hwm_record_bound",
        "native_sampled_hwm_original_context_changed",
        "native_sampled_hwm_original_source_changed",
        "native_sampled_hwm_committed_readback_changed",
        "native_sampled_hwm_write_or_readback_unknown",
    }
)


class NativeSampledHWMError(ValueError):
    """Closed source-safe codes only."""


def _deny(code="native_sampled_hwm_unavailable"):
    if type(code) is not str or code not in ERROR_CODES:
        code = "native_sampled_hwm_unavailable"
    raise NativeSampledHWMError(code) from None


def _assembly():
    # A fixed product module, never a caller adapter/callback/plugin.
    from app.trade_qualification import native_account_risk_assembly_v1

    return native_account_risk_assembly_v1


def _context(binding):
    context = _assembly()._native_hwm_binding_v1(binding, key=_SOURCE_KEY)
    if type(context) is not MappingProxyType or set(context) != {
        "factory",
        "custody_repository",
    }:
        _deny("native_sampled_hwm_original_context_required")
    factory, repository = context["factory"], context["custody_repository"]
    if (
        type(factory) is not async_sessionmaker
        or factory.class_ is not AsyncSession
        or type(repository) is not custody.RuntimeBundleCustodyRepository
    ):
        _deny("native_sampled_hwm_original_context_required")
    repository._check_factory()
    return factory, repository


def _utc(value):
    if type(value) is not datetime or value.tzinfo is not UTC:
        _deny("native_sampled_hwm_original_clock_changed")
    return value


def _view(facts, view, stamp, observation):
    assembly = _assembly()
    # The observation was calculated INSIDE the still-live opaque binding by
    # the fixed hook. The public function never accepts one as its input.
    if type(observation) is not assembly.NativeAccountHistoryFinancialObservationV4:
        _deny("native_sampled_hwm_original_observation_required")
    from app.database.repositories import (
        account_history_original_financial_read_v3 as reader,
    )
    from app.trade_qualification import (
        account_history_current_bundle_v2 as history_bundle,
    )
    from app.trade_qualification import account_history_original_stream_v1 as stream

    if (
        type(facts) is not history_bundle.CurrentHistorySourceFactsV2
        or type(observation.original_history)
        is not reader.OriginalHistoryFinancialPgReadObservationV3
        or type(observation.original_history.replay)
        is not stream.OriginalHistoryFinancialStreamReplayV3
        or type(observation.original_history.replay.financial_facts)
        is not financial.GlobalFinancialHistoryFactsV2
        or type(view) is not dict
        or type(view.get("current")) is not current.RecordedCurrentAccountBundleV4
    ):
        _deny("native_sampled_hwm_original_observation_required")
    scope = facts.scope
    checked_bootstrap(scope, LedgerScope)
    if (
        type(stamp) is not dict
        or set(stamp) != {"utc_ns", "monotonic_ns"}
        or any(type(stamp[name]) is not int or stamp[name] <= 0 for name in stamp)
    ):
        _deny("native_sampled_hwm_original_clock_changed")
    core_raw = observation.original_history.replay.financial_facts.receipt_json
    core = decode(core_raw, 8 * 1024 * 1024)
    receipt = decode(observation.receipt_json, 1048576)
    acquired = decode(observation.original_history.acquisition_receipt_json, 1048576)
    peak = core.get("sampled_balance_peak")
    if (
        type(peak) is not dict
        or core.get("sampled_balance_peak_missing_reason") is not None
        or core["policy_sha256"] != financial.POLICY_SHA256
        or core["component_policy_sha256"]["balance_measurement"]
        != measurement.POLICY_SHA256
        or peak["policy_sha256"] != measurement.POLICY_SHA256
        or peak["schema_version"] != measurement.PEAK_SCHEMA
        or peak["scope"]
        != [scope.environment, scope.account_id, scope.settlement_currency]
        or core["scope_sha256"] != sha(canonical(peak["scope"]))
        or type(core["through_sequence"]) is not int
        or core["through_sequence"] < 1
        or type(peak["sample_count"]) is not int
        or peak["sample_count"] != core["through_sequence"]
        or peak["index_head_sha256"] != core["index_head_sha256"]
        or core["through_sequence"] != receipt["original_head_sequence"]
        or core["index_head_sha256"] != receipt["original_head_sha256"]
        or acquired["observed_frames"] != core["through_sequence"]
        or acquired["global_financial_receipt_sha256"] != sha(core_raw)
        or acquired["composite_receipt_sha256"]
        != observation.original_history.replay.receipt_sha256
        or receipt["original_pg_acquisition_sha256"]
        != observation.original_history.acquisition_receipt_sha256
        or receipt["original_financial_stream_sha256"]
        != observation.original_history.replay.receipt_sha256
        or receipt["current_owned_native_source_verified"] is not True
        or receipt["same_actual_writer_reader_database_verified"] is not True
        or receipt["original_db_prefix_replayed"] is not True
        or any(
            core[name] is not False
            for name in (
                "account_complete",
                "account_revision_published",
                "execution_authority",
            )
        )
    ):
        _deny("native_sampled_hwm_original_prefix_changed")
    if (
        peak["measured_prefix_complete"] is not True
        or any(
            peak[name] is not None
            for name in (
                "initial_account_peak",
                "unobserved_intermediate_peak",
                "historical_account_peak",
                "external_cashflow_adjustment",
                "cashflow_adjusted_drawdown",
            )
        )
        or peak["native_clock_issued"] is not False
        or peak["current_owner_issued"] is not False
    ):
        _deny("native_sampled_hwm_unknown_promoted")
    live = view["current"]
    current_raw = measurement._measure_verified_packet_v1(
        live.packet, live.reference, scope
    )
    current_data = json.loads(current_raw)
    identity = [
        current_data[name]
        for name in (
            "account_id",
            "main_uid",
            "account_level",
            "position_mode",
            "origin",
            "registration_region",
            "environment",
            "settlement_currency",
        )
    ]
    if peak["account_identity"] != identity:
        _deny("native_sampled_hwm_account_identity_changed")
    current_equity = measurement._rational(current_data["equity"], positive=True)
    historical_sample_peak = measurement._rational(peak["peak_equity"], positive=True)
    observed_peak = max(current_equity, historical_sample_peak)
    source = {
        "scope": peak["scope"],
        "index_through_sequence": core["through_sequence"],
        "index_head_sha256": core["index_head_sha256"],
        "original_checkpoint_sha256": receipt["original_checkpoint_sha256"],
        "historical_index_sampled_peak": peak,
        "current_measurement": current_data,
        "observed_peak_including_current": measurement._fraction(observed_peak),
        "observed_peak_source": "CURRENT_V7"
        if current_equity > historical_sample_peak
        else "ORIGINAL_INDEX",
        "source_manifest_sha256": receipt["source_manifest_sha256"],
        "history_source_bundle_sha256": facts.bundle_sha256,
        "history_native_proof_sha256": facts.proof_sha256,
        "current_source_reference": observed.reference_document(live.reference),
        "current_proof_envelope_sha256": live.proof_envelope_sha256,
        "current_native_proof_sha256": live.native_proof_sha256,
        "current_native_readback_sha256": live.native_readback_sha256,
        "current_custody_id": view["custody_id"],
        "owned_source_observation_sha256": sha(observation.receipt_json),
        "original_pg_acquisition_sha256": observation.original_history.acquisition_receipt_sha256,
        "financial_facts_sha256": sha(core_raw),
        "financial_stream_sha256": observation.original_history.replay.receipt_sha256,
        "original_lifetimes": receipt["original_lifetimes"],
        "original_request_expires_at": receipt["original_request_expires_at"],
    }
    # These values are NOT a statement that every historical clock is native.
    # Their exact raw witnesses remain bound by the original core/stream head.
    return source, receipt, view


def _remaining(stamp, receipt, view):
    now = utc_from_ns(stamp["utc_ns"])
    anchors = receipt["original_lifetimes"]
    remaining = min(
        (anchors["shorter_monotonic_deadline_ns"] - stamp["monotonic_ns"]) / 1e9,
        (
            _utc(datetime.fromisoformat(receipt["original_request_expires_at"])) - now
        ).total_seconds(),
        (_utc(view["current"].expires_at) - now).total_seconds(),
        (_utc(view["control"].state.lease_until) - now).total_seconds(),
    )
    if remaining <= 0:
        _deny("native_sampled_hwm_original_deadline_expired")
    return remaining


async def record_owned_native_sampled_hwm_v1(owner):
    """Return the existing exact custody readback containing positive samples.

    A success means this live owner calculated and durably retained the bound
    observed values. It is not an initial/full historical HWM, cashflow-adjusted
    drawdown, portfolio/account revision or transferable owner. Commit/readback
    uncertainty preserves evidence and never retries or returns a result.
    """
    binding = None
    result = None
    failure = None
    try:
        if len(_UNKNOWN_BINDINGS) >= 16:
            _deny("native_sampled_hwm_finalization_unknown")
        binding = _assembly()._take_native_hwm_binding_v1(owner, key=_SOURCE_KEY)
        factory, repository = _context(binding)
        facts, view, stamp, observation = await _assembly()._native_hwm_recheck_v1(
            binding, key=_SOURCE_KEY
        )
        source, receipt, live_view = _view(facts, view, stamp, observation)
        stable = canonical(source)
        initial_stamp = dict(stamp)
        async with asyncio.timeout(_remaining(stamp, receipt, live_view)):
            payload = {
                "schema_version": SCHEMA,
                "policy_sha256": POLICY_SHA256,
                "observed_source": source,
                "owner_sampled_at": utc_from_ns(stamp["utc_ns"]).isoformat(),
                "sampled_values_recorded": True,
                "current_source_owner_rechecked": True,
                "all_historical_native_clocks_verified": None,
                "initial_account_peak": None,
                "historical_account_peak": None,
                "external_cashflow_adjustment": None,
                "cashflow_adjusted_drawdown": None,
                "account_revision_issued": False,
                "execution_authority": False,
            }
            raw = canonical(payload)
            if not 0 < len(raw) <= MAX_RECORD_BYTES:
                _deny("native_sampled_hwm_record_bound")
            proof_pin = sha(canonical({"purpose": SCHEMA, "source": source}))
            # No alternate factory/repository is an argument to this API.
            if any(
                actual is not expected
                for actual, expected in zip(
                    _context(binding), (factory, repository), strict=True
                )
            ):
                _deny("native_sampled_hwm_original_context_changed")
            sealed = await repository.seal_bundle(
                bundle_raw=raw,
                expected_sha256=sha(raw),
                proof_envelope_sha256=proof_pin,
            )
            committed = custody._consume_runtime_bundle_custody_receipt(
                sealed,
                expected_bundle_sha256=sha(raw),
                expected_proof_envelope_sha256=proof_pin,
            )
            facts, view, stamp, observation = await _assembly()._native_hwm_recheck_v1(
                binding, key=_SOURCE_KEY
            )
            after, receipt, live_view = _view(facts, view, stamp, observation)
            if (
                canonical(after) != stable
                or stamp["utc_ns"] < initial_stamp["utc_ns"]
                or stamp["monotonic_ns"] < initial_stamp["monotonic_ns"]
            ):
                _deny("native_sampled_hwm_original_source_changed")
            _remaining(stamp, receipt, live_view)
            reopened = await repository.read_retained_bundle(
                bundle_id=committed.bundle_id,
                expected_sha256=sha(raw),
                proof_envelope_sha256=proof_pin,
            )
            if (
                type(committed) is not custody.RetainedRuntimeBundleReadback
                or type(reopened) is not custody.RetainedRuntimeBundleReadback
                or committed.bundle_raw != raw
                or reopened.bundle_raw != raw
                or committed.bundle_sha256 != sha(raw)
                or reopened.bundle_sha256 != sha(raw)
                or committed.proof_envelope_sha256 != proof_pin
                or reopened.proof_envelope_sha256 != proof_pin
                or committed.bundle_id != reopened.bundle_id
                or committed.recorded_at != reopened.recorded_at
                or reopened.database_readback_at < committed.database_readback_at
                or reopened.readback_transaction_id == committed.readback_transaction_id
            ):
                _deny("native_sampled_hwm_committed_readback_changed")
            facts, view, stamp, observation = await _assembly()._native_hwm_recheck_v1(
                binding, key=_SOURCE_KEY
            )
            final, receipt, live_view = _view(facts, view, stamp, observation)
            if canonical(final) != stable or any(
                actual is not expected
                for actual, expected in zip(
                    _context(binding), (factory, repository), strict=True
                )
            ):
                _deny("native_sampled_hwm_original_source_changed")
            _remaining(stamp, receipt, live_view)
            # Existing custody DTO only: no new claim/capability/stamp type.
            result = committed
    except BaseException as error:  # noqa: BLE001 -- defer rethrow until source release; preserve cancellation.
        failure = error
    finally:
        if binding is not None:
            try:
                _assembly()._release_native_hwm_binding_v1(binding, key=_SOURCE_KEY)
            except BaseException as release_error:  # noqa: BLE001 -- retain unknown binding before sanitized rethrow; never retry release.
                # No retry or unchecked garbage-collection release. Retain the
                # opaque binding privately; no result may escape uncertainty.
                _UNKNOWN_BINDINGS.append(binding)
                if isinstance(
                    release_error,
                    (asyncio.CancelledError, KeyboardInterrupt, SystemExit),
                ):
                    failure = release_error
                elif failure is None:
                    failure = NativeSampledHWMError(
                        "native_sampled_hwm_finalization_unknown"
                    )
    if failure is not None:
        if isinstance(failure, asyncio.CancelledError):
            raise asyncio.CancelledError() from None
        if isinstance(failure, (KeyboardInterrupt, SystemExit)):
            raise type(failure)() from None
        if (
            type(failure) is NativeSampledHWMError
            and len(failure.args) == 1
            and type(failure.args[0]) is str
            and failure.args[0] in ERROR_CODES
        ):
            raise NativeSampledHWMError(failure.args[0]) from None
        _deny("native_sampled_hwm_write_or_readback_unknown")
    if type(result) is not custody.RetainedRuntimeBundleReadback:
        _deny("native_sampled_hwm_write_or_readback_unknown")
    return result
