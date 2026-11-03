"""Versioned cross-capture position session rules; inert source arithmetic only.

Historical CURRENT records keep their own original session bindings. Stable
account identity may span fresh leases; the native position archive must match
the LAST original CURRENT session. The legacy V2 helper is unchanged.
"""

from app.domain.source_primitives import canonical, sha
from app.trade_qualification import account_capture_journal as journal
from app.trade_qualification import account_financial_operands_v2 as legacy
from app.trade_qualification.reservations import LedgerScope, checked_bootstrap

POLICY_BYTES = canonical(
    {
        "version": "ctcc.original_prefix.position_session.v3",
        "legacy_position_policy_sha256": legacy.POLICY_SHA256,
        "historical_sessions": "each_original_session_binding_preserved_and_verified_at_ingest",
        "stable_identity": "same_demo_UID_mainUID_mode_origin_region_and_settlement_currency",
        "position_archive_session": "last_original_CURRENT_only",
        "funding_tail_seed_HWM_and_native_owner": "unknown_not_issued",
        "execution_authority": False,
    }
)
POLICY_SHA256 = sha(POLICY_BYTES)
_ACCOUNT_KEYS = frozenset(
    {
        "config",
        "session_binding_sha256",
        "origin",
        "registration_region",
        "environment",
        "settlement_currency",
    }
)


class OriginalPrefixPositionSessionError(ValueError):
    """Fixed local errors only."""


def _deny(code="original_prefix_position_source_invalid"):
    raise OriginalPrefixPositionSessionError(code) from None


def original_account_observations_v3(packets, references, scope):
    """Build private original-account context, never a native owner/complete flag."""
    try:
        checked_bootstrap(scope, LedgerScope)
        if type(packets) is not list or type(references) is not list or not packets:
            _deny("original_prefix_account_context_required")
        original = legacy._native_account_observations(packets, references)
        result = []
        for packet, item in zip(packets, original, strict=True):
            if (
                packet.plan.environment != scope.environment
                or packet.plan.settlement_currency != scope.settlement_currency
            ):
                _deny("original_prefix_account_scope_changed")
            if item["config"].get("settleCcy") not in (
                None,
                "",
                scope.settlement_currency,
            ):
                _deny("original_prefix_account_currency_changed")
            result.append(
                {
                    **item,
                    "environment": packet.plan.environment,
                    "settlement_currency": packet.plan.settlement_currency,
                }
            )
        return result
    except OriginalPrefixPositionSessionError:
        raise
    except Exception:  # noqa: BLE001 -- source/config details stay private
        _deny()


def native_position_rows_for_original_prefix_v3(source, accounts, scope, checked_at):
    """Use each historical identity, and bind archive to the exact last session.

    This helper receives contexts only after the core has reopened original
    B1/query/packet/witness bytes. Its standalone output remains inert. It does
    not accept a source-authenticity flag, replace native acquisition, or loosen
    the old V2/public same-session contract.
    """
    try:
        checked_bootstrap(scope, LedgerScope)
        if (
            type(source) is not legacy.NativePositionSourceV2
            or type(source.plan) is not legacy.position_pages.PositionHistoryPlanV1
            or type(source.identity) is not legacy.positions._NativePositionIdentityV1
            or type(accounts) is not list
            or not accounts
            or scope.environment != "demo"
            or scope.settlement_currency != "USDT"
        ):
            _deny("original_prefix_account_context_required")
        source.plan.__post_init__()
        source.identity.__post_init__()
        expected = (
            scope.account_id,
            source.identity.main_uid,
            source.identity.account_level,
            source.identity.position_mode,
            source.plan.origin,
            source.plan.registration_region,
            scope.environment,
            scope.settlement_currency,
        )
        for account in accounts:
            if (
                type(account) is not dict
                or set(account) != _ACCOUNT_KEYS
                or type(account["config"]) is not dict
            ):
                _deny("original_prefix_account_context_required")
            journal._sha(account["session_binding_sha256"])
            config = account["config"]
            actual = (
                config.get("uid"),
                config.get("mainUid"),
                config.get("acctLv"),
                config.get("posMode"),
                account["origin"],
                account["registration_region"],
                account["environment"],
                account["settlement_currency"],
            )
            if any(type(item) is not str for item in actual) or actual != expected:
                _deny("original_prefix_account_identity_or_scope_changed")
            # Missing settlement config remains an unknown, not an inferred
            # zero or complete claim. An explicitly different currency denies.
            if config.get("settleCcy") not in (None, "", scope.settlement_currency):
                _deny("original_prefix_account_currency_changed")
        # The unchanged original V2 verifier validates exact archive bytes,
        # identity, latest session, origin/currency/mode and availability cutoff.
        return legacy._native_position_rows_from_accounts(
            source, [accounts[-1]], scope, checked_at
        )
    except OriginalPrefixPositionSessionError:
        raise
    except legacy.AccountFinancialOperandsError:
        raise
    except Exception:  # noqa: BLE001 -- no source/native archive details escape
        _deny()
