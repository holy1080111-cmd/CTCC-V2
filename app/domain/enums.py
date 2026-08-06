from enum import StrEnum


class Side(StrEnum):
    LONG = "long"
    SHORT = "short"


class Direction(StrEnum):
    LONG = "long"
    SHORT = "short"
    NEUTRAL = "neutral"


class Decision(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"


class Broker(StrEnum):
    PAPER = "paper"
    OKX_DEMO = "okx_demo"
    OKX_LIVE = "okx_live"


class OrderType(StrEnum):
    MARKET = "market"
    LIMIT = "limit"
    STOP_MARKET = "stop_market"
    TAKE_PROFIT_MARKET = "take_profit_market"


class OrderPurpose(StrEnum):
    ENTRY = "entry"
    STOP_LOSS = "stop_loss"
    TAKE_PROFIT = "take_profit"
    SCALE_OUT = "scale_out"
    CLOSE = "close"


class OrderStatus(StrEnum):
    CREATED = "created"
    SUBMITTED = "submitted"
    ACCEPTED = "accepted"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCEL_PENDING = "cancel_pending"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    EXPIRED = "expired"


class PositionStatus(StrEnum):
    OPENING = "opening"
    OPEN = "open"
    PROTECTED = "protected"
    CLOSING = "closing"
    CLOSED = "closed"
    ERROR = "error"


class LifecycleState(StrEnum):
    CANDIDATE = "candidate"
    RISK_APPROVED = "risk_approved"
    SUBMITTED = "submitted"
    ACCEPTED = "accepted"
    PARTIAL_FILLED = "partial_filled"
    FILLED = "filled"
    PROTECTED = "protected"
    CLOSING = "closing"
    CLOSED = "closed"
    ARCHIVED = "archived"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    FAILED = "failed"


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"
