"""Synthetic orchestration checks; no database, credentials or provider I/O."""

from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from weakref import WeakKeyDictionary

import pytest

from app.demo_automation import native_source_only_v1 as subject

pytestmark = pytest.mark.asyncio


def _factories(*, frame_error=None):
    return SimpleNamespace(
        writer_factory=object(),
        frame_reader_engine=SimpleNamespace(dispose=AsyncMock(side_effect=frame_error)),
        custody_engine=SimpleNamespace(dispose=AsyncMock()),
    )


def _preflight(monkeypatch, *, control=None):
    factories = _factories()
    monkeypatch.setattr(subject, "_settings", lambda instrument: object())
    monkeypatch.setattr(
        subject.controlled, "_source_bootstrap_marker_v6", Mock(return_value=None)
    )
    monkeypatch.setattr(
        subject.selectors,
        "select_configured_native_demo_factories_v2",
        Mock(return_value=factories),
    )
    monkeypatch.setattr(
        subject,
        "verify_selected_native_roles_v1",
        AsyncMock(
            return_value={
                "database_head": "0055",
                "account_complete": False,
                "execution_authority": False,
            }
        ),
    )
    monkeypatch.setattr(
        subject,
        "inspect_existing_control",
        AsyncMock(
            return_value=control
            or subject.ExistingControl(1, False, False, False, False)
        ),
    )
    monkeypatch.setattr(
        subject,
        "_roots",
        Mock(return_value={"history-raw": Path("attempt/history-raw")}),
    )
    monkeypatch.setattr(subject, "_retain_incomplete", Mock())
    monkeypatch.setattr(
        subject, "_account_control_pin", AsyncMock(return_value=(7, "c" * 64))
    )
    monkeypatch.setattr(subject, "_publish_summary", Mock())
    return factories


async def test_history_failure_short_circuits_market_and_closes_preflight(monkeypatch):
    factories = _preflight(monkeypatch)
    history = AsyncMock(side_effect=ValueError("synthetic source failed"))
    market = AsyncMock()
    monkeypatch.setattr(subject, "_acquire_history", history)
    monkeypatch.setattr(subject, "_acquire_market", market)

    with pytest.raises(
        subject.NativeSourceOnlyErrorV1, match="^native_source_only_incomplete$"
    ):
        await subject.acquire_native_demo_source_only_v1("BTC-USDT-SWAP")

    history.assert_awaited_once()
    market.assert_not_awaited()
    subject._publish_summary.assert_not_called()
    subject._retain_incomplete.assert_called_once()
    factories.frame_reader_engine.dispose.assert_awaited_once()
    factories.custody_engine.dispose.assert_awaited_once()


async def test_successful_sources_still_return_only_deny(monkeypatch):
    _preflight(monkeypatch)
    history = AsyncMock(return_value=("a" * 64, ("123", "123", "first"), (7, "c" * 64)))
    market = AsyncMock(return_value="b" * 64)
    monkeypatch.setattr(subject, "_acquire_history", history)
    monkeypatch.setattr(subject, "_acquire_market", market)

    result = await subject.acquire_native_demo_source_only_v1("BTC-USDT-SWAP")

    market.assert_awaited_once()
    assert result.schema_version == "ctcc.demo_source_only_observation.v1"
    assert result.history_bundle_sha256 == "a" * 64
    assert result.market_receipt_sha256 == "b" * 64
    assert result.registration_site == "UNKNOWN"
    assert result.account_complete is result.history_tail_closed is False
    assert result.post_g12_eligible is result.execution_authority is False
    assert result.admission == "DENY"
    assert result.control_window_interlock_verified is False
    assert result.control_observation_semantics == "ENDPOINTS_ONLY"
    subject._publish_summary.assert_called_once()
    assert b'"execution_authority":false' in result.receipt_json


async def test_sticky_stop_does_not_need_clearing_for_deny_only_source(monkeypatch):
    _preflight(
        monkeypatch,
        control=subject.ExistingControl(1, False, True, True, True),
    )
    history = AsyncMock(return_value=("a" * 64, ("123", "123", "first"), (7, "c" * 64)))
    market = AsyncMock(return_value="b" * 64)
    monkeypatch.setattr(subject, "_acquire_history", history)
    monkeypatch.setattr(subject, "_acquire_market", market)

    result = await subject.acquire_native_demo_source_only_v1("BTC-USDT-SWAP")

    history.assert_awaited_once()
    market.assert_awaited_once()
    assert result.admission == "DENY"
    assert result.execution_authority is False
    subject._publish_summary.assert_called_once()


