"""Owned Demo account capture -> DB revision check -> recorded materialization.

This is a fail-closed runtime producer, not a complete-account or order issuer.
Only this invocation's signed, verified-TLS responses may receive that transport
classification. Replayed packets and MockTransport never do. Registration and
bootstrap artifact integrity is checked, but hashes are not source authentication.
No arm, reconciliation write, reservation, intent or exchange write occurs here.
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import ConfigDict, Field, field_validator, model_validator

from app.database.repositories.qualification_ledger import (
    LedgerBootstrapCheckpoint,
    LedgerCaptureCheckpoint,
    QualificationLedgerRepository,
)
from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_collector as collector
from app.trade_qualification import account_materializer as mapping
from app.trade_qualification import account_metadata as metadata
from app.trade_qualification import reservations
from app.trade_qualification.models import QualificationModel
from app.trade_qualification.portfolio import ObservedSource

BOOTSTRAP_KINDS = (
    "registration",
    "history_seed",
    "history_retention",
    "history_ingestion_watermark",
    "continuous_peak_window",
    "funding_accrual",
    "account_product_scope",
    "instrument_coverage",
)
BootstrapKind = Literal[
    "registration",
    "history_seed",
    "history_retention",
    "history_ingestion_watermark",
    "continuous_peak_window",
    "funding_accrual",
    "account_product_scope",
    "instrument_coverage",
]
MAX_BOOTSTRAP_BYTES = 4 * 1024 * 1024
_ERRORS = frozenset(
    {
        "account_runtime_invalid",
        "account_session_already_used",
        "owned_qualification_repository_required",
        "materialization_inputs_pin_mismatch",
        "caller_ledger_evidence_forbidden",
        "account_scope_mismatch",
        "runtime_clock_invalid",
        "unresolved_ledger_holds",
        "owned_capture_required",
        "ledger_revision_changed_during_capture",
        "owned_capture_provenance_invalid",
        "owned_ledger_checkpoint_required",
        "ledger_checkpoint_invalid",
        "bootstrap_scope_or_clock_mismatch",
        "registration_evidence_pin_mismatch",
        "bootstrap_history_coverage_incomplete",
        "bootstrap_invalid",
        "captured_metadata_invalid",
        "bootstrap_checkpoint_invalid",
    }
)


class AccountRuntimeError(ValueError):
    """Static local code only; no response, SQL, credential or UID in messages."""


class BootstrapArtifact(QualificationModel):
    """Immutable requirement artifact, with bytes retained and externally pinned.

    Coverage and receipt are recorded claims pending a source-specific verifier.
    Supplying every kind cannot establish continuous history, peak or product
    coverage. In particular bill generation time is never funding accrual time.
    """

    model_config = ConfigDict(
        str_strip_whitespace=False, ser_json_bytes="hex", val_json_bytes="hex"
    )
    kind: BootstrapKind
    raw_bytes: bytes = Field(min_length=1, max_length=262144)
    artifact_sha256: capture.Digest
    source_receipt_sha256: capture.Digest
    coverage_start: datetime
    coverage_end: datetime
    measured_receipt_at: datetime

    _times = field_validator("coverage_start", "coverage_end", "measured_receipt_at")(
        capture._utc
    )

    @model_validator(mode="after")
    def order_and_hash(self):
        if not self.coverage_start <= self.coverage_end <= self.measured_receipt_at:
            raise ValueError("bootstrap_clock_order")
        if hashlib.sha256(self.raw_bytes).hexdigest() != self.artifact_sha256:
            raise ValueError("bootstrap_artifact_hash_mismatch")
        return self


class AccountBootstrapEvidence(QualificationModel):
    """Sealed manifest of required proof artifacts, not proof of their truth."""

    model_config = ConfigDict(
        str_strip_whitespace=False, ser_json_bytes="hex", val_json_bytes="hex"
    )
    schema_version: Literal["ctcc.account_bootstrap_requirements.v1"] = (
        "ctcc.account_bootstrap_requirements.v1"
    )
    environment: Literal["demo"] = "demo"
    account_id: capture.Identifier
    main_uid: capture.Identifier
    settlement_currency: capture.Currency
    registration_region: Literal["global", "us_au", "eea"]
    instrument_ids: tuple[capture.Name, ...] = Field(min_length=1, max_length=128)
    sealed_at: datetime
    artifacts: tuple[BootstrapArtifact, ...] = Field(max_length=8)

    _time = field_validator("sealed_at")(capture._utc)

    @model_validator(mode="after")
    def uniqueness(self):
        if len(set(self.instrument_ids)) != len(self.instrument_ids) or len(
            {item.kind for item in self.artifacts}
        ) != len(self.artifacts):
            raise ValueError("bootstrap_duplicate_identity")
        if any(item.measured_receipt_at > self.sealed_at for item in self.artifacts):
            raise ValueError("bootstrap_seal_clock_order")
        return self


@dataclass(frozen=True, slots=True, repr=False)
class VerifiedBootstrapManifest:
    """Integrity-checked artifacts; source provenance remains unverified."""

    evidence: AccountBootstrapEvidence
    sha256: str
    missing_kinds: tuple[str, ...]


def _bootstrap_plain(value, depth=0):
    if depth > 8:
        raise AccountRuntimeError("bootstrap_invalid")
    kind = type(value)
    if kind is AccountBootstrapEvidence or kind is BootstrapArtifact:
        fields = object.__getattribute__(value, "__dict__")
        if (
            type(fields) is not dict
            or any(type(key) is not str for key in fields)
            or set(fields) != set(kind.model_fields)
            or object.__getattribute__(value, "__pydantic_extra__") is not None
            or object.__getattribute__(value, "__pydantic_private__") is not None
        ):
            raise AccountRuntimeError("bootstrap_invalid")
        return {key: _bootstrap_plain(item, depth + 1) for key, item in fields.items()}
    if kind is tuple and len(value) <= 128:
        return tuple(_bootstrap_plain(item, depth + 1) for item in value)
    if kind is str and len(value) <= 128 or kind is bytes and len(value) <= 262144:
        return value
    if kind is datetime:
        return capture._utc(value)
    raise AccountRuntimeError("bootstrap_invalid")


def freeze_account_bootstrap(evidence: AccountBootstrapEvidence) -> bytes:
    """Canonical bytes for no-clobber storage; writing/publishing is separate."""
    try:
        if type(evidence) is not AccountBootstrapEvidence:
            raise ValueError
        copied = AccountBootstrapEvidence.model_validate(
            _bootstrap_plain(evidence), strict=True
        )
        return capture._canonical(copied.model_dump(mode="json")).encode("utf-8")
    except Exception:  # noqa: BLE001, S110 -- malformed claims cannot leak raw content
        pass
    raise AccountRuntimeError("bootstrap_invalid")


def verify_account_bootstrap(
    payload: bytes, *, expected_sha256: str
) -> VerifiedBootstrapManifest:
    """Verify canonical encoding, duplicates, sizes, hashes, clocks and identity.

    The caller's external expected hash is required. No claim of continuous
    history, source authentication, or execution eligibility is produced.
    """
    try:
        if (
            type(payload) is not bytes
            or not 1 <= len(payload) <= MAX_BOOTSTRAP_BYTES
            or type(expected_sha256) is not str
            or hashlib.sha256(payload).hexdigest() != expected_sha256
        ):
            raise ValueError
        _, canonical = capture._decode_json(
            payload, limit=MAX_BOOTSTRAP_BYTES, wire=False
        )
        if canonical.encode("utf-8") != payload:
            raise ValueError
        evidence = AccountBootstrapEvidence.model_validate_json(payload, strict=True)
        if freeze_account_bootstrap(evidence) != payload:
            raise ValueError
        return VerifiedBootstrapManifest(
            evidence=evidence,
            sha256=expected_sha256,
            missing_kinds=tuple(
                kind
                for kind in BOOTSTRAP_KINDS
                if kind not in {item.kind for item in evidence.artifacts}
            ),
        )
    except Exception:  # noqa: BLE001, S110 -- redact source/parser details
        pass
    raise AccountRuntimeError("bootstrap_invalid")


@dataclass(frozen=True, slots=True, repr=False)
class DemoAccountRuntimeResult:
    """Private account audit result. Never accepted as a submission capability."""

    packet: capture.DemoAccountPacket
    materialization: mapping.AccountMaterializationResult
    materialization_inputs: mapping.AccountMaterializationInputs
    instrument_metadata: metadata.CapturedInstrumentMetadata
    ledger_checkpoint: LedgerCaptureCheckpoint
    completed_at: datetime
    transport_provenance: Literal["owned_signed_verified_tls", "synthetic_transport"]
    bootstrap_sha256: str | None
    blocking_reasons: tuple[str, ...]
    receipt_json: bytes
    receipt_sha256: str

    @property
    def admission(self) -> Literal["DENY"]:
        return "DENY"

    @property
    def execution_authority(self) -> Literal[False]:
        return False


class ControlledDemoAccountSession:
    """Own a copied credential handle and pinned region/UID for one capture.

    One use even on error. A subsequent attempt needs a newly constructed session
    and fresh plan, clock, DB checkpoints and barrier. No external packet, HTTP
    client, signer, URL, source-auth boolean, ledger claims or readback callback is
    accepted. The repository remains the existing DB0017 journal.
    """

    __slots__ = ("_credentials", "_pin", "_plan", "_used")

    def __init__(self, *, credentials, plan, expected_plan_sha256):
        try:
            if type(plan) not in {
                capture.RegionalDemoAccountCapturePlan,
                capture.AllProductDemoAccountCapturePlan,
            }:
                raise AccountRuntimeError("explicit_registration_region_required")
            selected = capture._checked_plan(plan, expected_plan_sha256)
            owned = collector.DemoAccountCredentials(
                *collector._credential_values(credentials)
            )
            if owned.session_binding_id != selected.session_binding_id:
                raise AccountRuntimeError("credential_session_mismatch")
            self._credentials = owned
            self._plan = selected
            self._pin = expected_plan_sha256
            self._used = False
        except AccountRuntimeError:
            raise
        except Exception:  # noqa: BLE001 -- credentials/validation must stay private
            raise AccountRuntimeError("account_session_invalid") from None

    def __repr__(self):
        return "<ControlledDemoAccountSession redacted>"

    async def collect_bootstrap(self, *, repository, clock, barrier_completed_at):
        """Acquire authenticatable raw evidence before any complete claims exist.

        This uses the same one-use session and native collector as normal capture.
        It records unknown local initialization explicitly, accepts no portfolio
        claims and does not publish an account revision, Arm or order authority.
        """
        from app.trade_qualification.account_bootstrap_runtime import collect_bootstrap

        return await collect_bootstrap(
            self,
            repository=repository,
            clock=clock,
            barrier_completed_at=barrier_completed_at,
        )

    async def collect_and_materialize(
        self,
        *,
        repository: QualificationLedgerRepository,
        clock,
        barrier_completed_at: datetime,
        inputs: mapping.AccountMaterializationInputs,
        expected_inputs_sha256: str,
        bootstrap_payload: bytes | None = None,
        expected_bootstrap_sha256: str | None = None,
    ) -> DemoAccountRuntimeResult:
        error = "account_runtime_invalid"
        try:
            return await self._collect(
                repository=repository,
                clock=clock,
                barrier_completed_at=barrier_completed_at,
                inputs=inputs,
                expected_inputs_sha256=expected_inputs_sha256,
                bootstrap_payload=bootstrap_payload,
                expected_bootstrap_sha256=expected_bootstrap_sha256,
            )
        except asyncio.CancelledError:
            raise
        except AccountRuntimeError as exc:
            if (
                type(exc) is AccountRuntimeError
                and len(exc.args) == 1
                and type(exc.args[0]) is str
                and exc.args[0] in _ERRORS
            ):
                error = exc.args[0]
        except Exception:  # noqa: BLE001, S110 -- no SQL, transport or credential leaks
            pass
        raise AccountRuntimeError(error)

    async def _collect(
        self,
        *,
        repository,
        clock,
        barrier_completed_at,
        inputs,
        expected_inputs_sha256,
        bootstrap_payload,
        expected_bootstrap_sha256,
    ):
        if self._used:
            raise AccountRuntimeError("account_session_already_used")
        self._used = True
        if type(repository) is not QualificationLedgerRepository:
            raise AccountRuntimeError("owned_qualification_repository_required")
        selected = capture._checked_plan(self._plan, self._pin)
        inputs = mapping.copy_materialization_inputs(inputs)
        if (
            type(expected_inputs_sha256) is not str
            or mapping.materialization_inputs_sha256(inputs) != expected_inputs_sha256
        ):
            raise AccountRuntimeError("materialization_inputs_pin_mismatch")
        if inputs.ledger is not None:
            raise AccountRuntimeError("caller_ledger_evidence_forbidden")
        if (inputs.account_id, inputs.settlement_currency) != (
            selected.expected_uid,
            selected.settlement_currency,
        ):
            raise AccountRuntimeError("account_scope_mismatch")
        barrier = capture._utc(barrier_completed_at)
        start = collector._read_clock(clock)
        if start <= barrier or start < selected.created_at:
            raise AccountRuntimeError("runtime_clock_invalid")
        bootstrap = None
        if bootstrap_payload is not None or expected_bootstrap_sha256 is not None:
            bootstrap = verify_account_bootstrap(
                bootstrap_payload, expected_sha256=expected_bootstrap_sha256
            )
            self._check_bootstrap(bootstrap, start)
        scope = reservations.LedgerScope(
            account_id=selected.expected_uid,
            settlement_currency=selected.settlement_currency,
        )
        before = await repository.read_capture_checkpoint(scope)
        self._check_checkpoint(before, scope)
        if before.observed_at < start:
            raise AccountRuntimeError("runtime_clock_invalid")
        if before.state.active:
            raise AccountRuntimeError("unresolved_ledger_holds")
        owned = await collector._collect_owned_demo_account_records(
            credentials=self._credentials,
            clock=clock,
            plan=selected,
            expected_plan_sha256=self._pin,
            barrier_completed_at=barrier,
        )
        if type(owned) is not collector._OwnedAccountCapture:
            raise AccountRuntimeError("owned_capture_required")
        packet = owned.packet
        frozen = capture.freeze_demo_account_packet(
            packet, expected_plan_sha256=self._pin
        )
        packet = capture.verify_demo_account_packet(
            frozen.payload,
            expected_sha256=frozen.sha256,
            expected_plan_sha256=self._pin,
        )
        after = await repository.read_capture_checkpoint(scope)
        self._check_checkpoint(after, scope)
        if before.state_sha256 != after.state_sha256:
            raise AccountRuntimeError("ledger_revision_changed_during_capture")
        if not (
            before.received_at
            <= packet.observations[0].request_started_at
            <= packet.completed_at
            <= owned.completed_at
            <= after.observed_at
        ):
            raise AccountRuntimeError("runtime_clock_invalid")
        # Use the pre-capture DB observation's real time. The matching post-read
        # proves no local revision/hold changed, not atomicity at the exchange.
        values = mapping._plain(inputs)
        values["ledger"] = mapping.LedgerEvidence(
            source=ObservedSource(
                source_sha256=before.state_sha256,
                observed_at=before.observed_at,
                received_at=before.received_at,
            ),
            state=before.state,
        )
        inputs = mapping.AccountMaterializationInputs.model_validate(
            values, strict=True
        )
        try:
            instrument_metadata = metadata.derive_captured_instrument_metadata(
                packet,
                expected_plan_sha256=self._pin,
                expected_packet_sha256=frozen.sha256,
                inputs=inputs,
                expected_inputs_sha256=mapping.materialization_inputs_sha256(inputs),
            )
        except metadata.CapturedMetadataError:
            raise AccountRuntimeError("captured_metadata_invalid") from None
        inputs = instrument_metadata.inputs
        mapped = mapping.materialize_demo_portfolio_snapshot(
            packet,
            expected_plan_sha256=self._pin,
            expected_packet_sha256=frozen.sha256,
            inputs=inputs,
            expected_inputs_sha256=mapping.materialization_inputs_sha256(inputs),
        )
        proofs = owned.peer_certificate_sha256
        if len(proofs) != len(packet.observations) or any(
            item is not None
            and (
                type(item) is not str
                or len(item) != 64
                or any(char not in "0123456789abcdef" for char in item)
            )
            for item in proofs
        ):
            raise AccountRuntimeError("owned_capture_provenance_invalid")
        transport = (
            "owned_signed_verified_tls"
            if all(item is not None for item in proofs)
            else "synthetic_transport"
        )
        gaps = set(mapped.incomplete_reasons)
        gaps.update(instrument_metadata.blocking_reasons)
        gaps.add("registration_provenance_unverified")
        if transport == "synthetic_transport":
            gaps.add("synthetic_transport_not_authenticated")
        if bootstrap is None:
            gaps.update("bootstrap_" + kind + "_missing" for kind in BOOTSTRAP_KINDS)
        else:
            gaps.update(
                "bootstrap_" + kind + "_missing" for kind in bootstrap.missing_kinds
            )
            gaps.add("bootstrap_source_provenance_unverified")
        finished = collector._read_clock(clock)
        if finished < after.received_at:
            raise AccountRuntimeError("runtime_clock_invalid")
        collector._check_batch(
            finished, start, after.received_at, selected.max_batch_seconds
        )
        receipt = capture._canonical(
            {
                "schema_version": "ctcc.controlled_demo_account_runtime.v2",
                "environment": "demo",
                "plan_sha256": self._pin,
                "packet_sha256": frozen.sha256,
                "inputs_sha256": mapping.materialization_inputs_sha256(inputs),
                "materialization_sha256": mapped.evaluation_sha256,
                "instrument_metadata_sha256": instrument_metadata.receipt_sha256,
                "ledger_checkpoint_sha256": before.state_sha256,
                "account_revision": before.state.account_revision,
                "ledger_revision": before.state.ledger_revision,
                "transport_provenance": transport,
                "peer_certificate_sha256": proofs,
                "bootstrap_sha256": None if bootstrap is None else bootstrap.sha256,
                "completed_at": finished.isoformat(),
                "blocking_reasons": sorted(gaps),
                "admission": "DENY",
                "execution_authority": False,
                "account_complete": False,
            }
        ).encode("utf-8")
        return DemoAccountRuntimeResult(
            packet,
            mapped,
            inputs,
            instrument_metadata,
            before,
            finished,
            transport,
            None if bootstrap is None else bootstrap.sha256,
            tuple(sorted(gaps)),
            receipt,
            hashlib.sha256(receipt).hexdigest(),
        )

    @staticmethod
    def _check_checkpoint(value, scope):
        if type(value) is not LedgerCaptureCheckpoint:
            raise AccountRuntimeError("owned_ledger_checkpoint_required")
        state = reservations.checked(value.state, reservations.LedgerScopeState)
        if (
            state.scope != scope
            or state.account_revision < 1
            or state.ledger_revision < 1
            or state.claims_sha256 is None
            or reservations.digest(state) != value.state_sha256
            or capture._utc(value.observed_at) > capture._utc(value.received_at)
        ):
            raise AccountRuntimeError("ledger_checkpoint_invalid")

    @staticmethod
    def _check_bootstrap_checkpoint(value, scope):
        if type(value) is not LedgerBootstrapCheckpoint:
            raise AccountRuntimeError("bootstrap_checkpoint_invalid")
        scope = reservations.checked_bootstrap(scope, reservations.LedgerScope)
        state = reservations.checked_bootstrap(
            value.state, reservations.LedgerScopeState
        )
        if (
            state.scope != scope
            or type(value.state_sha256) is not str
            or reservations.digest(state) != value.state_sha256
            or capture._utc(value.observed_at) > capture._utc(value.received_at)
            or state.ledger_revision < state.account_revision
            or (
                state.account_revision == 0
                and (
                    state.ledger_revision != 0
                    or state.claims_sha256 is not None
                    or state.active
                )
            )
            or (state.account_revision > 0 and state.claims_sha256 is None)
        ):
            raise AccountRuntimeError("bootstrap_checkpoint_invalid")

    def _check_bootstrap(self, bootstrap, now):
        evidence, plan = bootstrap.evidence, self._plan
        if (
            (
                evidence.account_id,
                evidence.main_uid,
                evidence.settlement_currency,
                evidence.registration_region,
            )
            != (
                plan.expected_uid,
                plan.expected_main_uid,
                plan.settlement_currency,
                plan.registration_region,
            )
            or evidence.sealed_at > now
            or not set(plan.leverage_instrument_ids) <= set(evidence.instrument_ids)
        ):
            raise AccountRuntimeError("bootstrap_scope_or_clock_mismatch")
        secrets = list(collector._credential_values(self._credentials)[:3])
        for item in evidence.artifacts:
            collector._no_secrets(item.raw_bytes, secrets)
            if item.kind == "registration" and (
                item.artifact_sha256 != plan.registration_evidence_sha256
            ):
                raise AccountRuntimeError("registration_evidence_pin_mismatch")
            if item.kind in {
                "history_seed",
                "history_retention",
                "history_ingestion_watermark",
                "continuous_peak_window",
                "funding_accrual",
            } and not (
                item.coverage_start <= plan.history_start
                and item.coverage_end >= plan.history_end
            ):
                raise AccountRuntimeError("bootstrap_history_coverage_incomplete")
