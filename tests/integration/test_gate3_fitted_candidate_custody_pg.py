"""Native PostgreSQL and TLS acceptance for the fitted Gate 3 custody boundary."""

from __future__ import annotations

import asyncio
import json
import os
import re
import ssl
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from importlib import import_module
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import event, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import app.database.models  # noqa: F401 - register the full migration metadata.
from app.core import native_role_preflight_0056_full_diagnostic_v1 as observation
from app.core import native_writer_observation_0056_v1 as writer_observation
from app.database.base import Base
from app.database.repositories import public_runtime_bundle_custody as raw_custody
from app.demo_automation.native_server_selectors_v2 import (
    ConfiguredNativeDemoFactoriesV2,
)
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
from scripts import current0055_runtime_grants as core_acl
from scripts import current0056_fitted_catalog_measurement_v1 as fitted_measurement
from scripts import current0056_fitted_delta_catalog_v1 as fitted_delta
from scripts import current0056_native_owner_pregrant_v1 as owner_pregrant
from scripts import current0056_postgrant_acl_measurement_v1 as postgrant_acl
from scripts import current_contract_source_scopes_v1 as source_scopes
from scripts import devval_campaign_physical_catalog_v1 as devval_catalog
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
_SOURCE_ROOT = Path(__file__).resolve().parents[2]
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


def _isolated_tls_database_config():
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
    return url, ssl.create_default_context(cafile=ca_file)


@pytest.fixture
async def fitted_database():
    url, tls = _isolated_tls_database_config()
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