async def test_active_arm_denies_before_roots_or_provider(monkeypatch):
    _preflight(
        monkeypatch,
        control=subject.ExistingControl(1, True, False, False, True),
    )
    history = AsyncMock()
    market = AsyncMock()
    monkeypatch.setattr(subject, "_acquire_history", history)
    monkeypatch.setattr(subject, "_acquire_market", market)

    with pytest.raises(
        subject.NativeSourceOnlyErrorV1,
        match="^native_source_only_control_unavailable$",
    ):
        await subject.acquire_native_demo_source_only_v1("BTC-USDT-SWAP")

    subject._roots.assert_not_called()
    subject._publish_summary.assert_not_called()
    history.assert_not_awaited()
    market.assert_not_awaited()


async def test_legacy_control_revision_change_denies_before_history(monkeypatch):
    _preflight(monkeypatch)
    subject.inspect_existing_control.side_effect = [
        subject.ExistingControl(1, False, False, False, False),
        subject.ExistingControl(2, False, False, False, False),
    ]
    history = AsyncMock()
    market = AsyncMock()
    monkeypatch.setattr(subject, "_acquire_history", history)
    monkeypatch.setattr(subject, "_acquire_market", market)

    with pytest.raises(
        subject.NativeSourceOnlyErrorV1,
        match="^native_source_only_control_revision_changed$",
    ):
        await subject.acquire_native_demo_source_only_v1("BTC-USDT-SWAP")

    history.assert_not_awaited()
    market.assert_not_awaited()
    subject._retain_incomplete.assert_called_once()


async def test_selector_source_only_roles_fail_before_signed_get(monkeypatch):
    from app.core import native_role_preflight_v1 as roles

    factories = _factories()
    private = SimpleNamespace(account_config_selector_v2=AsyncMock())
    monkeypatch.setattr(
        subject.selectors, "_configured_inputs", Mock(return_value=object())
    )
    monkeypatch.setattr(
        subject.selectors, "_server_settings_pin", Mock(return_value="pin")
    )
    monkeypatch.setattr(
        subject.selectors, "_route_artifact", Mock(return_value=object())
    )
    monkeypatch.setattr(
        subject.selectors,
        "select_configured_native_demo_factories_v2",
        Mock(return_value=factories),
    )
    monkeypatch.setattr(
        roles,
        "verify_selected_native_roles_v1",
        AsyncMock(side_effect=RuntimeError("synthetic role denial")),
    )

    with pytest.raises(subject.selectors.NativeDemoCallerReadinessErrorV2):
        await subject.selectors.select_configured_demo_identity_v2(
            private_client=private,
            read_session=object(),
            instrument_ids=("BTC-USDT-SWAP",),
            source_only_role_preflight=True,
        )

    roles.verify_selected_native_roles_v1.assert_awaited_once_with(factories)
    private.account_config_selector_v2.assert_not_awaited()
    factories.frame_reader_engine.dispose.assert_awaited_once()
    factories.custody_engine.dispose.assert_awaited_once()


async def test_cleanup_attempts_both_engines_after_one_failure():
    factories = _factories(frame_error=ValueError("synthetic close failure"))
    selection = SimpleNamespace(close=Mock())

    with pytest.raises(
        subject.NativeSourceOnlyErrorV1, match="^native_source_only_cleanup_uncertain$"
    ):
        await subject._dispose(selection, factories)

    selection.close.assert_called_once()
    factories.frame_reader_engine.dispose.assert_awaited_once()
    factories.custody_engine.dispose.assert_awaited_once()


async def test_failed_identity_selector_disposes_both_unreturned_factories():
    factories = _factories()

    await subject.selectors._dispose_failed_selection_factories_v2(factories)

    factories.frame_reader_engine.dispose.assert_awaited_once()
    factories.custody_engine.dispose.assert_awaited_once()


async def test_failed_identity_selector_cleanup_failure_remains_closed():
    factories = _factories(frame_error=ValueError("synthetic close failure"))

    with pytest.raises(
        subject.selectors.NativeDemoCallerReadinessErrorV2,
        match="^native_demo_selector_cleanup_uncertain$",
    ):
        await subject.selectors._dispose_failed_selection_factories_v2(factories)

    factories.frame_reader_engine.dispose.assert_awaited_once()
    factories.custody_engine.dispose.assert_awaited_once()


