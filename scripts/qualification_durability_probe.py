"""Synthetic journal probe for a real process kill and DB/Redis restart.

Runs only inside the isolated final-validation network. No exchange client,
real credentials, market observations, or execution authority are involved.
The host harness kills the seeded process after its fsynced marker, restarts
the actual database and Redis containers, then launches an independent verifier.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config.settings import Settings
from app.database.repositories.qualification_ledger import QualificationLedgerRepository
from app.trade_qualification.reservations import LedgerScope, QualificationLedgerError
from app.trade_qualification.submission_intent import VERSION_V2
from scripts.account_capture_durability_probe import (
    confirm_ready_account_captures,
    seed_account_captures,
    verify_account_captures,
)
from scripts.control_durability_probe import (
    confirm_ready_controls,
    seed_controls,
    verify_controls,
)
from scripts.hermetic_pytest import enabled_execution_authority


def isolated_database_url() -> str:
    url = os.environ.get("DATABASE_URL", "")
    try:
        parsed = make_url(url)
        settings = Settings(_env_file=None)
    except Exception:  # noqa: BLE001 -- do not expose private DSN/settings text
        raise RuntimeError("isolated_credential_free_validation_required") from None
    if (
        os.environ.get("CTCC_HERMETIC_DURABILITY") != "1"
        or settings.environment != "test"
        or settings.trading_mode != "analysis_only"
        or enabled_execution_authority(settings)
        or parsed.drivername != "postgresql+asyncpg"
        or re.fullmatch(r"ctcc-final-[a-f0-9]{12}-postgres", parsed.host or "") is None
        or parsed.username != "ctcc"
        or parsed.password is not None
        or parsed.port != 5432
        or bool(parsed.query)
        or parsed.database != "ctcc"
        or settings.okx_demo_credentials_configured
        or settings.okx_live_credentials_configured
    ):
        raise RuntimeError("isolated_credential_free_validation_required")
    return url


def canonical(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def write_marker(path: Path, value: dict) -> None:
    raw = canonical(value)
    envelope = canonical({"body": value, "sha256": hashlib.sha256(raw).hexdigest()})
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(envelope)
            stream.flush()
            os.fsync(stream.fileno())
        # Expose the ready filename only after complete fsync, with no clobber.
        # The host may send SIGKILL immediately when this filename appears.
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


async def synthetic_ledger_fixture():
    # The fixture owns synchronous asyncio.run() calls for synthetic source
    # capture. Keep those outside the probe's running database event loop.
    from tests.unit.qualification_ledger_fixtures import ledger_fixture

    return await asyncio.to_thread(
        ledger_fixture, account_id=f"987654321{uuid4().int % 10**12:012d}"
    )


async def synthetic_execution_binding(fixture):
    from tests.unit.qualification_execution_binding_fixtures import execution_binding

    return await asyncio.to_thread(execution_binding, fixture)


def exact_request_identity(intent, *, expected_uid):
    """Require the replayed v2 FOK request, never a legacy geometry-only intent."""
    body = json.loads(intent.canonical_json)
    request = body.get("exchange_request")
    if (
        body.get("version") != VERSION_V2
        or type(request) is not dict
        or request.get("environment") != "demo"
        or request.get("account_uid") != expected_uid
        or request.get("method") != "POST"
        or request.get("path") != "/api/v5/trade/order"
        or request.get("headers", {}).get("x-simulated-trading") != "1"
        or request.get("body", {}).get("ordType") != "fok"
        or body.get("exchange_request_sha256")
        != hashlib.sha256(canonical(request)).hexdigest()
        or intent.execution_authority
        or intent.order_retry_authority
    ):
        raise RuntimeError("probe_exact_v2_request_missing")
    return {
        "intent_version": body["version"],
        "exchange_request_sha256": body["exchange_request_sha256"],
    }


async def probe(mode: str, marker: Path) -> None:
    engine = create_async_engine(isolated_database_url(), poolclass=NullPool)
    sessions = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    try:
        if mode == "seed":
            # Fixture claims are synthetic and can never prove source authenticity.
            fixture = await synthetic_ledger_fixture()
            binding = await synthetic_execution_binding(fixture)
            repository = QualificationLedgerRepository(
                sessions, clock=lambda: fixture.now
            )
            await repository.reconcile_scope(fixture.claims, expected_revision=0)
            held = await repository.reserve(fixture.request)
            intent = await repository.consume_with_submission_intent(
                held.scope,
                held.original_event_key,
                expected_revision=2,
                execution_binding=binding,
            )
            intent_identity = exact_request_identity(
                intent, expected_uid=held.scope.account_id
            )
            uncertain = await repository.mark_uncertain(
                held.scope, held.original_event_key, expected_revision=3
            )
            state = await repository.read_scope(held.scope)
            if state.active != (uncertain,) or state.ledger_revision != 4:
                raise RuntimeError("seed_readback_mismatch")
            account_seed = await seed_account_captures(sessions, scope=held.scope)
            control_seed = await seed_controls(
                sessions, clock=lambda: fixture.now, active_uid=held.scope.account_id
            )
            confirm_ready_controls(
                control_seed, expected_active_uid=held.scope.account_id
            )
            confirm_ready_account_captures(account_seed)
            write_marker(
                marker,
                {
                    "schema": "ctcc.synthetic.qualification.crash-probe.v3",
                    "controls": control_seed.records,
                    "account_captures": account_seed.records,
                    "scope": held.scope.model_dump(mode="json"),
                    "event": held.original_event_key,
                    "reservation_id": held.reservation_id,
                    "intent_sha256": intent.sha256,
                    **intent_identity,
                    "control_arm_intent_observed_before_publish": True,
                    "coverage": held.coverage.model_dump(mode="json"),
                    "execution_authority": False,
                    "order_writes": 0,
                },
            )
            print("SYNTHETIC_DURABLE_COMMIT_READBACK_READY=1", flush=True)
            # Real SIGKILL is applied by the owning Docker validation harness.
            try:
                await asyncio.Event().wait()
            finally:
                # Keep both actual runtime owners/tokens alive until interruption.
                # SIGKILL cannot run this cleanup; cooperative cancellation revokes.
                for owner in control_seed.owners:
                    owner.revoke_now()
                # Retain unfinished actual RAM buffers until interruption. No
                # cancellation cleanup can manufacture a durable source receipt.
                for owner in account_seed.owners:
                    owner.closed = True
        else:
            raw = marker.read_bytes()
            if len(raw) > 32768:
                raise RuntimeError("probe_marker_oversize")
            envelope = json.loads(raw)
            body = envelope["body"]
            if envelope["sha256"] != hashlib.sha256(canonical(body)).hexdigest():
                raise RuntimeError("probe_marker_digest_mismatch")
            if body.get("schema") != "ctcc.synthetic.qualification.crash-probe.v3":
                raise RuntimeError("probe_marker_version_invalid")
            scope = LedgerScope.model_validate(body["scope"])
            repository = QualificationLedgerRepository(
                sessions, clock=lambda: datetime.now(UTC)
            )
            state = await repository.read_scope(scope)
            if len(state.active) != 1 or state.ledger_revision != 4:
                raise RuntimeError("durable_hold_missing_after_restart")
            held = state.active[0]
            if (
                held.state != "uncertain"
                or held.reservation_id != body["reservation_id"]
                or held.coverage.model_dump(mode="json") != body["coverage"]
            ):
                raise RuntimeError("durable_hold_changed_after_restart")
            intent = await repository.read_submission_intent(
                scope, body["event"], expected_sha256=body["intent_sha256"]
            )
            if intent.execution_authority or intent.order_retry_authority:
                raise RuntimeError("restart_regained_authority")
            intent_identity = exact_request_identity(
                intent, expected_uid=scope.account_id
            )
            if any(body.get(key) != value for key, value in intent_identity.items()):
                raise RuntimeError("probe_exact_request_changed_after_restart")
            try:
                await repository.consume_with_submission_intent(
                    scope, body["event"], expected_revision=4
                )
            except QualificationLedgerError:
                pass
            else:
                raise RuntimeError("restart_consumed_twice")
            if await repository.read_scope(scope) != state:
                raise RuntimeError("denied_replay_changed_hold")
            await verify_account_captures(
                sessions, body["account_captures"], scope=scope
            )
            print("ACCOUNT_CAPTURE_RESTART_RAW_AND_UNKNOWN_PREFIX=PASS")
            await verify_controls(
                sessions, body["controls"], expected_active_uid=scope.account_id
            )
            print("DEMO_CONTROL_RESTART_NO_ARM_ESTOP_PERSISTENCE=PASS")
            print("PROCESS_KILL_POSTGRES_REDIS_RESTART_DURABILITY=PASS")
            print("SYNTHETIC_CLAIMS=1;SOURCE_AUTHORITY=0;ORDER_WRITES=0")
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("seed", "verify"))
    parser.add_argument("--marker", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(probe(args.mode, args.marker))


if __name__ == "__main__":
    main()
