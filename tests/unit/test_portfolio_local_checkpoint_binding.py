"""The SQL mirror's session pin is a local flatness prerequisite only."""

import pytest

from app.database.repositories.qualification_ledger import (
    _checkpoint_credential_session_binding,
)


@pytest.mark.parametrize(
    "details",
    (
        None,
        {},
        {"credential_session_binding_id": None},
        {"credential_session_binding_id": ""},
        {"credential_session_binding_id": "a" * 63},
        {"credential_session_binding_id": "A" * 64},
        {"credential_session_binding_id": 7},
    ),
)
def test_missing_or_malformed_credential_session_is_unbound(details):
    _, bound = _checkpoint_credential_session_binding(details)
    assert bound is False


def test_exact_lowercase_session_pin_has_shape_only():
    value, bound = _checkpoint_credential_session_binding(
        {"credential_session_binding_id": "b" * 64}
    )
    assert value == "b" * 64
    assert bound is True


def test_dictionary_subclass_cannot_supply_a_hidden_session_pin():
    class ForeignDict(dict):
        def get(self, *_args):
            raise AssertionError("foreign callback must not run")

    assert _checkpoint_credential_session_binding(
        ForeignDict(credential_session_binding_id="a" * 64)
    ) == (None, False)
