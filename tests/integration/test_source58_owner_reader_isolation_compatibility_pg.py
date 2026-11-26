"""Actual TLS observations compared with the exact pre-isolation reader pair."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import sys
import traceback
from pathlib import Path

import pytest
from sqlalchemy import text

from app.domain import collector_current_0055_catalog_v1 as current55
from app.domain import collector_current_0056_inherited_catalog_v1 as current56
from scripts import native_owner_upgrade_0056_v1 as owner
from tests.integration import test_native_owner_upgrade_0056_v2_pg as lab

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
_actual57_lab = lab._actual57_lab
_actual56_owner_profile_lab = lab._actual56_owner_profile_lab
_actual55_v2_lab = lab._actual55_v2_lab
ROOT = Path(__file__).resolve().parents[2]
OLD = {
    "collector_current_0055_catalog_v1.py": "fa7e80105102a9eca72176c7c9243c604d275020fae37dd4fbd12f7a81d28652",
    "collector_current_0056_inherited_catalog_v1.py": "d2a489a59510e684b8b2efeea84929aacdd4f3b328a9678fffca78d9b94ea1ca",
}
NEW = {
    "collector_current_0055_catalog_v1.py": "e27ea4f7ff2577b81d3f6d920436f8054266629846549e074fed9ef1668d9a04",
    "collector_current_0056_inherited_catalog_v1.py": "7ef3e52af3eefd006a16ef0bc9d274ebf8d8e81917ad90c48994a78d39f1c3f1",
}

_LEGACY_REPLAY = """
import hashlib, json, sys
from pathlib import Path

