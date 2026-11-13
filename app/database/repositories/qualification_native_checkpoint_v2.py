"""Current55 finite-claims DATA admission for existing ledger checkpoints.

LedgerScopeState already contains only revisions, claims SHA and active
receipts. It is reused unchanged; no legacy portfolio snapshot/history seed is
constructed. This read never grants eligibility or mints a source/account cap.
"""

import hashlib
import json
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import object_session

from app.core import native_locked_demo_control_0056_v1 as locked
from app.core.native_role_witness_0056_v1 import RestrictedNativeRoles0056WitnessV1
from app.database.models.account_issued_revision import (
    DemoCollectorIssuedAccountRevision,
)
from app.database.models.qualification_ledger import QualificationAccountScope
from app.database.repositories import (
    collector_current_0055_multirole_catalog_v1 as catalog55,
)
from app.database.repositories.qualification_ledger import QualificationLedgerRepository
from app.domain.source_primitives import canonical
from app.trade_qualification import account_ledger_claims_v2 as codec
from app.trade_qualification import reservations as ledger

_CHECKPOINT_FENCE = text(
    "LOCK TABLE public.qualification_account_scopes, public.qualification_reservations, "
    "public.demo_collector_issued_account_revisions IN ACCESS SHARE MODE NOWAIT"
)


def _deny(code):
    raise ledger.QualificationLedgerError(code) from None


def _finite_payload(raw):
    # This selects a DATA decoder, never a source/head/permission contract.
    if type(raw) is not str or not 0 < len(raw) <= codec.MAX_CLAIMS_BYTES:
        return False
    try:
        body = json.loads(raw)
        return (
            type(body) is dict
            and body.get("schema_version") == "ctcc.account_ledger_claims.v2"
            and type(body.get("claims")) is dict
            and body["claims"].get("payload_schema")
            == "ctcc.account_ledger_claims.finite_api_asof.v1"
        )
    except (ValueError, TypeError, RecursionError):
        return False


@dataclass(frozen=True, slots=True, repr=False)
class _HeldQualificationCheckpoint0056V1:
    selected: object
    session: AsyncSession
    control: locked.NativeLockedDemoControl0056V1
    _held: locked.HeldNativeWriterControl0056V1

    def check(self) -> None:
        self._held.check_held()

    async def recheck_catalog(self) -> None:
        self.check()
        fresh = await locked.writer56.observe_restricted_readwrite_writer_0056_v1(
            self.session
        )
        self.check()
        if fresh != self.control.writer_catalog:
            _deny("ledger56_checkpoint_catalog_changed")

    async def recheck_control(self):
        scope = self.control.control.state.scope
        self.check()
        row = await locked.DemoControlRepository._locked(self.session, scope)
        self.check()
        await self.session.refresh(row)
        self.check()
        state, event = await locked.DemoControlRepositoryV2._snapshot(self.session, row)
        self.check()
        return state, event


_ACTIVE_HELD_CHECKPOINTS_0056 = {}


@asynccontextmanager
async def held_qualification_checkpoint_0056_v1(
    selected, session, control_scope, *, roles
):
    """Retain the exact restricted Control and schema within this transaction."""
    if type(roles) is not RestrictedNativeRoles0056WitnessV1:
        _deny("ledger56_checkpoint_original_roles_required")
    async with locked.held_native_writer_control_0056_v1(
        selected,
        session,
        control_scope,
        roles=roles,
        purpose=locked.NativeWriterControlPurpose0056.PREPARE_OBSERVATION,
    ) as held:
        engine = selected.writer_factory.kw["bind"].sync_engine
        if (
            session.get_bind(clause=_CHECKPOINT_FENCE) is not engine
            or session.get_bind(mapper=QualificationAccountScope) is not engine
            or session.get_bind(mapper=DemoCollectorIssuedAccountRevision) is not engine
        ):
            _deny("ledger56_checkpoint_original_writer_required")
        await session.execute(_CHECKPOINT_FENCE)
        held.check_held()
        witness = _HeldQualificationCheckpoint0056V1(
            selected, session, held.observation, held
        )
        await witness.recheck_catalog()
        _ACTIVE_HELD_CHECKPOINTS_0056[id(witness)] = witness
        try:
            yield witness
            await witness.recheck_catalog()
        finally:
            _ACTIVE_HELD_CHECKPOINTS_0056.pop(id(witness), None)


