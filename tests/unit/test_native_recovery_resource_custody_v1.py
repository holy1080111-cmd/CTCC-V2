"""Recovery cleanup evidence boundaries, with no source or order authority."""

import asyncio
import os
import threading
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.exchange.okx import native_reconciliation_observation_v1 as read
from app.exchange.okx.errors import OkxPrivateApiError
from app.exchange.okx.private_rest import OkxDemoPrivateRestClient
from app.trade_qualification import native_postsubmit_reconciliation_v1 as post
from tests.unit.exchange.test_private_rest import demo_settings


@pytest.mark.parametrize("classification", ("zero", "already_zero", "filled"))
def test_recovery_cleanup_requires_exact_registered_result_and_durable_pages(
    classification,
):
    receipt = b"synthetic recovered evidence"
    evidence = SimpleNamespace(receipt_json=receipt)
    pages = (("config_before", object()),)
    result = post.NativeReconciliationResultV1(
        "a" * 64, classification, "b" * 64, post.facts.sha(receipt), False
    )
    old_closure = (result.outcome_id, evidence, pages)
    state = {
        "result": result,
        "durable_observations": pages,
        "observations": list(pages),
    }
    if classification == "filled":
        state.update(
            recovered_filled_flat_result=result,
            verified_filled_flat_closure=old_closure,
            verified_closed_trade_readback=("c" * 64, evidence, pages),
        )
    else:
        state.update(owned_flat_result=result, verified_flat_closure=old_closure)
        if classification == "already_zero":
            state["verified_previously_closed_zero_recovery"] = (
                "c" * 64,
                receipt,
                pages,
            )
    assert post._recovery_committed_pages_v1(state, result) is pages
    copied = post.NativeReconciliationResultV1(
        result.reservation_id,
        classification,
        result.outcome_id,
        result.receipt_sha256,
        False,
    )
    assert copied == result and post._recovery_committed_pages_v1(state, copied) is None
    state["durable_observations"] = (("config_before", object()),)
    with pytest.raises(post.NativePostsubmitError):
        post._recovery_committed_pages_v1(state, result)


def test_recovery_cleanup_cannot_be_unlocked_by_result_with_missing_witness():
    result = post.NativeReconciliationResultV1(
        "a" * 64, "terminal", "b" * 64, "c" * 64, False
    )
    with pytest.raises(
        post.NativePostsubmitError, match="durable_verification_unavailable"
    ):
        post._recovery_committed_pages_v1({"result": result}, result)


def test_recovery_holds_incomplete_result_even_if_it_was_registered():
    result = post.NativeReconciliationResultV1(
        "a" * 64, "uncertain", "b" * 64, "c" * 64, True
    )
    assert post._recovery_committed_pages_v1({"result": result}, result) is None


