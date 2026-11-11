"""Native PostgreSQL and TLS acceptance for the fitted Gate 3 custody boundary."""

from __future__ import annotations

import asyncio
import json
import os
import re
import ssl
from datetime import UTC, datetime, timedelta
from importlib import import_module
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.domain.source_primitives import canonical, sha
from app.mie.validation.artifact import freeze_prospective_holdout_receipt
from app.mie.validation.controlled_fitted_holdout_reader_v1 import (
    ControlledFittedHoldoutReadV1,
    read_fitted_prospective_holdout_once,
)
from app.mie.validation.gate3_fitted_candidate_custody_v1 import (
    FittedCandidateInputsV1,
    Gate3FittedCandidateCustodyV1,
    Gate3FittedCustodyError,
)
from app.mie.validation.prospective import (
    Gate3ProspectiveHoldoutReceipt,
    Gate3ProspectivePreregistration,
    ProspectiveAccessOutcome,
)
from app.mie.validation.prospective_capture_schedule import (
    ProspectiveCoordinatePlanV1,
    build_capture_schedule,
)
from app.mie.validation.prospective_devval_campaign_plan_v1 import (
    ProspectiveDevvalCampaignPlanV1,
)
from app.mie.validation.prospective_devval_fitted_binding_v1 import (
    build_devval_candidate_binding,
)
from tests.integration.test_gate3_canonical_schedule_claim import migrate, pin_record
from tests.integration.test_gate3_committed_preregistration_seal import (
    append_pin,
    append_seal,
    seal_record,
)
from tests.unit.mie.test_devval_campaign_batch import synthetic_grid
from tests.unit.mie.test_devval_candidate_trials import _inputs
from tests.unit.mie.test_devval_fitted_binding import (
    _formal_preregistration,
    aligned_synthetic_case,
)
from tests.unit.mie.test_prospective_devval_campaign_plan_v1 import plan_input

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
_READ = "gate3_fitted_binding_read(text)"
_ACK_READ = "gate3_fitted_binding_ack_read(text)"
_ROLES = {
    "seal": ("gate3_prereg_seal_append(jsonb)", "gate3_prereg_seal_read(text)"),
    "seal_ack": (
        "gate3_prereg_seal_read(text)",
        "gate3_prereg_seal_ack_append(text)",
        "gate3_prereg_seal_ack_read(text)",
    ),
    "schedule": (
        "gate3_capture_schedule_claim_append(jsonb)",
        "gate3_capture_schedule_claim_read(text)",
    ),
    "publisher": ("gate3_fitted_binding_append(text,text,text)", _READ),
    "observer": (_READ, "gate3_fitted_binding_ack_append(text,text)", _ACK_READ),
    "reader": (_READ, _ACK_READ),
    "evaluator": (
        _READ,
        _ACK_READ,
        "gate3_fitted_evaluation_append(text,text,text,text,text,text)",
        "gate3_fitted_evaluation_read(text)",
    ),
}


def _migrate_fitted(connection, direction):
    module = import_module("migrations.versions.0056_gate3_fitted_candidate_custody")
    with Operations.context(MigrationContext.configure(connection)):
        getattr(module, direction)()


async def _grant(owner, names, keys):
    async with owner.begin() as connection:
        for key in keys:
            role = names[key]
            await connection.execute(text(f"GRANT USAGE ON SCHEMA public TO {role}"))
            for function in _ROLES[key]:
                await connection.execute(
                    text(f"GRANT EXECUTE ON FUNCTION public.{function} TO {role}")
                )


