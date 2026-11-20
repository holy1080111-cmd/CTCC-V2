"""Expected physical55 model from current source; never filter real reflection."""

from sqlalchemy import MetaData

FUTURE_TABLES = frozenset(
    {
        "gate3_fitted_candidate_bindings",
        "gate3_fitted_candidate_acks",
        "gate3_fitted_candidate_evaluations",
        "native_live_controls_v1",
        "native_live_reservations_v1",
        "native_live_intents_v1",
        "native_live_transitions_v1",
        "native_live_financial_epochs_v1",
        "native_live_financial_samples_v1",
        "native_live_lifecycle_keys_v1",
        "native_live_lifecycle_proofs_v1",
        "native_live_controlled_close_intents_v1",
        "native_live_controlled_close_claims_v1",
    }
)
FUTURE_INDEXES = frozenset(
    {
        "qualification_native_protected_hold_once_v58",
        "qualification_native_protective_flat_once_v58",
    }
)


def physical55_expected_metadata(current):
    target = MetaData(naming_convention=current.naming_convention)
    for table in current.sorted_tables:
        if table.name not in FUTURE_TABLES:
            copied = table.to_metadata(target)
            for index in tuple(copied.indexes):
                if index.name in FUTURE_INDEXES:
                    copied.indexes.remove(index)
    return target
