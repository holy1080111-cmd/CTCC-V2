"""The retention denial names safe schema defects without exposing catalog text."""

from app.database.repositories.qualification_ledger import (
    _event_journal_schema_mismatch_detail,
)

REQUIRED = {
    ("qualification_account_scopes", "qualification_scope_no_delete"): (
        11,
        "qualification_ledger_immutable",
    ),
    ("qualification_reservations", "qualification_reservation_no_delete"): (
        11,
        "qualification_ledger_immutable",
    ),
}


def test_schema_detail_names_missing_and_wrong_guards_without_catalog_values():
    rows = [
        (
            "qualification_reservations",
            "qualification_reservation_no_delete",
            "D",
            11,
            "untrusted_catalog_function",
            0,
            "untrusted_catalog_schema",
            False,
        )
    ]

    detail = _event_journal_schema_mismatch_detail(rows, REQUIRED, [])

    assert detail == (
        "triggers=qualification_account_scopes.qualification_scope_no_delete=missing,"
        "qualification_reservations.qualification_reservation_no_delete="
        "not_origin_enabled+function_mismatch+function_schema_mismatch+"
        "function_identity_mismatch;uid_unique=missing"
    )
    assert "untrusted_catalog" not in detail


def test_schema_detail_distinguishes_uid_key_mismatch_from_valid_trigger():
    rows = [
        (table, trigger, "O", 11, "qualification_ledger_immutable", 0, "public", True)
        for table, trigger in REQUIRED
    ]

    detail = _event_journal_schema_mismatch_detail(
        rows,
        REQUIRED,
        [
            (
                False,
                True,
                "environment,account_id,settlement_currency,original_event_key",
            )
        ],
    )

    assert detail == "triggers=valid;uid_unique=columns_mismatch"