@pytest.fixture
async def fitted_database():
    raw_url = os.environ.get("CTCC_DEVVAL_CUSTODY_TEST_DATABASE_URL")
    ca_file = os.environ.get("CTCC_DEVVAL_CUSTODY_TEST_TLS_CA_FILE")
    if raw_url is None and ca_file is None:
        pytest.skip("explicit isolated Gate 3 PostgreSQL TLS lab required")
    if not raw_url or not ca_file:
        pytest.fail("isolated Gate 3 PostgreSQL TLS lab requires both inputs")
    url = make_url(raw_url)
    if (
        url.drivername != "postgresql+asyncpg"
        or not url.username
        or not url.database
        or url.password is not None
        or url.query
        or not re.fullmatch(r"ctcc-final-[a-f0-9]{12}-postgres", url.host or "")
    ):
        pytest.fail(
            "fitted custody acceptance requires the isolated trust-auth TLS lab"
        )
    tls = ssl.create_default_context(cafile=ca_file)
    database = "ctcc_g3f_" + uuid4().hex
    names = {key: "ctcc_g3f_" + uuid4().hex for key in _ROLES}

    def make_engine(target):
        return create_async_engine(
            target, poolclass=NullPool, connect_args={"ssl": tls}
        )

    admin = make_engine(url)
    created_database = False
    created_roles = []
    engines = {}
    try:
        async with admin.connect() as connection:
            await connection.execution_options(isolation_level="AUTOCOMMIT")
            await connection.execute(text(f"CREATE DATABASE {database}"))
        created_database = True
        async with admin.begin() as connection:
            for role in names.values():
                await connection.execute(text(f"CREATE ROLE {role} LOGIN"))
        created_roles = list(names.values())
        db_url = url.set(database=database)
        owner = engines["owner"] = make_engine(db_url)
        async with owner.begin() as connection:
            for revision in ("0024", "0026", "0028", "0029", "0030", "0031", "0032"):
                await connection.run_sync(migrate, revision, "upgrade")
            await connection.run_sync(_migrate_fitted, "upgrade")
        await _grant(owner, names, _ROLES)
        for key, role in names.items():
            engines[key] = make_engine(db_url.set(username=role))
        async with owner.connect() as connection:
            db_oid = await connection.scalar(
                text(
                    "SELECT oid FROM pg_catalog.pg_database WHERE datname = current_database()"
                )
            )
            role_oids = {}
            for key, role in names.items():
                role_oids[key] = await connection.scalar(
                    text("SELECT oid FROM pg_catalog.pg_roles WHERE rolname=:role"),
                    {"role": role},
                )
        repos = {
            key: Gate3FittedCandidateCustodyV1(
                async_sessionmaker(engines[key], expire_on_commit=False),
                role=key,
                expected_host=db_url.host,
                expected_port=db_url.port or 5432,
                expected_database_name=database,
                expected_login_name=names[key],
                expected_database_oid=db_oid,
                expected_login_oid=role_oids[key],
                expected_publisher_oid=role_oids["publisher"],
                expected_observer_oid=role_oids["observer"],
                expected_evaluator_oid=role_oids["evaluator"],
            )
            for key in ("publisher", "observer", "reader", "evaluator")
        }
        yield engines, repos, names
    finally:
        for engine in reversed(tuple(engines.values())):
            await engine.dispose()
        if created_database:
            async with admin.connect() as connection:
                await connection.execution_options(isolation_level="AUTOCOMMIT")
                await connection.execute(text(f"DROP DATABASE {database}"))
        if created_roles:
            async with admin.begin() as connection:
                for role in created_roles:
                    await connection.execute(text(f"DROP ROLE {role}"))
        await admin.dispose()


@pytest.fixture(scope="module")
def fitted_inputs_source():
    plan = ProspectiveDevvalCampaignPlanV1(**plan_input())
    offset = datetime(2026, 10, 1, tzinfo=UTC) - plan.development.start_at

    def move(window):
        return window.model_copy(
            update={
                "start_at": window.start_at + offset,
                "end_at": window.end_at + offset,
            }
        )

    source_plan = ProspectiveDevvalCampaignPlanV1.model_validate(
        plan.model_copy(
            update={
                "planned_at": plan.planned_at + offset,
                "development": move(plan.development),
                "validation": move(plan.validation),
            }
        ).model_dump()
    )
    return aligned_synthetic_case.__wrapped__(synthetic_grid(source_plan))


async def _now(engine):
    async with engine.connect() as connection:
        return await connection.scalar(text("SELECT pg_catalog.clock_timestamp()"))


