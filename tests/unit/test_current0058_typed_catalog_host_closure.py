"""Actual host-source admission must cover the lazily imported typed codec."""

import sys
from pathlib import Path

import pytest

from scripts import current0058_full_hermetic_contract_v1 as contract

ROOT = Path(__file__).resolve().parents[2]
HELPER = "scripts/retained0017_catalog_sql_types_v1.py"


def _actual_source_map():
    paths = {ROOT / "scripts" / name for name in contract.HOST_HELPERS}
    # Archive admission supplies the whole source map, including app/__init__
    # and modules that a pinned host helper may import after this map is built.
    paths.update((ROOT / "app").rglob("*.py"))
    for name, module in tuple(sys.modules.items()):
        if name.startswith(("scripts.", "app.")):
            filename = getattr(module, "__file__", None)
            if type(filename) is str:
                path = Path(filename)
                if path.is_relative_to(ROOT):
                    paths.add(path)
    return {
        path.relative_to(ROOT).as_posix(): {
            "sha256": contract.digest(path.read_bytes())
        }
        for path in paths
    }


def test_original_actual_host_admission_accepts_exact_typed_helper_before_lazy_import(
    monkeypatch,
):
    monkeypatch.delitem(
        sys.modules, "scripts.retained0017_catalog_sql_types_v1", raising=False
    )
    assert Path(HELPER).name in contract.HOST_HELPERS
    contract.require_host_code(_actual_source_map())


@pytest.mark.parametrize("mutation", ["missing", "changed"])
def test_missing_or_changed_typed_helper_is_denied_even_before_its_lazy_import(
    monkeypatch, mutation
):
    monkeypatch.delitem(
        sys.modules, "scripts.retained0017_catalog_sql_types_v1", raising=False
    )
    files = _actual_source_map()
    if mutation == "missing":
        del files[HELPER]
    else:
        files[HELPER]["sha256"] = "0" * 64
    with pytest.raises((ValueError, KeyError)):
        contract.require_host_code(files)
