"""One actual original-prefix database acquisition with global financial replay.

Reuse DB0052 and the unchanged original native reader's Fence, strict catalogs,
backend checks and width1 page producer. This is an inert read observation;
caller factory/role/request/position DTOs cannot become a collector owner.
"""

import asyncio
import hashlib
from contextlib import aclosing
from dataclasses import dataclass, replace
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.repositories import account_history_original_native_read_v1 as native
from app.domain import account_history_partition_v1 as records
from app.trade_qualification import account_capture_journal as journal
from app.trade_qualification import account_history_global_financial_v2 as financial
from app.trade_qualification import account_history_original_stream_v1 as stream
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
class OriginalHistoryFinancialPgReadObservationV3:
    replay: stream.OriginalHistoryFinancialStreamReplayV3
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


async def replay_original_history_financial_from_pg_v3(
    session_factory,
    *,
    genesis_request,
    expected_checkpoint_sha256,
    approved_collector_role_oid,
    expected_financial_policy_sha256,
    position_source=None,
):
    """Read all CURRENT frames once, finish real replay, then commit and close.

    checked_at is measured by the SAME original Fence after all RPCs and final
    catalog/backend checks. It is never supplied by a caller or chosen as a
    future cutoff. No clock, expiry, original B1 or source timestamp is changed.
    Upstream credential custody/finality/HWM and account issuance stay separate.
    """
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
    accumulator = financial.OriginalHistoryFinancialAccumulatorV2(
        scope=LedgerScope(
            environment=request.scope.environment,
            account_id=request.scope.account_id,
            settlement_currency=request.scope.settlement_currency,
        ),
        expected_policy_sha256=financial.POLICY_SHA256,
    )
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
    }
    state["membership"].update(journal.canonical(request_identity))
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
            for statement in (
                "SET TRANSACTION ISOLATION LEVEL READ COMMITTED",
                "SET LOCAL search_path TO pg_catalog, public, pg_temp",
                "SET LOCAL TIME ZONE 'UTC'",
                "SET LOCAL lock_timeout='1000ms'",
                "SET LOCAL statement_timeout='10000ms'",
            ):
                await session.execute(text(statement))
                fence.check()
            before = await native._catalog(session, fence, approved_collector_role_oid)
            state["backend"] = await native._backend(session, fence)
            engine = stream._replay_engine(
                request.scope,
                request.through,
                request.head,
                expected_checkpoint_sha256,
                stream.POLICY_SHA256,
                _financial_v2=accumulator,
            )
            next(engine)
            # Keep the original page context alive through the real V1 checks
            # and CURRENT financial hook. Close/join this producer explicitly.
            async with aclosing(
                native._pages(session, request, fence, state)
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
            after = await native._catalog(session, fence, approved_collector_role_oid)
            if (
                before != after
                or state["backend"] != await native._backend(session, fence)
                or session.get_bind() is not bind
            ):
                _deny("original_pg_catalog_changed")
            checked_at = fence.check()
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
            fence.check()
            await session.commit()
            fence.check()
            close_attempted = True
            await session.close()
            closed_at = fence.check()
            receipt = journal.canonical(
                {
                    "schema_version": "ctcc.original_history_financial_pg_read_observation.v3",
                    **request_identity,
                    "composite_receipt_sha256": replay.receipt_sha256,
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
            observation = OriginalHistoryFinancialPgReadObservationV3(
                replay,
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
