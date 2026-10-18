"""Replay a recorded V7 account source into a DENY-only factual claim.

The DB0043 witness contains caller-written ledger claims. This projection
derives only a few independently measurable fields from its original B1
current chain and compares them with those claims. It does not authenticate
the exchange session or issue an account revision or PortfolioRiskSnapshot.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from fractions import Fraction

from app.trade_qualification import account_capture_journal as journal
from app.trade_qualification import account_current_source_verifier as current
from app.trade_qualification import account_observation_index as observed
from app.trade_qualification import reservations

POLICY_BYTES = journal.canonical(
    {
        "version": "ctcc.recorded_account_revision_source_claim.v1",
        "source": "DB0043_witness_original_B1_V7_current_chain_and_EOF_replay",
        "comparison": "exact_settlement_equity_and_exchange_available_margin",
        "source_authenticity_verified": False,
        "account_revision_issued": False,
        "account_complete": False,
        "execution_authority": False,
    }
)
POLICY_SHA256 = journal.digest(POLICY_BYTES)


class RecordedAccountClaimError(ValueError):
    """Fixed denial code; source values and account identifiers stay private."""


@dataclass(frozen=True, slots=True, repr=False)
class RecordedAccountClaim:
    receipt_json: bytes

    @property
    def receipt_sha256(self) -> str:
        return journal.digest(self.receipt_json)

    @property
    def account_complete(self) -> bool:
        return False

    @property
    def source_authenticity_verified(self) -> bool:
        return False

    @property
    def account_revision_issued(self) -> bool:
        return False

    @property
    def execution_authority(self) -> bool:
        return False

    @property
    def admission(self) -> str:
        return "DENY"


def _same_exact_amount(source, claimed):
    if source is None:
        return None
    return Fraction(int(source["numerator"]), int(source["denominator"])) == Fraction(
        claimed
    )


def derive_recorded_current_claim(
    chain,
    *,
    reference,
    scope,
    witness_sha256,
    account_revision,
    claims_sha256,
    claims,
    validated_at,
) -> RecordedAccountClaim:
    """Recompute measured operands; never accept a caller-supplied balance.

    The repository first verifies the immutable witness and obtains every
    argument under the UID lock. This pure function also checks the internal
    pins so an accidental unbound call cannot yield a plausible receipt.
    Historical validation time is the source terminal's DB time, not a claim
    that the source is current when this function is invoked.
    """
    try:
        if (
            type(claims) is not reservations.AccountLedgerClaims
            or claims.scope != scope
            or reservations.digest(claims) != claims_sha256
            or type(account_revision) is not int
            or account_revision < 1
            or type(witness_sha256) is not str
            or re.fullmatch(r"[a-f0-9]{64}", witness_sha256) is None
            or observed.source_reference(chain) != reference
        ):
            raise RecordedAccountClaimError("recorded_claim_source_binding_invalid")
        verification = current.verify_current_account_sources(
            chain,
            reference=reference,
            scope=scope,
            validated_at=validated_at,
            expected_policy_sha256=current.V7_POLICY_SHA256,
        )
        source = json.loads(verification.receipt_json)
        identity = source["identity"]
        if (
            source["schema_version"] != "ctcc.current_account_source_observation.v6"
            or source["source_reference"] != observed.reference_document(reference)
            or identity["environment"] != scope.environment
            or identity["uid"] != scope.account_id
            or identity["settlement_currency"] != scope.settlement_currency
            or source["account_complete"] is not False
            or source["source_authenticity_verified"] is not False
            or source["execution_authority"] is not False
            or source["admission"] != "DENY"
            or set(source["inventory_row_counts"]) != set(current.V7_INVENTORY_STREAMS)
        ):
            raise RecordedAccountClaimError("recorded_claim_source_contract_invalid")
        balance = source["balance"]
        equity_matches = _same_exact_amount(balance["equity"], claims.account.equity)
        available_matches = _same_exact_amount(
            balance["available_equity"], claims.account.available_margin
        )
        blockers = set(source["blocking_reasons"])
        if equity_matches is None or available_matches is None:
            blockers.add("recorded_settlement_balance_unknown")
        if equity_matches is False or available_matches is False:
            blockers.add("caller_balance_diverges_from_recorded_source")
        blockers.update(
            {
                "caller_written_account_revision_not_source_issued",
                "credential_session_and_registration_site_unverified",
                "complete_portfolio_and_local_state_unavailable",
            }
        )
        receipt = journal.canonical(
            {
                "schema_version": "ctcc.recorded_account_revision_source_claim.v1",
                "policy_sha256": POLICY_SHA256,
                "witness_sha256": witness_sha256,
                "caller_account_revision": account_revision,
                "caller_claims_sha256": claims_sha256,
                "recorded_current_reference": observed.reference_document(reference),
                "recorded_current_verifier_sha256": verification.receipt_sha256,
                "source_identity": identity,
                "source_balance": balance,
                "source_inventory_row_counts": source["inventory_row_counts"],
                "caller_claims_comparison": {
                    "settlement_equity_matches": equity_matches,
                    "exchange_available_margin_matches": available_matches,
                },
                "blocking_reasons": sorted(blockers),
                "source_authenticity_verified": False,
                "account_revision_issued": False,
                "portfolio_snapshot": None,
                "account_complete": False,
                "flat_start_permission": False,
                "execution_authority": False,
                "admission": "DENY",
            }
        )
        if len(receipt) > 262144:
            raise RecordedAccountClaimError("recorded_claim_document_bound")
        return RecordedAccountClaim(receipt)
    except RecordedAccountClaimError:
        raise
    except Exception:  # noqa: BLE001 -- never disclose private source or SQL text
        raise RecordedAccountClaimError("recorded_claim_source_unavailable") from None
