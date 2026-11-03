"""Fixed inert wire views shared by the native configuration actor and Root.

Only genuine private registries mint/consume operational capabilities. These
constructible DATA classes and their false flags grant no authority.
"""

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True, slots=True, repr=False)
class NativeLeverageConfigurationTransportContextV1:
    client: object = field(repr=False)
    scope: object = field(repr=False)
    session_factory: object = field(repr=False)
    control_owner: object = field(repr=False)
    origin: str = field(repr=False)
    credential_session_sha256: str = field(repr=False)
    original_expires_at: datetime
    monotonic_deadline_ns: int
    read_session: object = field(repr=False)
    configuration_intent_id: str = field(repr=False)


@dataclass(frozen=True, slots=True, repr=False)
class NativeLeverageConfigurationTransportViewV1:
    binding: object = field(repr=False)
    context: NativeLeverageConfigurationTransportContextV1 = field(repr=False)
    intent_id: str = field(repr=False)
    attempt_id: str = field(repr=False)
    request_json: bytes = field(repr=False)
    request_sha256: str
    intent_row_json: bytes = field(repr=False)
    attempt_row_json: bytes = field(repr=False)
    source_receipt_json: bytes = field(repr=False)
    plan_receipt_json: bytes = field(repr=False)
    expected_scope_json: bytes = field(repr=False)
    original_source_generation: object = field(repr=False)


@dataclass(frozen=True, slots=True, repr=False)
class NativeLeverageConfigurationLockViewV1:
    binding: object = field(repr=False)
    context: NativeLeverageConfigurationTransportContextV1 = field(repr=False)
    session: object = field(repr=False)
    session_transaction: object = field(repr=False)
    connection: object = field(repr=False)
    root_transaction: object = field(repr=False)
    database_metadata: tuple = field(repr=False)
    control_state: object = field(repr=False)
    control_event_sha256: str = field(repr=False)
    scope_json: bytes = field(repr=False)
    intent_row_json: bytes = field(repr=False)
    attempt_row_json: bytes = field(repr=False)
    local_checkpoint_json: bytes = field(repr=False)


@dataclass(frozen=True, slots=True, repr=False)
class ObservedNativeLeverageConfigurationV1:
    """Root-minted DATA identity, not an order/fill or configuration permit."""

    http_status: int | None
    exchange_code: str | None
    response_sha256: str | None
    execution_authority: bool = field(default=False, init=False)
    order_retry_authority: bool = field(default=False, init=False)


@dataclass(frozen=True, slots=True, repr=False)
class NativeLeverageConfigurationResponseViewV1:
    binding: object = field(repr=False)
    transport_view: NativeLeverageConfigurationTransportViewV1 = field(repr=False)
    response_handle: object = field(repr=False)
    observed: ObservedNativeLeverageConfigurationV1 = field(repr=False)
    received_journal: object = field(repr=False)
    transport_state: object = field(repr=False)


@dataclass(frozen=True, slots=True, repr=False)
class NativeLeverageConfigurationClosedViewV1:
    binding: object = field(repr=False)
    response_view: NativeLeverageConfigurationResponseViewV1 = field(repr=False)
    socket_sdk_observation: tuple = field(repr=False)
    closed_handle: object = field(repr=False)


@dataclass(frozen=True, slots=True, repr=False)
class NativeLeverageConfigurationResultV1:
    intent_id: str = field(repr=False)
    outcome_id: str = field(repr=False)
    receipt_sha256: str
    classification: str
    execution_authority: bool = field(default=False, init=False)
    order_retry_authority: bool = field(default=False, init=False)


@dataclass(frozen=True, slots=True, repr=False)
class NativeLeverageConfigurationCleanupViewV1:
    binding: object = field(repr=False)
    closed_handle: object = field(repr=False)
    result_cap: object = field(repr=False)
    result_view: object = field(repr=False)
    source_completion: object = field(repr=False)
    source_completion_view: object = field(repr=False)
    result_receipt_json: bytes = field(repr=False)
    source_cleanup_receipt_json: bytes = field(repr=False)
    original_source_generation: object = field(repr=False)
    transport_view: NativeLeverageConfigurationTransportViewV1 = field(repr=False)


@dataclass(frozen=True, slots=True, repr=False)
class NativeLeverageConfigurationRetirementViewV1:
    binding: object = field(repr=False)
    closed_handle: object = field(repr=False)
    cleanup_cap: object = field(repr=False)
    source_retirement: object = field(repr=False)
    result_receipt_json: bytes = field(repr=False)
    source_retirement_receipt_json: bytes = field(repr=False)