def check_held_qualification_checkpoint_0056_v1(
    repository, session, scope, row, witness
) -> None:
    if (
        type(witness) is not _HeldQualificationCheckpoint0056V1
        or _ACTIVE_HELD_CHECKPOINTS_0056.get(id(witness)) is not witness
        or witness.session is not session
        or type(repository) is not QualificationLedgerRepository
        or repository.session_factory is not witness.selected.writer_factory
        or type(scope) is not ledger.LedgerScope
        or type(row) is not QualificationAccountScope
        or object_session(row) is not session.sync_session
        or (scope.environment, scope.account_id)
        != (
            witness.control.control.state.scope.environment,
            witness.control.control.state.scope.account_id,
        )
        or (row.environment, row.account_id, row.settlement_currency)
        != (scope.environment, scope.account_id, scope.settlement_currency)
    ):
        _deny("ledger56_checkpoint_original_scope_required")
    witness.check()


async def validate_held_positive_checkpoint_0056_v1(
    repository, session, scope, row, witness
):
    def check():
        check_held_qualification_checkpoint_0056_v1(
            repository, session, scope, row, witness
        )
        return repository._now(row)

    check()
    if not _finite_payload(row.claims_json):
        if repository._claims(row).scope != scope:
            _deny("ledger_claim_scope_mismatch")
        return
    await _validate_held_finite_checkpoint_rows(session, scope, row, check=check)
    check()


async def validate_positive_checkpoint_claims_v2(repository, session, scope, row):
    """Validate actual historical row DATA, with no revision refresh/authority.

    The native caller retains its exact initial revisions or private own-chain
    transition across this read. A historical issued row is not made current
    evidence, and its old expiry/Arm is not reused as an eligibility permit.
    """
    if not _finite_payload(row.claims_json):
        if QualificationLedgerRepository._claims(row).scope != scope:
            _deny("ledger_claim_scope_mismatch")
        return
    ledger.checked_bootstrap(scope, ledger.LedgerScope)
    if (
        type(repository) is not QualificationLedgerRepository
        or type(session) is not AsyncSession
        or not session.in_transaction()
        or session.get_bind().dialect.name != "postgresql"
        or type(row) is not QualificationAccountScope
        or (row.environment, row.account_id, row.settlement_currency)
        != (scope.environment, scope.account_id, scope.settlement_currency)
        or type(row.account_revision) is not int
        or type(row.ledger_revision) is not int
        or not 0 < row.account_revision <= row.ledger_revision < 2**63
    ):
        _deny("ledger55_native_finite_checkpoint_context_required")

    def check():
        # Existing native callers supply their fixed owned clock, whose sample
        # also replays original context/lease. No clock/callback is an argument
        # to this data boundary.
        at = repository.clock()
        if type(at) is not datetime or at.tzinfo is not UTC or row.updated_at > at:
            _deny("ledger55_finite_checkpoint_clock_invalid")
        return at

    check()
    for statement in (
        "SET LOCAL search_path TO pg_catalog, public, pg_temp",
        "SET LOCAL TIME ZONE 'UTC'",
        "SET LOCAL synchronous_commit='on'",
        "SET LOCAL lock_timeout='1000ms'",
        "SET LOCAL statement_timeout='1000ms'",
    ):
        await session.execute(text(statement))
        check()
    observation = await catalog55.verify_actual_issuer_catalog_v2(session)
    check()
    if (
        observation["database_head"] != "0055"
        or observation["native_baseline_admission"] is not True
    ):
        _deny("ledger55_finite_checkpoint_catalog_denied")
    await _validate_held_finite_checkpoint_rows(session, scope, row, check=check)


