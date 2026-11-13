"""One actual original-prefix database acquisition with global financial replay.

Use the original reader Fence, source backend checks and width1 page producer.
The exact DB0055 catalog and the owned DB0056 catalog remain separate. This is
an inert observation. Factory, role, request and position DTOs confer no authority.
"""

import asyncio
import hashlib
from contextlib import aclosing
from dataclasses import dataclass, replace
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.repositories import account_history_original_native_read_v1 as native
from app.database.repositories import (
    collector_current_0055_multirole_catalog_v1 as current_catalog,
)
from app.domain import account_history_partition_v1 as records
from app.domain import controlled_history_current55_schema_v2 as controlled
from app.trade_qualification import account_capture_journal as journal
from app.trade_qualification import account_history_original_stream_v1 as stream
from app.trade_qualification import account_original_raw_proof_bank_v1 as raw_bank
from app.trade_qualification.reservations import LedgerScope

ERROR_CODES = native.ERROR_CODES | frozenset(
    {
        "original_financial_pg_policy_mismatch",
        "original_financial_pg_component_changed",
    }
)


class OriginalHistoryFinancialPgReadError(ValueError):
    """Fixed errors only; no original SQL/account/transport exception text."""


def _deny(code):
    if code not in ERROR_CODES:
        code = "original_pg_read_uncertain"
    raise OriginalHistoryFinancialPgReadError(code) from None


def _native_code(error):
    if (
        type(error) is native.OriginalHistoryPgReadError
        and len(error.args) == 1
        and type(error.args[0]) is str
        and error.args[0] in native.ERROR_CODES
    ):
        return error.args[0]
    return "original_pg_read_uncertain"


@dataclass(frozen=True, slots=True, repr=False)
class OriginalAccountRawProofBankPgReadObservationV1:
    replay: stream.OriginalHistoryFinancialStreamReplayV3
    raw_proof_bank: raw_bank.OriginalAccountRawProofBankValuesV1
    acquisition_receipt_json: bytes
    observed_frames: int
    first_request_started_at: datetime
    last_rpc_received_at: datetime
    measured_validation_at: datetime
    session_closed_at: datetime

    @property
    def acquisition_receipt_sha256(self):
        return journal.digest(self.acquisition_receipt_json)

    @property
    def native_history_prefix_verified(self):
        return False

    @property
    def account_complete(self):
        return False

    @property
    def account_revision_published(self):
        return False

    @property
    def execution_authority(self):
        return False

    @property
    def admission(self):
        return "DENY"


