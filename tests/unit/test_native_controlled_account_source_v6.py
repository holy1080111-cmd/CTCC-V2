"""Pure/negative V6 mechanisms; no source, DB, account or execution credit.

The temporal and byte-budget examples below are DATA primitive tests only.
Only real same-invocation PG/API acceptance can test a positive issued owner.
"""

from fractions import Fraction
from pathlib import Path

import pytest

from app.trade_qualification import native_controlled_account_source_v6 as source


@pytest.mark.parametrize(
    "kind",
    (
        source._AcquisitionV6,
        source._CoverageBindingV6,
        source._HistoryUseV6,
        source._IssuerOwnerV6,
        source.OwnedIssuedAccountRevisionV1,
        source._HeldIssuedAccountV1,
        source._DemoTransportBindingV2,
    ),
)
def test_native_source_owner_has_no_data_constructor(kind):
    with pytest.raises(source.ControlledAccountSourceError, match="not_constructible"):
        kind()


def test_exact_decimal_conversion_never_rounds_a_third():
    assert source._decimal_exact_v6(Fraction(3, 8)) == source.Decimal("0.375")
    with pytest.raises(source.ControlledAccountSourceError, match="rounding_forbidden"):
        source._decimal_exact_v6(Fraction(1, 3))


@pytest.mark.parametrize("value", (1, True, "0.5", source.Decimal("0.5")))
def test_exact_decimal_conversion_rejects_untyped_operands(value):
    with pytest.raises(
        source.ControlledAccountSourceError, match="exact_amount_required"
    ):
        source._decimal_exact_v6(value)


def test_distinct_source_roots_preserve_all_four_actual_paths(tmp_path):
    paths = tuple(
        tmp_path / name for name in ("history", "companion", "current", "native")
    )
    roots = source.ControlledAccountRootsV6(*paths)
    assert (
        roots.history_raw_root,
        roots.history_companion_root,
        roots.account_proof_root,
        roots.account_native_root,
    ) == paths


def test_source_roots_cannot_alias_or_nest(tmp_path):
    with pytest.raises(source.ControlledAccountSourceError, match="distinct_roots"):
        source.ControlledAccountRootsV6(
            tmp_path, tmp_path / "nested", tmp_path / "third", tmp_path / "fourth"
        )
    with pytest.raises(source.ControlledAccountSourceError, match="distinct_roots"):
        source.ControlledAccountRootsV6(
            Path("relative"),
            tmp_path / "second",
            tmp_path / "third",
            tmp_path / "fourth",
        )


@pytest.mark.parametrize(
    "change", ("utc_equal", "mono_equal", "utc_before", "mono_before")
)
def test_fresh_request_requires_strictly_after_both_original_barrier_axes(change):
    barrier = {"utc_ns": 100, "monotonic_ns": 200}
    later = {"utc_ns": 101, "monotonic_ns": 201}
    if change == "utc_equal":
        later["utc_ns"] = 100
    if change == "mono_equal":
        later["monotonic_ns"] = 200
    if change == "utc_before":
        later["utc_ns"] = 99
    if change == "mono_before":
        later["monotonic_ns"] = 199
    with pytest.raises(ValueError):
        source._strictly_after_v6(barrier, later)


def test_fresh_witness_scan_does_not_relabel_historical_business_times():
    barrier = {"utc_ns": 100, "monotonic_ns": 200}
    requests = [
        {"phase": "request_start", "stamp": {"utc_ns": 101, "monotonic_ns": 201}},
        {"phase": "request_dispatch", "stamp": {"utc_ns": 102, "monotonic_ns": 202}},
    ]
    assert source._request_witnesses_after_v6(barrier, requests) is None
    # No historical transaction timestamp is accepted as a request witness.
    with pytest.raises(source.ControlledAccountSourceError, match="no_fresh_request"):
        source._request_witnesses_after_v6(
            barrier, [{"phase": "historical_fill", "stamp": barrier}]
        )


def test_retained_byte_budget_counts_the_real_bytes_and_rejects_excess():
    # This private DATA primitive cannot insert a registry entry or mint a cap.
    at_limit = b"x" * source._MAX_RETAINED_V6
    assert source._retained_budget_v6({"claims_json": at_limit}) is None
    with pytest.raises(source.ControlledAccountSourceError, match="byte_capacity"):
        source._retained_budget_v6({"claims_json": at_limit}, extra=(b"y",))


def test_prestart_attempt_close_retires_before_the_body_can_start():
    events = []
    state = object()

    async def body():
        events.append("body_started")

    coroutine = body()

    def retire(actual, *, uncertain):
        assert actual is state and uncertain is True
        events.append("retired")

    attempt = source._AcquisitionAttemptV6(state, coroutine, _retire=retire)
    attempt.close()
    assert events == ["retired"]
    assert coroutine.cr_frame is None
    attempt.close()
    assert events == ["retired"]


def test_source_data_cannot_be_promoted_by_foreign_issuer_or_transport_keys():
    with pytest.raises(source.ControlledAccountSourceError, match="purpose"):
        source._issuer_key_v6(object())
    with pytest.raises(source.ControlledAccountSourceError, match="purpose"):
        source._transport_key_v6(object())
    with pytest.raises(source.ControlledAccountSourceError, match="purpose"):
        source._issued_purpose_v6(object())
