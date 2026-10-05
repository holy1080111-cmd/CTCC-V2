"""Declared synthetic raw owner only; fixture construction before event loops."""

from types import SimpleNamespace

from app.trade_qualification import account_capture as account
from tests.unit import test_original_candidate_precursor_v2 as raw_fixture


def capture_direction(direction, tmp_path_factory):
    return raw_fixture.captured.__wrapped__(
        SimpleNamespace(param=direction), tmp_path_factory
    )


def capture_history(strategy, direction, tmp_path_factory):
    return raw_fixture.history_captured.__wrapped__(
        SimpleNamespace(param=(strategy, direction)), tmp_path_factory
    )


def arguments(diagnostic, packet, *, strategy="fvg_return", **updates):
    frozen = account.freeze_demo_account_packet(
        packet, expected_plan_sha256=packet.plan_sha256
    )
    old = raw_fixture.inputs(diagnostic, packet, strategy=strategy)
    return {
        "strategy": strategy,
        "expected_public_bundle_sha256": diagnostic.packet.bundle_sha256,
        "expected_account_plan_sha256": packet.plan_sha256,
        "expected_account_packet_sha256": frozen.sha256,
        "created_at": old["created_at"],
        "service_deadline": old["service_deadline"],
        **updates,
    }
