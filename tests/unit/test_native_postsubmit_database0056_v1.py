"""Negative DATA and safety paths never revive an expired trading selector."""

import builtins
from types import CodeType, FunctionType, SimpleNamespace

import pytest

from app.database.repositories import native_postsubmit_v1 as repository
from app.trade_qualification import native_controlled_account_source_v6 as source
from app.trade_qualification.reservations import LedgerScope


def _inert_database(*, current56):
    # Execute the pristine production predicate with inert types/registries.
    # No real native registry, selector, lease or database is modified.
    class OldRepository:
        pass

    class NewRepository:
        pass

    class Selected:
        pass

    class Roles:
        pass

    selected, roles, factory, token = Selected(), Roles(), object(), object()
    selected.writer_factory = factory
    repo = NewRepository() if current56 else OldRepository()
    repo.session_factory = factory
    repo._selected, repo._roles, repo._scope = selected, roles, "demo-scope"
    ctrl = {
        "repository": repo,
        "factory": factory,
        "scope": "demo-scope",
        "db0056_factories": selected if current56 else None,
        "db0056_roles": roles if current56 else None,
    }
    record = {
        "token": token,
        "state": {"control": ctrl},
        "repository": repo,
        "factory": factory,
        "scope": "demo-scope",
        "database_profile": (selected, roles) if current56 else None,
    }
    records = {token: record}
    function = FunctionType(
        source._transport_safety_database_v6.__code__,
        {
            "__builtins__": __builtins__,
            "_deny": source._deny,
            "_TRANSPORT_V6": records,
            "DemoControlRepositoryV2": OldRepository,
            "repository56": SimpleNamespace(DemoControlRepository0056V1=NewRepository),
            "selector56": SimpleNamespace(ConfiguredNativeDemoFactoriesV2=Selected),
            "witness56": SimpleNamespace(RestrictedNativeRoles0056WitnessV1=Roles),
        },
    )
    return function, record, records


@pytest.mark.parametrize("current56", [False, True])
def test_spent_transport_replays_original_database_without_a_trading_lease(current56):
    replay, record, _ = _inert_database(current56=current56)
    assert replay(record) is record["database_profile"]


@pytest.mark.parametrize("change", ["role", "factory", "scope", "registry", "old55"])
def test_spent0056_transport_rejects_changed_database_without_old55_fallback(change):
    replay, record, records = _inert_database(current56=True)
    if change == "role":
        record["repository"]._roles = object()
    elif change == "factory":
        record["database_profile"][0].writer_factory = object()
    elif change == "scope":
        record["repository"]._scope = "foreign"
    elif change == "registry":
        records.clear()
    else:
        record["database_profile"] = None
    with pytest.raises(
        source.ControlledAccountSourceError,
        match="original_transport_database_changed$",
    ):
        replay(record)


def _inert_inhibitor(*, broken_database):
    codes = [
        value
        for value in source._make_transport_inhibitor_v6.__code__.co_consts
        if type(value) is CodeType and value.co_name == "inhibit"
    ]
    assert len(codes) == 1

    class Owner:
        pass

    owner, factory, repo, operation = Owner(), object(), object(), object()
    owner.repository = repo
    state = {"control": {"owner": owner, "factory": factory, "scope": "demo"}}
    record = {
        "state": state,
        "owner": owner,
        "repository": repo,
        "factory": factory,
        "scope": "demo",
    }
    events = []

    def revoke(actual, *, stop):
        assert actual is owner and stop is True
        events.append("local_stop")

    def retire(actual, *, uncertain):
        assert actual is state and uncertain is True
        events.append("retain_unknown")

    def database(actual):
        assert actual is record
        events.append("database")
        if broken_database:
            source._deny("synthetic_database_changed")

    def persist(actual):
        assert actual is record
        events.append("persist")
        return operation

    values = {
        "owner_type": Owner,
        "revoke": revoke,
        "database": database,
        "persist": persist,
    }

    def cell(value):
        return (lambda: value).__closure__[0]

    code = codes[0]
    function = FunctionType(
        code,
        {
            "__builtins__": __builtins__,
            "_deny": source._deny,
            "_RETIRE_SOURCE_V6": retire,
        },
        closure=tuple(cell(values[name]) for name in code.co_freevars),
    )
    return function, record, events, operation


def test_conservative_stop_revokes_locally_before_database_persistence():
    inhibit, record, events, operation = _inert_inhibitor(broken_database=False)
    assert inhibit(record) is operation
    assert events == ["local_stop", "retain_unknown", "database", "persist"]