async def _replay_original_history_raw_bank_from_pg(
    session_factory,
    *,
    genesis_request,
    expected_checkpoint_sha256,
    approved_collector_role_oid,
    lineage_session_factory,
    expected_financial_policy_sha256,
    expected_raw_bank_policy_sha256,
    _api_version,
    expected_api_population_policy_sha256,
    position_source=None,
    _owned_bank_0056=None,
):
    """Read all CURRENT frames once, finish real replay, then commit and close.

    checked_at is measured by the SAME original Fence after all RPCs and final
    catalog/backend checks. It is never supplied by a caller or chosen as a
    future cutoff. No clock, expiry, original B1 or source timestamp is changed.
    Upstream credential custody/finality/HWM and account issuance stay separate.
    """
    if (
        type(_api_version) is not int
        or _api_version not in {1, 2}
        or (_owned_bank_0056 is not None and _api_version != 2)
    ):
        _deny("original_financial_pg_policy_mismatch")
    if (_api_version == 1 and expected_api_population_policy_sha256 is not None) or (
        _api_version == 2
        and (
            type(expected_api_population_policy_sha256) is not str
            or expected_api_population_policy_sha256
            != raw_bank.API_POPULATION_POLICY_SHA256_V2
        )
    ):
        _deny("original_financial_pg_policy_mismatch")
    try:
        native._check_request(
            genesis_request, expected_checkpoint_sha256, approved_collector_role_oid
        )
    except native.OriginalHistoryPgReadError as error:
        _deny(_native_code(error))
    if (
        type(session_factory) is not async_sessionmaker
        or session_factory.class_ is not AsyncSession
    ):
        _deny("original_pg_factory_invalid")
    # Copy validated original inputs once; later external object mutation cannot
    # rebind request scope/revisions/expiry while this transaction awaits IO.
    request = replace(
        genesis_request,
        scope=records.HistoryPartitionScopeV1(*records._scope(genesis_request.scope)),
    )
    try:
        fence = native._Fence(request.expires_at)
    except native.OriginalHistoryPgReadError as error:
        _deny(_native_code(error))
    policy_raw = stream._financial_stream_policy_bytes_v3()
    if (
        type(expected_financial_policy_sha256) is not str
        or journal.digest(policy_raw) != expected_financial_policy_sha256
    ):
        _deny("original_financial_pg_policy_mismatch")
    if (
        type(expected_raw_bank_policy_sha256) is not str
        or expected_raw_bank_policy_sha256 != raw_bank.POLICY_SHA256
    ):
        _deny("original_financial_pg_policy_mismatch")
    scope = LedgerScope(
        environment=request.scope.environment,
        account_id=request.scope.account_id,
        settlement_currency=request.scope.settlement_currency,
    )
    api_population = None
    if _api_version == 2:
        api_population = raw_bank.OriginalAccountApiPopulationAccumulatorV2(scope=scope)
        proof_bank = api_population._original
    else:
        proof_bank = raw_bank.OriginalAccountRawProofBankAccumulatorV1(scope=scope)
    accumulator = proof_bank._fold  # Exact original V2 class; old stream gate stays.
    engine = None
    state = {
        "backend": None,
        "first_started": None,
        "last_received": None,
        "frames": 0,
        "membership": hashlib.sha256(),
    }
    request_identity = {
        "scope_sha256": journal.digest(
            journal.canonical(records._scope(request.scope))
        ),
        "through_sequence": request.through,
        "original_head_sha256": request.head,
        "original_checkpoint_sha256": expected_checkpoint_sha256,
        "control_revision": request.control,
        "account_revision": request.account,
        "ledger_revision": request.ledger,
        "original_expires_at": request.expires_at.isoformat(),
        "approved_collector_role_oid": approved_collector_role_oid,
        "financial_stream_policy_sha256": expected_financial_policy_sha256,
        "raw_bank_policy_sha256": expected_raw_bank_policy_sha256,
    }
    if _owned_bank_0056 is not None:
        request_identity["database_head"] = "0056"
    if _api_version == 2:
        request_identity["api_population_policy_sha256"] = (
            expected_api_population_policy_sha256
        )
    state["membership"].update(journal.canonical(request_identity))

    async def actual_catalog(session):
        if _owned_bank_0056 is None:
            return await current_catalog.verify_actual_raw_history_catalog_v1(
                session,
                fence,
                approved_collector_role_oid,
                lineage_session_factory,
                session_factory,
            )
        from app.trade_qualification.native_account_risk_assembly_v1 import (
            _verify_owned_original_bank_catalog_0056_v6,
        )

        return await _verify_owned_original_bank_catalog_0056_v6(
            _owned_bank_0056,
            session,
            fence,
            request=request,
            checkpoint=expected_checkpoint_sha256,
            approved_reader_oid=approved_collector_role_oid,
            frame_factory=session_factory,
            writer_factory=lineage_session_factory,
        )

    session, close_attempted = None, False
    try:
        async with asyncio.timeout_at(fence.loop_deadline):
            session = session_factory()
            if (
                type(session) is not AsyncSession
                or session.get_bind().dialect.name != "postgresql"
            ):
                _deny("original_pg_session_invalid")
            bind = session.get_bind()
            fence.check()
            await session.begin()
            fence.check()
            if _owned_bank_0056 is not None:
                await session.execute(text("SET TRANSACTION READ WRITE"))
                fence.check()
            for statement in (
                "SET TRANSACTION ISOLATION LEVEL READ COMMITTED",
                "SET LOCAL search_path TO pg_catalog, public, pg_temp",
                "SET LOCAL TIME ZONE 'UTC'",
                "SET LOCAL lock_timeout='1000ms'",
                "SET LOCAL statement_timeout='10000ms'",
            ):
                await session.execute(text(statement))
                fence.check()
            before = await actual_catalog(session)
            state["backend"] = await native._backend(session, fence)
            engine = stream._replay_engine(
                request.scope,
                request.through,
                request.head,
                expected_checkpoint_sha256,
                stream.POLICY_SHA256,
                _financial_v2=accumulator,
                _raw_bank_v1=proof_bank,
                _api_population_v2=api_population,
            )
            next(engine)
            # Keep the original page context alive through the real V1 checks
            # and CURRENT financial hook. Close/join this producer explicitly.
            async with aclosing(
                _controlled_current55_pages_v2(session, request, fence, state)
            ) as producer:
                async for page in producer:
                    fence.check()
                    if type(page) is not stream.OriginalHistoryPageV1:
                        _deny("original_pg_read_uncertain")
                    engine.send(page)
                    fence.check()
                original_replay = stream._finish_replay_engine(engine)
                fence.check()
            if (
                state["frames"] != request.through
                or state["backend"] is None
                or session.get_bind() is not bind
            ):
                _deny("original_pg_incomplete")
            after = await actual_catalog(session)
            if (
                before != after
                or state["backend"] != await native._backend(session, fence)
                or session.get_bind() is not bind
            ):
                _deny("original_pg_catalog_changed")
            checked_at = fence.check()
            proof_bank._freeze_verified_raw_state_v1()
            fence.check()
            replay = stream._finish_financial_stream_v3(
                original_replay,
                accumulator,
                request.scope,
                request.through,
                request.head,
                expected_checkpoint_sha256,
                checked_at,
                position_source,
                policy_raw,
                asyncio.current_task(),
            )
            if type(replay) is not stream.OriginalHistoryFinancialStreamReplayV3:
                _deny("original_financial_pg_component_changed")
            raw_values = proof_bank._finish_after_original_stream_v1(
                replay.financial_facts,
                expected_through=request.through,
                expected_head=request.head,
                checked_at=checked_at,
            )
            fence.check()
            api_values = None
            if _api_version == 2:
                api_values = api_population._finish_after_original_stream_v2(
                    raw_values,
                    expected_through=request.through,
                    expected_head=request.head,
                    checked_at=checked_at,
                )
                fence.check()
            await session.commit()
            fence.check()
            close_attempted = True
            await session.close()
            closed_at = fence.check()
            receipt = journal.canonical(
                {
                    "schema_version": (
                        "ctcc.current55.original_history_raw_proof_bank_pg_read_observation.v1"
                        if _api_version == 1
                        else "ctcc.current56.original_history_api_population_pg_read_observation.v2"
                        if _owned_bank_0056 is not None
                        else "ctcc.current55.original_history_api_population_pg_read_observation.v2"
                    ),
                    **request_identity,
                    **(
                        {
                            "api_population_receipt_sha256": journal.digest(
                                api_values.receipt_json
                            )
                        }
                        if _api_version == 2
                        else {}
                    ),
                    "composite_receipt_sha256": replay.receipt_sha256,
                    "raw_bank_receipt_sha256": journal.digest(raw_values.receipt_json),
                    "raw_source_sha256": raw_values.raw_source_sha256,
                    "original_stream_receipt_sha256": replay.original_replay.receipt_sha256,
                    "global_financial_receipt_sha256": journal.digest(
                        replay.financial_facts.receipt_json
                    ),
                    "acquisition_membership_sha256": state["membership"].hexdigest(),
                    "catalog_sha256": journal.digest(before),
                    "backend_pid": state["backend"][0],
                    "transaction_id": state["backend"][1],
                    "observed_frames": state["frames"],
                    "first_request_started_at": state["first_started"].isoformat(),
                    "last_rpc_received_at": state["last_received"].isoformat(),
                    "measured_validation_at": checked_at.isoformat(),
                    "session_closed_at": closed_at.isoformat(),
                    "original_db_prefix_replayed": True,
                    "upstream_credential_custody_verified": False,
                    "native_history_prefix_verified": False,
                    "account_complete": False,
                    "account_revision_published": False,
                    "execution_authority": False,
                    "admission": "DENY",
                }
            )
            if len(receipt) > stream.observed.MAX_RECEIPT_BYTES:
                _deny("original_financial_pg_component_changed")
            if _api_version == 1:
                observation = OriginalAccountRawProofBankPgReadObservationV1(
                    replay,
                    raw_values,
                    receipt,
                    state["frames"],
                    state["first_started"],
                    state["last_received"],
                    checked_at,
                    closed_at,
                )
            else:
                observation = OriginalAccountApiPopulationPgReadObservationV2(
                    replay,
                    api_values,
                    receipt,
                    state["frames"],
                    state["first_started"],
                    state["last_received"],
                    checked_at,
                    closed_at,
                )
        fence.check()
        return observation
    except asyncio.CancelledError:
        raise asyncio.CancelledError() from None
    except OriginalHistoryFinancialPgReadError:
        raise
    except native.OriginalHistoryPgReadError as error:
        _deny(_native_code(error))
    except Exception:  # noqa: BLE001 -- raw SQL/source errors stay private
        _deny("original_pg_read_uncertain")
    finally:
        accumulator.cancel()
        proof_bank.cancel()
        if api_population is not None:
            api_population.cancel()
        if engine is not None:
            engine.close()
        if session is not None and not close_attempted:
            close_attempted = True
            try:
                async with asyncio.timeout(native.FAILED_READ_CLOSE_SECONDS):
                    await session.close()
            except asyncio.CancelledError:
                raise asyncio.CancelledError() from None
            except Exception:  # noqa: BLE001 -- uncertainty cannot return a receipt
                _deny("original_pg_cleanup_uncertain")


