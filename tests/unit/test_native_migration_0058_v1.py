"""Migration/ORM source contracts only; PostgreSQL acceptance is separate."""

from importlib import import_module

from sqlalchemy.dialects.postgresql import dialect
from sqlalchemy.schema import CreateIndex

from app.database.models import native_live_financial_epoch_v1 as financial_models
from app.database.models import native_live_ledger_v1 as live_models
from app.database.models.submission_reporting import QualificationSubmissionOutcome
from app.domain import native_live_financial_epoch_schema_v1 as financial
from app.domain import native_live_ledger_schema_v1 as live
from app.domain import native_protective_hold_schema_v58 as hold
from app.domain import native_submission_binding_schema_v58 as submission

subject = import_module("migrations.versions.0058_protected_hold_and_live_intent")


def test_actual58_is_additive_after57_and_never_grants_or_arms(monkeypatch):
    assert subject.revision == "0058" and subject.down_revision == "0057"
    statements = []
    monkeypatch.setattr(subject.op, "execute", statements.append)
    subject.upgrade()
    assert statements[0] == subject._LOCK
    assert all(statement in statements for statement in hold.statements())
    assert hold.CURRENT_SQL in statements
    assert submission.CURRENT_SQL in statements
    assert statements.index(hold.CURRENT_SQL) < statements.index(submission.CURRENT_SQL)
    assert any(submission.ANCESTOR_BODY_SHA256 in statement for statement in statements)
    assert any(submission.CURRENT_BODY_SHA256 in statement for statement in statements)
    assert all(statement in statements for statement in live.UPGRADE_SQL)
    assert all(statement in statements for statement in financial.UPGRADE_SQL)
    assert statements.index(live.UPGRADE_SQL[-1]) < statements.index(
        financial.UPGRADE_SQL[0]
    )
    assert "protected58_financial_guard_changed" in statements[-1]
    assert not any(
        statement.lstrip().upper().startswith("GRANT ") for statement in statements
    )
    assert not any(
        "INSERT INTO public.native_live_controls_v1" in statement
        for statement in statements
    )


def test_downgrade_checks_retained_custody_before_removing_only_new_objects(
    monkeypatch,
):
    statements = []
    monkeypatch.setattr(subject.op, "execute", statements.append)
    subject.downgrade()
    refusal = next(
        index
        for index, value in enumerate(statements)
        if "protected58_preserve_history_and_revoke_grants" in value
    )
    assert refusal < statements.index(subject.previous.CURRENT_SQL)
    assert refusal < statements.index(submission.ANCESTOR_SQL)
    assert "native_live_financial_epochs_v1" in statements[refusal]
    assert "native_live_financial_samples_v1" in statements[refusal]
    assert "pg_catalog.acldefault('f',p.proowner)" in statements[refusal]
    assert statements.index(financial.DOWNGRADE_SQL[-1]) < statements.index(
        live.DOWNGRADE_SQL[0]
    )
    assert any(submission.CURRENT_BODY_SHA256 in statement for statement in statements)
    assert statements[-len(live.DOWNGRADE_SQL) :] == list(live.DOWNGRADE_SQL)
    assert not any(
        value.lstrip().upper().startswith(("DELETE ", "TRUNCATE "))
        for value in statements
    )
    assert not any("CASCADE" in value for value in statements)


def test_new_live_models_and_demo_indexes_match_source_spec_names_and_predicates():
    assert (
        tuple(
            model.__tablename__
            for model in (
                financial_models.NativeLiveFinancialEpochV1,
                financial_models.NativeLiveFinancialSampleV1,
            )
        )
        == financial.TABLES
    )
    assert (
        tuple(
            model.__tablename__
            for model in (
                live_models.NativeLiveControlV1,
                live_models.NativeLiveReservationV1,
                live_models.NativeLiveIntentV1,
                live_models.NativeLiveTransitionV1,
            )
        )
        == live.TABLES
    )
    indexes = {
        index.name: str(CreateIndex(index).compile(dialect=dialect()))
        for index in QualificationSubmissionOutcome.__table__.indexes
        if index.name.endswith("_v58")
    }
    assert len(indexes) == 2
    assert {
        sql.replace(
            "ON qualification_submission_outcomes",
            "ON public.qualification_submission_outcomes",
        )
        for sql in indexes.values()
    } == set(hold.INDEXES)
