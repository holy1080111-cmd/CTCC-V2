"""Exact bounded native input/output checks before foreign callbacks or replay."""

from datetime import UTC, datetime
from decimal import Decimal
from gc import get_referents
from types import MappingProxyType

from app.domain.source_primitives import canonical, decode, sha
from app.trade_qualification import account_capture as account
from app.trade_qualification import public_market_collector_v2 as public
from app.trade_qualification.full_public_numeric_v2_models import (
    FullPublicEconomicsDiagnosticV2,
    FullPublicLocationDiagnosticV2,
)

MAX_RECORD_BYTES = 8 * 1024 * 1024
_CLOSED = (
    "execution_authority",
    "qualification_performed",
    "execution_recheck_performed",
    "native_clock_verified",
    "original_source_verified",
    "account_complete",
    "atomic_risk_reserved",
    "metadata_current_owned",
)


class FullPublicNumericV2Error(ValueError):
    """Static redacted denial only."""


def deny(code):
    raise FullPublicNumericV2Error(code)


def exact_utc(value):
    if type(value) is not datetime or value.tzinfo is not UTC:
        deny("numeric_v2_exact_utc_required")
    return value


def pin(value):
    if (
        type(value) is not str
        or len(value) != 64
        or any(char not in "0123456789abcdef" for char in value)
    ):
        deny("numeric_v2_exact_pin_required")
    return value


def native_mapping(value):
    if type(value) is MappingProxyType:
        referents = get_referents(value)
        if len(referents) != 1 or type(referents[0]) is not dict:
            deny("numeric_v2_native_mapping_required")
        return referents[0]
    if type(value) is not dict:
        deny("numeric_v2_native_mapping_required")
    return value


def primitive_tree(value, *, depth=0, budget=None, account_models=False):
    if budget is None:
        budget = [250000, account.MAX_PACKET_BYTES]
    budget[0] -= 1
    if depth > 24 or budget[0] < 0:
        deny("numeric_v2_tree_budget")
    kind = type(value)
    if value is None or kind is bool:
        return
    if kind is int:
        if abs(value) > 10**40:
            deny("numeric_v2_integer_bound")
        return
    if kind is Decimal:
        if (
            not value.is_finite()
            or len(value.as_tuple().digits) > 128
            or abs(value.as_tuple().exponent) > 128
            or len(str(value)) > 128
        ):
            deny("numeric_v2_decimal_bound")
        return
    if kind is datetime:
        exact_utc(value)
        return
    if kind is str or kind is bytes:
        if len(value) > MAX_RECORD_BYTES:
            deny("numeric_v2_scalar_bytes_bound")
        size = len(value.encode("utf-8")) if kind is str else len(value)
        budget[1] -= size
        if size > MAX_RECORD_BYTES or budget[1] < 0:
            deny("numeric_v2_scalar_bytes_bound")
        return
    if kind is dict or kind is MappingProxyType:
        fields = native_mapping(value)
        if len(fields) > 8192:
            deny("numeric_v2_mapping_bound")
        for key, item in fields.items():
            if type(key) is not str or len(key) > 512:
                deny("numeric_v2_native_key_required")
            primitive_tree(
                item, depth=depth + 1, budget=budget, account_models=account_models
            )
        return
    if kind is list or kind is tuple:
        if len(value) > 8192:
            deny("numeric_v2_sequence_bound")
        for item in value:
            primitive_tree(
                item, depth=depth + 1, budget=budget, account_models=account_models
            )
        return
    if account_models and any(kind is model for model in account._MODELS):
        fields = native_mapping(object.__getattribute__(value, "__dict__"))
        if len(fields) != len(kind.model_fields) or any(
            type(key) is not str or key not in kind.model_fields for key in fields
        ):
            deny("numeric_v2_account_exact_fields_required")
        for name in ("__pydantic_extra__", "__pydantic_private__"):
            extra = object.__getattribute__(value, name)
            if extra is not None and len(native_mapping(extra)) != 0:
                deny("numeric_v2_account_hidden_fields")
        selected = object.__getattribute__(value, "__pydantic_fields_set__")
        if (
            type(selected) is not set
            or len(selected) > len(kind.model_fields)
            or any(
                type(key) is not str or key not in kind.model_fields for key in selected
            )
        ):
            deny("numeric_v2_account_field_set_invalid")
        for item in fields.values():
            primitive_tree(item, depth=depth + 1, budget=budget, account_models=True)
        return
    deny("numeric_v2_native_primitive_required")


def raw_inputs(public_packet, account_packet):
    if type(public_packet) is not public.CollectedPublicMarketV2:
        deny("numeric_v2_exact_raw_public_required")
    raw = object.__getattribute__(public_packet, "packet_json")
    if type(raw) is not bytes or not 1 <= len(raw) <= public.MAX_PACKET_BYTES:
        deny("numeric_v2_raw_public_bytes_bound")
    if type(account_packet) is not account.DemoAccountPacket:
        deny("numeric_v2_exact_raw_account_required")
    primitive_tree(account_packet, account_models=True)


def record_document(value, expected):
    if (
        expected is not FullPublicLocationDiagnosticV2
        and expected is not FullPublicEconomicsDiagnosticV2
    ) or type(value) is not expected:
        deny("numeric_v2_exact_result_required")
    raw = object.__getattribute__(value, "receipt_json")
    if type(raw) is not bytes or not 1 <= len(raw) <= MAX_RECORD_BYTES:
        deny("numeric_v2_result_bytes_bound")
    document = decode(raw, MAX_RECORD_BYTES)
    primitive_tree(document, budget=[250000, MAX_RECORD_BYTES])
    if canonical(document) != raw:
        deny("numeric_v2_result_not_canonical")
    fields = {
        "schema_version",
        "kind",
        "profile_sha256",
        "bindings",
        "precursor",
        "g1",
        "location",
        "selection",
        "economics",
        "math_checks_passed",
        "action",
        "code",
        "admission",
        "unknown",
        *_CLOSED,
    }
    schema = (
        "ctcc.full_public_location_diagnostic.v2"
        if expected is FullPublicLocationDiagnosticV2
        else "ctcc.full_public_economics_diagnostic.v2"
    )
    if (
        type(document) is not dict
        or set(document) != fields
        or document["schema_version"] != schema
        or document["admission"] != "DENY"
    ):
        deny("numeric_v2_result_fields_invalid")
    if any(
        type(document[name]) is not bool or document[name] is not False
        for name in _CLOSED
    ):
        deny("numeric_v2_result_cannot_grant_authority")
    if type(document["math_checks_passed"]) is not bool:
        deny("numeric_v2_result_math_flag_invalid")
    return document, sha(raw)


def encoded_record(document, expected):
    primitive_tree(document, budget=[250000, MAX_RECORD_BYTES])
    value = expected(canonical(document))
    record_document(value, expected)
    return value