@dataclass(frozen=True, slots=True, repr=False)
class OriginalAccountApiPopulationPgReadObservationV2:
    replay: stream.OriginalHistoryFinancialStreamReplayV3
    api_population: raw_bank.OriginalAccountApiPopulationValuesV2
    acquisition_receipt_json: bytes
    observed_frames: int
    first_request_started_at: datetime
    last_rpc_received_at: datetime
    measured_validation_at: datetime
    session_closed_at: datetime

    @property
    def raw_proof_bank(self):
        return self.api_population.original_raw_bank

    @property
    def acquisition_receipt_sha256(self):
        return journal.digest(self.acquisition_receipt_json)

    @property
    def native_history_prefix_verified(self):
        return False

    @property
    def account_complete(self):
        return False

    @property
    def account_revision_published(self):
        return False

    @property
    def execution_authority(self):
        return False

    @property
    def admission(self):
        return "DENY"


async def replay_original_history_raw_proof_bank_from_pg_v1(
    session_factory,
    *,
    genesis_request,
    expected_checkpoint_sha256,
    approved_collector_role_oid,
    lineage_session_factory,
    expected_financial_policy_sha256,
    expected_raw_bank_policy_sha256,
    position_source=None,
):
    """The unchanged V1 data contract; no V2 source producer is selected."""
    return await _replay_original_history_raw_bank_from_pg(
        session_factory,
        genesis_request=genesis_request,
        expected_checkpoint_sha256=expected_checkpoint_sha256,
        approved_collector_role_oid=approved_collector_role_oid,
        lineage_session_factory=lineage_session_factory,
        expected_financial_policy_sha256=expected_financial_policy_sha256,
        expected_raw_bank_policy_sha256=expected_raw_bank_policy_sha256,
        _api_version=1,
        expected_api_population_policy_sha256=None,
        position_source=position_source,
    )


