"""Exact physical58 projection DATA for the original one-shot Notion issuer."""

from sqlalchemy import select

from app.database.models.submission_reporting import (
    QualificationReportProjectionReceipt as Projection,
)
from app.database.models.submission_reporting import QualificationReportSpool as Spool
from app.database.repositories import native_closed_reporting_v55 as reports
from app.domain.source_primitives import canonical
from app.trade_evidence import native_current58_reporting_v1 as current
from app.trade_evidence import outbox, post_submit


async def read_current58_notion_projection_v1(binding, *, key):
    from app.trade_evidence import native_notion_delivery_source_v55 as source

    reports._need(key is source._REPOSITORY_KEY_V55)
    cap = source._replay_current58_reporting_lease_v1(
        binding, key=source._CURRENT58_KEY_V1
    )
    selected, _ = current.current58_reporting_context_v1(
        cap, key=current._CONTEXT_KEY_V1
    )
    factory, root, report_id, namespace, expected = (
        source._replay_actual_reporting_factory_v55(
            binding, key=reports._SOURCE_FACTORY_KEY_V55
        )
    )
    reports._need(factory is selected.writer_factory)

    def check():
        reports._need(
            source._replay_current58_reporting_lease_v1(
                binding, key=source._CURRENT58_KEY_V1
            )
            is cap
        )
        source._replay_actual_reporting_factory_v55(
            binding, key=reports._SOURCE_FACTORY_KEY_V55
        )

    async with current.current58_reporting_transaction_v1(
        cap, key=current._CONTEXT_KEY_V1
    ) as session:
        rows = tuple(
            (
                await session.scalars(
                    select(Spool)
                    .filter_by(report_id=report_id, queue_namespace=namespace)
                    .limit(2)
                )
            ).all()
        )
        check()
        reports._need(len(rows) == 1 and type(rows[0]) is Spool)
        spool = rows[0]
        outcome, report, policy = await reports._replay_native_terminal_spool(
            session, spool
        )
        check()
        reports._need(type(report) is reports.NativeTerminalReportDataV55)
        projection = await session.get(
            Projection, spool.spool_id, populate_existing=True
        )
        check()
        reports._need(
            type(projection) is Projection
            and projection.envelope_sha256 == expected
            and projection.report_sha256 == spool.report_sha256
            and projection.policy_sha256 == spool.policy_sha256
            and projection.queue_namespace == namespace
            and projection.verified_at >= spool.created_at
        )
        view = outbox.validate_outbox_view(
            outbox.read_job(root, report_id, clock=outbox.actual_utc)
        )
        reports._need(
            view.envelope.envelope_sha256 == expected
            and view.envelope.payload
            == post_submit._outbox_payload(report, spool.report_sha256)
            and view.envelope.policy == policy
        )
        metadata = await reports.lineage55._metadata(session)
        check()
        proof = canonical(
            {
                "schema_version": "ctcc.native_historical_notion_projection.v1",
                "database_head": "0058",
                "spool_id": spool.spool_id,
                "outcome_id": outcome.outcome_id,
                "reservation_id": spool.reservation_id,
                "report_id": report_id,
                "report_sha256": spool.report_sha256,
                "policy_sha256": spool.policy_sha256,
                "policy_json": spool.policy_json,
                "queue_namespace": namespace,
                "created_at": spool.created_at.isoformat(),
                "projection_verified_at": projection.verified_at.isoformat(),
                "envelope_sha256": expected,
                "payload_json": view.envelope.payload.model_dump_json(round_trip=True),
                "execution_authority": False,
                "order_retry_authority": False,
            }
        )
        reports._need(0 < len(proof) <= 65536)
    check()
    return reports.NativeNotionProjectionReadbackV55(proof, tuple(metadata), session)
