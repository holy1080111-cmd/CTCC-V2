"""Original exchange OCO exit -> forensic DATA, never a local close or grant."""

from dataclasses import dataclass, field

from app.trade_evidence import execution_forensics, forensics
from app.trade_evidence import native_closed_forensics_v55 as shared


@dataclass(frozen=True, slots=True, repr=False)
class NativeProtectiveForensicsDataV58:
    receipt_json: bytes = field(repr=False)
    analysis: forensics.TradeForensicsResult = field(repr=False)
    execution_costs: execution_forensics.ExecutionForensicsReplay = field(repr=False)
    report_eligible: bool = field(default=False, init=False)
    execution_authority: bool = field(default=False, init=False)
    order_retry_authority: bool = field(default=False, init=False)


def reconstruct_native_protective_forensics_v58(target, original, final):
    """Replay the original OCO, child fills, all pages and final flat inventory.

    Funding effective accrual and unmeasured price paths remain unknown. Exact
    entry/exit prices already include spread/slippage; those diagnostic costs
    are never deducted twice. Durable SQL lineage and outbox eligibility must
    be checked independently by their owning repository.
    """
    return shared._reconstruct_closed_forensics_profile(
        target,
        original,
        None,
        None,
        final,
        profile=shared.ClosedForensicsProfileV1.ORIGINAL_OCO,
    )
