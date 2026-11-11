"""Catalog metadata for Gate 3 fitted bindings, acknowledgements, and attempts."""

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import OID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class Gate3FittedCandidateBinding(Base):
    __tablename__ = "gate3_fitted_candidate_bindings"
    __table_args__ = (
        CheckConstraint(
            "seal_sha256 ~ '^[a-f0-9]{64}$' AND binding_sha256 ~ '^[a-f0-9]{64}$'",
            name="hashes",
        ),
        CheckConstraint(
            "pg_catalog.octet_length(binding_json) BETWEEN 1 AND 16384", name="payload"
        ),
        CheckConstraint("publication_xid ~ '^[1-9][0-9]{0,19}$'", name="xids"),
    )

    seal_sha256: Mapped[str] = mapped_column(
        String(64),
        ForeignKey(
            "gate3_preregistration_seals.seal_sha256",
            name="fk_gate3_fitted_binding_seal",
        ),
        primary_key=True,
    )
    binding_sha256: Mapped[str] = mapped_column(String(64), unique=True)
    binding_json: Mapped[str] = mapped_column(Text)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    publisher_oid: Mapped[int] = mapped_column(OID)
    publication_xid: Mapped[str] = mapped_column(Text)


class Gate3FittedCandidateAck(Base):
    __tablename__ = "gate3_fitted_candidate_acks"
    __table_args__ = (
        CheckConstraint(
            "seal_sha256 ~ '^[a-f0-9]{64}$' AND binding_sha256 ~ '^[a-f0-9]{64}$'",
            name="hashes",
        ),
        CheckConstraint("acknowledgement_xid ~ '^[1-9][0-9]{0,19}$'", name="xids"),
    )

    seal_sha256: Mapped[str] = mapped_column(
        String(64),
        ForeignKey(
            "gate3_fitted_candidate_bindings.seal_sha256",
            name="fk_gate3_fitted_ack_binding",
        ),
        primary_key=True,
    )
    binding_sha256: Mapped[str] = mapped_column(String(64))
    acknowledged_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    observer_oid: Mapped[int] = mapped_column(OID)
    acknowledgement_xid: Mapped[str] = mapped_column(Text)


class Gate3FittedCandidateEvaluation(Base):
    __tablename__ = "gate3_fitted_candidate_evaluations"
    __table_args__ = (
        CheckConstraint(
            "seal_sha256 ~ '^[a-f0-9]{64}$' AND binding_sha256 ~ '^[a-f0-9]{64}$' "
            "AND receipt_sha256 ~ '^[a-f0-9]{64}$' AND dataset_sha256 ~ '^[a-f0-9]{64}$' "
            "AND attempt_sha256 ~ '^[a-f0-9]{64}$'",
            name="hashes",
        ),
        CheckConstraint(
            "acknowledgement_xid ~ '^[1-9][0-9]{0,19}$' AND reservation_xid ~ '^[1-9][0-9]{0,19}$'",
            name="xids",
        ),
    )

    seal_sha256: Mapped[str] = mapped_column(
        String(64),
        ForeignKey(
            "gate3_fitted_candidate_acks.seal_sha256",
            name="fk_gate3_fitted_evaluation_ack",
        ),
        primary_key=True,
    )
    binding_sha256: Mapped[str] = mapped_column(String(64))
    acknowledgement_xid: Mapped[str] = mapped_column(Text)
    receipt_sha256: Mapped[str] = mapped_column(String(64))
    dataset_sha256: Mapped[str] = mapped_column(String(64))
    attempt_sha256: Mapped[str] = mapped_column(String(64))
    reserved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    evaluator_oid: Mapped[int] = mapped_column(OID)
    reservation_xid: Mapped[str] = mapped_column(Text)
