"""Reviewed current-source component code; never replace historical N234 pins."""

import hashlib

from scripts import current_contract_source_scopes_v1 as scopes

# Exact reviewed source57 at e174d67. These are code identities, not a database
# or native startup reference. Original source56/N234 constants stay separate.
_SOURCE57 = {
    "app/core/native_role_witness_0056_v1.py": "3f7f5c3139e2c2e0a2574779560c48b579e62c793d39ad8962ef9f3bdb3baed7",
    "app/core/native_startup_candidate_0056_v1.py": "69458555dea8f36c6b7773dfe86fd7598222b69220c1b6a6a64bc8ca632eef48",
    "app/core/native_role_identity_0056_v2.py": "bb126e2ca8683be56c6912e94765c6f21815ef7f6e8be2d1df3431ac9f17a3a3",
    "app/core/native_role_preflight_0056_full_diagnostic_v1.py": "ef2b35fef1539dc22ef4d66ba831338465a606f17f08b3966a2500b8765d7b0a",
}


def reviewed_component_source_pins_v1(root, *, original_source56):
    before = scopes.database0056_source_scope(root)
    if before.actual_source_head == "0056":
        expected = dict(original_source56)
    elif before.actual_source_head == "0057":
        from scripts.historical0056_owner_source_on_0057_v1 import (
            require_historical_owner_source57,
        )

        assert require_historical_owner_source57(root) == before
        expected = dict(_SOURCE57)
    elif before.actual_source_head == "0058":
        from scripts.historical0056_owner_source_on_0058_v1 import (
            _REVIEWED_SOURCE58_FILES,
            require_historical_owner_source58,
        )

        assert require_historical_owner_source58(root) == before
        expected = {name: _REVIEWED_SOURCE58_FILES[name] for name in _SOURCE57}
    else:
        raise AssertionError("unsupported_current_component_source")
    assert set(expected) == set(_SOURCE57)
    assert all(
        not (root / name).is_symlink()
        and hashlib.sha256((root / name).read_bytes()).hexdigest() == pin
        for name, pin in expected.items()
    )
    assert scopes.database0056_source_scope(root) == before
    return expected
