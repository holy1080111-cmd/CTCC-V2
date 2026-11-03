"""Negative/pure DATA contracts only; no genuine owner, DB or remote credit."""

from datetime import UTC, datetime

import pytest

from app.trade_evidence import delivery_source_authority as gate
from app.trade_evidence import native_notion_delivery_source_v55 as source
from app.trade_qualification import native_continuation_guard_v1 as guard


@pytest.mark.parametrize("value", (None, False, True, {}, object()))
def test_flags_and_data_do_not_authorize_a_notion_request(value):
    with pytest.raises(gate.DeliverySourceAuthorityUnavailable):
        gate.require_issuer_backed_projection(
            value, adapter=None, payload=None, claim=None
        )


def test_native_source_cannot_be_constructed_from_data():
    with pytest.raises(source.NativeNotionSourceUnavailable):
        source.OwnedNativeNotionDeliverySourceV55()


def test_exact_unregistered_shell_does_not_replace_live_source_registry(tmp_path):
    empty = object.__new__(source.OwnedNativeNotionDeliverySourceV55)
    with pytest.raises(source.NativeNotionSourceUnavailable):
        source.require_prepared_native_notion_source_v55(
            empty, root=tmp_path, report_ids=("report",)
        )
    with pytest.raises(source.NativeNotionSourceUnavailable):
        source.require_native_notion_preflight_v55(empty, client=None, destination=None)
    with pytest.raises(source.NativeNotionSourceUnavailable):
        source.consume_native_notion_dispatch_v55(
            empty, adapter=None, payload=None, claim=None
        )


def test_a_private_key_without_the_actual_issuing_context_is_not_a_factory():
    with pytest.raises(source.NativeNotionSourceUnavailable):
        source._replay_actual_reporting_factory_v55(
            object(), key=source.reports._SOURCE_FACTORY_KEY_V55
        )


def test_callback_object_is_rejected_before_any_attribute_is_called():
    class Callback:
        def __getattribute__(self, name):
            pytest.fail("caller object callback must not supply source authority")

    with pytest.raises(source.NativeNotionSourceUnavailable):
        source.require_native_notion_dispatch_v55(Callback(), adapter=None)


def test_metadata_shape_is_data_only_and_preserves_distinct_xid_requirement():
    now = datetime(2026, 11, 3, 1, tzinfo=UTC)
    data = ("writer", "writer", "none", 1, False, False, False, 1, "1", now)
    assert source._metadata(data) == data
    for offset, invalid in ((0, ""), (2, "writer"), (4, True), (7, 0), (8, "0")):
        changed = list(data)
        changed[offset] = invalid
        with pytest.raises(source.NativeNotionSourceUnavailable):
            source._metadata(tuple(changed))


def test_real_source_guard_rejects_a_replaced_db_reader(monkeypatch):
    monkeypatch.setattr(
        source.reports,
        "read_native_notion_projection_v55",
        lambda *args, **kwargs: None,
    )
    with pytest.raises(guard.NativeSourceClosureError):
        source._NATIVE_GUARD()
