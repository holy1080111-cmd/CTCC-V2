"""Durable pre-G12 diagnostic custody; never a source or order authority.

This records hashes of bounded original inputs before publication and the exact
new six-file receipt after publication. It deliberately does not retain the
original input values, authenticate their source, or grant execution permission.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.source_primitives import canonical, decode, sha
from app.trade_evidence import gates, storage
from app.trade_qualification import public_source_runtime
from app.trade_qualification.recheck_models import freeze_recheck_origin
from app.trade_qualification.timing import event_identity

_CLAIM_SCHEMA = "ctcc.g12.invocation_claim.v1"
_PUBLICATION_SCHEMA = "ctcc.g12.invocation_publication.v1"
_FILES = frozenset(storage.FILE_NAMES)
_PUBLICATION_FIELDS = frozenset(
    {
        "schema_version",
        "invocation_id",
        "claim_sha256",
        "candidate_sha256",
        "event_key",
        "evidence_root_sha256",
        "evidence_sha256",
        "report_sha256",
        "file_sha256",
        "publication_completed_at",
        "admission",
        "publication_provenance_verified",
        "execution_authority",
    }
)
_MAX_DOCUMENT = 8192
_MAX_INPUTS = 32 * 1024 * 1024


class G12InvocationCustodyError(ValueError):
    """A fixed denial code; no private source, SQL or path details."""


def _deny(code: str):
    raise G12InvocationCustodyError(code)


def _jsonable(value):
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json", round_trip=True)
    if type(value) in (dict, MappingProxyType):
        return {key: _jsonable(item) for key, item in value.items()}
    if type(value) in (tuple, list):
        return [_jsonable(item) for item in value]
    if type(value) is frozenset:
        return sorted(_jsonable(item) for item in value)
    if type(value) is datetime:
        return value.astimezone(UTC).isoformat()
    if type(value) is Decimal:
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if value is None or type(value) in (str, int, bool):
        return value
    _deny("g12_custody_original_inputs_invalid")


def _digest(value) -> str:
    raw = json.dumps(
        _jsonable(value),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("ascii")
    if not raw or len(raw) > _MAX_INPUTS:
        _deny("g12_custody_original_inputs_unbounded")
    return sha(raw)


def _document(value: dict) -> tuple[bytes, str]:
    raw = canonical(value)
    if not 0 < len(raw) <= _MAX_DOCUMENT:
        _deny("g12_custody_document_unbounded")
    return raw, sha(raw)


def _root_pin(root: Path, report_id: str) -> str:
    try:
        storage._report_name(report_id)
        root = storage._root_path(root)
        with public_source_runtime._native_recheck_root(root) as directory:
            if any(
                name.casefold() == report_id.casefold() for name in directory.names()
            ):
                raise ValueError
            first = public_source_runtime._native_recheck_root_identity(root)
            if hasattr(directory, "fd"):
                held = os.fstat(directory.fd)
                if first != (held.st_dev, held.st_ino):
                    raise ValueError
        if public_source_runtime._native_recheck_root_identity(root) != first:
            raise ValueError
        return sha(canonical([str(root), first[0], first[1]]))
    except Exception:  # noqa: BLE001 -- no filesystem path in a rejection
        _deny("g12_custody_root_invalid")


@dataclass(frozen=True, slots=True)
class G12InvocationClaim:
    invocation_id: str
    claim_json: bytes
    claim_sha256: str
    db_claimed_at: datetime | None = None
    admission: Literal["DENY"] = "DENY"
    source_authenticity_verified: Literal[False] = False
    execution_authority: Literal[False] = False

    @property
    def values(self) -> dict:
        return decode(self.claim_json, _MAX_DOCUMENT)


@dataclass(frozen=True, slots=True)
class G12InvocationPublication:
    invocation_id: str
    publication_json: bytes
    publication_sha256: str
    db_recorded_at: datetime | None = None
    admission: Literal["DENY"] = "DENY"
    publication_provenance_verified: Literal[False] = False
    execution_authority: Literal[False] = False


def prepare_g12_claim(
    evidence_root: Path,
    market,
    pre,
    inputs: dict,
    *,
    account_uid: str,
    account_plan_sha256: str,
) -> G12InvocationClaim:
    """Prepare fixed hashes from the already version-replayed original run."""
    try:
        intent = inputs["intent"]
        candidate = pre.result
        key = event_identity(pre.prefix.detection)
        account = inputs["risk_inputs"].account
        deadline = min(
            intent.expires_at,
            pre.prefix.detection.trigger.expires_at,
            candidate.entry_zone.expires_at,
            pre.prefix.timing.latest_valid_entry_time,
        )
        if (
            not pre.pre_evidence_complete
            or key is None
            or type(account_uid) is not str
            or not 1 <= len(account_uid) <= 32
            or account is None
            or account.account_id != account_uid
            or type(account_plan_sha256) is not str
            or len(account_plan_sha256) != 64
            or candidate.candidate_entry != intent.candidate_entry
            or candidate.stop_loss is None
            or candidate.take_profit is None
            or deadline.utcoffset() is None
        ):
            raise ValueError
        root_sha = _root_pin(evidence_root, intent.report_id)
        candidate_sha = sha(
            canonical(
                {
                    "report_id": intent.report_id,
                    "instrument_id": intent.instrument_id,
                    "direction": intent.direction,
                    "event_key": key,
                    "entry": str(candidate.candidate_entry),
                    "stop_loss": str(candidate.stop_loss),
                    "take_profit": str(candidate.take_profit),
                    "deadline": deadline.astimezone(UTC).isoformat(),
                }
            )
        )
        raw, digest = _document(
            {
                "schema_version": _CLAIM_SCHEMA,
                "invocation_id": uuid4().hex,
                "environment": "demo",
                "account_uid_sha256": sha(canonical(["demo", account_uid])),
                "account_plan_sha256": account_plan_sha256,
                "event_key": key,
                "report_id": intent.report_id,
                "instrument_id": intent.instrument_id,
                "direction": intent.direction,
                "entry": str(candidate.candidate_entry),
                "stop_loss": str(candidate.stop_loss),
                "take_profit": str(candidate.take_profit),
                "deadline": deadline.astimezone(UTC).isoformat(),
                "candidate_sha256": candidate_sha,
                "original_run_sha256": gates._digest(pre),
                "original_market_sha256": gates._digest(market),
                "original_inputs_sha256": _digest(inputs),
                "evidence_root_sha256": root_sha,
                "admission": "DENY",
                "source_authenticity_verified": False,
                "execution_authority": False,
            }
        )
        return G12InvocationClaim(
            decode(raw, _MAX_DOCUMENT)["invocation_id"], raw, digest
        )
    except G12InvocationCustodyError:
        raise
    except Exception:  # noqa: BLE001 -- no caller/private detail in error
        _deny("g12_custody_claim_invalid")


def prepare_g12_publication(
    claim: G12InvocationClaim,
    evidence: gates.EvidenceGateRun,
    evidence_root: Path,
) -> G12InvocationPublication:
    """Bind only a fresh G12 six-file write to the prior claim."""
    try:
        values = claim.values
        receipt = evidence.receipt
        origin = freeze_recheck_origin(evidence)
        files = {item.name: item.sha256 for item in receipt.files}
        if (
            claim.db_claimed_at is None
            or receipt.status != "written"
            or set(files) != _FILES
            or len(receipt.files) != 6
            or evidence.result.report_id != values["report_id"]
            or evidence.pre_evidence_sha256 != evidence.pre_evidence.evaluation_sha256
            or gates._digest(evidence.pre_evidence) != values["original_run_sha256"]
            or origin.original_event_key != values["event_key"]
            or str(origin.candidate.candidate_entry) != values["entry"]
            or str(origin.candidate.stop_loss) != values["stop_loss"]
            or str(origin.candidate.take_profit) != values["take_profit"]
            or origin.deadline.astimezone(UTC).isoformat() != values["deadline"]
            or not _read_exact_six(
                evidence_root, receipt, values["evidence_root_sha256"]
            )
            or receipt.completed_at <= claim.db_claimed_at
        ):
            raise ValueError
        raw, digest = _document(
            {
                "schema_version": _PUBLICATION_SCHEMA,
                "invocation_id": claim.invocation_id,
                "claim_sha256": claim.claim_sha256,
                "candidate_sha256": values["candidate_sha256"],
                "event_key": values["event_key"],
                "evidence_root_sha256": values["evidence_root_sha256"],
                "evidence_sha256": evidence.evaluation_sha256,
                "report_sha256": receipt.report_sha256,
                "file_sha256": files,
                "publication_completed_at": receipt.completed_at.astimezone(
                    UTC
                ).isoformat(),
                "admission": "DENY",
                "publication_provenance_verified": False,
                "execution_authority": False,
            }
        )
        return G12InvocationPublication(claim.invocation_id, raw, digest)
    except Exception:  # noqa: BLE001 -- no private evidence/path in error
        _deny("g12_custody_publication_invalid")


def _read_exact_six(
    root: Path,
    receipt,
    expected_root_sha256: str,
    *,
    expected_packet: dict[str, bytes] | None = None,
) -> bool:
    """Read six files under one held, identity-checked native root context."""
    try:
        storage._report_name(receipt.report_id)
        root = storage._root_path(root)
        native = storage._windows_root if os.name == "nt" else storage._posix_root
        with native(root) as directory:
            root_identity = public_source_runtime._native_recheck_root_identity(root)
            if hasattr(directory, "fd"):
                held = os.fstat(directory.fd)
                if root_identity != (held.st_dev, held.st_ino):
                    return False
            if (
                sha(canonical([str(root), root_identity[0], root_identity[1]]))
                != expected_root_sha256
            ):
                return False
            with directory.child(receipt.report_id) as report_directory:
                report_identity = public_source_runtime._native_recheck_root_identity(
                    report_directory.path
                )
                if hasattr(report_directory, "fd"):
                    held = os.fstat(report_directory.fd)
                    if report_identity != (held.st_dev, held.st_ino):
                        return False
                if set(report_directory.names()) != _FILES:
                    return False
                packet = {
                    name: report_directory.read(
                        name,
                        storage.MAX_REPORT_BYTES
                        if name == "report.json"
                        else storage.MAX_PNG_BYTES,
                    )
                    for name in storage.FILE_NAMES
                }
                storage._packet(receipt.report_id, packet)
                if expected_packet is not None and (
                    type(expected_packet) is not dict
                    or set(expected_packet) != _FILES
                    or any(type(item) is not bytes for item in expected_packet.values())
                    or packet != expected_packet
                ):
                    return False
                if not all(
                    sha(packet[item.name]) == item.sha256
                    and len(packet[item.name]) == item.size_bytes
                    for item in receipt.files
                ):
                    return False
                return (
                    public_source_runtime._native_recheck_root_identity(
                        report_directory.path
                    )
                    == report_identity
                    and public_source_runtime._native_recheck_root_identity(root)
                    == root_identity
                )
    except Exception:  # noqa: BLE001 -- tamper, alias or malformed PNG denies
        return False


def require_committed_publication_files(
    claim: G12InvocationClaim,
    publication: G12InvocationPublication,
    evidence: gates.EvidenceGateRun,
    evidence_root: Path,
) -> None:
    """Reopen the exact six G12 files after the DB commit/readback await."""
    try:
        value = decode(publication.publication_json, _MAX_DOCUMENT)
        receipt = evidence.receipt
        files = {item.name: item.sha256 for item in receipt.files}
        if (
            type(claim) is not G12InvocationClaim
            or type(publication) is not G12InvocationPublication
            or publication.db_recorded_at is None
            or publication.invocation_id != claim.invocation_id
            or value["claim_sha256"] != claim.claim_sha256
            or value["file_sha256"] != files
            or value["report_sha256"] != receipt.report_sha256
            or value["evidence_sha256"] != evidence.evaluation_sha256
            or value["publication_completed_at"]
            != receipt.completed_at.astimezone(UTC).isoformat()
            or receipt.status != "written"
            or receipt.report_id != claim.values["report_id"]
            or not _read_exact_six(
                evidence_root, receipt, claim.values["evidence_root_sha256"]
            )
        ):
            raise ValueError
    except Exception:  # noqa: BLE001 -- committed rows remain, capture denied
        _deny("g12_custody_postcommit_files_changed")


def require_replayed_publication_computation(
    claim: G12InvocationClaim,
    publication: G12InvocationPublication,
    evidence: gates.EvidenceGateRun,
    evidence_root: Path,
    market,
    pre,
    inputs: dict,
) -> None:
    """Replay G1--G12 and the exact six bytes after durable readback.

    The original inputs and the committed publication are both checked again
    after the database await. This read-only check cannot authenticate their
    source, create a reservation, or issue execution authority. A late failure
    leaves the accepted publication and its files intact.
    """
    try:
        from app.trade_evidence import g12_computational_proof_v1 as proof

        if (
            type(claim) is not G12InvocationClaim
            or type(publication) is not G12InvocationPublication
            or publication.db_recorded_at is None
            or publication.invocation_id != claim.invocation_id
            or type(evidence)
            not in {
                gates.EvidenceGateRun,
                gates.HistoryEvidenceGateRunV2,
                gates.HistoryEvidenceGateRunV3,
                gates.HistoryEvidenceGateRunV4,
                gates.HistoryEvidenceGateRunV5,
                gates.HistoryEvidenceGateRunV6,
            }
        ):
            raise ValueError
        require_original_unchanged(claim, market, pre, inputs)
        checked = gates._copy(evidence, type(evidence))
        if checked.pre_evidence != pre:
            raise ValueError
        receipt = checked.receipt
        published = decode(publication.publication_json, _MAX_DOCUMENT)
        if (
            receipt is None
            or receipt.status != "written"
            or published["claim_sha256"] != claim.claim_sha256
            or published["evidence_sha256"] != checked.evaluation_sha256
            or published["report_sha256"] != receipt.report_sha256
            or published["file_sha256"]
            != {item.name: item.sha256 for item in receipt.files}
            or receipt.report_directory != str(evidence_root / checked.result.report_id)
            or tuple(item.name for item in receipt.files) != storage.FILE_NAMES
            or checked.rendered_files != receipt.files
        ):
            raise ValueError
        packet = proof._replay_expected_packet(checked, market, inputs, "liquidity_v2")
        proof._replay_g12_result(checked)
        if set(packet) != set(storage.FILE_NAMES) or not _read_exact_six(
            evidence_root,
            receipt,
            claim.values["evidence_root_sha256"],
            expected_packet=packet,
        ):
            raise ValueError
        # A slow render or filesystem read cannot retain an old source pin.
        require_original_unchanged(claim, market, pre, inputs)
    except Exception:  # noqa: BLE001 -- redact source, DB and path failures
        _deny("g12_custody_computation_replay_failed")


def require_prepublication_root(claim: G12InvocationClaim, root: Path) -> None:
    """Check the original empty report slot again after the DB await."""
    if (
        type(claim) is not G12InvocationClaim
        or claim.db_claimed_at is None
        or _root_pin(root, claim.values["report_id"])
        != claim.values["evidence_root_sha256"]
    ):
        _deny("g12_custody_prepublication_root_changed")


def require_original_unchanged(claim: G12InvocationClaim, market, pre, inputs) -> None:
    """An await cannot silently swap the exact replay operands under a claim."""
    try:
        value = claim.values
        if (
            type(claim) is not G12InvocationClaim
            or _digest(inputs) != value["original_inputs_sha256"]
            or gates._digest(market) != value["original_market_sha256"]
            or gates._digest(pre) != value["original_run_sha256"]
            or pre.result.report_id != value["report_id"]
            or pre.prefix.intent.instrument_id != value["instrument_id"]
        ):
            raise ValueError
    except Exception:  # noqa: BLE001 -- bounded fixed denial
        _deny("g12_custody_original_changed")


class G12InvocationCustodyRepository:
    """Two committed inserts with independent-session readback, still DENY."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        if not isinstance(session_factory, async_sessionmaker):
            _deny("g12_custody_session_factory_required")
        self.session_factory = session_factory

    async def _read_claim(self, claim: G12InvocationClaim) -> G12InvocationClaim:
        async with self.session_factory() as session:
            row = (
                (
                    await session.execute(
                        text(
                            "SELECT environment,account_uid_sha256,event_key,claim_json,claim_sha256,db_claimed_at "
                            "FROM public.g12_invocation_claims WHERE invocation_id=:id"
                        ),
                        {"id": claim.invocation_id},
                    )
                )
                .mappings()
                .one_or_none()
            )
        value = claim.values
        if (
            row is None
            or row["environment"] != "demo"
            or row["account_uid_sha256"] != value["account_uid_sha256"]
            or row["event_key"] != value["event_key"]
            or row["claim_json"].encode("utf-8") != claim.claim_json
            or row["claim_sha256"] != claim.claim_sha256
            or sha(claim.claim_json) != claim.claim_sha256
            or type(row["db_claimed_at"]) is not datetime
            or row["db_claimed_at"].utcoffset() is None
        ):
            _deny("g12_custody_claim_readback_mismatch")
        return G12InvocationClaim(
            claim.invocation_id,
            claim.claim_json,
            claim.claim_sha256,
            row["db_claimed_at"].astimezone(UTC),
        )

    async def claim(self, claim: G12InvocationClaim) -> G12InvocationClaim:
        if type(claim) is not G12InvocationClaim or claim.db_claimed_at is not None:
            _deny("g12_custody_new_claim_required")
        value = claim.values
        try:
            async with self.session_factory() as session, session.begin():
                await session.execute(text("SET LOCAL lock_timeout = '3s'"))
                await session.execute(text("SET LOCAL statement_timeout = '10s'"))
                await session.execute(
                    text(
                        "INSERT INTO public.g12_invocation_claims "
                        "(invocation_id,environment,account_uid_sha256,event_key,claim_json,claim_sha256) "
                        "VALUES (:id,'demo',:uid,:event,:doc,:digest)"
                    ),
                    {
                        "id": claim.invocation_id,
                        "uid": value["account_uid_sha256"],
                        "event": value["event_key"],
                        "doc": claim.claim_json.decode("ascii"),
                        "digest": claim.claim_sha256,
                    },
                )
            return await self._read_claim(claim)
        except G12InvocationCustodyError:
            raise
        except Exception:  # noqa: BLE001 -- uncertain commit never starts G12
            _deny("g12_custody_claim_unavailable")

    async def publication(
        self,
        claim: G12InvocationClaim,
        publication: G12InvocationPublication,
    ) -> G12InvocationPublication:
        if (
            type(claim) is not G12InvocationClaim
            or type(publication) is not G12InvocationPublication
            or claim.db_claimed_at is None
            or publication.db_recorded_at is not None
            or publication.invocation_id != claim.invocation_id
        ):
            _deny("g12_custody_publication_input_invalid")
        value = decode(publication.publication_json, _MAX_DOCUMENT)
        if value["claim_sha256"] != claim.claim_sha256:
            _deny("g12_custody_publication_claim_mismatch")
        try:
            if await self._read_claim(claim) != claim:
                _deny("g12_custody_claim_changed")
            async with self.session_factory() as session, session.begin():
                await session.execute(text("SET LOCAL lock_timeout = '3s'"))
                await session.execute(text("SET LOCAL statement_timeout = '10s'"))
                await session.execute(
                    text(
                        "INSERT INTO public.g12_invocation_publications "
                        "(invocation_id,claim_sha256,publication_json,publication_sha256) "
                        "VALUES (:id,:claim,:doc,:digest)"
                    ),
                    {
                        "id": claim.invocation_id,
                        "claim": claim.claim_sha256,
                        "doc": publication.publication_json.decode("ascii"),
                        "digest": publication.publication_sha256,
                    },
                )
            return await self.read_publication(claim, publication.publication_sha256)
        except G12InvocationCustodyError:
            raise
        except Exception:  # noqa: BLE001 -- uncertain commit blocks post-G12 capture
            _deny("g12_custody_publication_unavailable")

    async def read_publication(
        self, claim: G12InvocationClaim, expected_sha256: str
    ) -> G12InvocationPublication:
        """Audit a committed row after restart; never resume post-G12 capture."""
        try:
            if (
                type(claim) is not G12InvocationClaim
                or claim.db_claimed_at is None
                or type(expected_sha256) is not str
                or len(expected_sha256) != 64
                or await self._read_claim(claim) != claim
            ):
                raise ValueError
            async with self.session_factory() as session:
                row = (
                    (
                        await session.execute(
                            text(
                                "SELECT claim_sha256,publication_json,publication_sha256,db_recorded_at "
                                "FROM public.g12_invocation_publications WHERE invocation_id=:id"
                            ),
                            {"id": claim.invocation_id},
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
            if row is None:
                _deny("g12_custody_publication_readback_mismatch")
            value = decode(row["publication_json"].encode("utf-8"), _MAX_DOCUMENT)
            original = claim.values
            completed_at = datetime.fromisoformat(value["publication_completed_at"])
            if (
                row["claim_sha256"] != claim.claim_sha256
                or row["publication_sha256"] != expected_sha256
                or sha(row["publication_json"].encode("utf-8")) != expected_sha256
                or canonical(value) != row["publication_json"].encode("utf-8")
                or set(value) != _PUBLICATION_FIELDS
                or value["schema_version"] != _PUBLICATION_SCHEMA
                or value["invocation_id"] != claim.invocation_id
                or value["claim_sha256"] != claim.claim_sha256
                or value["candidate_sha256"] != original["candidate_sha256"]
                or value["event_key"] != original["event_key"]
                or value["evidence_root_sha256"] != original["evidence_root_sha256"]
                or value["admission"] != "DENY"
                or value["execution_authority"] is not False
                or value["publication_provenance_verified"] is not False
                or completed_at.utcoffset() is None
                or completed_at <= claim.db_claimed_at
                or completed_at > row["db_recorded_at"]
                or completed_at >= datetime.fromisoformat(original["deadline"])
                or set(value["file_sha256"]) != _FILES
                or any(
                    type(digest) is not str or len(digest) != 64
                    for digest in value["file_sha256"].values()
                )
                or type(row["db_recorded_at"]) is not datetime
                or row["db_recorded_at"].utcoffset() is None
                or row["db_recorded_at"] < claim.db_claimed_at
            ):
                _deny("g12_custody_publication_readback_mismatch")
            return G12InvocationPublication(
                claim.invocation_id,
                row["publication_json"].encode("utf-8"),
                expected_sha256,
                row["db_recorded_at"].astimezone(UTC),
            )
        except G12InvocationCustodyError:
            raise
        except Exception:  # noqa: BLE001 -- audit never grants an execution path
            _deny("g12_custody_publication_readback_mismatch")
