"""Normal typed requests and denied forged ownership; no DB, venue or Arm."""

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.database.repositories import native_protective_hold_v1 as hold_repository
from app.demo_automation import DemoAutomationSafetyError
from app.demo_automation import native_caller_v2 as caller
from app.demo_automation.native_requests_v2 import (
    PROTECTED_ENTRY_CONFIRMATION_V1,
    NativeDemoArmExecuteRequestV2,
    NativeDemoProtectedEntryRequestV1,
)
from app.domain import native_protective_hold_schema_v58 as hold_schema
from app.domain.demo_automation import ARM_PHRASE, EXECUTE_PHRASE
from app.trade_qualification import native_protected_hold_lifecycle_v1 as holding
from app.trade_qualification import native_protected_hold_recovery_v1 as recovery
from tests.unit.native_function_primitive_fixture_v1 import foreign_primitive_v1


def _request(**changes):
    raw = {
        "instrument_id": "BTC-USDT-SWAP",
        "strategy": "trend_pullback",
        "contracts": "1",
        "leverage": 3,
        "arm_confirmation": ARM_PHRASE,
        "execute_confirmation": EXECUTE_PHRASE,
        "hold_confirmation": PROTECTED_ENTRY_CONFIRMATION_V1,
    }
    raw.update(changes)
    return NativeDemoProtectedEntryRequestV1(**raw)


def test_new_owned_lifecycle_and_data_recovery_guards_compile_coherently():
    holding._NATIVE_GUARD()
    recovery._NATIVE_GUARD()


def test_normal_mode_binds_extra_exact_phrase_to_same_original_frozen_entry():
    request = _request()
    planned = caller._freeze_request_v2(request)
    assert (
        planned.action == "arm_execute"
        and planned.confirmation == PROTECTED_ENTRY_CONFIRMATION_V1
    )
    original = request.model_dump(exclude={"hold_confirmation"})
    legacy = caller._freeze_request_v2(NativeDemoArmExecuteRequestV2(**original))
    assert legacy.confirmation is None and legacy != planned
    assert (
        planned.instrument_id,
        planned.strategy,
        planned.contracts,
        planned.leverage,
    ) == (legacy.instrument_id, legacy.strategy, legacy.contracts, legacy.leverage)


@pytest.mark.parametrize(
    "change",
    [
        {"hold_confirmation": "true"},
        {"hold_confirmation": True},
        {"hold_confirmation": None},
        {"hold_confirmation": PROTECTED_ENTRY_CONFIRMATION_V1 + " "},
    ],
)
def test_constructed_or_mutated_normal_phrase_cannot_grant_arm(change):
    with pytest.raises(ValidationError):
        _request(**change)
    request = NativeDemoProtectedEntryRequestV1.model_construct(
        **{**_request().model_dump(), **change}
    )
    with pytest.raises(DemoAutomationSafetyError):
        caller._freeze_request_v2(request)


def test_normal_holding_is_not_an_extra_flag_on_acceptance_request():
    raw = _request().model_dump()
    with pytest.raises(ValidationError):
        NativeDemoArmExecuteRequestV2(**raw)
    request = _request()
    planned = caller._freeze_request_v2(request)
    request.__dict__["hold_confirmation"] = "changed"
    with pytest.raises(DemoAutomationSafetyError):
        caller._check_request_v2(request, planned)


@pytest.mark.asyncio
async def test_constructed_hold_result_never_creates_owned_retirement():
    completion = holding.NativeProtectedHoldCompletionV1(
        "a" * 64, "b" * 64, "c" * 64, object()
    )
    with pytest.raises(ValueError):
        holding.take_owned_protected_hold_completion_v1(
            completion, {}, key=caller._SETTLEMENT_KEY_V1
        )
    assert completion.risk_hold_retained is completion.entry_authority_revoked is True
    assert completion.execution_authority is completion.order_retry_authority is False


@pytest.mark.asyncio
async def test_unowned_data_fields_never_invoke_foreign_hash_or_equality():
    class Foreign:
        def __hash__(self):
            raise AssertionError("unowned DATA hash executed")

        def __eq__(self, other):
            raise AssertionError("unowned DATA equality executed")

    completion = holding.NativeProtectedHoldCompletionV1(
        "a" * 64, "b" * 64, "c" * 64, Foreign()
    )
    with pytest.raises(ValueError):
        holding.take_owned_protected_hold_completion_v1(
            completion, {}, key=caller._SETTLEMENT_KEY_V1
        )


@pytest.mark.asyncio
async def test_unregistered_data_recovery_cannot_call_get_or_resolve(monkeypatch):
    called = []

    def refuse(_state):
        called.append("original-source-refusal")
        raise ValueError("synthetic-original-source-refusal")

    # Foreign primitive exercises ordering only; it is explicitly not a native
    # source/caller/DB witness and cannot pass the exported production guard.
    primitive = foreign_primitive_v1(
        recovery.reconcile_owned_protected_history_v1,
        {"_NATIVE_GUARD": lambda: None, "post": SimpleNamespace(_source=refuse)},
    )
    with pytest.raises(ValueError, match="synthetic-original-source-refusal"):
        await primitive({})
    assert called == ["original-source-refusal"]


@pytest.mark.asyncio
async def test_installed_schema_uses_bare_pg_argument_types_and_refuses_named_substitution():
    """Synthetic SQL metadata handoff only; not an actual PostgreSQL receipt."""
    rows = [
        (
            hold_schema.FUNCTION,
            hold_schema.FUNCTION_IDENTITY,
            hold_schema.PREDICATE_BODY_SHA256,
            False,
            ["search_path=pg_catalog, pg_temp"],
        ),
        (
            "qualification_reservation_update",
            "",
            hold_schema.CURRENT_BODY_SHA256,
            False,
            ["search_path=pg_catalog, pg_temp"],
        ),
    ]

    class SyntheticSession:
        async def execute(self, query):
            sql = str(query)
            assert "pg_catalog.oidvectortypes(p.proargtypes)" in sql
            assert "pg_get_function_identity_arguments" not in sql
            return SimpleNamespace(all=lambda: rows)

    await hold_repository._require_installed_schema(None, SyntheticSession())
    rows[0] = (
        rows[0][0],
        "rid text, closing_at timestamp with time zone",
        *rows[0][2:],
    )
    with pytest.raises(ValueError):
        await hold_repository._require_installed_schema(None, SyntheticSession())
