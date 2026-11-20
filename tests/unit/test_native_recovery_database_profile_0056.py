"""Replay the actual DATA-only predicate with inert original registries."""

from types import FunctionType, SimpleNamespace

import pytest

from app.trade_qualification import native_controlled_account_source_v6 as source


def fixture(*, current56=True):
    class Selected:
        pass

    class Roles:
        pass

    selected, roles = Selected(), Roles()
    writer, raw = object(), object()
    selected.writer_factory, selected.frame_reader_factory = writer, raw
    roles.observation = SimpleNamespace(database_head="0056")
    token = object()
    record = {
        "generation": token,
        "source_state": None,
        "transport": None,
        "control_owner": None,
        "writer_factory": writer,
        "raw_factory": raw,
        "recovery_database_profile": (selected, roles) if current56 else None,
    }
    records = {token: record}
    replay = FunctionType(
        source._recovery_database_profile_v6.__code__,
        {
            "__builtins__": __builtins__,
            "_RECONCILIATION_READ_V6": records,
            "_deny": source._deny,
            "selector56": SimpleNamespace(ConfiguredNativeDemoFactoriesV2=Selected),
            "witness56": SimpleNamespace(RestrictedNativeRoles0056WitnessV1=Roles),
        },
    )
    return replay, record, records


@pytest.mark.parametrize("current56", [True, False])
def test_retained_original_data_profile_needs_no_trading_selector_replay(current56):
    replay, record, _ = fixture(current56=current56)
    assert replay(record) is record["recovery_database_profile"]


@pytest.mark.parametrize(
    "fault",
    ["registry", "missing", "transport", "owner", "head", "writer", "raw", "roles"],
)
def test_recovery_database_drift_never_falls_back_to_old55(fault):
    replay, record, records = fixture()
    if fault == "registry":
        records.clear()
    elif fault == "missing":
        del record["recovery_database_profile"]
    elif fault in {"transport", "owner"}:
        record["transport" if fault == "transport" else "control_owner"] = object()
    elif fault == "head":
        record["recovery_database_profile"][1].observation.database_head = "0055"
    elif fault in {"writer", "raw"}:
        record["writer_factory" if fault == "writer" else "raw_factory"] = object()
    else:
        record["recovery_database_profile"] = (
            record["recovery_database_profile"][0],
            object(),
        )
    with pytest.raises(
        source.ControlledAccountSourceError, match="original_database_required"
    ):
        replay(record)


def test_unregistered_recovery_data_dictionary_has_no_profile_authority():
    replay, record, _ = fixture()
    with pytest.raises(source.ControlledAccountSourceError):
        replay(dict(record))
