import pytest

from app.dashboard.router import dashboard


@pytest.mark.asyncio
async def test_frontend_declares_snapshot_contract() -> None:
    response = await dashboard()
    html = response.body.decode("utf-8")

    assert (
        'SUPPORTED_SNAPSHOT_CONTRACT_VERSION = "1.0"'
        in html
    )
    assert (
        "MAX_SNAPSHOT_FUTURE_SKEW_MS = 30000"
        in html
    )
    assert "EXPECTED_SNAPSHOT_SOURCES" in html

    for source_name in (
        "balance",
        "positions",
        "algo_orders",
        "automation",
        "performance",
        "validation",
        "events",
    ):
        assert f'"{source_name}"' in html


@pytest.mark.asyncio
async def test_frontend_validates_snapshot_contract() -> None:
    response = await dashboard()
    html = response.body.decode("utf-8")

    required_markers = [
        "class SnapshotContractError extends Error",
        "function validateSnapshotContract(snapshot)",
        "unsupported_contract_version",
        "invalid_snapshot_id",
        "generated_at_timezone_missing",
        "generated_at_too_far_in_future",
        "source_set_mismatch",
        "invalid_source_status",
        "complete_source_status_mismatch",
        "successful_source_missing_value",
        "failed_source_contains_value",
        "list_source_not_array",
        "failed_source_contains_items",
    ]

    for marker in required_markers:
        assert marker in html


@pytest.mark.asyncio
async def test_contract_gate_runs_before_render() -> None:
    response = await dashboard()
    html = response.body.decode("utf-8")

    validation_index = html.index(
        "validateSnapshotContract(snapshot);"
    )

    first_render_index = html.index(
        "renderBalance(snapshot.balance);"
    )

    assert validation_index < first_render_index

    assert 'stateElement.textContent = "Contract Error"' in html
    assert "lastContractError = null" in html
    assert "error.code" in html
    assert "SnapshotContractError" in html
