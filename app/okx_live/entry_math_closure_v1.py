"""Fixed source assembly for the pure computations retained by an ENTRY seal.

This roster is source code, not a runtime configuration or an observed catalog.
Import/guard installation occurs before an operator session can open.
"""

from dataclasses import fields, is_dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from fractions import Fraction
from importlib import import_module
from types import MappingProxyType

from pydantic import BaseModel

_MODULE_NAMES = (
    "app.analysis.service",
    "app.analysis.mathematical_core",
    "app.domain.analysis",
    "app.domain.market",
    "app.indicators.core",
    "app.indicators.causal_state",
    "app.indicators.causal_trend",
    "app.indicators.conformal_return",
    "app.market.quality.candles",
    "app.regime.classifier",
    "app.structure.engine",
    "app.strategies.base",
    "app.strategies.conditions",
    "app.strategies.regime",
    "app.strategies.mathematical_confirmation",
    "app.strategies.structural_protection",
    "app.trade_qualification.continuation",
    "app.trade_qualification.current_conditions",
    "app.trade_qualification.current_economics_v2",
    "app.trade_qualification.data",
    "app.trade_qualification.data_v2",
    "app.trade_qualification.economics",
    "app.trade_qualification.engine",
    "app.trade_qualification.event_models",
    "app.trade_qualification.events",
    "app.trade_qualification.executable_quote_live_v1",
    "app.trade_qualification.executable_quote_v2",
    "app.trade_qualification.fixed_original_event_path_v1",
    "app.trade_qualification.fixed_protection",
    "app.trade_qualification.funding_observation_v2",
    "app.trade_qualification.high_vol_momentum_qualification_v1",
    "app.trade_qualification.history_prefix",
    "app.trade_qualification.location",
    "app.trade_qualification.models",
    "app.trade_qualification.native_original_bracket_policy_v1",
    "app.trade_qualification.original_candidate_precursor_v2",
    "app.trade_qualification.owned_current_original_prefix_v1",
    "app.trade_qualification.portfolio",
    "app.trade_qualification.range_policy",
    "app.trade_qualification.regime_admission",
    "app.trade_qualification.reversal_policy",
    "app.trade_qualification.service",
    "app.trade_qualification.sweep_contract",
    "app.trade_qualification.sweep_history_permission",
    "app.trade_qualification.timing",
    "app.okx_live.finite_portfolio_v1",
)


def original_entry_math_modules_v1():
    return tuple(import_module(name) for name in _MODULE_NAMES)


def _data_constant(value, depth=0, *, budget=None):
    """Only literal calculation operands; never an owner registry/callback."""
    if budget is None:
        budget = [8192]
    budget[0] -= 1
    if depth > 24 or budget[0] < 0:
        return False
    kind = type(value)
    if (
        value is None
        or kind in (str, bool, int, bytes, Decimal, Fraction, float, datetime)
        or type(kind) is type(Enum)
    ):
        return True
    if kind in (tuple, list, set, frozenset):
        return len(value) <= 8192 and all(
            _data_constant(v, depth + 1, budget=budget) for v in value
        )
    if kind in (dict, MappingProxyType):
        if kind is MappingProxyType:
            from app.okx_live.entry_input_dag_v2 import _mapping

            value = _mapping(value)
        return len(value) <= 8192 and all(
            _data_constant(k, depth + 1, budget=budget)
            and _data_constant(v, depth + 1, budget=budget)
            for k, v in value.items()
        )
    if type(kind) is type(BaseModel) and BaseModel in type.__getattribute__(
        kind, "__mro__"
    ):
        return type.__getattribute__(kind, "__module__").startswith("app.")
    if type(kind) is type and is_dataclass(kind):
        return type.__getattribute__(kind, "__module__").startswith("app.") and all(
            _data_constant(
                object.__getattribute__(value, f.name), depth + 1, budget=budget
            )
            for f in fields(value)
        )
    return False


def original_entry_math_data_v1():
    """Observe mutable constant contents again, in addition to source code."""
    return {
        module.__name__: {
            name: value
            for name, value in vars(module).items()
            if name.lstrip("_").isupper() and _data_constant(value)
        }
        for module in original_entry_math_modules_v1()
    }