def test_database_drift_still_revokes_and_retains_unknown_without_persistence():
    inhibit, record, events, _ = _inert_inhibitor(broken_database=True)
    with pytest.raises(source.ControlledAccountSourceError, match="database_changed$"):
        inhibit(record)
    assert events == ["local_stop", "retain_unknown", "database"]


@pytest.mark.parametrize("held_kind", ["absent", "foreign_binding", "foreign_session"])
async def test0056_postsubmit_catalog_requires_its_own_still_held_transaction(
    held_kind,
):
    binding, session, selected = object(), object(), object()
    held = SimpleNamespace(selected=selected, session=session)
    active = {}
    if held_kind == "foreign_binding":
        active[session] = (object(), held)
    elif held_kind == "foreign_session":
        held.session = object()
        active[session] = (binding, held)

    async def legacy(_):
        pytest.fail("DB0056 must not fall back to the old55 catalog")

    checked = FunctionType(
        repository._checked_postsubmit_catalog.__code__,
        {
            "__builtins__": __builtins__,
            "_database_profile": lambda actual: (selected, object()),
            "_HELD_POSTSUBMIT_0056": active,
            "verify_actual_issuer_catalog_v2": legacy,
            "need": repository.need,
        },
    )
    with pytest.raises(
        repository.NativePostsubmitRepositoryError,
        match="original_0056_transaction_required$",
    ):
        await checked(binding, session)


async def test0056_retained_catalog_rechecks_actual_held_witness_without_admission():
    binding, session, selected = object(), object(), object()
    events = []

    async def recheck():
        events.append("actual_catalog")

    held = SimpleNamespace(selected=selected, session=session, recheck_catalog=recheck)
    checked = FunctionType(
        repository._checked_postsubmit_catalog.__code__,
        {
            "__builtins__": __builtins__,
            "_database_profile": lambda actual: (selected, object()),
            "_HELD_POSTSUBMIT_0056": {session: (binding, held)},
            "_source": lambda actual: events.append("source"),
            "need": repository.need,
        },
    )
    assert await checked(binding, session) is None
    assert events == ["actual_catalog", "source"]


def _inert_retained_origin():
    factory, state, calls = object(), {}, []
    view = SimpleNamespace(
        session_factory=factory,
        scope=LedgerScope(
            environment="demo", account_id="730057", settlement_currency="USDT"
        ),
    )
    profile = (SimpleNamespace(writer_factory=factory), object())
    pins, routes, views = ["original-pin"], ["original-route"], [view]

    def replay(actual, *, key):
        assert actual is view
        calls.append("genuine_active_transport")
        return profile

    account = SimpleNamespace(
        history=SimpleNamespace(_factory_pin=lambda actual: pins[0]),
        native=SimpleNamespace(_database_connection_pin_v4=lambda actual: routes[0]),
        _replay_reconciliation_database_v6=replay,
    )
    original_import = builtins.__import__

    def import_inert(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "app.trade_qualification" and fromlist == (
            "native_controlled_account_source_v6",
        ):
            return SimpleNamespace(native_controlled_account_source_v6=account)
        return original_import(name, globals, locals, fromlist, level)

    function = FunctionType(
        repository._database_profile.__code__,
        {
            "__builtins__": {**vars(builtins), "__import__": import_inert},
            "_source": lambda binding: (state, views[0]),
            "_SOURCE_KEY_V1": object(),
            "canonical": source.canonical,
            "need": repository.need,
        },
    )
    return function, state, views, pins, routes, calls, profile, account


def test_retained_origin_survives_actual_transport_registry_retirement():
    replay, state, _, _, _, calls, profile, account = _inert_retained_origin()
    assert replay(object()) is profile
    saved = state["postsubmit_database_origin_v6"]

    def removed(*args, **kwargs):
        pytest.fail("negative durable cleanup must not revive the retired transport")

    account._replay_reconciliation_database_v6 = removed
    assert replay(object()) is profile
    assert state["postsubmit_database_origin_v6"] is saved
    assert calls == ["genuine_active_transport"]


@pytest.mark.parametrize("change", ["factory_pin", "route", "scope", "view"])
def test_retired_origin_rejects_database_or_source_changes(change):
    replay, _, views, pins, routes, _, _, _ = _inert_retained_origin()
    replay(object())
    if change == "factory_pin":
        pins[0] = "changed"
    elif change == "route":
        routes[0] = "changed"
    elif change == "scope":
        views[0].scope = LedgerScope(
            environment="demo", account_id="730058", settlement_currency="USDT"
        )
    else:
        views[0] = SimpleNamespace(**vars(views[0]))
    with pytest.raises(
        repository.NativePostsubmitRepositoryError, match="original_database_changed$"
    ):
        replay(object())
