"""Historical source identity and separate58 DATA catalog, without SQL/venue."""

import hashlib
import importlib.util
from pathlib import Path

from app.domain import native_closed_report_catalog_v55 as historical
from app.domain import native_protective_report_catalog_v58 as current

ROOT = Path(__file__).resolve().parents[2]


def test_actual0055_extension_source_identity_stays_exact_under_source58():
    path = ROOT / "migrations/versions/0055_operator_recovery_acknowledgements.py"
    spec = importlib.util.spec_from_file_location("declared_report_source0055", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.report_catalog is historical
    assert (
        hashlib.sha256(Path(historical.__file__).read_bytes()).hexdigest()
        == "7544d567232005bc289d6bff173724574649af2936d3881ddb92795ccd22b70f"
    )
    module._extension_identity()


def test_explicit58_catalog_shares_frozen_relation_rules_but_expects_new_body():
    assert current.CATALOG_SQL == historical.CATALOG_SQL
    assert current._COLUMNS is historical._COLUMNS
    assert current._CONSTRAINTS is historical._CONSTRAINTS
    assert current._INDEXES is historical._INDEXES
    assert current._RIGHTS is historical._RIGHTS
    assert current.current.ELIGIBLE_BODY_SHA256 != historical.schema.body_sha(
        historical.schema.ELIGIBLE_SQL, "$ctcc$"
    )
    current.current.statements()