async def _fitted_proposal(engines, fitted_inputs_source):
    batch, study, trials = fitted_inputs_source
    now = await _now(engines["owner"])
    start = now.replace(second=0, microsecond=0) + timedelta(minutes=3)
    end = start + timedelta(minutes=1)
    formal = _formal_preregistration(batch, study, trials, holdout_start=start)
    coordinate = ProspectiveCoordinatePlanV1(
        holdout_id=formal.prospective_holdout.holdout_id,
        origin="https://openapi.okx.com",
        instrument_ids=formal.training_dataset.instrument_ids,
        start_at=start,
        end_at=end,
        expected_rows=2,
    )
    window = formal.prospective_holdout.model_copy(
        update={
            "end_at": end,
            "first_permitted_access_at": end,
            "publication_lag_seconds": 0,
            "expected_rows": 2,
            "expected_artifact_count": 2,
            "coordinate_plan_sha256": coordinate.canonical_sha256(),
        }
    )
    seal = Gate3ProspectivePreregistration.model_validate(
        formal.model_copy(update={"prospective_holdout": window}).model_dump()
    )
    seal_pin = seal.canonical_sha256()
    archive_pin = sha(b"synthetic TLS custody source archive")
    binding = build_devval_candidate_binding(
        study,
        batch=batch,
        trial_result=trials,
        preregistration=seal,
        expected_preregistration_sha256=seal_pin,
        source_archive_sha256=archive_pin,
    )
    _, legacy, _ = _inputs(study)
    inputs = FittedCandidateInputsV1(
        binding=binding,
        expected_binding_sha256=binding.sha256,
        batch=batch,
        study=study,
        trial_result=trials,
        legacy_scores=legacy,
        preregistration=seal,
        expected_preregistration_sha256=seal_pin,
        source_archive_sha256=archive_pin,
    )
    schedule = build_capture_schedule(
        seal=seal, coordinate_plan=coordinate, planned_at=now
    )
    await append_seal(engines["seal"], seal_record(seal, schedule))
    return inputs, coordinate


async def _append(engine, statement, **params):
    async with engine.begin() as connection:
        await connection.execute(text(statement), params)


async def _accept_seal(engines, inputs):
    await _append(
        engines["seal_ack"],
        "SELECT public.gate3_prereg_seal_ack_append(:seal)",
        seal=inputs.expected_preregistration_sha256,
    )


async def test_fitted_binding_role_separation_and_immutable_readback(
    fitted_database, fitted_inputs_source
):
    engines, repos, names = fitted_database
    inputs, coordinate = await _fitted_proposal(engines, fitted_inputs_source)
    seal = inputs.preregistration
    with pytest.raises(Gate3FittedCustodyError):
        await repos["publisher"].publish(inputs)
    await _accept_seal(engines, inputs)
    before_ack = build_capture_schedule(
        seal=seal, coordinate_plan=coordinate, planned_at=await _now(engines["owner"])
    )
    with pytest.raises(DBAPIError, match="gate3_fitted"):
        await append_pin(engines["schedule"], pin_record(seal, before_ack))
    committed = await repos["publisher"].publish(inputs)
    with pytest.raises(DBAPIError):
        await _append(
            engines["publisher"],
            "SELECT public.gate3_fitted_binding_ack_append(:seal,:binding)",
            seal=seal.canonical_sha256(),
            binding=inputs.binding.sha256,
        )
    ack = await repos["observer"].acknowledge(inputs)
    observed = await repos["reader"].read_acknowledged(
        inputs, expected_ack_sha256=ack.ack_sha256
    )
    assert observed.ack_sha256 == ack.ack_sha256
    assert ack.binding_sha256 == committed.binding_sha256 == inputs.binding.sha256
    assert ack.publication_xid != ack.acknowledgement_xid
    after_ack = build_capture_schedule(
        seal=seal, coordinate_plan=coordinate, planned_at=await _now(engines["owner"])
    )
    await append_pin(engines["schedule"], pin_record(seal, after_ack))
    with pytest.raises(DBAPIError, match="gate3_fitted"):
        await _append(
            engines["owner"],
            "UPDATE public.gate3_fitted_candidate_acks SET binding_sha256=binding_sha256",
        )
    await _append(
        engines["owner"],
        f"GRANT SELECT ON public.gate3_fitted_candidate_acks TO {names['reader']}",
    )
    with pytest.raises(Gate3FittedCustodyError):
        await repos["reader"].read_acknowledged(
            inputs, expected_ack_sha256=ack.ack_sha256
        )


