"""Real native SQLAlchemy engine shapes without connections; no PG acceptance."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import asyncpg
import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.database.repositories.public_receipt_post_read_observation import (
    PublicReceiptPostReadRepository,
)
from app.database.repositories.public_receipt_witness import (
    PublicReceiptWitnessRepository,
)
from app.mie.validation import public_checkpoint_service as module
from tests.unit.research.public_receipt_fixtures import plan_for


@pytest.fixture
def native_pair(monkeypatch):
    calls = []

    async def forbidden_connect(*args, **kwargs):
        calls.append(True)
        raise AssertionError("unit guard must never connect")

    monkeypatch.setattr(asyncpg, "connect", forbidden_connect)
    engine = create_async_engine(
        "postgresql+asyncpg://ctcc_fixture@127.0.0.1/ctcc_no_io", poolclass=NullPool
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    witness = PublicReceiptWitnessRepository(factory)
    observation = PublicReceiptPostReadRepository(factory, witness)
    yield witness, observation, engine, calls
    engine.sync_engine.dispose()
    assert not calls


@pytest.mark.asyncio
async def test_private_factories_ignore_caller_swap_restore_during_repository_await(
    native_pair, monkeypatch
):
    witness, observation, engine, calls = native_pair
    caller_pins = module._source_database_pins(witness, observation)
    owned_witness, owned_observation = module._owned_source_repositories(caller_pins)
    owned_pins = module._source_database_pins(owned_witness, owned_observation)
    assert owned_witness is not witness and owned_observation is not observation
    assert owned_witness.session_factory is not witness.session_factory
    assert owned_observation.session_factory is not observation.session_factory
    assert owned_witness.session_factory.kw["bind"] is engine
    original_factory = witness.session_factory

    async def callback(*args, **kwargs):
        calls.append(True)
        raise AssertionError("caller provider callback must not run")

    # Substitute SQL results only: execute the original private repository role
    # methods and native AsyncSession boundaries without opening a connection.
    row = SimpleNamespace(
        direct_login=True,
        privileged=False,
        owner_member=False,
        member_of_other_role=False,
        direct_table_access=False,
        can_append=True,
        can_read=True,
        can_create=False,
        can_witness=False,
    )

    async def execute(session, statement):
        witness.session_factory = callback
        observation.session_factory = callback
        witness.read_latest = callback
        observation.append_new = callback
        try:
            await asyncio.sleep(0)
            assert owned_witness.session_factory is not callback
            assert owned_observation.append_new.__self__ is owned_observation
            return SimpleNamespace(one_or_none=lambda: row)
        finally:
            witness.session_factory = original_factory
            observation.session_factory = original_factory
            del witness.read_latest
            del observation.append_new

    monkeypatch.setattr(AsyncSession, "execute", execute)
    await owned_witness.verify_role()
    await owned_observation.verify_role()
    module._source_database_pins(witness, observation, caller_pins)
    module._source_database_pins(owned_witness, owned_observation, owned_pins)
    assert not calls


def test_native_unconnected_factory_and_original_methods_have_positive_guard_path(
    native_pair,
):
    witness, observation, engine, _ = native_pair
    pins = module._source_database_pins(witness, observation)
    assert pins.engines == (engine, engine)
    assert module._source_database_pins(witness, observation, pins) == pins


@pytest.mark.parametrize(
    "defect",
    [
        "factory_callback",
        "repo_override",
        "unknown_field",
        "subclass",
        "method_replacement",
        "static_replacement",
        "session_subclass",
        "native_creator_callback",
        "native_event_callback",
        "native_kw_callback",
    ],
)
@pytest.mark.asyncio
async def test_invalid_provider_denies_before_any_factory_or_database_io(
    native_pair, monkeypatch, defect
):
    witness, observation, engine, calls = native_pair

    async def callback(*args, **kwargs):
        calls.append(True)
        raise AssertionError("callback must never run")

    if defect == "factory_callback":
        witness.session_factory = callback
    elif defect == "repo_override":
        observation.read = callback
    elif defect == "unknown_field":
        witness.native = True
    elif defect == "subclass":

        class Subclass(PublicReceiptWitnessRepository):
            pass

        witness = Subclass(witness.session_factory)
        observation.witness_repository = witness
    elif defect == "method_replacement":
        monkeypatch.setattr(PublicReceiptWitnessRepository, "read_latest", callback)
    elif defect == "static_replacement":
        monkeypatch.setattr(
            PublicReceiptPostReadRepository, "_role_guard", staticmethod(callback)
        )
    elif defect == "session_subclass":

        class SessionSubclass(AsyncSession):
            pass

        witness.session_factory = async_sessionmaker(engine, class_=SessionSubclass)
    elif defect == "native_creator_callback":
        alternate = create_async_engine(
            "postgresql+asyncpg://ctcc_fixture@127.0.0.1/ctcc_no_io",
            async_creator=callback,
            poolclass=NullPool,
        )
        witness.session_factory = async_sessionmaker(alternate)
    elif defect == "native_event_callback":
        event.listen(
            engine.sync_engine, "before_execute", lambda *args: calls.append(True)
        )
    else:
        witness.session_factory.kw["info"] = {"callback": callback}
    service = module.PublicCheckpointCaptureService(
        module._ISSUER, None, witness, "b" * 32, None
    )
    plan = plan_for(2)
    with pytest.raises(
        module.PublicWitnessError, match="source_post_read_capture_failed"
    ):
        await service.capture_source_with_post_read_v3(
            plan=plan,
            expected_plan_sha256=plan.canonical_sha256(),
            observation_repository=observation,
        )
    assert not calls


@pytest.mark.parametrize(
    "mutation",
    ["factory", "engine", "option", "instance_method", "class_method", "event"],
)
def test_late_native_provider_mutation_rejects_pinned_readback(
    native_pair, monkeypatch, mutation
):
    witness, observation, engine, calls = native_pair
    pins = module._source_database_pins(witness, observation)
    if mutation == "factory":
        observation.session_factory = async_sessionmaker(engine, expire_on_commit=False)
    elif mutation == "engine":
        alternate = create_async_engine(
            "postgresql+asyncpg://ctcc_fixture@127.0.0.1/ctcc_no_io", poolclass=NullPool
        )
        observation.session_factory.kw["bind"] = alternate
    elif mutation == "option":
        observation.session_factory.kw["expire_on_commit"] = True
    elif mutation == "instance_method":
        observation.append_new = lambda *args, **kwargs: calls.append(True)
    elif mutation == "class_method":
        monkeypatch.setattr(
            PublicReceiptWitnessRepository,
            "append",
            lambda *args, **kwargs: calls.append(True),
        )
    else:
        event.listen(
            engine.sync_engine, "before_execute", lambda *args: calls.append(True)
        )
    with pytest.raises(module.PublicWitnessError, match="source_native_database"):
        module._source_database_pins(witness, observation, pins)
    assert not calls