async def test_second_selector_identity_change_denies_before_public_capture(
    monkeypatch,
):
    factories = _factories()

    class Lease:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    class Client:
        def __init__(self, *, settings):
            self.settings = settings

        def pinned_read_session(self):
            return Lease()

    selection = SimpleNamespace(
        scope_config=SimpleNamespace(expected_uid="456", expected_main_uid="123"),
        current_plan=SimpleNamespace(session_binding_id="second"),
        factories=factories,
        close=Mock(),
    )
    acquire = AsyncMock()
    monkeypatch.setattr(subject, "OkxDemoPrivateRestClient", Client)
    monkeypatch.setattr(
        subject.selectors,
        "select_configured_demo_identity_v2",
        AsyncMock(return_value=selection),
    )
    monkeypatch.setattr(
        subject,
        "verify_selected_native_roles_v1",
        AsyncMock(
            return_value={
                "database_head": "0055",
                "account_complete": False,
                "execution_authority": False,
            }
        ),
    )
    monkeypatch.setattr(
        subject.market, "acquire_initial_demo_market_source_v4", acquire
    )

    with pytest.raises(
        subject.NativeSourceOnlyErrorV1,
        match="^native_source_only_account_identity_changed$",
    ):
        await subject._acquire_market(
            object(),
            "BTC-USDT-SWAP",
            {},
            ("123", "123", "first"),
            subject.ExistingControl(1, False, False, False, False),
            (7, "c" * 64),
        )

    acquire.assert_not_awaited()
    selection.close.assert_called_once()
    factories.frame_reader_engine.dispose.assert_awaited_once()
    factories.custody_engine.dispose.assert_awaited_once()


async def test_source_owned_handoff_burns_current_owner_without_authority(monkeypatch):
    history = subject.history
    owners = WeakKeyDictionary()
    monkeypatch.setattr(history, "_OWNERS", owners)
    check = Mock()
    monkeypatch.setattr(history, "_check_pins", check)
    owner = object.__new__(history.OwnedNativeHistoryCurrentSourceV2)
    state = {
        "partition_current_key": history._PARTITION_CURRENT_V2,
        "source_sha256": "a" * 64,
        "raw_inventory": {},
        "companion_inventory": {},
    }
    owners[owner] = state

    assert history.consume_owned_native_history_current_source_only_v1(owner) == (
        "a" * 64
    )
    check.assert_called_once()
    assert state == {}
    assert owner not in owners
    with pytest.raises(history.NativeHistorySourceError):
        history.consume_owned_native_history_current_source_only_v1(owner)


async def test_source_owned_discard_burns_failed_current_owner(monkeypatch):
    history = subject.history
    owners = WeakKeyDictionary()
    monkeypatch.setattr(history, "_OWNERS", owners)
    owner = object.__new__(history.OwnedNativeHistoryCurrentSourceV2)
    state = {"source_sha256": "a" * 64}
    owners[owner] = state

    history.discard_owned_native_history_current_source_only_v1(owner)

    assert state == {}
    assert owner not in owners


async def test_market_evaluation_receipt_is_published_once_and_read_back(monkeypatch):
    raw = subject.canonical(
        {
            "schema_version": "ctcc.current_market_evaluation.v4",
            "source_authenticity_verified": True,
            "durable_raw_readback_verified": True,
            "registration_site": "UNKNOWN",
            "account_complete": False,
            "historical_availability_verified": False,
            "execution_authority": False,
        }
    )
    evaluation = subject.market.CurrentMarketEvaluationV4(None, None, None, None, raw)
    files = {}

    class Directory:
        def names(self):
            return list(files)

        def publish(self, name, value):
            assert name not in files
            files[name] = value

        def read(self, name, maximum):
            assert len(files[name]) <= maximum
            return files[name]

    @contextmanager
    def root(_path):
        yield Directory()

    monkeypatch.setattr(subject.storage, "_root_context", root)
    paths = {"history-raw": Path("attempt/history-raw")}

    assert subject._publish_market_receipt(paths, evaluation) == subject.sha(raw)
    assert files == {"market-evaluation.v4.json": raw}
    with pytest.raises(
        subject.NativeSourceOnlyErrorV1,
        match="^native_source_only_market_receipt_exists$",
    ):
        subject._publish_market_receipt(paths, evaluation)


