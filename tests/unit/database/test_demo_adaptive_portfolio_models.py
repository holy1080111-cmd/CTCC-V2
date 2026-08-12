from sqlalchemy.dialects.postgresql import JSONB

from app.database.models.demo_automation import DemoAutomationState


def test_demo_automation_state_persists_portfolio_and_symbol_cooldowns() -> None:
    table = DemoAutomationState.__table__

    assert isinstance(table.c.active_trades.type, JSONB)
    assert table.c.active_trades.nullable is False
    assert isinstance(table.c.symbol_cooldowns.type, JSONB)
    assert table.c.symbol_cooldowns.nullable is False
    assert table.c.equity_basis.nullable is True


def test_demo_portfolio_state_does_not_add_credentials() -> None:
    columns = set(DemoAutomationState.__table__.c.keys())

    assert {"active_trades", "symbol_cooldowns", "equity_basis"} <= columns
    assert {
        "api_key",
        "api_secret",
        "passphrase",
        "secret",
        "token",
    }.isdisjoint(columns)