async def replay_original_history_api_population_from_pg_v2(
    session_factory,
    *,
    genesis_request,
    expected_checkpoint_sha256,
    approved_collector_role_oid,
    lineage_session_factory,
    expected_financial_policy_sha256,
    expected_raw_bank_policy_sha256,
    expected_api_population_policy_sha256,
    position_source=None,
):
    """Same controlled PG acquisition; derive V2 facts at every real CURRENT hook.

    This selects a data contract, not an account/reader/credential capability.
    Actual raw/writer catalogs, fixed source request/revisions/expiry, original
    page producer and final independent catalog/backend checks remain required.
    """
    return await _replay_original_history_raw_bank_from_pg(
        session_factory,
        genesis_request=genesis_request,
        expected_checkpoint_sha256=expected_checkpoint_sha256,
        approved_collector_role_oid=approved_collector_role_oid,
        lineage_session_factory=lineage_session_factory,
        expected_financial_policy_sha256=expected_financial_policy_sha256,
        expected_raw_bank_policy_sha256=expected_raw_bank_policy_sha256,
        _api_version=2,
        expected_api_population_policy_sha256=expected_api_population_policy_sha256,
        position_source=position_source,
    )


async def _replay_owned_original_history_api_population_from_pg_0056_v1(
    binding,
    session_factory,
    *,
    genesis_request,
    expected_checkpoint_sha256,
    approved_collector_role_oid,
    lineage_session_factory,
    expected_financial_policy_sha256,
    expected_raw_bank_policy_sha256,
    expected_api_population_policy_sha256,
    position_source,
):
    """Use the genuine V6 BANK phase with DB0056 verification in the frame transaction."""
    if binding is None:
        _deny("original_pg_read_uncertain")
    return await _replay_original_history_raw_bank_from_pg(
        session_factory,
        genesis_request=genesis_request,
        expected_checkpoint_sha256=expected_checkpoint_sha256,
        approved_collector_role_oid=approved_collector_role_oid,
        lineage_session_factory=lineage_session_factory,
        expected_financial_policy_sha256=expected_financial_policy_sha256,
        expected_raw_bank_policy_sha256=expected_raw_bank_policy_sha256,
        _api_version=2,
        expected_api_population_policy_sha256=expected_api_population_policy_sha256,
        position_source=position_source,
        _owned_bank_0056=binding,
    )