async def _validate_held_finite_checkpoint_rows(session, scope, row, *, check):
    """Validate original history after the caller held its actual catalog fence."""
    raw = row.claims_json.encode("utf-8")
    claims = codec.decode_finite_account_ledger_claims_v1(raw)
    if (
        codec.encode_finite_account_ledger_claims_v1(claims) != raw
        or claims.scope != scope
        or hashlib.sha256(raw).hexdigest() != row.claims_sha256
    ):
        _deny("ledger55_finite_checkpoint_canonical_claims_changed")
    values = tuple(
        (
            await session.scalars(
                select(DemoCollectorIssuedAccountRevision)
                .where(
                    DemoCollectorIssuedAccountRevision.environment == scope.environment,
                    DemoCollectorIssuedAccountRevision.account_id == scope.account_id,
                    DemoCollectorIssuedAccountRevision.settlement_currency
                    == scope.settlement_currency,
                    DemoCollectorIssuedAccountRevision.account_revision
                    == row.account_revision,
                )
                .limit(2)
            )
        ).all()
    )
    now = check()
    if len(values) != 1 or type(values[0]) is not DemoCollectorIssuedAccountRevision:
        _deny("ledger55_finite_checkpoint_issued_row_missing_or_conflict")
    issued = values[0]
    if type(issued.proof_json) is not str:
        _deny("ledger55_finite_checkpoint_proof_type_invalid")
    proof_raw = issued.proof_json.encode("utf-8")
    if not 1 <= len(proof_raw) <= 1024 * 1024:
        _deny("ledger55_finite_checkpoint_proof_bound")
    try:
        proof = json.loads(proof_raw)
        if type(proof) is not dict or canonical(proof) != proof_raw:
            _deny("ledger55_finite_checkpoint_proof_canonical_invalid")
        sampled_at = datetime.fromisoformat(proof["owner_sampled_at"])
        original_expires_at = datetime.fromisoformat(proof["original_expires_at"])
    except (
        ValueError,
        TypeError,
        KeyError,
        OverflowError,
        RecursionError,
        UnicodeError,
    ):
        _deny("ledger55_finite_checkpoint_proof_canonical_invalid")
    expected = {
        "schema_version",
        "scope",
        "claims_sha256",
        "source_lineage_sha256",
        "hwm_custody_id",
        "hwm_bundle_sha256",
        "previous_account_revision",
        "previous_ledger_revision",
        "account_revision",
        "ledger_revision",
        "control_revision",
        "control_event_sha256",
        "control_state_sha256",
        "db_role_oid",
        "db_backend_pid",
        "db_transaction_id",
        "owner_sampled_at",
        "original_expires_at",
        "proofs",
        "execution_authority",
    }
    proof_names = (
        "registration",
        "current_identity",
        "history_prefix",
        "product_scope",
        "history_coverage",
        "cashflow_and_funding_finality",
        "loss_streak_seed",
        "unchanged_window_drawdown",
        "inventory_metadata",
        "protection",
        "local_ledger",
        "current_portfolio",
    )
    if (
        set(proof) != expected
        or type(proof.get("proofs")) is not dict
        or set(proof["proofs"]) != set(proof_names)
        or any(
            type(proof["proofs"][name]) is not str
            or len(proof["proofs"][name]) != 64
            or any(char not in "0123456789abcdef" for char in proof["proofs"][name])
            for name in proof_names
        )
        or sampled_at.tzinfo is not UTC
        or original_expires_at.tzinfo is not UTC
        or type(issued.db_recorded_at) is not datetime
        or issued.db_recorded_at.tzinfo is not UTC
        or original_expires_at != issued.original_expires_at
        or not sampled_at <= issued.db_recorded_at < original_expires_at
    ):
        _deny("ledger55_finite_checkpoint_proof_columns_invalid")
    if (
        issued.claims_json.encode("utf-8") != raw
        or issued.claims_sha256 != row.claims_sha256
        or issued.issuer_sha256 != hashlib.sha256(proof_raw).hexdigest()
        or issued.account_revision != row.account_revision
        or issued.account_revision != issued.previous_account_revision + 1
        or issued.ledger_revision != issued.previous_ledger_revision + 1
        # This is a historical-shape check, NOT an expected-revision allowance.
        # The current source captures exact row.ledger_revision in its metadata;
        # eligibility still requires the private native own-transition chain.
        or issued.ledger_revision > row.ledger_revision
        or type(issued.db_recorded_at) is not datetime
        or issued.db_recorded_at.tzinfo is not UTC
        or issued.db_recorded_at > now
        or proof.get("schema_version") != "ctcc.collector_issued_account_revision.v1"
        or proof.get("scope")
        != [scope.environment, scope.account_id, scope.settlement_currency]
        or proof.get("claims_sha256") != row.claims_sha256
        or proof["source_lineage_sha256"] != issued.source_lineage_sha256
        or proof["hwm_custody_id"] != issued.hwm_custody_id
        or proof["hwm_bundle_sha256"] != issued.hwm_bundle_sha256
        or type(proof["previous_account_revision"]) is not int
        or proof["previous_account_revision"] != issued.previous_account_revision
        or type(proof["previous_ledger_revision"]) is not int
        or proof["previous_ledger_revision"] != issued.previous_ledger_revision
        or type(proof["control_revision"]) is not int
        or proof["control_revision"] != issued.control_revision
        or proof["control_event_sha256"] != issued.control_event_sha256
        or proof["control_state_sha256"] != issued.control_state_sha256
        or type(proof.get("account_revision")) is not int
        or proof["account_revision"] != issued.account_revision
        or type(proof.get("ledger_revision")) is not int
        or proof["ledger_revision"] != issued.ledger_revision
        or proof.get("execution_authority") is not False
    ):
        _deny("ledger55_finite_checkpoint_actual_issued_binding_changed")
    # No successful result/capability is returned; the unchanged caller records
    # only the exact existing metadata LedgerScopeState and its original hash.