def _migrate_runtime_custody_head(connection):
    config = Config(str(_SOURCE_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(_SOURCE_ROOT / "migrations"))
    script = ScriptDirectory.from_config(config)
    scope = source_scopes.actual_current_source_scope(_SOURCE_ROOT)
    assert scope.actual_source_head == scope.database_target == "0056"
    assert script.get_current_head() == "0056"
    assert script.get_revision("0056").down_revision == "0055"
    context = MigrationContext.configure(
        connection,
        opts={
            "fn": lambda heads, _: script._upgrade_revs("0056", heads),
            "target_metadata": Base.metadata,
            "compare_type": True,
        },
    )
    assert context.get_current_heads() == ()
    with Operations.context(context), context.begin_transaction():
        context.run_migrations()
    readback = source_scopes.verify_database_scope(connection, _SOURCE_ROOT, scope)
    assert readback["source_head"] == readback["database_head"] == "0056"


@pytest.fixture
async def runtime_custody_actual56():
    url, tls = _isolated_tls_database_config()
    assert (url.username, url.database, url.port) == ("ctcc", "ctcc", 5432)
    assert tls.check_hostname and tls.verify_mode == ssl.CERT_REQUIRED

    def engine(target):
        return create_async_engine(
            target, poolclass=NullPool, connect_args={"ssl": tls}
        )

    database = f"ctcc_g3_raw_{uuid4().hex}"
    login = f"ctcc_g3_raw_{uuid4().hex}"
    target = url.set(database=database)
    provisioner, owner, restricted = (
        engine(url),
        engine(target),
        engine(target.set(username=login)),
    )
    created_database = created_role = False
    try:
        async with provisioner.connect() as connection:
            await connection.execution_options(isolation_level="AUTOCOMMIT")
            assert await connection.scalar(
                text("SELECT current_setting('server_version_num')::int")
            ) in range(170000, 180000)
            await connection.execute(text(f'CREATE DATABASE "{database}"'))
            created_database = True
        async with provisioner.begin() as connection:
            await connection.execute(text(f'CREATE ROLE "{login}" LOGIN'))
        created_role = True
        async with owner.begin() as connection:
            await connection.run_sync(_migrate_runtime_custody_head)
            for grant in (
                "USAGE ON SCHEMA public",
                "SELECT ON TABLE public.alembic_version",
                "EXECUTE ON FUNCTION public.public_runtime_bundle_v1_append(text,text,text,bytea)",
                "EXECUTE ON FUNCTION public.public_runtime_bundle_v1_read(text)",
            ):
                await connection.execute(text(f'GRANT {grant} TO "{login}"'))
        yield owner, restricted, database, login
    finally:
        await restricted.dispose()
        await owner.dispose()
        try:
            if created_database:
                async with provisioner.connect() as connection:
                    await connection.execution_options(isolation_level="AUTOCOMMIT")
                    await connection.execute(
                        text(f'DROP DATABASE "{database}" WITH (FORCE)')
                    )
        finally:
            try:
                if created_role:
                    async with provisioner.begin() as connection:
                        await connection.execute(text(f'DROP ROLE "{login}"'))
            finally:
                await provisioner.dispose()


async def test_actual56_runtime_raw_custody_seals_and_reads_under_tls(
    runtime_custody_actual56,
):
    owner, restricted, database, login = runtime_custody_actual56
    async with restricted.connect() as connection:
        identity = (
            await connection.execute(
                text("""
                    SELECT pg_catalog.current_database(), SESSION_USER::text,
                           SESSION_USER = CURRENT_USER,
                           pg_catalog.inet_server_port(),
                           EXISTS (SELECT 1 FROM pg_catalog.pg_stat_ssl
                                   WHERE pid = pg_catalog.pg_backend_pid() AND ssl)
                    """)
            )
        ).one()
        assert tuple(identity) == (database, login, True, 5432, True)
        assert (
            await connection.scalar(
                text("SELECT version_num FROM public.alembic_version")
            )
            == "0056"
        )
        with pytest.raises(DBAPIError) as denied:
            await connection.execute(
                text("SELECT 1 FROM public.public_runtime_raw_bundles_v1")
            )
        assert denied.value.orig.sqlstate == "42501"

    factory = async_sessionmaker(restricted, autoflush=False, expire_on_commit=False)
    repository = raw_custody.RuntimeBundleCustodyRepository(factory)
    await repository.verify_deployment()
    bundle = b'{"declared_synthetic":"current0056_runtime_raw_custody"}'
    pins = {
        "expected_sha256": sha(bundle),
        "proof_envelope_sha256": sha(b"synthetic-current0056-raw-custody-proof"),
    }
    receipt = await repository.seal_bundle(bundle_raw=bundle, **pins)
    committed = raw_custody._consume_runtime_bundle_custody_receipt(
        receipt,
        expected_bundle_sha256=pins["expected_sha256"],
        expected_proof_envelope_sha256=pins["proof_envelope_sha256"],
    )
    assert committed.bundle_raw == bundle
    assert committed.append_transaction_id != committed.readback_transaction_id

    fresh = raw_custody.RuntimeBundleCustodyRepository(factory)
    await fresh.verify_deployment()
    retained = await fresh.read_retained_bundle_by_pins(**pins)
    assert retained.bundle_id == committed.bundle_id
    assert retained.bundle_raw == bundle
    assert retained.bundle_sha256 == pins["expected_sha256"]
    assert retained.proof_envelope_sha256 == pins["proof_envelope_sha256"]
    assert retained.readback_transaction_id not in {
        None,
        committed.append_transaction_id,
        committed.readback_transaction_id,
    }
    assert retained.execution_authority is False

    async with owner.begin() as connection:
        await connection.execute(
            text("""
                CREATE TABLE public.ctcc_g3_raw_extra_parent (
                    bundle_id varchar(32) REFERENCES public.public_runtime_raw_bundles_v1(bundle_id)
                )
                """)
        )
    with pytest.raises(
        raw_custody.RuntimeBundleCustodyError,
        match="runtime_bundle_custody_unavailable",
    ):
        await fresh.read_retained_bundle_by_pins(**pins)
    with pytest.raises(
        raw_custody.RuntimeBundleCustodyError,
        match="runtime_bundle_custody_unavailable",
    ):
        await raw_custody.RuntimeBundleCustodyRepository(factory).verify_deployment()


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


async def test_fitted_ack_recovery_reads_one_committed_ack_under_tls(
    fitted_database, fitted_inputs_source, monkeypatch, tmp_path
):
    engines, repos, _ = fitted_database
    inputs, _ = await _fitted_proposal(engines, fitted_inputs_source)
    await _accept_seal(engines, inputs)
    committed = await repos["publisher"].publish(inputs)

    async def lost_readback(*_args, **_kwargs):
        raise Gate3FittedCustodyError("TEST_ACK_RECEIPT_LOST_AFTER_COMMIT")

    observer = repos["observer"]
    reader = repos["reader"]
    with monkeypatch.context() as scoped:
        scoped.setattr(observer, "_read_acknowledged", lost_readback)
        with pytest.raises(
            Gate3FittedCustodyError, match="TEST_ACK_RECEIPT_LOST_AFTER_COMMIT"
        ):
            await observer.acknowledge(inputs)

    async def counts():
        async with engines["owner"].connect() as connection:
            result = await connection.execute(
                text(
                    "SELECT (SELECT count(*) FROM public.gate3_fitted_candidate_bindings), "
                    "(SELECT count(*) FROM public.gate3_fitted_candidate_acks), "
                    "(SELECT count(*) FROM public.gate3_fitted_candidate_evaluations)"
                )
            )
            return tuple(result.one())

    assert await counts() == (1, 1, 0)
    with pytest.raises(Gate3FittedCustodyError, match="^FITTED_CUSTODY_ROLE_REFUSED$"):
        await observer.recover_acknowledgement(inputs)
    with monkeypatch.context() as scoped:
        # The real observer retains its ACK append grant under this app label.
        scoped.setattr(observer, "_role", "reader")
        with pytest.raises(
            Gate3FittedCustodyError, match="^FITTED_CUSTODY_RECOVERY_ROLE_REFUSED$"
        ):
            await observer.recover_acknowledgement(inputs)

    recovered = await reader.recover_acknowledgement(inputs)
    observed = recovered.acknowledgement
    assert observed.seal_sha256 == inputs.expected_preregistration_sha256
    assert observed.binding_sha256 == committed.binding_sha256 == inputs.binding.sha256
    assert observed.publication_xid == committed.publication_xid
    assert observed.acknowledgement_xid != committed.publication_xid
    assert not recovered.independent_ack_pin_verified
    assert not recovered.predictive_oos_eligible and not recovered.execution_authority

    pin_path = tmp_path / "reviewed-ack.sha256"
    with pin_path.open("x", encoding="ascii") as stream:
        stream.write(observed.ack_sha256 + "\n")
    pinned = await reader.read_acknowledged(
        inputs, expected_ack_sha256=pin_path.read_text(encoding="ascii").strip()
    )
    assert pinned.ack_sha256 == observed.ack_sha256
    assert pinned.acknowledgement_xid == observed.acknowledgement_xid
    assert pinned.database_read_at >= observed.database_read_at
    assert await counts() == (1, 1, 0)


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

    evaluator = repos["evaluator"]
    db_now = await _now(engines["owner"])
    with pytest.raises(
        Gate3FittedCustodyError, match="^FITTED_CUSTODY_GLOBAL_TIME_NOT_REACHED$"
    ):
        await evaluator.reserve_evaluation(
            inputs,
            expected_ack_sha256=ack.ack_sha256,
            receipt_sha256=receipt.sha256,
            dataset_sha256=sha(dataset),
            not_before=db_now + timedelta(hours=1),
        )

    async def reader():
        return await read_fitted_prospective_holdout_once(
            custody=evaluator,
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
    # The later SQL gate now reaches the append function. The durable unique
    # reservation still rejects the second attempt on this exact seal.
    with pytest.raises(
        Gate3FittedCustodyError, match="^FITTED_CUSTODY_TRANSACTION_UNRESOLVED$"
    ):
        await evaluator.reserve_evaluation(
            inputs,
            expected_ack_sha256=ack.ack_sha256,
            receipt_sha256=receipt.sha256,
            dataset_sha256=sha(dataset),
            not_before=permitted,
        )
    async with engines["owner"].connect() as connection:
        assert (
            await connection.scalar(
                text("SELECT count(*) FROM public.gate3_fitted_candidate_evaluations")
            )
            == 1
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


# DB0056 fitted-delta physical catalog: diagnostic only, no native startup seal.
def _delta56_identifier(value: str) -> str:
    assert value.isascii() and value.replace("_", "").isalnum()
    assert value.startswith("ctcc_") and len(value) < 63
    return '"' + value + '"'


@pytest.fixture
async def _delta56_two_fresh_databases():
    url, tls = _isolated_tls_database_config()
    assert (url.username, url.database, url.port) == ("ctcc", "ctcc", 5432)
    assert tls.check_hostname and tls.verify_mode == ssl.CERT_REQUIRED
    fitted = {
        key: f"ctcc_g3f_{key}_{uuid4().hex[:12]}"
        for key in ("publisher", "observer", "reader", "evaluator")
    }
    demo = tuple(
        f"ctcc_native_{key}_{uuid4().hex[:12]}"
        for key in ("writer", "custody", "frame")
    )
    names = (*fitted.values(), *demo)
    assert len({*names, "ctcc"}) == 8
    databases = tuple(f"ctcc_g3fcat_{uuid4().hex[:12]}" for _ in range(2))
    assert len(set(databases)) == 2
    admin = create_async_engine(url, poolclass=NullPool, connect_args={"ssl": tls})
    engines = []
    created_roles = []
    created_databases = []
    try:
        fitted_delta.pinned_migration(_SOURCE_ROOT)
        for name in names:
            async with admin.begin() as connection:
                await connection.execute(
                    text(f"CREATE ROLE {_delta56_identifier(name)} LOGIN")
                )
            created_roles.append(name)
        for database in databases:
            async with admin.connect() as connection:
                await connection.execution_options(isolation_level="AUTOCOMMIT")
                await connection.execute(
                    text(f"CREATE DATABASE {_delta56_identifier(database)}")
                )
            created_databases.append(database)
            engine = create_async_engine(
                url.set(database=database),
                poolclass=NullPool,
                connect_args={"ssl": tls},
            )
            engines.append(engine)
            async with engine.begin() as connection:
                await connection.run_sync(_migrate_runtime_custody_head)
                for role in names:
                    await connection.execute(
                        text(
                            "GRANT USAGE ON SCHEMA public TO "
                            f"{_delta56_identifier(role)}"
                        )
                    )
                for kind, role in fitted.items():
                    for signature in _ROLES[kind]:
                        await connection.execute(
                            text(
                                "GRANT EXECUTE ON FUNCTION public."
                                f"{signature} TO {_delta56_identifier(role)}"
                            )
                        )
        yield engines, fitted, demo, databases
    finally:
        for engine in reversed(engines):
            await engine.dispose()
        try:
            for database in reversed(created_databases):
                async with admin.connect() as connection:
                    await connection.execution_options(isolation_level="AUTOCOMMIT")
                    await connection.execute(
                        text(
                            f"DROP DATABASE {_delta56_identifier(database)} WITH (FORCE)"
                        )
                    )
        finally:
            try:
                for role in reversed(created_roles):
                    async with admin.begin() as connection:
                        await connection.execute(
                            text(f"DROP ROLE {_delta56_identifier(role)}")
                        )
            finally:
                await admin.dispose()


async def _delta56_read_catalog(
    engine, *, capture_cluster_identity=False
) -> tuple[dict, str, int, str | None]:
    async with engine.connect() as connection, connection.begin():
        await connection.execute(
            text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        )
        await connection.execute(text("SET LOCAL search_path TO pg_catalog, pg_temp"))
        result = await connection.execute(text(fitted_delta.CATALOG_SQL))
        value = result.scalar_one()
        if isinstance(value, str):
            value = json.loads(value)
        assert type(value) is dict
        identity = (
            await connection.execute(
                text("""
                SELECT pg_catalog.current_database(), d.oid
                FROM pg_catalog.pg_database d
                WHERE d.datname=pg_catalog.current_database()
            """)
            )
        ).one()
        assert type(identity[0]) is str and type(identity[1]) is int
        cluster_identifier = None
        if capture_cluster_identity:
            cluster_identifier = await connection.scalar(
                text(
                    "SELECT system_identifier::text FROM pg_catalog.pg_control_system()"
                )
            )
            assert type(cluster_identifier) is str and cluster_identifier.isdecimal()
        return value, identity[0], identity[1], cluster_identifier


async def _delta56_owner_change(engine, sql: str) -> None:
    async with engine.begin() as connection:
        await connection.execute(text(sql))


async def test_actual0056_two_fresh_fitted_catalogs_and_drift_refusal(
    _delta56_two_fresh_databases,
):
    engines, fitted, demo, databases = _delta56_two_fresh_databases
    roles = {"owner": "ctcc", "demo_roles": demo, "fitted_roles": fitted}
    retain_measurement = os.environ.get(devval_catalog.HERMETIC_CATALOG_ENV) == "1"
    first, first_name, first_oid, cluster_identifier = await _delta56_read_catalog(
        engines[0], capture_cluster_identity=retain_measurement
    )
    second, second_name, second_oid, _ = await _delta56_read_catalog(engines[1])
    assert (first_name, second_name) == databases and first_oid != second_oid
    expected_commit_guards = [
        ("gate3_fitted_ack_commit_guard", "gate3_fitted_candidate_acks"),
        ("gate3_fitted_binding_commit_guard", "gate3_fitted_candidate_bindings"),
        ("gate3_fitted_evaluation_commit_guard", "gate3_fitted_candidate_evaluations"),
    ]
    for document in (first, second):
        observed_commit_guards = sorted(
            (row["name"], row["table"])
            for row in document["constraints"]
            if row["type"] == "t"
            and row["validated"] is True
            and row["deferrable"] is True
            and row["deferred"] is True
        )
        assert observed_commit_guards == expected_commit_guards
    digest = fitted_delta.inspect_catalog_document(first, **roles)
    assert fitted_delta.inspect_catalog_document(second, **roles) == digest
    assert sha(canonical(first)) == digest
    if retain_measurement:
        assert cluster_identifier is not None
        fitted_measurement.retain_hermetic_measurement(
            catalog=first,
            **roles,
            cluster_system_identifier=cluster_identifier,
            database_name=first_name,
            database_oid=first_oid,
        )
    with pytest.raises(
        fitted_delta.CatalogRefusal, match="fitted_catalog_native_reference_unsealed"
    ):
        fitted_delta.require_native_reference(first, **roles)

    trials = (
        (
            (
                "ALTER FUNCTION public.gate3_fitted_binding_read(text) "
                "SET search_path=pg_catalog,public,pg_temp"
            ),
            (
                "ALTER FUNCTION public.gate3_fitted_binding_read(text) "
                "SET search_path=pg_catalog,pg_temp"
            ),
            "fitted_catalog_function_shape_changed",
        ),
        (
            (
                "GRANT EXECUTE ON FUNCTION public.gate3_fitted_binding_read(text) "
                f"TO {_delta56_identifier(demo[0])}"
            ),
            (
                "REVOKE EXECUTE ON FUNCTION public.gate3_fitted_binding_read(text) "
                f"FROM {_delta56_identifier(demo[0])}"
            ),
            "fitted_catalog_function_acl_changed",
        ),
        (
            (
                "ALTER TABLE public.gate3_capture_schedule_key_claims "
                "DISABLE TRIGGER gate3_fitted_candidate_claim_guard"
            ),
            (
                "ALTER TABLE public.gate3_capture_schedule_key_claims "
                "ENABLE ALWAYS TRIGGER gate3_fitted_candidate_claim_guard"
            ),
            "fitted_catalog_trigger_changed",
        ),
        (
            (
                "GRANT SELECT ON TABLE public.gate3_fitted_candidate_acks "
                f"TO {_delta56_identifier(fitted['reader'])}"
            ),
            (
                "REVOKE SELECT ON TABLE public.gate3_fitted_candidate_acks "
                f"FROM {_delta56_identifier(fitted['reader'])}"
            ),
            "fitted_catalog_table_acl_changed",
        ),
    )
    for mutation, undo, refusal in trials:
        await _delta56_owner_change(engines[1], mutation)
        try:
            changed, name, oid, _ = await _delta56_read_catalog(engines[1])
            assert (name, oid) == (second_name, second_oid)
            with pytest.raises(fitted_delta.CatalogRefusal, match=refusal):
                fitted_delta.inspect_catalog_document(changed, **roles)
            assert changed != first
        finally:
            await _delta56_owner_change(engines[1], undo)
        restored, name, oid, _ = await _delta56_read_catalog(engines[1])
        assert (name, oid) == (second_name, second_oid)
        assert fitted_delta.inspect_catalog_document(restored, **roles) == digest


_ROLE_OBS56_IDENTITY = text("""
SELECT current_user::text, session_user::text,
       pg_catalog.current_setting('role'), pg_catalog.pg_backend_pid(),
       (SELECT oid::bigint FROM pg_catalog.pg_roles WHERE rolname=current_user),
       EXISTS(SELECT 1 FROM pg_catalog.pg_stat_ssl
              WHERE pid=pg_catalog.pg_backend_pid() AND ssl),
       pg_catalog.current_setting('server_version_num'),
       (SELECT oid::bigint FROM pg_catalog.pg_database
        WHERE datname=pg_catalog.current_database()),
       pg_catalog.pg_postmaster_start_time(),
       pg_catalog.inet_server_addr()::text,
       pg_catalog.inet_server_port()
""")
_ROLE_OBS56_REFUSAL = "^native_roles_0056_observation_unavailable$"


_GRANT_LOCK_0056 = text("SELECT pg_catalog.pg_advisory_xact_lock(5883109675522810055)")
_GRANT_TX_SETTINGS = (
    "SET TRANSACTION ISOLATION LEVEL READ COMMITTED",
    "SET TRANSACTION READ WRITE",
    *owner_pregrant._OWNER_SETTINGS[2:],
)


async def _read_test_fitted_write_graph(connection, owner_name, demo):
    await connection.exec_driver_sql("SET LOCAL search_path TO pg_catalog, pg_temp")
    document = await connection.scalar(text(fitted_delta.OWNER_READWRITE_CATALOG_SQL))
    fitted_delta.inspect_owner_readwrite_catalog(
        document, owner=owner_name, demo_roles=demo
    )
    await connection.exec_driver_sql(
        "SET LOCAL search_path TO pg_catalog, public, pg_temp"
    )
    # Compare only physical facts across profiles after validating each actual
    # transaction separately. Neither digest becomes a startup reference.
    return {
        key: value
        for key, value in document.items()
        if key not in {"read_only", "read_committed"}
    }


async def _install_test_demo_grants(owner, demo):
    names = dict(zip(("writer", "custody", "frame_reader"), demo, strict=True))
    sql = owner_pregrant.exact_42_grant_sql_0056(**names)
    owner_name = owner.url.username
    assert re.fullmatch(r"ctcc_native_obs56_[0-9a-f]{12}", owner.url.database)
    fitted_delta.pinned_migration(_SOURCE_ROOT)
    owner_factory = async_sessionmaker(owner, autoflush=False, expire_on_commit=False)
    async with owner_factory() as session, session.begin():
        for setting in observation._OWNER_SETTINGS:
            await session.execute(text(setting))
        read_identity = await observation._identity_at_0056(session, owner_name)
        read_graph = await observation._catalog(session, fitted_delta.CATALOG_SQL)
        fitted_delta.inspect_catalog_document(
            read_graph, owner=owner_name, demo_roles=demo, fitted_roles=None
        )
    fitted_physical = {
        key: value
        for key, value in read_graph.items()
        if key not in {"read_only", "repeatable"}
    }
    plan = owner_pregrant._grant_objects(**names)
    relations = ", ".join(
        f"public.{name}"
        for name in sorted(
            {item.target for item in plan if item.kind == "TABLE"} | fitted_delta.TABLES
        )
    )

    async with owner.begin() as connection:
        for setting in _GRANT_TX_SETTINGS:
            await connection.exec_driver_sql(setting)
        await connection.execute(_GRANT_LOCK_0056)
        assert (
            await observation._identity_at_0056(connection, owner_name) == read_identity
        )
        await connection.exec_driver_sql(
            f"LOCK TABLE {relations} IN ACCESS EXCLUSIVE MODE NOWAIT"
        )
        owner_oid = read_identity[3]
        assert (
            await connection.scalar(observation.current55._OWNER_OID_SQL) == owner_oid
        )
        assert tuple(
            (
                await connection.execute(
                    core_acl._OWNER_POLICY_SQL, {"owner": owner_oid}
                )
            ).one()
        ) == (True, True)
        inherited = (
            owner_pregrant.inherited56.observe_owner_readwrite_installation_0056(
                await connection.scalar(
                    text(owner_pregrant.inherited56.OWNER_CATALOG_SQL)
                )
            )
        )
        assert inherited["role_oid"] == owner_oid
        assert inherited["native_definitions_sha256"] == (
            "6243338abe9cfcbb4eb613bc3d21cd7adbaf8059644e8b7a1c2df9755ac340c7"
        )
        assert (
            await _read_test_fitted_write_graph(connection, owner_name, demo)
            == fitted_physical
        )
        role_oids = await core_acl._roles(connection, demo, owner_oid)
        await observation._require_no_effective_demo_fitted_execute(connection, demo)
        before = await owner_pregrant._exact_acl_phase(
            connection, role_oids, owner_oid, **names, granted=False
        )
        for statement in sql:
            await connection.exec_driver_sql(statement)
        assert (
            await owner_pregrant._exact_acl_phase(
                connection, role_oids, owner_oid, **names, granted=True
            )
            == before
        )
        await observation._require_no_effective_demo_fitted_execute(connection, demo)
        assert (
            await _read_test_fitted_write_graph(connection, owner_name, demo)
            == fitted_physical
        )
        assert await core_acl._roles(connection, demo, owner_oid) == role_oids
        assert (
            await observation._identity_at_0056(connection, owner_name) == read_identity
        )


async def _verify_actual_tls_roles(engines):
    async with AsyncExitStack() as stack:
        connections = [
            await stack.enter_async_context(engine.connect()) for engine in engines
        ]
        observed = [
            tuple((await connection.execute(_ROLE_OBS56_IDENTITY)).one())
            for connection in connections
        ]
    names = tuple(engine.url.username for engine in engines)
    assert len(set(names)) == 4
    assert tuple(row[:3] for row in observed) == tuple(
        (name, name, "none") for name in names
    )
    assert len({row[3] for row in observed}) == len({row[4] for row in observed}) == 4
    assert all(row[5] is True and row[6] == "170011" for row in observed)
    assert len({row[7:] for row in observed}) == 1


@asynccontextmanager
async def _open_actual_0056_role_observation_lab(*, grant_demo: bool):
    url, tls = _isolated_tls_database_config()
    assert (url.username, url.database, url.port) == ("ctcc", "ctcc", 5432)
    assert tls.check_hostname and tls.verify_mode == ssl.CERT_REQUIRED
    database = f"ctcc_native_obs56_{uuid4().hex[:12]}"
    demo = tuple(
        f"ctcc_obs56_{role}_{uuid4().hex[:12]}"
        for role in ("writer", "custody", "frame")
    )
    database_sql = _delta56_identifier(database)
    assert len(set(demo)) == 3

    def engine(target):
        return create_async_engine(
            target, poolclass=NullPool, connect_args={"ssl": tls}
        )

    target = url.set(database=database)
    admin = engine(url)
    owner = engine(target)
    role_engines = tuple(engine(target.set(username=name)) for name in demo)
    created_database = False
    created_roles = False
    try:
        async with admin.connect() as connection:
            await connection.execution_options(isolation_level="AUTOCOMMIT")
            initial = tuple((await connection.execute(_ROLE_OBS56_IDENTITY)).one())
            assert initial[:3] == ("ctcc", "ctcc", "none")
            assert initial[5] is True and initial[6] == "170011"
            await connection.execute(text(f"CREATE DATABASE {database_sql}"))
            created_database = True
        async with admin.begin() as connection:
            for name in demo:
                await connection.execute(
                    text(f"CREATE ROLE {_delta56_identifier(name)} LOGIN")
                )
        created_roles = True
        async with owner.begin() as connection:
            await connection.run_sync(_migrate_runtime_custody_head)
        owner_factory = async_sessionmaker(
            owner, autoflush=False, expire_on_commit=False
        )
        pregrant = None
        if os.environ.get(devval_catalog.HERMETIC_CATALOG_ENV) == "1":
            pregrant = await owner_pregrant.observe_owner_pregrant_diagnostic_0056(
                owner_factory,
                source=_SOURCE_ROOT,
                writer=demo[0],
                custody=demo[1],
                frame_reader=demo[2],
            )
        if grant_demo:
            await _install_test_demo_grants(owner, demo)
        await _verify_actual_tls_roles((owner, *role_engines))
        factories = tuple(
            async_sessionmaker(bound, autoflush=False, expire_on_commit=False)
            for bound in (owner, *role_engines)
        )
        owner_factory, writer_factory, custody_factory, frame_factory = factories
        yield (
            owner,
            owner_factory,
            ConfiguredNativeDemoFactoriesV2(
                writer_factory,
                custody_factory,
                frame_factory,
                role_engines[1],
                role_engines[2],
            ),
            pregrant,
        )
    finally:
        try:
            async with AsyncExitStack() as stack:
                for candidate in (owner, *role_engines):
                    stack.push_async_callback(candidate.dispose)
        finally:
            try:
                if created_database:
                    async with admin.connect() as connection:
                        await connection.execution_options(isolation_level="AUTOCOMMIT")
                        await connection.execute(
                            text(f"DROP DATABASE {database_sql} WITH (FORCE)")
                        )
            finally:
                try:
                    if created_roles:
                        async with admin.begin() as connection:
                            for name in reversed(demo):
                                await connection.execute(
                                    text(f"DROP ROLE {_delta56_identifier(name)}")
                                )
                finally:
                    await admin.dispose()


@pytest.fixture
async def _actual_0056_role_observation_lab():
    async with _open_actual_0056_role_observation_lab(grant_demo=True) as lab:
        yield lab


@pytest.fixture
async def _actual_0056_ungranted_lab():
    async with _open_actual_0056_role_observation_lab(grant_demo=False) as lab:
        yield lab


async def test_actual0056_owner_grant_lock_and_rollback(
    _actual_0056_ungranted_lab,
):
    owner, owner_factory, selected, _ = _actual_0056_ungranted_lab
    demo = tuple(
        factory.kw["bind"].url.username
        for factory in (
            selected.writer_factory,
            selected.custody_factory,
            selected.frame_reader_factory,
        )
    )
    names = dict(zip(("writer", "custody", "frame_reader"), demo, strict=True))
    before = await owner_pregrant.observe_owner_pregrant_diagnostic_0056(
        owner_factory, source=_SOURCE_ROOT, **names
    )
    async with owner.begin() as competing:
        await competing.execute(_GRANT_LOCK_0056)
        with pytest.raises(DBAPIError) as locked:
            await _install_test_demo_grants(owner, demo)
        assert locked.value.orig.sqlstate == "55P03"

    executed_grants = []

    def fail_last_grant(_conn, _cursor, statement, parameters, _ctx, _many):
        if statement.startswith("GRANT "):
            executed_grants.append(statement)
            if len(executed_grants) == 42:
                return "SELECT 1/0", parameters
        return statement, parameters

    event.listen(
        owner.sync_engine, "before_cursor_execute", fail_last_grant, retval=True
    )
    try:
        with pytest.raises(DBAPIError) as failed:
            await _install_test_demo_grants(owner, demo)
        assert failed.value.orig.sqlstate == "22012"
        assert tuple(executed_grants) == owner_pregrant.exact_42_grant_sql_0056(**names)
    finally:
        event.remove(owner.sync_engine, "before_cursor_execute", fail_last_grant)
    assert (
        await owner_pregrant.observe_owner_pregrant_diagnostic_0056(
            owner_factory, source=_SOURCE_ROOT, **names
        )
        == before
    )

    await _install_test_demo_grants(owner, demo)
    readback = await observation.observe_restricted_native_roles_0056_v1(
        selected, source=_SOURCE_ROOT
    )
    assert (
        readback.inherited_native_definitions_sha256
        == before.inherited_native_definitions_sha256
    )
    assert readback.startup_admission is readback.execution_authority is False


async def test_actual0056_full_native_role_observation_tls(
    _actual_0056_role_observation_lab,
):
    _, owner_factory, selected, pregrant = _actual_0056_role_observation_lab
    first = await observation.observe_native_roles_0056_v1(
        selected, owner_factory=owner_factory, source=_SOURCE_ROOT
    )
    second = await observation.observe_native_roles_0056_v1(
        selected, owner_factory=owner_factory, source=_SOURCE_ROOT
    )
    assert first == second
    assert first.database_head == "0056"
    assert first.admission == "DENY"
    assert first.native_reference_sealed is first.native_baseline_admission is False
    assert first.startup_admission is first.execution_authority is False
    assert first.account_complete is False
    restricted = await observation.observe_restricted_native_roles_0056_v1(
        selected, source=_SOURCE_ROOT
    )
    assert (
        restricted.inherited_native_definitions_sha256,
        restricted.frame_writer_observation_sha256,
        restricted.raw_custody_catalog_sha256,
    ) == (
        first.inherited_native_definitions_sha256,
        first.frame_writer_observation_sha256,
        first.raw_custody_catalog_sha256,
    )
    assert restricted.startup_admission is restricted.execution_authority is False
    async with selected.writer_factory() as session, session.begin():
        await observation.current55._session_settings(session, frame=True)
        writer = await writer_observation.observe_restricted_readwrite_writer_0056_v1(
            session
        )
    assert writer.native_definitions_sha256 == first.inherited_native_definitions_sha256
    assert writer.startup_admission is writer.execution_authority is False
    if pregrant is not None:
        postgrant_acl.retain_hermetic_measurement(
            cluster_system_identifier_sha256=pregrant.cluster_system_identifier_sha256,
            full=first,
            restricted=restricted,
        )
        fitted_measurement.retain_hermetic_inherited_measurement(
            cluster_system_identifier_sha256=pregrant.cluster_system_identifier_sha256,
            owner_pregrant_inherited_sha256=pregrant.inherited_native_definitions_sha256,
            owner_pregrant_readwrite_inherited_sha256=(
                pregrant.inherited_readwrite_native_definitions_sha256
            ),
            owner_pregrant_fitted_raw_sha256=pregrant.fitted_raw_owner_catalog_sha256,
            owner_pregrant_fitted_normalized_sha256=pregrant.fitted_normalized_owner_catalog_sha256,
            postgrant_readonly_inherited_sha256=first.inherited_native_definitions_sha256,
            postgrant_readwrite_inherited_sha256=writer.native_definitions_sha256,
        )


async def test_actual0056_full_native_role_rejects_swapped_roles_and_extra_grant(
    _actual_0056_role_observation_lab,
):
    owner, owner_factory, selected, _ = _actual_0056_role_observation_lab
    args = {"owner_factory": owner_factory, "source": _SOURCE_ROOT}
    baseline = await observation.observe_native_roles_0056_v1(selected, **args)
    swapped = replace(
        selected,
        writer_factory=selected.frame_reader_factory,
        frame_reader_factory=selected.writer_factory,
        frame_reader_engine=selected.writer_factory.kw["bind"],
    )
    with pytest.raises(
        observation.NativeRoles0056ObservationError, match=_ROLE_OBS56_REFUSAL
    ):
        await observation.observe_native_roles_0056_v1(swapped, **args)

    writer = selected.writer_factory.kw["bind"]
    writer_name = _delta56_identifier(writer.url.username)
    relation = "public.gate3_fitted_candidate_bindings"
    async with owner.begin() as connection:
        await connection.execute(
            text(f"GRANT SELECT ON TABLE {relation} TO {writer_name}")
        )
    try:
        async with writer.connect() as connection:
            assert (
                await connection.scalar(
                    text(
                        "SELECT pg_catalog.has_table_privilege(current_user,:relation,'SELECT')"
                    ),
                    {"relation": relation},
                )
                is True
            )
        with pytest.raises(
            observation.NativeRoles0056ObservationError, match=_ROLE_OBS56_REFUSAL
        ):
            await observation.observe_native_roles_0056_v1(selected, **args)
    finally:
        async with owner.begin() as connection:
            await connection.execute(
                text(f"REVOKE SELECT ON TABLE {relation} FROM {writer_name}")
            )
    assert await observation.observe_native_roles_0056_v1(selected, **args) == baseline

    # PUBLIC EXECUTE is effective without an ACL directly naming a Demo role.
    routine = "public.gate3_fitted_binding_read(text)"
    demo_names = tuple(
        factory.kw["bind"].url.username
        for factory in (
            selected.writer_factory,
            selected.custody_factory,
            selected.frame_reader_factory,
        )
    )
    async with owner.begin() as connection:
        await connection.exec_driver_sql(
            f"GRANT EXECUTE ON FUNCTION {routine} TO PUBLIC"
        )
    try:
        async with writer.connect() as connection:
            assert (
                await connection.scalar(
                    text(
                        "SELECT pg_catalog.has_function_privilege(current_user,:routine,'EXECUTE')"
                    ),
                    {"routine": routine},
                )
                is True
            )
        async with selected.writer_factory() as session, session.begin():
            await observation.current55._session_settings(session, frame=False)
            writer_oid = await session.scalar(
                text("SELECT current_user::regrole::oid::bigint")
            )
            await observation.current55._exact_existing_acl(
                await session.connection(), demo_names, writer_oid
            )
            with pytest.raises(
                observation.NativeRoles0056ObservationError, match=_ROLE_OBS56_REFUSAL
            ):
                await observation._require_no_effective_demo_fitted_execute(
                    session, demo_names
                )
        with pytest.raises(
            observation.NativeRoles0056ObservationError, match=_ROLE_OBS56_REFUSAL
        ):
            await observation.observe_native_roles_0056_v1(selected, **args)
    finally:
        async with owner.begin() as connection:
            await connection.exec_driver_sql(
                f"REVOKE EXECUTE ON FUNCTION {routine} FROM PUBLIC"
            )
    assert await observation.observe_native_roles_0056_v1(selected, **args) == baseline


async def test_actual0056_between_snapshot_grant_denies_fresh_writer_recheck(
    _actual_0056_role_observation_lab, monkeypatch
):
    owner, _, selected, _ = _actual_0056_role_observation_lab
    baseline = await observation.observe_restricted_native_roles_0056_v1(
        selected, source=_SOURCE_ROOT
    )
    assert baseline.startup_admission is baseline.execution_authority is False
    writer_name = _delta56_identifier(selected.writer_factory.kw["bind"].url.username)
    relation = "public.gate3_fitted_candidate_bindings"
    original = observation._restricted_frame_writer
    injected = False

    async def grant_after_first_snapshot(*args):
        nonlocal injected
        first = await original(*args)
        # The first writer/frame transactions have closed. This independent
        # owner transaction commits an actual extra grant before fresh readback.
        async with owner.begin() as connection:
            await connection.execute(
                text(f"GRANT SELECT ON TABLE {relation} TO {writer_name}")
            )
        async with selected.writer_factory() as session, session.begin():
            assert (
                await session.scalar(
                    text(
                        "SELECT pg_catalog.has_table_privilege("
                        "current_user,:relation,'SELECT')"
                    ),
                    {"relation": relation},
                )
                is True
            )
        injected = True
        return first

    monkeypatch.setattr(
        observation, "_restricted_frame_writer", grant_after_first_snapshot
    )
    try:
        with pytest.raises(
            observation.NativeRoles0056ObservationError, match=_ROLE_OBS56_REFUSAL
        ):
            await observation.observe_restricted_native_roles_0056_v1(
                selected, source=_SOURCE_ROOT
            )
        assert injected
    finally:
        monkeypatch.setattr(observation, "_restricted_frame_writer", original)
        async with owner.begin() as connection:
            await connection.execute(
                text(f"REVOKE SELECT ON TABLE {relation} FROM {writer_name}")
            )
    assert (
        await observation.observe_restricted_native_roles_0056_v1(
            selected, source=_SOURCE_ROOT
        )
        == baseline
    )


async def test_actual0056_native_lifespan_denies_before_recovery_cas(
    _actual_0056_role_observation_lab, monkeypatch
):
    from unittest.mock import AsyncMock

    from app import main as app_main
    from app.config.settings import Settings
    from app.core import native_only_lifespan
    from app.core import native_startup_control_observation_0056_v1 as startup56
    from app.core.native_role_preflight_v1 import NativeRolePreflightError
    from app.database.models.demo_automation import DemoAutomationState

    _, owner_factory, selected, _ = _actual_0056_role_observation_lab
    writer_factory = selected.writer_factory
    writer_engine = writer_factory.kw["bind"]
    observed = await observation.observe_restricted_native_roles_0056_v1(
        selected, source=_SOURCE_ROOT
    )
    assert observed.database_head == "0056"
    assert observed.startup_admission is observed.execution_authority is False

    instant = datetime(2026, 1, 1, tzinfo=UTC)
    async with owner_factory.begin() as session:
        session.add(
            DemoAutomationState(
                id=1,
                control_revision=7,
                session_date=instant.date(),
                armed=True,
                restart_latch_required=True,
                active_trades={"synthetic-tracked-position": {"retained": True}},
                updated_at=instant,
            )
        )

    async def read_control():
        async with writer_factory() as session:
            return await session.scalar(
                text(
                    "SELECT pg_catalog.to_jsonb(s)::text FROM public.demo_automation_state s WHERE s.id=1"
                )
            )

    before = await read_control()
    before_state = json.loads(before)
    assert before_state["control_revision"] == 7 and before_state["armed"] is True
    observed_control = await startup56.observe_startup_control_0056_v1(selected)
    assert observed_control.database_head == "0056"
    assert observed_control.control == native_only_lifespan.ExistingControl(
        revision=7,
        armed=True,
        emergency_stop=False,
        locked=False,
        restart_latch_required=True,
    )
    assert (
        observed_control.startup_admission
        is observed_control.execution_authority
        is False
    )
    assert await read_control() == before
    settings = Settings(
        _env_file=None,
        ctcc_native_only_lifespan_v1=True,
        environment="production",
        trading_mode="okx_demo",
        web_concurrency=1,
        okx_demo_enabled=True,
        okx_demo_api_key="",
        okx_demo_api_secret="",
        okx_demo_api_passphrase="",
        **dict.fromkeys(native_only_lifespan._MUST_BE_FALSE, False),
    )
    forbidden_recovery = AsyncMock(side_effect=AssertionError("unexpected recovery"))
    automation = object()
    monkeypatch.setattr(app_main, "settings", settings)
    monkeypatch.setattr(app_main, "AsyncSessionFactory", writer_factory)
    monkeypatch.setattr(app_main, "engine", writer_engine)
    monkeypatch.setattr(app_main, "safe_demo_automation", automation)
    monkeypatch.setattr(app_main, "start_native_only", forbidden_recovery)
    monkeypatch.setattr(
        observation.selectors,
        "select_configured_native_demo_factories_v2",
        lambda: selected,
    )

    instance = app_main._build_app()
    with pytest.raises(
        NativeRolePreflightError, match="^native_role_preflight_unavailable$"
    ):
        async with app_main.lifespan(instance):
            pytest.fail("DB0056 unexpectedly served without startup admission")
    assert instance.state.native_only_recovery_confirmed is False
    forbidden_recovery.assert_not_awaited()
    assert await read_control() == before


async def test_actual0056_restricted_account_control_read_replays_exact_scope(
    _actual_0056_role_observation_lab,
):
    from app.core import native_account_control_observation_0056_v1 as account56
    from app.core.native_startup_control_observation_0056_v1 import (
        NativeStartupControl0056Error,
    )
    from app.database.repositories.demo_control import DemoControlRepository
    from app.trade_qualification.demo_control import ControlScope

    owner, owner_factory, selected, _ = _actual_0056_role_observation_lab
    seed = DemoControlRepository(owner_factory, clock=lambda: datetime.now(UTC))
    first_scope = ControlScope("demo", "000001")
    second_scope = ControlScope("demo", "1")
    await seed.latch_stop(first_scope, command_id="a" * 64)
    first = await seed.latch_stop(first_scope, command_id="b" * 64)
    second = await seed.latch_stop(second_scope, command_id="c" * 64)

    async def read(scope):
        return await account56.observe_account_control_0056_v1(selected, scope)

    # A second transaction holds the writer row lock. This observation must
    # replay the committed head without trying to lock the row for mutation.
    async with owner.begin() as connection:
        await connection.execute(
            text("""
            SELECT 1 FROM public.demo_account_controls
            WHERE environment='demo' AND account_id=:uid FOR UPDATE
            """),
            {"uid": first_scope.account_id},
        )
        actual = await read(first_scope)
        assert actual.control.state == first.state and first.state.revision == 2
        assert actual.control.event_sha256 == first.event_sha256
        assert actual.control.observed_at >= first.state.updated_at
        assert actual.database_head == "0056"
        assert actual.startup_admission is actual.execution_authority is False
        assert actual.control.execution_authority is False

    actual_second = await read(second_scope)
    assert actual_second.control.state == second.state and second.state.revision == 1
    assert actual_second.control.event_sha256 == second.event_sha256
    assert actual_second.control.event_sha256 != actual.control.event_sha256
    with pytest.raises(NativeStartupControl0056Error):
        await read(ControlScope("demo", "000002"))

    # A restricted role with an intact current row but a hidden lineage tail
    # may not turn that incomplete snapshot into a successful read.
    async with owner.begin() as connection:
        await connection.exec_driver_sql(
            "ALTER TABLE public.demo_control_journal ENABLE ROW LEVEL SECURITY"
        )
    try:
        with pytest.raises(NativeStartupControl0056Error):
            await read(first_scope)
    finally:
        async with owner.begin() as connection:
            await connection.exec_driver_sql(
                "ALTER TABLE public.demo_control_journal DISABLE ROW LEVEL SECURITY"
            )
    assert (await read(first_scope)).control.event_sha256 == first.event_sha256
