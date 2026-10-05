"""Keep historical Live entry scripts unable to bypass qualification."""

from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
DENIAL = 'throw "CTCC_STOP:LIVE_QUALIFIED_ONE_SHOT_AUTHORITY_UNAVAILABLE"'


@pytest.mark.parametrize(
    ("script_name", "entry_route"),
    (
        ("execute_okx_live_micro_order.ps1", "/api/okx-live/orders"),
        (
            "run_okx_live_automation_once.ps1",
            "/api/okx-live/automation/run-once",
        ),
    ),
)
def test_historical_live_entry_script_stops_before_credentials_or_api_io(
    script_name: str, entry_route: str
) -> None:
    source = (SCRIPTS / script_name).read_text(encoding="utf-8")
    lines = source.splitlines()
    strict_mode = lines.index("Set-StrictMode -Version Latest")
    next_statement = next(
        line.strip()
        for line in lines[strict_mode + 1 :]
        if line.strip() and not line.lstrip().startswith("#")
    )

    assert next_statement == DENIAL
    assert source.index(DENIAL) < source.index("function Read-EnvValue")
    assert source.index(DENIAL) < source.index('$token = Read-EnvValue "API_TOKEN"')
    # Retain the historical route for review while this early stop bars it.
    assert entry_route in source
