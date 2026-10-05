"""Reject proxy-wrapped caller code before original-source preflight can run it."""

from collections.abc import Mapping
from types import MappingProxyType

import pytest

from app.trade_qualification.one_shot import OneShotInputError, _guard_original


class ForeignMapping(Mapping):
    def __init__(self, calls):
        self.calls = calls

    def __len__(self):
        self.calls.append("len")
        return 1

    def __iter__(self):
        self.calls.append("iter")
        return iter(("passed",))

    def __getitem__(self, key):
        self.calls.append("getitem")
        return True


@pytest.mark.parametrize("container", ("top", "list", "tuple", "dict", "proxy-proxy"))
def test_foreign_mapping_proxy_cannot_run_callbacks(container):
    calls = []
    proxy = MappingProxyType(ForeignMapping(calls))
    value = {
        "top": proxy,
        "list": [proxy],
        "tuple": (proxy,),
        "dict": {"gate": proxy},
        "proxy-proxy": MappingProxyType(proxy),
    }[container]
    with pytest.raises(OneShotInputError, match="^one_shot_preflight_invalid$"):
        _guard_original(value)
    assert calls == []


def test_proxy_wrapped_dict_subclass_is_not_exact_source():
    calls = []

    class ForeignDict(dict):
        def __len__(self):
            calls.append("len")
            return super().__len__()

        def __iter__(self):
            calls.append("iter")
            return super().__iter__()

        def items(self):
            calls.append("items")
            return super().items()

    with pytest.raises(OneShotInputError, match="^one_shot_preflight_invalid$"):
        _guard_original(MappingProxyType(ForeignDict(passed=True)))
    assert calls == []


@pytest.mark.parametrize(
    "value", (MappingProxyType({}), MappingProxyType({"observed": (True, None, 1)}))
)
def test_exact_native_dict_proxy_keeps_original_acceptance(value):
    assert _guard_original(value) is None
