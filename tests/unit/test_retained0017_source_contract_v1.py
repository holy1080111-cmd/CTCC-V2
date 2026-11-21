"""Pure source graph/shape and no-vendor host replay; no SQL or migration."""

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from scripts import retained0017_catalog_baseline_v1 as catalog
from scripts import retained0017_source_contract_v1 as subject

ROOT = Path(__file__).resolve().parents[2]
PREFIX = "7a998b64b61f5bb30d65c405a995f3d00ffc12d15344b7cbda309f1c58b17cd2"
COLUMNS = "d0e37ae0fc5b6ba6ac37076448ccbb92250be099e6bee85090995322b054db0c"


def test_current_contract_matches_independently_recorded_legacy_graph_and_shape():
    # Recorded from the existing Alembic graph/canonical archive hash path
    # before adoption; minimum shape is DATA, not full schema acceptance.
    value = catalog.source17_contract_v1()
    assert len(value["columns"]) == 48 and value["source17_prefix_sha256"] == PREFIX
    assert (
        hashlib.sha256(
            json.dumps(value["columns"], sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        == COLUMNS
    )
    assert "control_revision" not in value["columns"]["demo_automation_state"]


def test_comparator_host_replay_is_genuinely_stdlib_only_without_site_packages():
    code = """import json,sys
sys.path.insert(0,sys.argv[1])
from scripts.retained0017_catalog_baseline_v1 import source17_contract_v1
value=source17_contract_v1()
assert not any(name.split('.')[0] in {'alembic','sqlalchemy','pydantic','app'} for name in sys.modules)
print(json.dumps({'prefix':value['source17_prefix_sha256'],'tables':len(value['columns'])}))
"""
    completed = subprocess.run(
        [sys.executable, "-I", "-S", "-c", code, str(ROOT)],
        capture_output=True,
        check=False,
        timeout=15,
    )
    assert completed.returncode == 0 and completed.stderr == b""
    assert json.loads(completed.stdout) == {"prefix": PREFIX, "tables": 48}


@pytest.fixture
def copied_source():
    # Public source only. Short, new owned scratch avoids unrelated MAX_PATH
    # failures when the surrounding acceptance evidence directory is deep.
    with tempfile.TemporaryDirectory(prefix="ctcc_r17src_") as directory:
        root = Path(directory).resolve()
        shutil.copyfile(ROOT / "alembic.ini", root / "alembic.ini")
        (root / "migrations").mkdir()
        shutil.copytree(
            ROOT / "migrations/versions",
            root / "migrations/versions",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        yield root


def test_literal_prefix_is_parsed_without_executing_even_top_level_migration_code(
    copied_source,
):
    path = next((copied_source / "migrations/versions").glob("0001_*.py"))
    with path.open("a", encoding="utf-8") as output:
        output.write("\nraise RuntimeError('source_metadata_must_not_execute')\n")
    value = subject.source17_contract_from_root_v1(copied_source)
    assert value["source17_prefix_sha256"] != PREFIX
    assert value["columns"] == catalog.source17_contract_v1()["columns"]


@pytest.mark.parametrize(
    "drift", ["missing", "branch", "computed_revision", "duplicate", "layout"]
)
def test_unknown_graph_or_layout_cannot_be_accepted_as_the_reviewed_prefix(
    copied_source, drift
):
    versions = copied_source / "migrations/versions"
    if drift == "layout":
        path = copied_source / "alembic.ini"
        raw = path.read_text()
        assert "script_location = migrations" in raw
        path.write_text(
            raw.replace("script_location = migrations", "script_location = elsewhere")
        )
    else:
        path = next(versions.glob("0058_*.py"))
        if drift == "missing":
            path.unlink()
        elif drift == "duplicate":
            shutil.copyfile(path, versions / "0058_unreviewed_duplicate.py")
        else:
            raw = path.read_text()
            old = 'down_revision = "0057"' if drift == "branch" else 'revision = "0058"'
            new = (
                'down_revision = "0056"'
                if drift == "branch"
                else 'revision = str("0058")'
            )
            assert old in raw
            path.write_text(raw.replace(old, new))
    with pytest.raises(subject.Retained0017SourceContractRefused):
        subject.source17_contract_from_root_v1(copied_source)