root, project = map(Path, sys.argv[1:])
sys.path.insert(0, str(root))
sys.path.append(str(project))
from app.domain import collector_current_0055_catalog_v1 as old55
from app.domain import collector_current_0056_inherited_catalog_v1 as old56
assert old56.inherited is old55
args = json.loads(sys.stdin.buffer.read(6000001))
observed_source = {}
for module in (old55, old56):
    path = Path(module.__file__)
    assert path.parent == root / 'app/domain'
    observed_source[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
assert observed_source == args['source']

def observe55_writer(value):
    return old55.observe_actual_writer_catalog(value, read_only='on')

def rejects(callback, value):
    try:
        callback(value)
    except old55.Current0055CatalogError:
        return True
    return False

rows = {}
for head, owner, writer in (
    ('0055', old55.observe_actual_owner_catalog, observe55_writer),
    ('0056', old56.observe_owner_installation_0056, old56.observe_writer_0056),
):
    value = args['catalogs'][head]
    rows[head] = {
        'owner_rc': owner(value['owner_rc']), 'writer_rc': writer(value['writer_rc']),
        'owner_rr_denied': rejects(owner, value['owner_rr']),
        'writer_rr_denied': rejects(writer, value['writer_rr']),
    }
print(json.dumps(rows, sort_keys=True, separators=(',',':')))
"""


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _canonical(value):
    return json.dumps(
        value, sort_keys=True, ensure_ascii=True, separators=(",", ":")
    ).encode("ascii")


def _legacy_replay(snapshot, directory):
    shutil.copytree(
        ROOT / "app", directory / "app", ignore=shutil.ignore_patterns("__pycache__")
    )
    for name, digest in OLD.items():
        assert _sha((ROOT / "app/domain" / name).read_bytes()) == NEW[name]
        frozen = (
            ROOT / "tests/fixtures/source58_legacy_owner_reader" / (name + ".txt")
        ).read_bytes()
        assert _sha(frozen) == digest
        (directory / "app/domain" / name).write_bytes(frozen)
    raw = _canonical({"source": OLD, "catalogs": snapshot})
    assert 0 < len(raw) <= 6_000_000
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", _LEGACY_REPLAY, str(directory), str(ROOT)],
        input=raw,
        capture_output=True,
        check=False,
        timeout=35,
        env={"PATH": os.defpath, "LC_ALL": "C.UTF-8", "TZ": "UTC"},
        cwd=directory,
    )
    assert (
        result.returncode == 0 and not result.stderr and 0 < len(result.stdout) <= 65536
    )
    return json.loads(result.stdout)


async def _capture(engine, query, expected_isolation):
    async with engine.connect() as connection, connection.begin():
        if expected_isolation == "repeatable read":
            await connection.exec_driver_sql(owner._READBACK_SETTINGS[0])
        else:
            await connection.exec_driver_sql(
                "SET TRANSACTION ISOLATION LEVEL READ COMMITTED READ ONLY"
            )
        for command in owner._READBACK_SETTINGS[1:]:
            await connection.exec_driver_sql(command)
        await connection.exec_driver_sql("SET LOCAL synchronous_commit='on'")
        assert (
            await connection.scalar(
                text("""SELECT EXISTS(
            SELECT 1 FROM pg_catalog.pg_stat_ssl
            WHERE pid=pg_catalog.pg_backend_pid() AND ssl)""")
            )
            is True
        )
        value = await connection.scalar(text(query))
        for key in ("issued", "lineage"):
            assert value[key]["server"]["isolation"] == expected_isolation
            assert value[key]["server"]["read_only"] == "on"
        return value


def _writer55(value):
    return current55.observe_actual_writer_catalog(value, read_only="on")


def _admit(callback, value, *, phase):
    try:
        return callback(value)
    except current55.Current0055CatalogError as error:
        cause = error.__context__ or error
        coordinates = [
            (Path(frame.filename).name, frame.name, frame.lineno)
            for frame in traceback.extract_tb(cause.__traceback__)[-8:]
        ]
        pytest.fail(f"{phase}:{type(cause).__name__}:{coordinates}", pytrace=False)


def _publish(evidence):
    target = Path("/validation-results/reader-isolation-compatibility.json")
    assert os.environ["CTCC_OWNER_READER_ISOLATION_COMPATIBILITY"] == str(target)
    payload = _canonical(evidence)
    with target.open("xb") as stream:
        assert stream.write(payload) == len(payload)
        stream.flush()
        os.fsync(stream.fileno())
    assert target.read_bytes() == payload


async def test_actual_source58_rc_owner_writer_and_explicit_owner_rr_match_frozen_readers(
    _actual55_v2_lab,
    tmp_path,
):
    engine, selected, _pins, prepared = _actual55_v2_lab
    writer = selected.writer_factory.kw["bind"]
    snapshot, compat, records = {}, {}, {}
    for head, module, observe_owner, observe_writer in (
        ("0055", current55, current55.observe_actual_owner_catalog, _writer55),
        (
            "0056",
            current56,
            current56.observe_owner_installation_0056,
            current56.observe_writer_0056,
        ),
    ):
        if head == "0056":
            async with engine.begin() as connection:
                await connection.run_sync(owner._upgrade_exact_0055, *prepared[1:])
        raw = {
            "owner_rc": await _capture(
                engine, module.OWNER_CATALOG_SQL, "read committed"
            ),
            "writer_rc": await _capture(
                writer, module.ACTOR_CATALOG_SQL, "read committed"
            ),
            "owner_rr": await _capture(
                engine, module.OWNER_CATALOG_SQL, "repeatable read"
            ),
            "writer_rr": await _capture(
                writer, module.ACTOR_CATALOG_SQL, "repeatable read"
            ),
        }
        assert raw["owner_rc"]["issued"]["actor"] != raw["writer_rc"]["issued"]["actor"]
        snapshot[head] = raw
        with pytest.raises(current55.Current0055CatalogError):
            observe_owner(raw["owner_rr"])
        with pytest.raises(current55.Current0055CatalogError):
            observe_writer(raw["writer_rr"])
        with pytest.raises(current55.Current0055CatalogError):
            module._observe_owner_repeatable_read_catalog_v1(raw["owner_rc"])
        own_rc = _admit(observe_owner, raw["owner_rc"], phase=f"{head}_owner_rc")
        writer_rc = _admit(observe_writer, raw["writer_rc"], phase=f"{head}_writer_rc")
        own_rr = _admit(
            module._observe_owner_repeatable_read_catalog_v1,
            raw["owner_rr"],
            phase=f"{head}_owner_rr",
        )
        assert own_rr == own_rc
        assert all(
            row["execution_authority"] is False for row in (own_rc, writer_rc, own_rr)
        )
        compat[head] = {
            "owner_rc": own_rc,
            "writer_rc": writer_rc,
            "owner_rr_denied": True,
            "writer_rr_denied": True,
        }
        records[head] = {
            "owner_rc_definitions_sha256": own_rc["native_definitions_sha256"],
            "writer_rc_definitions_sha256": writer_rc["native_definitions_sha256"],
            "owner_rr_definitions_sha256": own_rr["native_definitions_sha256"],
            "legacy_rc_full_observation_exact": True,
            "legacy_and_default_rr_refused": True,
            "explicit_owner_rr_refuses_rc": True,
        }
    assert await asyncio.to_thread(_legacy_replay, snapshot, tmp_path) == compat
    evidence = {
        "schema": "ctcc.source58.owner_reader_isolation_compatibility.v1",
        "observed_tls_sessions": 8,
        "frozen_source_commit": "93e648fafbd79fa64bad98f6ab8ebede8653c417",
        "frozen_reader_sha256": OLD,
        "actual_reader_sha256": NEW,
        "heads": records,
        "execution_authority": False,
    }
    await asyncio.to_thread(_publish, evidence)
