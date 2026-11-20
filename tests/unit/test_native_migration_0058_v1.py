"""Migration/ORM source contracts only; PostgreSQL acceptance is separate."""

from importlib import import_module

from sqlalchemy.dialects.postgresql import dialect
from sqlalchemy.schema import CreateIndex

from app.database.models import native_live_ledger_v1 as live_models
from app.database.models.submission_reporting import QualificationSubmissionOutcome
from app.domain import native_live_ledger_schema_v1 as live
from app.domain import native_protective_hold_schema_v58 as hold

subject = import_module("migrations.versions.0058_protected_hold_and_live_intent")


def test_actual58_is_additive_after57_and_never_grants_or_arms(monkeypatch):
    assert subject.revision == "0058" and subject.down_revision == "0057"
    statements = []
    monkeypatch.setattr(subject.op, "execute", statements.append)
    subject.upgrade()
    assert statements[0] == subject._LOCK
    assert all(statement in statements for statement in hold.statements())
    assert hold.CURRENT_SQL in statements
    assert statements[-len(live.UPGRADE_SQL) :] == list(live.UPGRADE_SQL)
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
