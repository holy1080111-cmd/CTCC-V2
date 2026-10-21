"""Remote isolated PostgreSQL parser/catalog evidence for reviewed DB0046 only."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace


def run(args, *, raw=None):
    result = subprocess.run(args, input=raw, capture_output=True, timeout=60, check=False)
    if result.returncode:
        raise RuntimeError("isolated_schema_probe_command_failed")
    return result.stdout


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--container", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    migration = root / "migrations/versions/0046_public_runtime_bundle_custody.py"
    statements = []
    shim = ModuleType("alembic")
    shim.op = SimpleNamespace(execute=statements.append)
    sys.modules["alembic"] = shim
    spec = importlib.util.spec_from_file_location("reviewed_0046", migration)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.revision == "0046" and module.down_revision == "0045"
    module.upgrade()
    psql = [
        "docker",
        "exec",
        "-i",
        args.container,
        "psql",
        "-X",
        "-qAt",
        "-v",
        "ON_ERROR_STOP=1",
        "-U",
        "ctcc_probe",
        "-d",
        "ctcc_probe",
    ]
    ddl = ("BEGIN;\n" + ";\n".join(statements) + ";\nCOMMIT;\n").encode()
    run(psql, raw=ddl)
    query = """
    SELECT jsonb_build_object(
      'server_version',current_setting('server_version'),
      'constraints',(SELECT jsonb_agg(jsonb_build_object(
        'name',conname,'definition',pg_get_constraintdef(oid,true),
        'definition_sha256',encode(sha256(convert_to(pg_get_constraintdef(oid,true),'UTF8')),'hex'))
        ORDER BY conname) FROM pg_constraint
        WHERE conrelid='public.public_runtime_raw_bundles_v1'::regclass),
      'functions',(SELECT jsonb_agg(jsonb_build_object(
        'name',proname,'body_sha256',encode(sha256(convert_to(prosrc,'UTF8')),'hex'))
        ORDER BY proname) FROM pg_proc WHERE pronamespace='public'::regnamespace
        AND proname LIKE 'public_runtime_bundle_v1_%'),
      'triggers',(SELECT jsonb_agg(jsonb_build_object(
        'name',tgname,'type',tgtype,'enabled',tgenabled) ORDER BY tgname)
        FROM pg_trigger WHERE tgrelid='public.public_runtime_raw_bundles_v1'::regclass
        AND NOT tgisinternal));
    """
    catalog = json.loads(run(psql, raw=query.encode()))
    if len(catalog["constraints"]) != 4 or len(catalog["functions"]) != 4:
        raise RuntimeError("isolated_schema_probe_inventory_mismatch")
    args.output.mkdir(parents=True, exist_ok=False)
    receipt = {
        "scope": "isolated_db0046_postgresql_parser_catalog_only",
        "head": run(["git", "rev-parse", "HEAD"]).decode().strip(),
        "tree": run(["git", "rev-parse", "HEAD^{tree}"]).decode().strip(),
        "migration_sha256": hashlib.sha256(migration.read_bytes()).hexdigest(),
        "catalog": catalog,
        "external_order_writes": 0,
        "execution_authority": False,
        "full_migration_acceptance": False,
    }
    raw = json.dumps(receipt, sort_keys=True, indent=2).encode() + b"\n"
    with (args.output / "catalog-readback.json").open("xb") as stream:
        stream.write(raw)
    print(
        json.dumps(
            {
                "receipt_sha256": hashlib.sha256(raw).hexdigest(),
                "migration_sha256": receipt["migration_sha256"],
            }
        )
    )


if __name__ == "__main__":
    main()
