"""Exercise the real default entry point before any application import."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/container_startup_0058_v1.py"
CHILD = """
import importlib.abc, json, os, runpy, sys
request = json.loads(sys.stdin.read())
os.environ.clear()
os.environ.update(request)
class Forbidden(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'app','alembic','uvicorn','sqlalchemy','asyncpg','httpx','ccxt'}:
            raise AssertionError('application_or_provider_import_attempted')
sys.meta_path.insert(0, Forbidden())
runpy.run_path(sys.argv[1], run_name='__main__')
"""


def _paper_environment():
    # Independent configuration copied from the actual rendered Paper profile.
    template = yaml.safe_load(
        (ROOT / "config/compose.local-analysis.template.yaml").read_text("utf-8")
    )
    return dict(template["services"]["api"]["environment"])


def _run(environment):
    completed = subprocess.run(
        [sys.executable, "-I", "-S", "-c", CHILD, str(SCRIPT)],
        input=json.dumps(environment),
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )
    assert completed.returncode == 78, completed.stderr
    assert completed.stderr == ""
    document = json.loads(completed.stdout)
    assert set(document) == {"status", "code"}
    assert document["status"] == "REFUSED"
    return document["code"]


@pytest.mark.parametrize(
    "mode", [None, "", "demo", "live", "offline", "paper", "persistent_paper "]
)
def test_default_and_unsupported_modes_stop_before_application_import(mode):
    environment = _paper_environment()
    if mode is None:
        environment.pop("CTCC_CONTAINER_MODE_V1")
    else:
        environment["CTCC_CONTAINER_MODE_V1"] = mode
    assert _run(environment) == (
        "container_mode_required"
        if mode in (None, "")
        else "container_mode_unsupported"
    )


@pytest.mark.parametrize(
    "name,value",
    [
        ("ENVIRONMENT", "development"),
        ("TRADING_MODE", "okx_demo"),
        ("WEB_CONCURRENCY", "2"),
        ("CTCC_LOCAL_ANALYSIS_ONLY_V1", "false"),
        ("PAPER_PERSISTENCE_ENABLED", "false"),
        ("CTCC_NATIVE_ONLY_LIFESPAN_V1", "true"),
        ("CTCC_NATIVE_DEMO_MANUAL_LIFESPAN_0056_V1", "true"),
        ("OKX_WS_ENABLED", "true"),
        ("PAPER_AUTO_TICKS", "true"),
        ("AUTO_TRADE", "true"),
        ("LIVE_TRADING", "true"),
        ("OKX_DEMO_ALLOW_ORDER_WRITES", "true"),
        ("OKX_LIVE_ENABLED", "true"),
        ("NOTION_OUTBOX_ENABLED", "true"),
        ("SUBMISSION_OUTBOX_ENABLED", "true"),
        ("OKX_DEMO_API_KEY", "synthetic_key_do_not_echo"),
        ("OKX_LIVE_API_SECRET", "synthetic_secret_do_not_echo"),
    ],
)
def test_legacy_or_unsafe_configuration_never_imports_services(name, value):
    environment = _paper_environment()
    environment[name] = value
    assert _run(environment) == "container_configuration_refused"


def test_missing_explicit_safety_switch_is_not_a_safe_default():
    environment = _paper_environment()
    environment.pop("PAPER_AUTO_TICKS")
    assert _run(environment) == "container_configuration_refused"


def test_closed_paper_configuration_has_no_head_only_or_receipt_bypass():
    environment = _paper_environment()
    environment.update(
        CTCC_DATABASE_HEAD="0058",
        CTCC_SCHEMA_VERIFIED="true",
        CTCC_CONTAINER_ADMITTED="true",
    )
    assert _run(environment) == "container_paper_source_admission_pending"


def test_container_and_generated_profile_use_same_no_ddl_entry_point():
    dockerfile = (ROOT / "Dockerfile").read_text("utf-8")
    command = next(
        line.removeprefix("CMD ")
        for line in dockerfile.splitlines()
        if line.startswith("CMD ")
    )
    assert json.loads(command) == [
        "python",
        "-B",
        "-m",
        "scripts.container_startup_0058_v1",
    ]
    assert "alembic upgrade" not in dockerfile
    template = yaml.safe_load(
        (ROOT / "config/compose.local-analysis.template.yaml").read_text("utf-8")
    )
    command = template["services"]["api"]["command"][0]
    assert "exec python -B -m scripts.container_startup_0058_v1" in command
    assert "alembic" not in command and "uvicorn" not in command
    compose = yaml.safe_load((ROOT / "compose.yaml").read_text("utf-8"))
    assert "command" not in compose["services"]["api"]
    assert compose["services"]["api"]["restart"] == "no"