async def test_fitted_evaluation_is_once_and_after_permitted_access(
    fitted_database, fitted_inputs_source, tmp_path
):
    engines, repos, _ = fitted_database
    inputs, _ = await _fitted_proposal(engines, fitted_inputs_source)
    await _accept_seal(engines, inputs)
    await repos["publisher"].publish(inputs)
    ack = await repos["observer"].acknowledge(inputs)
    seal = inputs.preregistration
    permitted = seal.prospective_holdout.first_permitted_access_at
    manifest = b"synthetic holdout manifest"
    dataset = b"synthetic holdout bytes"
    holdout_dataset = seal.training_dataset.model_copy(
        update={
            "dataset_id": "dataset:synthetic:fitted-holdout:v1",
            "manifest_sha256": sha(manifest),
            "content_sha256": sha(dataset),
            "expected_rows": 2,
            "first_event_at": seal.prospective_holdout.end_at,
            "last_event_at": seal.prospective_holdout.end_at,
            "frozen_at": permitted,
            "event_time_field": "closed_at",
        }
    )
    receipt = freeze_prospective_holdout_receipt(
        Gate3ProspectiveHoldoutReceipt(
            receipt_id="receipt:synthetic:fitted-holdout:v1",
            recorded_at=permitted,
            preregistration=seal,
            preregistration_sha256=seal.canonical_sha256(),
            first_accessed_at=permitted,
            holdout_dataset=holdout_dataset,
            acquisition_plan_sha256=seal.prospective_holdout.coordinate_plan_sha256,
            artifact_count=2,
            all_artifacts_verified=True,
            access_policy_compliant=True,
            access_outcome=ProspectiveAccessOutcome.SEALED_UNREAD,
            candidate_changed_after_preregistration=False,
            predictive_oos_eligible=True,
        )
    )
    with pytest.raises(DBAPIError, match="gate3_fitted"):
        await _append(
            engines["evaluator"],
            "SELECT public.gate3_fitted_evaluation_append(:seal,:binding,:ack,:receipt,:dataset,:attempt)",
            seal=seal.canonical_sha256(),
            binding=inputs.binding.sha256,
            ack=ack.acknowledgement_xid,
            receipt=receipt.sha256,
            dataset=sha(dataset),
            attempt=sha(b"early"),
        )
    manifest_path, dataset_path = tmp_path / "manifest", tmp_path / "dataset"
    manifest_path.write_bytes(manifest)
    dataset_path.write_bytes(dataset)
    async with asyncio.timeout(250):
        while await _now(engines["owner"]) < permitted:
            await asyncio.sleep(1)

    async def reader():
        return await read_fitted_prospective_holdout_once(
            custody=repos["evaluator"],
            inputs=inputs,
            expected_ack_sha256=ack.ack_sha256,
            receipt_bytes=receipt.payload,
            expected_receipt_sha256=receipt.sha256,
            manifest_path=manifest_path,
            dataset_path=dataset_path,
        )

    results = await asyncio.gather(reader(), reader(), return_exceptions=True)
    reads = [
        result
        for result in results
        if isinstance(result, ControlledFittedHoldoutReadV1)
    ]
    assert (
        len(reads) == 1
        and sum(isinstance(result, Gate3FittedCustodyError) for result in results) == 1
    )
    observed = reads[0]
    assert (observed.manifest_bytes, observed.dataset_bytes) == (manifest, dataset)
    assert observed.reservation.reserved_at >= permitted
    assert observed.reservation.receipt_sha256 == receipt.sha256
    assert observed.reservation.dataset_sha256 == sha(dataset)
    assert (
        not observed.independent_first_access_verified
        and not observed.execution_authority
    )


async def test_fitted_binding_refuses_changed_bytes_and_empty_downgrade(
    fitted_database, fitted_inputs_source
):
    engines, repos, names = fitted_database
    owner = engines["owner"]
    async with owner.begin() as connection:
        await connection.run_sync(_migrate_fitted, "downgrade")
        assert (
            await connection.scalar(
                text(
                    "SELECT pg_catalog.to_regclass('public.gate3_fitted_candidate_bindings')"
                )
            )
            is None
        )
        await connection.run_sync(_migrate_fitted, "upgrade")
    await _grant(owner, names, ("publisher", "observer", "reader", "evaluator"))
    inputs, _ = await _fitted_proposal(engines, fitted_inputs_source)
    await _accept_seal(engines, inputs)
    raw = inputs.binding.payload
    changed = json.loads(raw)
    changed["selected_trial_configuration_sha256"] = sha(b"wrong selected trial")
    for payload in (b" " + raw, canonical(changed)):
        with pytest.raises(DBAPIError, match="gate3_fitted"):
            await _append(
                engines["publisher"],
                "SELECT public.gate3_fitted_binding_append(:seal,:binding,:raw)",
                seal=inputs.expected_preregistration_sha256,
                binding=sha(payload),
                raw=payload.decode("utf-8"),
            )
    await repos["publisher"].publish(inputs)
    with pytest.raises(DBAPIError, match="gate3_fitted"):
        async with owner.begin() as connection:
            await connection.run_sync(_migrate_fitted, "downgrade")
