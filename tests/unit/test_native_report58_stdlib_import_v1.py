"""Exercise the genuine no-site-packages host import, without SQL or authority."""

import json
import subprocess
import sys
from pathlib import Path

import pytest


def test_actual_source58_specs_import_without_site_packages_and_keep_exact_bytes():
    root = Path(__file__).resolve().parents[2]
    code = "\n".join(
        (
            "import sys,json",
            "sys.path.insert(0," + repr(str(root)) + ")",
            "from app.domain import native_protective_report_schema_v58 as report",
            "from app.domain import native_submission_binding_schema_v58 as binding",
            "report.statements()",
            "binding.statements()",
            "assert 'pydantic' not in sys.modules and 'sqlalchemy' not in sys.modules",
            "print(json.dumps([report.REPORT_POLICY_SHA256,report.ELIGIBLE_BODY_SHA256,binding.CURRENT_BODY_SHA256]))",
        )
    )
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-c", code],
        capture_output=True,
        check=True,
        timeout=20,
    )
    assert result.stderr == b""
    assert json.loads(result.stdout) == [
        "f98ef8019797c0fd7b18abd320daceb1b1f607fe340df4c80e96eefbf18eb542",
        "7a88b3edd413f6b6bae69b9acada0055a13bddd9200e6a8a6c8326f46d7978b1",
        "119d6b36169a35bcbef18ea57357839cdb28384d4c009deefcd2bd62e81c5ec8",
    ]


def test_host_readback_rejects_a_loaded_spec_from_different_source_bytes():
    from app.domain import native_protective_report_schema_v58 as report
    from scripts import current0058_full_hermetic_contract_v1 as contract

    root = Path(__file__).resolve().parents[2]
    files = {
        path.relative_to(root).as_posix(): {
            "sha256": contract.digest(path.read_bytes())
        }
        for path in (root / "app").rglob("*.py")
    }
    contract._require_loaded_app_sources(files)
    assert (
        report.REPORT_POLICY_SHA256
        == "f98ef8019797c0fd7b18abd320daceb1b1f607fe340df4c80e96eefbf18eb542"
    )
    files["app/domain/native_protective_report_schema_v58.py"]["sha256"] = "0" * 64
    with pytest.raises(ValueError):
        contract._require_loaded_app_sources(files)
