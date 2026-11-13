"""Native TLS acceptance of label-free custody, using synthetic inputs only."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.database.repositories.public_runtime_bundle_custody import runtime_bundle_id_v1
from app.domain.source_primitives import sha
from app.mie.validation.prospective_devval_campaign_plan_v1 import (
    freeze_prospective_devval_campaign_v1,
)
from app.mie.validation.prospective_devval_global_companion_schedule_v1 import (
    freeze_global_devval_companion_schedule_v1,
)
from app.mie.validation.prospective_devval_prediction_publication_v1 import (
    _prediction_proof,
)
from app.mie.validation.prospective_devval_research_blueprint_v1 import (
    compile_research_campaign,
)
from app.mie.validation.prospective_devval_validation_inputs_v1 import (
    ValidationEventIdentityV1,
)
from app.mie.validation.prospective_devval_validation_prediction_v1 import (
    DevvalValidationPredictionV1,
    ValidationPredictionSideV1,
)
from tests.integration.test_devval_campaign_custody_pg_v1 import (
    _ACK,
    _PUBLISH,
    _SQL,
)
from tests.integration.test_devval_campaign_custody_pg_v1 import (
    tls_evidence_db as tls_evidence_db,  # noqa: PLC0414 -- shared isolated TLS fixture.
)
from tests.integration.test_gate3_canonical_schedule_claim import migrate
from tests.unit.mie.test_devval_global_companion_schedule_v1 import _inputs

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
_DIAGNOSTIC = "ctcc_devval_prediction_diagnostic"
_INSTALL = _SQL.with_name("gate3_devval_validation_prediction_diagnostic_v1.sql")
_ANCHOR = text(
    f"SELECT {_DIAGNOSTIC}.anchor_schedule(:campaign,:schedule,:payload,:blueprint)"
)
_PREDICT = text(
    f"SELECT {_DIAGNOSTIC}.publish_prediction(:campaign,:schedule,:slot,:prediction,:bundle)"
)
_ACKNOWLEDGE = text(
    f"SELECT {_DIAGNOSTIC}.acknowledge_prediction(:campaign,:schedule,:slot,:prediction)"
)
_READ = text(
    f"SELECT * FROM {_DIAGNOSTIC}.read_prediction(:campaign,:schedule,:slot,:prediction)"
)
_RAW_APPEND = text(
    "SELECT public.public_runtime_bundle_v1_append(:bundle,:prediction,:proof,:raw)"
)


def _geometry(now, *, historical):
    inputs = _inputs()
    blueprint = inputs["blueprint"]
    capture = blueprint.capture
    offset = timedelta(days=-30 if historical else 2)
    first_open = now.replace(hour=0, minute=0, second=0, microsecond=0) + offset
    shift = first_open - capture.development.start_at
    blueprint = blueprint.model_copy(
        update={
            "capture": capture.model_copy(
                update={
                    "planned_at": (first_open if historical else now)
                    - timedelta(hours=1),
                    **{
                        name: window.model_copy(
                            update={
                                "start_at": window.start_at + shift,
                                "end_at": window.end_at + shift,
                            }
                        )
                        for name, window in (
                            ("development", capture.development),
                            ("validation", capture.validation),
                        )
                    },
                },
            )
        },
    )
    plan = compile_research_campaign(blueprint)
    manifest = freeze_prospective_devval_campaign_v1(plan)
    schedule = freeze_global_devval_companion_schedule_v1(
        **{
            **inputs,
            "blueprint": blueprint,
            "expected_blueprint_sha256": blueprint.canonical_sha256(),
            "campaign_plan": plan,
        }
    )
    return manifest, blueprint, schedule


def _anchor_args(manifest, blueprint, schedule):
    return {
        "campaign": manifest.sha256,
        "schedule": schedule.sha256,
        "payload": schedule.contract.canonical_json(),
        "blueprint": blueprint.canonical_json(),
    }


def _prediction(manifest, schedule, ordinal, *, marker="c", proof_override=None):
    slot = schedule.contract.slots[ordinal]
    close = slot.event_closed_at
    prediction = DevvalValidationPredictionV1(
        identity=ValidationEventIdentityV1(
            schedule_sha256=schedule.sha256,
            coverage_sha256="b" * 64,
            batch_sha256=marker * 64,
            slot_ordinal=ordinal,
            event_closed_at=close,
            decision_at=None,
            decision_cutoff_at=slot.decision_cutoff_at,
        ),
        feature_projection_sha256="d" * 64,
        raw_event_sha256="e" * 64,
        policy_sha256="f" * 64,
        development_fit_sha256="1" * 64,
        development_fit_input_sha256="2" * 64,
        final_development_rows_sha256="3" * 64,
        latest_development_label_read_at=close - timedelta(hours=25),
        validation_feature_history_start_at=close - timedelta(hours=21),
        feature_plan_sha256="4" * 64,
        scorer_config_sha256=schedule.contract.legacy_scorer_config_sha256,
        status="incomplete",
        sides=tuple(
            ValidationPredictionSideV1(
                instrument_id=symbol,
                status="missing",
                feature_replay_sha256=None,
                raw_result_sha256=None,
                raw_direction=None,
                bin_index=None,
                probability=None,
            )
            for symbol in schedule.contract.instrument_ids
        ),
    )
    raw = prediction.canonical_json_bytes()
    digest = prediction.canonical_sha256()
    proof = proof_override or _prediction_proof(prediction, digest)
    bundle = runtime_bundle_id_v1(bundle_sha256=digest, proof_envelope_sha256=proof)
    return {"bundle": bundle, "prediction": digest, "proof": proof, "raw": raw}, {
        "campaign": manifest.sha256,
        "schedule": schedule.sha256,
        "slot": ordinal,
        "prediction": digest,
        "bundle": bundle,
    }


async def _install(owner, publisher, observer):
    async with owner.begin() as connection:
        await connection.run_sync(migrate, "0046", "upgrade")
    async with owner.connect() as connection:
        raw_connection = await connection.get_raw_connection()
        await raw_connection.driver_connection.execute(
            _INSTALL.read_text(encoding="utf-8")
        )
    async with owner.begin() as connection:
        for engine, signatures in (
            (
                publisher,
                (
                    "anchor_schedule(text,text,text,text)",
                    "publish_prediction(text,text,integer,text,text)",
                ),
            ),
            (
                observer,
                (
                    "acknowledge_prediction(text,text,integer,text)",
                    "read_prediction(text,text,integer,text)",
                ),
            ),
        ):
            login = engine.url.username
            assert login and login.isascii() and login.replace("_", "").isalnum()
            await connection.execute(
                text(f"GRANT USAGE ON SCHEMA {_DIAGNOSTIC} TO {login}")
            )
            for signature in signatures:
                await connection.execute(
                    text(
                        f"GRANT EXECUTE ON FUNCTION {_DIAGNOSTIC}.{signature} TO {login}"
                    )
                )


async def _seed_synthetic_history(owner, publisher, observer, manifest, schedule):
    # Diagnostic protocol clocks remain native. Only this owner-only fixture
    # supplies old synthetic parent rows so no days-long market wait is needed.
    plan = manifest.contract.plan
    recorded_at = plan.planned_at + timedelta(seconds=1)
    async with owner.begin() as connection:
        publisher_oid, observer_oid = (
            await connection.execute(
                text("SELECT to_regrole(:publisher)::oid, to_regrole(:observer)::oid"),
                {
                    "publisher": publisher.url.username,
                    "observer": observer.url.username,
                },
            )
        ).one()
        await connection.execute(
            text("""
                INSERT INTO ctcc_devval_custody.campaigns VALUES (
                  :campaign,:payload,:campaign_id,:window,
                  :d0,:d1,:v0,:v1,:recorded,:publisher,'11')
            """),
            {
                "campaign": manifest.sha256,
                "payload": manifest.contract.canonical_json(),
                "campaign_id": plan.campaign_id,
                "window": uuid4().hex * 2,
                "d0": plan.development.start_at,
                "d1": plan.development.end_at,
                "v0": plan.validation.start_at,
                "v1": plan.validation.end_at,
                "recorded": recorded_at,
                "publisher": publisher_oid,
            },
        )
        await connection.execute(
            text(
                "INSERT INTO ctcc_devval_custody.acknowledgements VALUES (:campaign,:at,:observer,'12')"
            ),
            {
                "campaign": manifest.sha256,
                "at": recorded_at + timedelta(seconds=1),
                "observer": observer_oid,
            },
        )
        await connection.execute(
            text(
                f"INSERT INTO {_DIAGNOSTIC}.schedule_anchors VALUES (:campaign,:schedule,:payload,:at,:publisher,'13'::xid8)"
            ),
            {
                "campaign": manifest.sha256,
                "schedule": schedule.sha256,
                "payload": schedule.contract.canonical_json(),
                "at": recorded_at + timedelta(seconds=2),
                "publisher": publisher_oid,
            },
        )


async def _rejected(engine, statement, args, *, state):
    with pytest.raises(DBAPIError) as failure:
        async with engine.begin() as connection:
            await connection.execute(statement, args)
    assert failure.value.orig.sqlstate == state


async def _read(observer, args):
    async with observer.begin() as connection:
        await connection.execute(
            text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        )
        return (await connection.execute(_READ, args)).mappings().one_or_none()


async def test_native_tls_validation_prediction_schedule_slot_and_independent_ack(
    tls_evidence_db,
):
    owner, campaign_publisher, campaign_observer, spare = tls_evidence_db
    # Commit the future campaign with the two pre-existing native roles.
    publisher, observer = spare, campaign_observer
    async with owner.connect() as connection:
        now = await connection.scalar(text("SELECT pg_catalog.clock_timestamp()"))
    assert isinstance(now, datetime)
    manifest, blueprint, schedule = _geometry(now.astimezone(UTC), historical=False)
    async with campaign_publisher.begin() as connection:
        await connection.execute(
            _PUBLISH,
            {"sha": manifest.sha256, "payload": manifest.contract.canonical_json()},
        )
    async with campaign_observer.begin() as connection:
        await connection.execute(_ACK, {"sha": manifest.sha256})
    # Reuse the observer login only after removing its campaign mutation grant.
    async with owner.begin() as connection:
        login = observer.url.username
        assert login and login.isascii() and login.replace("_", "").isalnum()
        await connection.execute(
            text(
                f"REVOKE EXECUTE ON FUNCTION ctcc_devval_custody.acknowledge_campaign(text) FROM {login}"
            )
        )
        publisher_login = publisher.url.username
        assert (
            publisher_login
            and publisher_login.isascii()
            and publisher_login.replace("_", "").isalnum()
        )
        await connection.execute(
            text(
                f"REVOKE EXECUTE ON FUNCTION ctcc_devval_custody.claim_dispatch(text,text) FROM {publisher_login}"
            )
        )
    await _install(owner, publisher, observer)
    anchor_args = _anchor_args(manifest, blueprint, schedule)
    await _rejected(
        publisher,
        _ANCHOR,
        {
            **anchor_args,
            "payload": anchor_args["payload"] + " ",
            "schedule": sha((anchor_args["payload"] + " ").encode()),
        },
        state="P0001",
    )
    omitted = schedule.contract.model_copy(
        update={"slots": schedule.contract.slots[1:]}
    )
    await _rejected(
        publisher,
        _ANCHOR,
        {
            **anchor_args,
            "payload": omitted.canonical_json(),
            "schedule": omitted.canonical_sha256(),
        },
        state="P0001",
    )
    async with publisher.begin() as connection:
        await connection.execute(_ANCHOR, anchor_args)
    await _rejected(publisher, _ANCHOR, anchor_args, state="23505")
    future_ordinal = next(
        i
        for i, slot in enumerate(schedule.contract.slots)
        if slot.partition.value == "validation"
    )
    raw, future = _prediction(manifest, schedule, future_ordinal)
    async with owner.begin() as connection:
        await connection.execute(_RAW_APPEND, raw)
    await _rejected(publisher, _PREDICT, future, state="P0001")

    past_manifest, _, past_schedule = _geometry(now.astimezone(UTC), historical=True)
    await _seed_synthetic_history(
        owner, publisher, observer, past_manifest, past_schedule
    )
    ordinal = next(
        i
        for i, slot in enumerate(past_schedule.contract.slots)
        if slot.partition.value == "validation"
    )
    first_raw, first = _prediction(past_manifest, past_schedule, ordinal)
    altered_raw, alternate = _prediction(
        past_manifest, past_schedule, ordinal, marker="d"
    )
    wrong_raw, wrong = _prediction(
        past_manifest, past_schedule, ordinal, marker="e", proof_override="f" * 64
    )
    async with owner.begin() as connection:
        for raw in (first_raw, altered_raw, wrong_raw):
            await connection.execute(_RAW_APPEND, raw)
    await _rejected(publisher, _PREDICT, wrong, state="P0001")
    await _rejected(observer, _PREDICT, first, state="42501")
    async with publisher.begin() as transaction:
        await transaction.execute(_PREDICT, first)
        assert await _read(observer, first) is None
        await _rejected(observer, _ACKNOWLEDGE, first, state="P0002")
        # A second direct publisher session offers different valid DB0046
        # bytes while the same semantic key is still locked by the first.
        with pytest.raises(DBAPIError) as blocked:
            async with publisher.begin() as competing:
                await competing.execute(text("SET LOCAL lock_timeout = '200ms'"))
                await competing.execute(_PREDICT, alternate)
        assert blocked.value.orig.sqlstate == "55P03"
    await _rejected(publisher, _PREDICT, alternate, state="23505")
    await _rejected(publisher, _ACKNOWLEDGE, first, state="42501")
    await _rejected(observer, _ACKNOWLEDGE, alternate, state="P0002")
    assert (await _read(observer, first))["acknowledged_at"] is None
    async with observer.begin() as connection:
        await connection.execute(_ACKNOWLEDGE, first)
    await _rejected(observer, _ACKNOWLEDGE, first, state="23505")
    await _rejected(observer, _READ, first, state="P0001")
    recovered = await _read(observer, first)
    assert recovered == await _read(observer, first)
    assert recovered["db0046_bundle_id"] == first_raw["bundle"]
    assert recovered["db0046_proof_sha256"] == first_raw["proof"]
    assert (
        recovered["anchored_at"]
        < recovered["db0046_recorded_at"]
        < recovered["published_at"]
        < recovered["acknowledged_at"]
    )
    assert recovered["publisher_oid"] != recovered["observer_oid"]
    assert (
        len(
            {
                recovered[name]
                for name in ("anchor_xid", "publication_xid", "acknowledgement_xid")
            }
        )
        == 3
    )
    await _rejected(
        observer, text(f"SELECT * FROM {_DIAGNOSTIC}.publications"), {}, state="42501"
    )
    async with owner.begin() as connection:
        await connection.execute(
            text(
                f"GRANT EXECUTE ON FUNCTION {_DIAGNOSTIC}.anchor_schedule(text,text,text,text) "
                f"TO {observer.url.username}"
            )
        )
    await _rejected(observer, _ACKNOWLEDGE, first, state="P0001")
    await _rejected(
        owner, text(f"TRUNCATE {_DIAGNOSTIC}.acknowledgements"), {}, state="P0001"
    )
    async with owner.connect() as connection:
        assert await connection.scalar(
            text("SELECT to_regclass('public.alembic_version') IS NULL")
        )
        assert (
            await connection.scalar(
                text("""
            SELECT count(*) FROM pg_catalog.pg_constraint
            WHERE contype='f' AND confrelid='public.public_runtime_raw_bundles_v1'::regclass
        """)
            )
            == 0
        )
