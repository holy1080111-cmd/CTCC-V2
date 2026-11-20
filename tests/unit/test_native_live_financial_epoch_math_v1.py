"""Prospective epoch arithmetic only, not a DB enrollment or Live acceptance."""

from datetime import timedelta
from decimal import Decimal
from fractions import Fraction

import pytest

from app.domain import native_live_financial_epoch_schema_v1 as spec
from app.domain.source_primitives import canonical, decode
from app.okx_live import financial_epoch_math_v1 as subject
from app.okx_live import financial_rows_v1 as rows
from tests.unit import test_native_live_financial_rows_v1 as fixtures


def _loss(fills, bills, position):
    fills[1]["fillPnl"] = "-1"
    bills[1]["pnl"] = "-1"
    position.update(pnl="-1", realizedPnl="-1.03")


def test_genesis_preserves_real_pre_epoch_daily_and_weekly_losses_and_labels_sampled_peak():
    source = rows._derive_original_financial_rows_v1(fixtures._view(mutation=_loss))
    epoch = subject.epoch_document(source)
    document = decode(
        subject.sample_document(source, original_epoch_json=epoch, original_prefix=()),
        maximum=2097152,
    )
    assert subject.read_ratio(document["daily_net_pnl"]) == Fraction(-103, 100)
    assert subject.read_ratio(document["weekly_net_pnl"]) == Fraction(-103, 100)
    assert document["controlled_outcomes"] == [] and document["loss_streak"] == 0
    assert document["peak_window_started_at"] == source.observed_at.isoformat()
    assert subject.read_ratio(document["measured_peak"]) == 100
    assert document["account_complete"] is document["execution_authority"] is False


def test_every_durable_equity_sample_is_used_and_caller_peak_fields_are_not_adopted(
    monkeypatch,
):
    first = rows._derive_original_financial_rows_v1(fixtures._view())
    epoch = subject.epoch_document(first)
    raw = subject.sample_document(first, original_epoch_json=epoch, original_prefix=())
    earlier = decode(raw, maximum=2097152)
    earlier["measured_peak"] = {"numerator": "99999", "denominator": "1"}
    monkeypatch.setattr(fixtures, "NOW", fixtures.NOW + timedelta(minutes=10))
    current = rows._derive_original_financial_rows_v1(fixtures._view())
    from dataclasses import replace

    current = replace(current, equity=Decimal(95))
    later = decode(
        subject.sample_document(
            current, original_epoch_json=epoch, original_prefix=(canonical(earlier),)
        ),
        maximum=2097152,
    )
    assert subject.read_ratio(later["measured_peak"]) == 100
    assert later["peak_observed_at"] == first.observed_at.isoformat()
    assert len(later["controlled_outcomes"]) == 1


def test_only_complete_post_genesis_closed_run_with_actual_funding_can_advance_streak(
    monkeypatch,
):
    first = rows._derive_original_financial_rows_v1(fixtures._view())
    epoch = subject.epoch_document(first)
    raw = subject.sample_document(first, original_epoch_json=epoch, original_prefix=())
    monkeypatch.setattr(fixtures, "NOW", fixtures.NOW + timedelta(minutes=10))
    current = rows._derive_original_financial_rows_v1(fixtures._view(mutation=_loss))
    later = decode(
        subject.sample_document(
            current, original_epoch_json=epoch, original_prefix=(raw,)
        ),
        maximum=2097152,
    )
    assert later["loss_streak"] == 1
    assert subject.read_ratio(later["controlled_outcomes"][0]["net_pnl"]) == Fraction(
        -103, 100
    )
    assert subject.read_ratio(
        later["controlled_outcomes"][0]["settled_funding"]
    ) == Fraction(-1, 100)


@pytest.mark.parametrize("changed", (False, None, 1, "0"))
def test_epoch_boolean_missing_or_arbitrary_seed_is_not_adopted(changed):
    source = rows._derive_original_financial_rows_v1(fixtures._view())
    epoch = decode(subject.epoch_document(source), maximum=65536)
    epoch["prospective_seed"] = changed
    with pytest.raises(ValueError):
        subject.sample_document(
            source, original_epoch_json=canonical(epoch), original_prefix=()
        )


def test_epoch_spec_has_separate_collector_no_live_write_arm_or_erase_privilege():
    grants = spec.live_financial_collector_grant_sql_v1("ctcc_live_financial")
    assert all(
        "UPDATE" not in statement
        and "DELETE" not in statement
        and "TRUNCATE" not in statement
        and "EXECUTE" not in statement
        for statement in grants
    )
    assert all("demo" not in statement for statement in grants)
    assert len(spec.UPGRADE_SQL) == 6 and len(spec.DOWNGRADE_SQL) == 6
    assert any(
        "retained_prefix_downgrade_denied" in statement
        for statement in spec.DOWNGRADE_SQL
    )
    assert "no_reset" in spec.POLICY_BYTES.decode()
