"""Owned synthetic integration child: exits only after a requested durable boundary."""

import asyncio
import json
import os
import sys
from datetime import datetime

from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.database.models.submission_reporting import (
    QualificationReportProjectionReceipt,
)
from app.database.repositories.submission_reporting import SubmissionReportingRepository
from app.trade_evidence import outbox
from app.trade_evidence.submission_reporting import decode_capture, sha
from app.trade_qualification.reservations import LedgerScope


async def run(value):
    engine = create_async_engine(os.environ["DATABASE_URL"], poolclass=NullPool)
    sessions = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    at = datetime.fromisoformat(value["at"])
    repository = SubmissionReportingRepository(sessions, clock=lambda: at)
    if value["mode"] == "after_outcome_commit":

        async def exit_after_commit(*args, **kwargs):
            os._exit(73)

        repository.read_observation = exit_after_commit
        capture = decode_capture(
            value["capture_json"], sha(value["capture_json"].encode())
        )
        await repository.record_observation(
            LedgerScope.model_validate_json(json.dumps(value["scope"]), strict=True),
            value["event_key"],
            expected_revision=value["revision"],
            intent_sha256=value["intent_sha256"],
            capture=capture,
            queue_namespace=value["namespace"],
            outbox_policy=outbox.OutboxPolicy(),
        )
    elif value["mode"] == "after_enqueue_before_receipt":
        from pathlib import Path

        def exit_before_insert(*args):
            os._exit(74)

        event.listen(
            QualificationReportProjectionReceipt, "before_insert", exit_before_insert
        )
        await repository.project_one(
            value["spool_id"],
            root=Path(value["root"]),
            queue_namespace=value["namespace"],
        )
    else:
        raise RuntimeError("unknown_synthetic_crash_stage")
    await engine.dispose()
    raise RuntimeError("expected_crash_boundary_not_reached")


if __name__ == "__main__":
    if os.environ.get("CTCC_SUBMISSION_CRASH_CHILD") != "1":
        raise SystemExit("explicit synthetic child gate required")
    raw = sys.stdin.buffer.read(200001)
    if not 0 < len(raw) <= 200000:
        raise SystemExit("bounded synthetic child input required")
    asyncio.run(run(json.loads(raw)))