class _Body(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield b"a" * 65536
        yield b"b"


@pytest.mark.asyncio
@pytest.mark.parametrize("selector_bound", (True, False))
async def test_recovery_selector_retains_stream_but_enforces_its_smaller_bound(
    selector_bound,
):
    client = OkxDemoPrivateRestClient(settings=demo_settings())
    response = httpx.Response(200, stream=_Body())
    with client.pinned_read_session() as lease:
        journal = read.ReadJournalV1(
            "/api/v5/account/config",
            (),
            "https://www.okx.com/api/v5/account/config",
            lease.binding_id,
        )
        if selector_bound:
            with pytest.raises(OkxPrivateApiError) as error:
                await client._read_demo_selector_bytes_v2(
                    response, _reconciliation_journal=journal, _selector_bound=True
                )
            assert error.value.code == "demo_selector_response_bound"
            assert journal.faults == ["response_invalid"] and journal.eof_stamp is None
        else:
            raw = await client._read_demo_selector_bytes_v2(
                response, _reconciliation_journal=journal
            )
            assert raw == b"a" * 65536 + b"b"
            assert journal.faults == [] and journal.eof_stamp is not None
        assert journal.chunks == [b"a" * 65536, b"b"]


def _recovery_journal(lease):
    path = "/api/v5/account/config"
    url = "https://www.okx.com" + path
    now = datetime.now(UTC)
    journal = read.ReadJournalV1(path, (), url, lease)
    journal.signature = "unit-custody-only"
    journal.observation = read.DemoReconciliationObservationV1(
        path,
        (),
        url,
        post.facts.sha(journal.signature.encode()),
        lease,
        now,
        1,
        now,
        2,
        now,
        3,
        200,
        (),
        b'{"code":"0","data":[]}',
        "a" * 64,
        "www.okx.com",
    )
    return journal


@pytest.mark.asyncio
@pytest.mark.parametrize("late_failure", (False, True))
async def test_recovery_cleans_only_new_owners_and_preserves_custody_on_late_failure(
    monkeypatch,
    late_failure,
):
    # This is the retirement coordinator, with simulated source/transport
    # observations and real unconnected SQLAlchemy engine/pool disposal.
    # It never establishes account identity or execution authority.
    url = "postgresql+asyncpg://fixture:fixture@127.0.0.1:1/fixture"
    reader, custody, writer = (
        create_async_engine(url, poolclass=NullPool) for _ in range(3)
    )
    pools = tuple(engine.sync_engine.pool for engine in (reader, custody, writer))
    factories = post._selector.ConfiguredNativeDemoFactoriesV2(
        async_sessionmaker(writer),
        async_sessionmaker(custody),
        async_sessionmaker(reader),
        custody,
        reader,
    )
    reader_dispose, custody_dispose, writer_dispose = (
        reader.dispose,
        custody.dispose,
        writer.dispose,
    )
    with monkeypatch.context() as patches:
        try:
            binding, generation = object(), object()
            selection = object.__new__(post._selector.ConfiguredDemoIdentitySelectionV2)
            initial, fresh = _recovery_journal("initial"), _recovery_journal("fresh")
            selector_client = SimpleNamespace(
                _native_reconciliation_read_journals_v1=[initial]
            )
            fresh_client = SimpleNamespace(
                _native_reconciliation_read_journals_v1=[fresh]
            )
            selector = {
                "record": object(),
                "factories": factories,
                "private_client": selector_client,
                "read_session": SimpleNamespace(binding_id="initial"),
                "selector_signature": initial.signature,
                "evidence": SimpleNamespace(
                    selector_raw_body=initial.observation.raw_body
                ),
            }
            record = {
                "generation": generation,
                "client": fresh_client,
                "writer_factory": factories.writer_factory,
                "selector_record": selector["record"],
                "previous_read_context": selector["read_session"],
                "source_state": None,
                "transport": None,
                "control_owner": None,
                "read_lease_binding_id": "fresh",
            }
            reads = []

            def observe(item, lease):
                reads.append((item, lease))
                return object()

            source = SimpleNamespace(
                _RECONCILIATION_READ_V6={generation: record},
                _RECOVERY_SELECTION_USES_V6={selection: generation},
                _reconciliation_identity_v6=lambda record, *, deadline: None,
                _REQUIRE_SOURCE_KERNEL_V6=lambda: None,
                _closed_reconciliation_get_v6=observe,
            )
            receipt = b"fixture-closure"
            result = post.NativeReconciliationResultV1(
                "a" * 64,
                "terminal_zero_fill_flat",
                "b" * 64,
                post.facts.sha(receipt),
                False,
            )
            pages = (("config_before", fresh.observation),)
            history = (b"fixture-history",)
            state = {
                "binding": binding,
                "source": SimpleNamespace(
                    source_generation=generation,
                    client=fresh_client,
                    session_factory=factories.writer_factory,
                ),
                "kind": "recovery",
                "closed": True,
                "recovery_selection": selection,
                "task": asyncio.current_task(),
                "loop": asyncio.get_running_loop(),
                "pid": os.getpid(),
                "thread": threading.get_ident(),
                "guard": object(),
                "result": result,
                "owned_flat_result": result,
                "durable_observations": pages,
                "observations": list(pages),
                "verified_flat_closure": (
                    result.outcome_id,
                    SimpleNamespace(receipt_json=receipt),
                    pages,
                ),
                "recovery_history_retained": history,
            }
            caller = {
                "id": "fixture",
                "selection": selection,
                "factories": factories,
                "client": selector_client,
                "controller": None,
            }
            attempt = object.__new__(post._Attempt)
            attempt.state = state
            patches.setattr(post, "_UNKNOWN", {binding: state})
            patches.setattr(
                post, "_captured_flat_cleanup_source_v1", lambda state: source
            )
            patches.setattr(
                post.guard, "check_saved_native_guard_v1", lambda value: None
            )
            patches.setattr(post._caller, "_HELD", {caller["id"]: caller})
            patches.setattr(post._caller, "_ready", lambda service, state: None)
            patches.setattr(post._selector, "_record", lambda selection: selector)
            patches.setattr(post._selector, "_SELECTIONS", {selection: selector})
            patches.setattr(
                post._repository, "_RECOVERY_HISTORY_BUFFERS_V1", {binding: history}
            )
            patches.setattr(post._repository, "_RECOVERY_CLOSE_HISTORIES_V1", {})

            if late_failure:

                async def fail(engine, *, close=True):
                    if engine is custody:
                        raise RuntimeError("synthetic second-pool disposal failure")
                    await reader_dispose.__func__(engine, close=close)

                patches.setattr(type(custody), "dispose", fail)
                with pytest.raises(RuntimeError, match="second-pool"):
                    await post._dispose_completed_recovery_v1(
                        object(), caller, attempt, result
                    )
                assert post._UNKNOWN[binding] is state
                assert source._RECOVERY_SELECTION_USES_V6[selection] is generation
                assert source._RECONCILIATION_READ_V6[generation] is record
                assert post._selector._SELECTIONS[selection] is selector
                assert post._caller._HELD[caller["id"]] is caller
                assert post._repository._RECOVERY_HISTORY_BUFFERS_V1[binding] is history
                assert custody.sync_engine.pool is pools[1]
            else:
                assert await post._dispose_completed_recovery_v1(
                    object(), caller, attempt, result
                )
                assert (
                    post._UNKNOWN
                    == post._caller._HELD
                    == post._selector._SELECTIONS
                    == {}
                )
                assert (
                    source._RECOVERY_SELECTION_USES_V6
                    == source._RECONCILIATION_READ_V6
                    == {}
                )
                assert post._repository._RECOVERY_HISTORY_BUFFERS_V1 == {}
                assert custody.sync_engine.pool is not pools[1]
            assert reads == [(initial, "initial"), (fresh, "fresh")]
            assert reader.sync_engine.pool is not pools[0]
            assert writer.sync_engine.pool is pools[2]
        finally:
            await reader_dispose()
            await custody_dispose()
            await writer_dispose()
