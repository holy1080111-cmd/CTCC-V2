"""Synthetic checks for a redacted, retained Live GET-only readback."""

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.config.settings import Settings
from app.domain.source_primitives import ClockStamp, sha
from app.okx_live import micro_live_readonly_preflight_v1 as subject


@pytest.fixture
def readback(tmp_path, monkeypatch):
    settings = Settings(
        _env_file=None,
        environment="production",
        trading_mode="live",
        okx_live_enabled=True,
        paper_auto_execution=False,
        okx_demo_allow_order_writes=False,
        okx_demo_auto_execution=False,
        okx_live_api_key="synthetic-key",
        okx_live_api_secret="synthetic-secret",
        okx_live_api_passphrase="synthetic-passphrase",
        okx_live_expected_uid="1234567890123456",
        okx_live_expected_main_uid="1234567890123457",
    )
    lease = sha(b"synthetic lease")
    origin = settings.okx_live_rest_base_url
    current_id = f"{100:032x}"
    current_pin = sha(b"synthetic current custody")
    pins = tuple(
        subject.LiveHistoryCustodyPinV1(
            attempt_id=f"{index:032x}",
            custody_sha256=sha(f"history {index}".encode()),
            selector=subject.select_live_account_history_page_v1(
                family,
                begin_ms="1785000000000",
                end_ms="1785000001000",
                inst_type=None if family.startswith("bills_") else "SWAP",
            ),
        )
        for index, family in enumerate(subject._FAMILIES, 1)
    )

    def stamp(value):
        return ClockStamp(utc_ns=1_785_000_002_000_000_000 + value, monotonic_ns=value)

    def receipt(attempt, plan, ordinal):
        return {
            "synthetic_mock": False,
            "plan_sha256": sha(plan),
            "config_before_custody_sha256": sha(f"{attempt} before".encode()),
            "config_after_custody_sha256": sha(f"{attempt} after".encode()),
            "capture_completed": stamp(ordinal * 100 + 10).model_dump(),
        }

    current_receipt = receipt(
        current_id,
        subject.current._plan(
            current_id,
            tuple(sorted(settings.okx_live_allowed_symbol_list)),
            lease_binding_id=lease,
            origin=origin,
        ),
        0,
    ) | {"no_open_positions_or_pending_orders_observed": True}
    by_attempt = {
        pin.attempt_id: receipt(
            pin.attempt_id,
            subject.history._plan(
                pin.attempt_id, pin.selector, lease_binding_id=lease, origin=origin
            ),
            index,
        )
        | {"explicit_terminal_page_observed": True, "page_count": 1, "row_count": 0}
        for index, pin in enumerate(pins, 1)
    }
    account = SimpleNamespace(
        uid=settings.okx_live_expected_uid.get_secret_value(),
        main_uid=settings.okx_live_expected_main_uid.get_secret_value(),
        account_mode="2",
        position_mode="net_mode",
    )
    changes = {"lease": lease}

    def config(root, *, attempt_id, expected_custody_sha256):
        chain = root.name
        ordinal = 0 if chain == current_id else int(chain, 16)
        selected = current_receipt if chain == current_id else by_attempt[chain]
        child = "before" if attempt_id == subject.current._CONFIG_BEFORE else "after"
        assert selected[f"config_{child}_custody_sha256"] == expected_custody_sha256
        return {}, SimpleNamespace(
            synthetic_mock=False,
            lease_binding_id=changes["lease"],
            origin=origin,
            raw_body=b"synthetic private config",
            stamps=(stamp(ordinal * 100),),
        )

    def current(root, *, attempt_id, expected_custody_sha256):
        assert root == tmp_path and attempt_id == current_id
        assert expected_custody_sha256 == current_pin
        return current_receipt

    def history(root, *, attempt_id, expected_custody_sha256):
        assert root == tmp_path
        assert expected_custody_sha256 == pins[int(attempt_id, 16) - 1].custody_sha256
        return by_attempt[attempt_id]

    monkeypatch.setattr(subject.current, "read_live_current_account_v1", current)
    monkeypatch.setattr(subject.history, "read_live_account_history_v1", history)
    monkeypatch.setattr(subject, "_read_custodied_live_account_config", config)
    monkeypatch.setattr(subject.current, "_check_config", lambda body: account)
    return (
        {
            "settings": settings,
            "private_root": tmp_path,
            "current_attempt_id": current_id,
            "expected_current_custody_sha256": current_pin,
            "history_pins": pins,
            "expected_retained_lease_binding_id": lease,
        },
        current_receipt,
        by_attempt,
        changes,
    )


def test_exact_readback_records_only_observed_exchange_flatness(readback):
    arguments, current, _, _ = readback
    result = subject.build_micro_live_readonly_preflight_v1(**arguments)
    assert result.exchange_flat_observed is True
    assert (
        result.exchange_position_count,
        result.exchange_pending_order_count,
        result.exchange_pending_algo_count,
    ) == (0, 0, 0)
    assert result.tracked_exposure_count is result.active_risk_reservation_count is None
    assert result.unresolved_intent_count is result.durable_safety_latch_clear is None
    assert result.current_process_arm_state is None
    assert result.micro_live_ready_awaiting_operator_arm is False
    assert result.execution_authority is result.source_authenticity_verified is False
    assert result.account_requests == result.order_writes == 0
    raw = subject.micro_live_readonly_preflight_bytes_v1(result)
    assert b"synthetic-secret" not in raw and b"1234567890123456" not in raw

    current["no_open_positions_or_pending_orders_observed"] = False
    occupied = subject.build_micro_live_readonly_preflight_v1(**arguments)
    assert occupied.exchange_flat_observed is False
    assert (
        occupied.exchange_position_count is occupied.exchange_pending_algo_count is None
    )


@pytest.mark.parametrize("defect", ("live_flag", "lease", "terminal", "plan"))
def test_changed_sources_or_unsafe_settings_are_refused(readback, defect):
    arguments, _, history, changes = readback
    first = next(iter(history.values()))
    if defect == "live_flag":
        arguments["settings"] = arguments["settings"].model_copy(
            update={"okx_live_allow_order_writes": True}
        )
    elif defect == "lease":
        changes["lease"] = sha(b"other lease")
    elif defect == "terminal":
        first["explicit_terminal_page_observed"] = False
    else:
        first["plan_sha256"] = sha(b"foreign history selector")
    with pytest.raises(subject.MicroLiveReadbackError):
        subject.build_micro_live_readonly_preflight_v1(**arguments)


@pytest.mark.parametrize(
    "field",
    (
        "execution_authority",
        "source_authenticity_verified",
        "live_entry_authority_available",
        "live_maintenance_authority_available",
        "micro_live_ready_awaiting_operator_arm",
    ),
)
def test_true_authority_cannot_enter_canonical_wire(readback, field):
    arguments, _, _, _ = readback
    result = subject.build_micro_live_readonly_preflight_v1(**arguments)
    changed = result.model_copy(update={field: True})
    with pytest.raises(ValidationError):
        subject.micro_live_readonly_preflight_bytes_v1(changed)
