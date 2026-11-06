"""Inert draft: one explicit Demo source acquisition, never an order path.

This module is not imported by the API, a CLI, a scheduler, or a Compose service.
It is a review seam for a future separately launched source-only process.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

from app.config.settings import get_settings
from app.core.native_only_lifespan import (
    ExistingControl,
    inspect_existing_control,
    require_native_only_settings,
)
from app.core.native_role_preflight_v1 import verify_selected_native_roles_v1
from app.database.repositories.demo_control import (
    ControlObservation,
)
from app.database.repositories.demo_control_current55_v2 import DemoControlRepositoryV2
from app.database.repositories.public_runtime_bundle_custody import (
    RuntimeBundleCustodyRepository,
)
from app.demo_automation import native_server_selectors_v2 as selectors
from app.demo_automation.native_caller_v2 import _market_policy
from app.demo_automation.native_private_root_v2 import (
    mkdir_private_evidence_child_v2,
    require_private_evidence_base_v2,
)
from app.domain.source_primitives import canonical, sha
from app.exchange.okx.private_rest import OkxDemoPrivateRestClient
from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_history_source_v1 as history
from app.trade_qualification import account_native_proof_storage as storage
from app.trade_qualification import demo_market_source_v4 as market
from app.trade_qualification import native_controlled_account_source_v6 as controlled
from app.trade_qualification.account_runtime import ControlledDemoAccountSession
from app.trade_qualification.demo_control import ControlScope

_LOCK = asyncio.Lock()
_ROOT_NAMES = (
    "history-raw",
    "history-companion",
    "market-public",
    "market-account-proof",
)
_HASH = re.compile(r"[a-f0-9]{64}\Z", re.ASCII)
_ORIGIN = "https://openapi.okx.com"
_INCOMPLETE_NAME = "source-only-terminal-incomplete.v1.json"
_INCOMPLETE = canonical(
    {
        "schema_version": "ctcc.demo_source_only_terminal_incomplete.v1",
        "outcome": "INCOMPLETE",
        "admission": "DENY",
        "execution_authority": False,
    }
)


class NativeSourceOnlyErrorV1(RuntimeError):
    """A fixed code; source, SQL, account identity and secrets never escape."""


def _deny(code: str) -> None:
    raise NativeSourceOnlyErrorV1(code) from None


@dataclass(frozen=True, slots=True, repr=False)
class NativeSourceOnlyObservationV1:
    history_bundle_sha256: str
    market_receipt_sha256: str
    schema_version: Literal["ctcc.demo_source_only_observation.v1"] = field(
        default="ctcc.demo_source_only_observation.v1", init=False
    )
    registration_site: Literal["UNKNOWN"] = field(default="UNKNOWN", init=False)
    account_complete: Literal[False] = field(default=False, init=False)
    history_tail_closed: Literal[False] = field(default=False, init=False)
    post_g12_eligible: Literal[False] = field(default=False, init=False)
    execution_authority: Literal[False] = field(default=False, init=False)
    admission: Literal["DENY"] = field(default="DENY", init=False)
    control_window_interlock_verified: Literal[False] = field(default=False, init=False)
    control_observation_semantics: Literal["ENDPOINTS_ONLY"] = field(
        default="ENDPOINTS_ONLY", init=False
    )

    def __post_init__(self) -> None:
        if any(
            type(value) is not str or _HASH.fullmatch(value) is None
            for value in (self.history_bundle_sha256, self.market_receipt_sha256)
        ):
            _deny("native_source_only_receipt_invalid")

    @property
    def receipt_json(self) -> bytes:
        return canonical(
            {
                "schema_version": self.schema_version,
                "history_bundle_sha256": self.history_bundle_sha256,
                "market_receipt_sha256": self.market_receipt_sha256,
                "registration_site": self.registration_site,
                "account_complete": self.account_complete,
                "history_tail_closed": self.history_tail_closed,
                "post_g12_eligible": self.post_g12_eligible,
                "execution_authority": self.execution_authority,
                "admission": self.admission,
                "control_window_interlock_verified": self.control_window_interlock_verified,
                "control_observation_semantics": self.control_observation_semantics,
            }
        )

    @property
    def receipt_sha256(self) -> str:
        return sha(self.receipt_json)


def _settings(instrument_id: str):
    settings = get_settings()
    require_native_only_settings(settings)
    if (
        settings.ctcc_native_only_lifespan_v1 is not True
        or settings.okx_demo_credentials_configured is not True
        or settings.okx_demo_rest_base_url != _ORIGIN
        or settings.okx_demo_read_max_retries != 0
        or type(instrument_id) is not str
        or instrument_id not in settings.okx_demo_allowed_symbol_list
    ):
        _deny("native_source_only_settings_denied")
    return settings


def _retain_incomplete(root: Path) -> None:
    """No-clobber terminal failure; a partial summary never outranks it."""
    try:
        with storage._root_context(root) as directory:
            if _INCOMPLETE_NAME not in directory.names():
                directory.publish(_INCOMPLETE_NAME, _INCOMPLETE)
            if directory.read(_INCOMPLETE_NAME, len(_INCOMPLETE)) != _INCOMPLETE:
                _deny("native_source_only_failure_retention_changed")
    except NativeSourceOnlyErrorV1:
        raise
    except Exception:  # noqa: BLE001 -- path and filesystem state stay private
        _deny("native_source_only_failure_retention_unavailable")


def _roots(settings) -> dict[str, Path]:
    base = require_private_evidence_base_v2(settings.okx_demo_native_evidence_root)
    attempt_name = "source-only-" + uuid4().hex
    root = base / attempt_name
    created = False
    try:
        with storage._root_context(base) as directory:
            mkdir_private_evidence_child_v2(directory, attempt_name)
            created = True
            require_private_evidence_base_v2(str(root))
            with directory.child(attempt_name) as attempt:
                for name in _ROOT_NAMES:
                    mkdir_private_evidence_child_v2(attempt, name)
        paths = {name: root / name for name in _ROOT_NAMES}
        if len({storage._root_identity(path) for path in paths.values()}) != len(
            _ROOT_NAMES
        ):
            _deny("native_source_only_roots_invalid")
        for path in paths.values():
            require_private_evidence_base_v2(str(path))
            with storage._root_context(path) as directory:
                if directory.names():
                    _deny("native_source_only_roots_not_empty")
        return paths
    except BaseException:
        # Existence does not prove that this attempt created the root.
        if created:
            _retain_incomplete(root)
        raise


async def _checked_roles(factories) -> None:
    roles = await verify_selected_native_roles_v1(factories)
    if (
        type(roles) is not dict
        or roles.get("database_head") != "0055"
        or roles.get("account_complete") is not False
        or roles.get("execution_authority") is not False
    ):
        _deny("native_source_only_role_preflight_changed")


async def _check_control(factory, expected: ExistingControl) -> None:
    """Endpoint comparison only; never claim a held cross-request interlock."""
    actual = await inspect_existing_control(factory)
    if (
        type(expected) is not ExistingControl
        or type(actual) is not ExistingControl
        or actual != expected
        or actual.armed
    ):
        _deny("native_source_only_control_revision_changed")


async def _account_control_pin(factory, uid: str, expected=None) -> tuple[int, str]:
    """Bounded account-scoped Control observation, not a held Arm fence."""
    scope = ControlScope("demo", uid)
    repository = DemoControlRepositoryV2(factory, clock=lambda: datetime.now(UTC))
    async with asyncio.timeout(5):
        observation = await repository.read(scope)
    if (
        type(observation) is not ControlObservation
        or observation.state.scope != scope
        or observation.state.arm_expires_at is not None
        or observation.state.arm_intent_current(observation.observed_at)
    ):
        _deny("native_source_only_account_control_unavailable")
    pin = (observation.state.revision, observation.event_sha256)
    if expected is not None and pin != expected:
        _deny("native_source_only_account_control_revision_changed")
    return pin


async def _dispose(selection, factories) -> None:
    """Close both independently; cancellation cannot turn cleanup into PASS."""
    close_failed = False
    if selection is not None:
        try:
            selection.close()
        except Exception:  # noqa: BLE001 -- private selector state cannot escape
            close_failed = True
    if factories is None:
        if close_failed:
            _deny("native_source_only_cleanup_uncertain")
        return
    pending = asyncio.gather(
        factories.frame_reader_engine.dispose(),
        factories.custody_engine.dispose(),
        return_exceptions=True,
    )
    try:
        async with asyncio.timeout(5):
            results = await asyncio.shield(pending)
    except asyncio.CancelledError:
        try:
            async with asyncio.timeout(5):
                await asyncio.shield(pending)
        except BaseException:  # noqa: BLE001 -- retain cancellation after bounded close
            pending.cancel()
        raise
    except Exception:  # noqa: BLE001 -- no connection detail escapes
        pending.cancel()
        _deny("native_source_only_cleanup_uncertain")
    if close_failed or any(isinstance(item, BaseException) for item in results):
        _deny("native_source_only_cleanup_uncertain")


def _history_bundle_hash(raw_root: Path) -> str:
    """Read the already accepted private bundle without exporting its contents."""
    with storage._root_context(raw_root) as directory:
        accepted_raw = directory.read("accepted.json", 32768)
        bundle_raw = directory.read(
            "source.bundle.json", history.custody.MAX_BUNDLE_BYTES
        )
    try:
        accepted = json.loads(accepted_raw)
        if (
            type(accepted) is not dict
            or set(accepted)
            != {
                "schema_version",
                "bundle_sha256",
                "proof_sha256",
                "custody_bundle_id",
                "custody_recorded_at",
                "custody_readback_at",
                "execution_authority",
            }
            or accepted["schema_version"] != "ctcc.native_history_source_readback.v1"
            or accepted["execution_authority"] is not False
            or canonical(accepted) != accepted_raw
            or type(accepted["bundle_sha256"]) is not str
            or _HASH.fullmatch(accepted["bundle_sha256"]) is None
            or accepted["bundle_sha256"] != sha(bundle_raw)
        ):
            raise ValueError
        return accepted["bundle_sha256"]
    except Exception:  # noqa: BLE001 -- no raw source or path in outcome
        _deny("native_source_only_history_readback_changed")


def _publish_summary(paths: dict[str, Path], observation) -> None:
    if type(observation) is not NativeSourceOnlyObservationV1:
        _deny("native_source_only_receipt_invalid")
    root = paths["history-raw"].parent
    raw = observation.receipt_json
    with storage._root_context(root) as directory:
        if "source-only-summary.v1.json" in directory.names():
            _deny("native_source_only_summary_exists")
        directory.publish("source-only-summary.v1.json", raw)
        if directory.read("source-only-summary.v1.json", len(raw)) != raw:
            _deny("native_source_only_summary_readback_changed")


def _publish_market_receipt(paths: dict[str, Path], evaluation) -> str:
    """Persist the V4 evaluation separately from its sealed source bundle."""
    if type(evaluation) is not market.CurrentMarketEvaluationV4:
        _deny("native_source_only_market_receipt_invalid")
    raw = evaluation.receipt_json
    try:
        document = json.loads(raw)
        if (
            type(raw) is not bytes
            or not 0 < len(raw) <= 32768
            or type(document) is not dict
            or canonical(document) != raw
            or document.get("schema_version") != "ctcc.current_market_evaluation.v4"
            or document.get("source_authenticity_verified") is not True
            or document.get("durable_raw_readback_verified") is not True
            or document.get("registration_site") != "UNKNOWN"
            or document.get("account_complete") is not False
            or document.get("historical_availability_verified") is not False
            or document.get("execution_authority") is not False
        ):
            raise ValueError
    except Exception:  # noqa: BLE001 -- receipt and account content stay private
        _deny("native_source_only_market_receipt_invalid")
    root = paths["history-raw"].parent
    name = "market-evaluation.v4.json"
    with storage._root_context(root) as directory:
        if name in directory.names():
            _deny("native_source_only_market_receipt_exists")
        directory.publish(name, raw)
        if directory.read(name, len(raw)) != raw:
            _deny("native_source_only_market_receipt_readback_changed")
    return sha(raw)


async def _acquire_history(
    settings, instrument_id: str, paths: dict[str, Path], expected_control
):
    client = OkxDemoPrivateRestClient(settings=settings)
    selection = factories = owner = None
    try:
        with client.pinned_read_session() as lease:
            selection = await selectors.select_configured_demo_identity_v2(
                private_client=client,
                read_session=lease,
                instrument_ids=(instrument_id,),
                source_only_role_preflight=True,
            )
            factories = selection.factories
            await _checked_roles(factories)
            await _check_control(factories.writer_factory, expected_control)
            scope = selection.scope_config
            account_control_pin = await _account_control_pin(
                factories.writer_factory, scope.expected_uid
            )
            identity = (
                scope.expected_uid,
                scope.expected_main_uid,
                selection.current_plan.session_binding_id,
            )
            owner = await history.acquire_native_history_current_source_v2(
                read_session=lease,
                private_client=client,
                scope_config=scope,
                session_factory=factories.writer_factory,
                custody_session_factory=factories.custody_factory,
                raw_root=paths["history-raw"],
                companion_root=paths["history-companion"],
            )
            # The source-owned diagnostic handoff burns the opaque CURRENT
            # owner without borrowing the financial assembler's private key.
            owner_digest = history.consume_owned_native_history_current_source_only_v1(
                owner
            )
            owner = None
            digest = _history_bundle_hash(paths["history-raw"])
            if digest != owner_digest:
                _deny("native_source_only_history_readback_changed")
            await _check_control(factories.writer_factory, expected_control)
            await _account_control_pin(
                factories.writer_factory, scope.expected_uid, account_control_pin
            )
            return digest, identity, account_control_pin
    finally:
        if owner is not None:
            history.discard_owned_native_history_current_source_only_v1(owner)
        await _dispose(selection, factories)


async def _acquire_market(
    settings,
    instrument_id: str,
    paths: dict[str, Path],
    original_identity,
    expected_control,
    expected_account_control,
) -> str:
    client = OkxDemoPrivateRestClient(settings=settings)
    selection = factories = None
    try:
        with client.pinned_read_session() as lease:
            selection = await selectors.select_configured_demo_identity_v2(
                private_client=client,
                read_session=lease,
                instrument_ids=(instrument_id,),
                source_only_role_preflight=True,
            )
            factories = selection.factories
            await _checked_roles(factories)
            scope = selection.scope_config
            if (
                type(original_identity) is not tuple
                or len(original_identity) != 3
                or (scope.expected_uid, scope.expected_main_uid)
                != original_identity[:2]
                or selection.current_plan.session_binding_id == original_identity[2]
            ):
                _deny("native_source_only_account_identity_changed")
            await _check_control(factories.writer_factory, expected_control)
            await _account_control_pin(
                factories.writer_factory,
                scope.expected_uid,
                expected_account_control,
            )
            session = ControlledDemoAccountSession.from_pinned_read_session(
                read_session=lease,
                plan=selection.current_plan,
                expected_plan_sha256=capture.plan_sha256(selection.current_plan),
            )
            owned = await market.acquire_initial_demo_market_source_v4(
                paths["market-public"],
                account_proof_root=paths["market-account-proof"],
                instrument_id=instrument_id,
                market_policy=_market_policy(settings),
                account_session=session,
                private_client=client,
                custody_repository=RuntimeBundleCustodyRepository(
                    factories.custody_factory
                ),
            )
            evaluation = market.evaluate_owned_current_market_v4(owned)
            if (
                evaluation.source_authenticity_verified is not True
                or evaluation.durable_raw_readback_verified is not True
                or evaluation.registration_site != "UNKNOWN"
                or evaluation.account_complete is not False
                or evaluation.execution_authority is not False
            ):
                _deny("native_source_only_market_readback_changed")
            await _check_control(factories.writer_factory, expected_control)
            await _account_control_pin(
                factories.writer_factory,
                scope.expected_uid,
                expected_account_control,
            )
            return _publish_market_receipt(paths, evaluation)
    finally:
        await _dispose(selection, factories)


async def acquire_native_demo_source_only_v1(
    instrument_id: str,
) -> NativeSourceOnlyObservationV1:
    """No CLI/router calls this draft; never Arm, submit or issue account authority."""
    if _LOCK.locked():
        _deny("native_source_only_invocation_busy")
    async with _LOCK:
        paths = None
        try:
            settings = _settings(instrument_id)
            controlled._source_bootstrap_marker_v6()
            # Select and verify actual restricted logins before private GET.
            preflight = selectors.select_configured_native_demo_factories_v2()
            try:
                await _checked_roles(preflight)
                control = await inspect_existing_control(preflight.writer_factory)
                # A sticky stop/lock remains intact while this source-only
                # diagnostic reads and stores evidence. It is never an Arm
                # clearance or trading readiness signal. An active Arm would
                # race the original Control owner, so refuse this invocation.
                if type(control) is not ExistingControl or control.armed:
                    _deny("native_source_only_control_unavailable")
                writer_factory = preflight.writer_factory
            finally:
                await _dispose(None, preflight)
            paths = _roots(settings)
            await _check_control(writer_factory, control)
            history_digest, identity, account_control_pin = await _acquire_history(
                settings, instrument_id, paths, control
            )
            await _check_control(writer_factory, control)
            await _account_control_pin(writer_factory, identity[0], account_control_pin)
            # A failed history phase never begins a second private/public source.
            market_digest = await _acquire_market(
                settings,
                instrument_id,
                paths,
                identity,
                control,
                account_control_pin,
            )
            await _check_control(writer_factory, control)
            await _account_control_pin(writer_factory, identity[0], account_control_pin)
            result = NativeSourceOnlyObservationV1(history_digest, market_digest)
            _publish_summary(paths, result)
            return result
        except asyncio.CancelledError:
            if paths is not None:
                _retain_incomplete(paths["history-raw"].parent)
            raise
        except NativeSourceOnlyErrorV1:
            if paths is not None:
                _retain_incomplete(paths["history-raw"].parent)
            raise
        except Exception:  # noqa: BLE001 -- provider, DB and file detail stay private
            if paths is not None:
                _retain_incomplete(paths["history-raw"].parent)
            _deny("native_source_only_incomplete")