async def test_incomplete_terminal_is_no_clobber_and_idempotent(monkeypatch):
    files = {}

    class Directory:
        def names(self):
            return list(files)

        def publish(self, name, value):
            assert name not in files
            files[name] = value

        def read(self, name, maximum):
            assert len(files[name]) <= maximum
            return files[name]

    @contextmanager
    def root(_path):
        yield Directory()

    monkeypatch.setattr(subject.storage, "_root_context", root)
    subject._retain_incomplete(Path("attempt"))
    subject._retain_incomplete(Path("attempt"))
    assert files == {subject._INCOMPLETE_NAME: subject._INCOMPLETE}
    assert b'"outcome":"INCOMPLETE"' in files[subject._INCOMPLETE_NAME]


async def test_partial_attempt_root_creation_retains_incomplete(monkeypatch):
    class Directory:
        def mkdir(self, _name):
            return None

        @contextmanager
        def child(self, _name):
            raise ValueError("synthetic child creation failure")
            yield

    @contextmanager
    def root(_path):
        yield Directory()

    retain = Mock()
    monkeypatch.setattr(
        subject, "require_private_evidence_base_v2", lambda _raw: Path("private")
    )
    monkeypatch.setattr(subject.storage, "_root_context", root)
    monkeypatch.setattr(subject, "_retain_incomplete", retain)

    with pytest.raises(ValueError, match="synthetic child creation failure"):
        subject._roots(SimpleNamespace(okx_demo_native_evidence_root="private"))

    retain.assert_called_once()
    assert retain.call_args.args[0].parent == Path("private")


async def test_failed_attempt_creation_cannot_write_to_unowned_collision(
    monkeypatch, tmp_path
):
    token = "1" * 32
    existing = tmp_path / ("source-only-" + token)
    existing.mkdir()

    class Directory:
        def mkdir(self, _name):
            raise PermissionError("synthetic creation denied")

    @contextmanager
    def root(_path):
        yield Directory()

    retain = Mock()
    monkeypatch.setattr(subject, "uuid4", lambda: SimpleNamespace(hex=token))
    monkeypatch.setattr(
        subject, "require_private_evidence_base_v2", lambda _raw: tmp_path
    )
    monkeypatch.setattr(subject.storage, "_root_context", root)
    monkeypatch.setattr(subject, "_retain_incomplete", retain)
    with pytest.raises(PermissionError, match="synthetic creation denied"):
        subject._roots(SimpleNamespace(okx_demo_native_evidence_root="private"))

    retain.assert_not_called()
    assert list(existing.iterdir()) == []


async def test_account_control_endpoint_change_denies_without_clearing_stop(
    monkeypatch,
):
    class Observation:
        def __init__(self, scope):
            self.state = SimpleNamespace(
                scope=scope,
                revision=7,
                arm_expires_at=None,
                arm_intent_current=Mock(return_value=False),
            )
            self.event_sha256 = "c" * 64
            self.observed_at = datetime.now(UTC)

    async def read(scope):
        return Observation(scope)

    monkeypatch.setattr(subject, "ControlObservation", Observation)
    monkeypatch.setattr(
        subject,
        "DemoControlRepositoryV2",
        Mock(return_value=SimpleNamespace(read=read)),
    )

    assert await subject._account_control_pin(object(), "123") == (7, "c" * 64)
    with pytest.raises(
        subject.NativeSourceOnlyErrorV1,
        match="^native_source_only_account_control_revision_changed$",
    ):
        await subject._account_control_pin(object(), "123", (6, "c" * 64))


async def test_account_control_pending_arm_denies_source_phase(monkeypatch):
    class Observation:
        def __init__(self, scope):
            self.state = SimpleNamespace(
                scope=scope,
                revision=7,
                arm_expires_at=datetime.now(UTC),
                arm_intent_current=Mock(return_value=False),
            )
            self.event_sha256 = "c" * 64
            self.observed_at = datetime.now(UTC)

    async def read(scope):
        return Observation(scope)

    monkeypatch.setattr(subject, "ControlObservation", Observation)
    monkeypatch.setattr(
        subject,
        "DemoControlRepositoryV2",
        Mock(return_value=SimpleNamespace(read=read)),
    )

    with pytest.raises(
        subject.NativeSourceOnlyErrorV1,
        match="^native_source_only_account_control_unavailable$",
    ):
        await subject._account_control_pin(object(), "123")
