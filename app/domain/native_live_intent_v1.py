"""Exact Live intent DATA and conservative hold arithmetic, never qualification."""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.native_live_ledger_schema_v1 import SCHEMA
from app.domain.source_primitives import canonical, decode, sha

Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Uid = Annotated[str, Field(pattern=r"^[1-9][0-9]{0,39}$")]
Amount = Annotated[
    Decimal, Field(gt=0, max_digits=60, decimal_places=20, allow_inf_nan=False)
]


class LiveIntentDataV1(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    environment: Literal["live"] = "live"
    account_uid: Uid
    main_uid: Uid
    credential_session_sha256: Digest
    source_revision_sha256: Digest
    process_sha256: Digest
    policy_sha256: Digest
    arm_id: Digest
    expected_control_revision: int = Field(ge=1, lt=2**63)
    event_key: Digest
    candidate_sha256: Digest
    original_candidate_json: str = Field(min_length=1, max_length=32768)
    report_sha256: Digest
    evidence_sha256: Digest
    recheck_sha256: Digest
    instrument_id: Annotated[str, Field(pattern=r"^[A-Z0-9]{1,20}-USDT-SWAP$")]
    direction: Literal["long", "short"]
    entry: Amount
    stop_loss: Amount
    take_profit: Amount
    executable_price_bound: Amount
    contracts: Amount
    leverage: int = Field(ge=1, le=3)
    margin_mode: Literal["isolated"] = "isolated"
    position_side: Literal["net", "long", "short"]
    client_order_id: Annotated[str, Field(pattern=r"^CTCCL[A-Za-z0-9]{1,27}$")]
    protection_client_id: Annotated[str, Field(pattern=r"^CTCCA[A-Za-z0-9]{1,27}$")]
    risk_samples: tuple[Amount, ...] = Field(min_length=1, max_length=64)
    margin_amount: Amount
    notional_amount: Amount
    deadline: datetime

    @model_validator(mode="after")
    def fixed_data(self):
        raw = self.original_candidate_json.encode("ascii")
        original = decode(raw, maximum=32768)
        expected = {
            "environment": "live",
            "instrument_id": self.instrument_id,
            "direction": self.direction,
            "entry": format(self.entry, "f"),
            "stop_loss": format(self.stop_loss, "f"),
            "take_profit": format(self.take_profit, "f"),
            "event_key": self.event_key,
            "policy_sha256": self.policy_sha256,
        }
        original_expiry = (
            datetime.fromisoformat(original["expires_at"])
            if type(original) is dict and type(original.get("expires_at")) is str
            else None
        )
        if (
            self.deadline.tzinfo is not UTC
            or type(original) is not dict
            or canonical(original) != raw
            or any(original.get(name) != value for name, value in expected.items())
            or sha(raw) != self.candidate_sha256
            or type(original_expiry) is not datetime
            or original_expiry.tzinfo is not UTC
            or self.deadline > original_expiry
            or not (
                (self.stop_loss < self.entry < self.take_profit)
                if self.direction == "long"
                else (self.take_profit < self.entry < self.stop_loss)
            )
            or self.position_side not in ("net", self.direction)
        ):
            raise ValueError("native_live_intent_data_refused")
        return self


def checked_live_intent_data_v1(value):
    if type(value) is not LiveIntentDataV1:
        raise ValueError("native_live_intent_data_refused")
    return LiveIntentDataV1.model_validate(value.model_dump(mode="python"), strict=True)


def live_account_lock_key_v1(uid: str) -> int:
    from pydantic import TypeAdapter

    uid = TypeAdapter(Uid).validate_python(uid, strict=True)
    return int.from_bytes(
        bytes.fromhex(sha(("ctcc-live-ledger-v1:live:" + uid).encode("ascii")))[:8],
        "big",
        signed=True,
    )


def exact_live_intent_document_v1(value):
    value = checked_live_intent_data_v1(value)
    reservation_id = sha(
        canonical(
            ["ctcc-live-reservation-v1", "live", value.account_uid, value.event_key]
        )
    )
    request = {
        "instId": value.instrument_id,
        "tdMode": value.margin_mode,
        "side": "buy" if value.direction == "long" else "sell",
        "posSide": value.position_side,
        "ordType": "fok",
        "sz": format(value.contracts, "f"),
        "px": format(value.executable_price_bound, "f"),
        "clOrdId": value.client_order_id,
        "attachAlgoOrds": [
            {
                "attachAlgoClOrdId": value.protection_client_id,
                "tpTriggerPx": format(value.take_profit, "f"),
                "tpOrdPx": "-1",
                "tpTriggerPxType": "mark",
                "slTriggerPx": format(value.stop_loss, "f"),
                "slOrdPx": "-1",
                "slTriggerPxType": "mark",
            }
        ],
    }
    request_json = canonical(request).decode("ascii")
    document = value.model_dump(mode="json")
    document.update(
        schema=SCHEMA,
        reservation_id=reservation_id,
        request_json=request_json,
        request_sha256=sha(request_json.encode("ascii")),
        risk_amount=format(max(value.risk_samples), "f"),
        qualification_verified=False,
        execution_authority=False,
        admission="DENY",
    )
    raw = canonical(document)
    if len(raw) > 65536:
        raise ValueError("native_live_intent_data_bound")
    return reservation_id, raw, sha(raw)