async def _controlled_current55_pages_v2(session, genesis_request, fence, state):
    after, previous, previous_page = 0, None, None
    while after < genesis_request.through:
        request = replace(genesis_request, after=after, previous=previous)
        started = fence.check()
        value = await native._execute_one(
            session, controlled.FRAME_SQL, fence, native._parameters(request)
        )
        received = fence.check()
        from app.trade_qualification import account_original_page_memo_v1 as page_memo

        with page_memo._native_page_context(fence, request, started, received):
            decoded = native.wire.decode_original_frame_wire_v1(
                value, request=request, client_received_at=received
            )
            # Frozen DB0052 uses clock_timestamp() AFTER exporting the original
            # rows, not transaction_timestamp(). Never rewrite any timestamp.
            if not started <= decoded.db_observed_at <= received:
                _deny("original_pg_request_causality")
            backend = (decoded.backend_pid, decoded.transaction_id)
            if state["first_started"] is None:
                state["first_started"] = started
            if state["backend"] != backend:
                _deny("original_pg_backend_changed")
            page = native.wire.original_frame_width1_page_v1(
                decoded, page_index=after, previous_page_sha256=previous_page
            )
            page_sha256 = stream.original_history_page_sha256_v1(page)
            state["membership"].update(
                journal.canonical(
                    {
                        "sequence": decoded.frame.batch.sequence,
                        "event_sha256": decoded.frame.batch.event_sha256,
                        "page_sha256": page_sha256,
                        "request_started_at": started.isoformat(),
                        "db_observed_at": decoded.db_observed_at.isoformat(),
                        "client_received_at": received.isoformat(),
                        "backend_pid": decoded.backend_pid,
                        "transaction_id": decoded.transaction_id,
                    }
                )
            )
            fence.check()
            yield page
            # A yielded page is accepted by the in-task pure replay before this
            # cursor advances. B3 predecessor and page predecessor stay distinct.
            fence.check()
        after = decoded.frame.batch.sequence
        previous = decoded.frame.batch.event_sha256
        previous_page = page_sha256
        state["frames"], state["last_received"] = after, received
        # Do not accumulate raw frames or decoded observations across pages.
        del value, decoded, page
