"""Real source guard before new DATA helpers; no runtime/account/order calls."""

import pytest

from app.trade_qualification.native_continuation_guard_v1 import (
    NativeSourceClosureError,
)

_PREFIX_FOREIGN_CALLS = []


def _foreign(*_args, **_kwargs):
    _PREFIX_FOREIGN_CALLS.append("ran")
    raise RuntimeError("foreign DATA helper ran")


@pytest.mark.parametrize(
    "name",
    (
        "_bank_rows_v1",
        "_prepare_bank_prefix_v1",
        "_complete_prepared_bank_sha_v1",
    ),
)
@pytest.mark.parametrize("change", ("binding", "same_function_code"))
def test_genuine_guard_denies_changed_prefix_helper_before_callback(
    monkeypatch, name, change
):
    from app.okx_live import owned_qualification_v1 as qualification

    qualification._NATIVE_GUARD()
    _PREFIX_FOREIGN_CALLS.clear()
    source = qualification.source
    if change == "binding":
        monkeypatch.setattr(source, name, _foreign)
    else:
        original = getattr(source, name)
        monkeypatch.setattr(
            source, "_PREFIX_FOREIGN_CALLS", _PREFIX_FOREIGN_CALLS, raising=False
        )
        monkeypatch.setattr(original, "__code__", _foreign.__code__)
    with pytest.raises(NativeSourceClosureError):
        qualification._NATIVE_GUARD()
        getattr(source, name)(None)
    assert _PREFIX_FOREIGN_CALLS == []
