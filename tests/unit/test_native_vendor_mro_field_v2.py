"""Regression for dataclass lookup inheriting type.mro as a fake default."""

from dataclasses import MISSING, fields

from app.exchange.okx import native_demo_vendor_integrity_v2 as vendor


def test_mro_is_required_data_not_inherited_type_mro_default():
    field = next(item for item in fields(vendor._FunctionPinV2) if item.name == "mro")
    assert field.default is MISSING and field.default_factory is MISSING
    actual = vendor.admit_native_demo_vendors_v2()
    vendor.require_native_demo_vendors_v2(actual)
    assert len(actual.bindings) == len(actual._functions) == 26
    assert all(
        pin.mro is type.__getattribute__(pin.lookup, "__mro__")
        for pin in actual._functions
    )
