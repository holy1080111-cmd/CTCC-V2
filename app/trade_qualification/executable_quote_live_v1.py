"""Exact Live public quote diagnostic using unchanged source/time arithmetic."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from app.trade_qualification import executable_quote_v2 as shared
from app.trade_qualification.funding_observation_v2 import (
    FundingObservationError,
    FundingObservationV2,
)

_policy = shared.json.loads(shared.POLICY_BYTES)
_policy.update(
    version="ctcc.executable_quote_live_diagnostic_policy.v1", environments=["live"]
)
POLICY_BYTES = shared._canonical(_policy)
POLICY_SHA256 = shared._sha(POLICY_BYTES)
del _policy


@dataclass(frozen=True, slots=True)
class LiveExecutableQuoteV1:
    report_id: str
    instrument_id: str
    environment: Literal["live"]
    capture_started_at: datetime
    capture_completed_at: datetime
    ticker: shared.RestTickerObservationV2
    mark: shared.RestMarkObservationV2
    funding: FundingObservationV2
    schema_version: str = field(default="ctcc.executable_quote_live.v1", init=False)
    region: str = field(default="global", init=False)
    source_authenticity_verified: Literal[False] = field(default=False, init=False)
    execution_authority: Literal[False] = field(default=False, init=False)
    admission: Literal["DENY"] = field(default="DENY", init=False)


@dataclass(frozen=True, slots=True)
class LiveExecutableQuoteInspectionV1:
    receipt_json: bytes

    @property
    def receipt_sha256(self):
        return shared._sha(self.receipt_json)

    @property
    def execution_authority(self):
        return False


def inspect_live_executable_quote_v1(
    value: LiveExecutableQuoteV1,
    *,
    current_time: datetime,
    expected_policy_sha256: str = POLICY_SHA256,
) -> LiveExecutableQuoteInspectionV1:
    """No account, source ownership, Arm, reservation or submit authority."""
    now, document = None, None
    try:
        now = shared._utc(current_time)
        now, document = shared._inspect_profile(
            value,
            now,
            expected_policy_sha256,
            quote_type=LiveExecutableQuoteV1,
            quote_schema="ctcc.executable_quote_live.v1",
            policy_sha256=POLICY_SHA256,
            environments=("live",),
        )
        code = "profile_checks_satisfied"
    except shared.ExecutableQuoteV2Error as exc:
        code = str(exc)
    except FundingObservationError:
        code = "funding_observation_invalid"
    except (AttributeError, TypeError, ValueError, OverflowError):
        code = "quote_observation_invalid"
    return LiveExecutableQuoteInspectionV1(
        shared._canonical(
            {
                "schema_version": "ctcc.executable_quote_live_inspection.v1",
                "policy_sha256": POLICY_SHA256,
                "environment": "live",
                "profile_satisfied": document is not None,
                "code": code,
                "validated_at": None if now is None else now.isoformat(),
                "quote_document": document,
                "quote_sha256": None
                if document is None
                else shared._sha(shared._canonical(document)),
                "source_authenticity_verified": False,
                "point_in_time_verified": False,
                "account_complete": False,
                "execution_authority": False,
                "admission": "DENY",
            }
        )
    )
