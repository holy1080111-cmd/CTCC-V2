"""Owner search_path cannot select a different submission guard namespace."""

from hashlib import sha256

import pytest

from app.domain import native_submission_binding_schema_v58 as subject


def test_upgrade_and_restore_target_the_exact_public_function_with_identical_original_body():
    original = "CREATE OR REPLACE FUNCTION qualification_submission_binding("
    qualified = "CREATE OR REPLACE FUNCTION public.qualification_submission_binding("
    current = subject.statements()[0]
    restored = subject.restore_statements()[0]
    assert original not in current and original not in restored
    assert current.count(qualified) == restored.count(qualified) == 1
    assert restored.replace(qualified, original, 1) == subject.ANCESTOR_SQL
    assert (
        sha256(restored.split("$$")[1].encode()).hexdigest()
        == subject.ANCESTOR_BODY_SHA256
    )
    assert (
        sha256(current.split("$$")[1].encode()).hexdigest()
        == subject.CURRENT_BODY_SHA256
    )
    assert subject.previous.statements() is not None


@pytest.mark.parametrize(
    "sql",
    (
        "",
        "CREATE FUNCTION other()",
        subject.ANCESTOR_SQL * 2,
        subject.ANCESTOR_SQL.replace(
            "qualification_submission_binding(", "foreign_guard("
        ),
    ),
)
def test_missing_duplicate_or_changed_header_cannot_be_silently_retargeted(sql):
    with pytest.raises(ValueError, match="native_submission58_original_guard_changed"):
        subject._public_ddl(sql)
