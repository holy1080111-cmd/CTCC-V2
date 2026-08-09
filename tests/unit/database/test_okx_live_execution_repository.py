from app.database.repositories.okx_live_execution import (
    OkxLiveExecutionRepository,
    _EXECUTION_ADVISORY_LOCK_ID,
)


def test_execution_intent_detail_codes_are_strict_and_bounded() -> None:
    values = OkxLiveExecutionRepository._safe_detail_codes(
        [
            "OKX_REST_ACKNOWLEDGED",
            "contains spaces and secret=abc",
            "okx_rest_acknowledged",
        ]
    )

    assert values == ["okx_rest_acknowledged"]


def test_execution_intent_unknown_details_collapse_to_unspecified() -> None:
    assert OkxLiveExecutionRepository._safe_detail_codes(["secret=abc"]) == [
        "unspecified"
    ]


def test_live_execution_uses_a_fixed_database_wide_advisory_lock() -> None:
    assert isinstance(_EXECUTION_ADVISORY_LOCK_ID, int)
    assert _EXECUTION_ADVISORY_LOCK_ID > 0
