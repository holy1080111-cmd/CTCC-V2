"""Interactive local setup: hidden token input and GET-only real schema binding.

Creates no Notion pages/database/properties and never enables a worker or trades.
Token files are written outside Git/evidence roots with user-only permissions.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import hashlib
import json
import os
import uuid
import warnings
from datetime import UTC, datetime
from pathlib import Path

from pydantic import SecretStr

from app.trade_evidence import notion_adapter as notion
from app.trade_evidence import storage
from app.trade_evidence.notion_binding import (
    database_sources,
    external_secret_path,
    read_notion_resource,
    save_private_token,
    verify_binding,
)
from app.trade_evidence.runtime import isolated_client


def choose(entries, *, prompt, input_fn=input):
    if not entries:
        raise ValueError("notion_setup_required_field_missing")
    for index, item in enumerate(entries, 1):
        # JSON escaping prevents remote field names from injecting terminal controls.
        print(f"{index}: {json.dumps(item, ensure_ascii=True)}")
    selected = input_fn(prompt)
    if (
        not selected.isascii()
        or not selected.isdigit()
        or not 1 <= int(selected) <= len(entries)
    ):
        raise ValueError("notion_setup_selection_invalid")
    return entries[int(selected) - 1]


async def discover_destination(
    client, token, database_id, *, input_fn=input, source_id=None, property_names=None
):
    database_id = notion._uuid(database_id)
    database = await read_notion_resource(
        client, token, kind="databases", identity=database_id
    )
    source_ids = database_sources(database, database_id)
    selected = (
        notion._uuid(source_id)
        if source_id is not None
        else choose(list(source_ids), prompt="Data source number: ", input_fn=input_fn)
    )
    if selected not in source_ids:
        raise ValueError("notion_setup_source_not_in_database")
    if property_names is not None and set(property_names) != set(notion.ROLES):
        raise ValueError("notion_setup_exact_property_names_required")
    schema = await read_notion_resource(
        client, token, kind="data_sources", identity=selected
    )
    properties = schema.get("properties")
    if type(properties) is not dict or len(properties) > 256:
        raise ValueError("notion_setup_schema_invalid")
    pins = []
    for role in notion.ROLES:
        kind = "title" if role == "report_id" else "rich_text"
        entries = [
            {"name": name, "property_id": item.get("id"), "kind": kind}
            for name, item in properties.items()
            if type(item) is dict and item.get("type") == kind
        ]
        if property_names is None:
            item = choose(
                entries, prompt=f"Property number for {role}: ", input_fn=input_fn
            )
        else:
            matches = [item for item in entries if item["name"] == property_names[role]]
            if len(matches) != 1:
                raise ValueError("notion_setup_property_name_or_type_mismatch")
            item = matches[0]
        pins.append(notion.NotionPropertyPin(role=role, **item))
    destination = notion.NotionDestination(
        database_id=database_id, data_source_id=selected, properties=tuple(pins)
    )
    notion._schema(schema, destination)
    receipt = await verify_binding(client, token, destination)
    return destination, receipt


def write_local_record(path: Path, raw: bytes):
    with path.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    if path.read_bytes() != raw:
        raise ValueError("notion_setup_local_readback_failed")


async def configure(args):
    root = storage._root_path(args.outbox_root)
    if not root.is_dir():
        raise ValueError("notion_setup_outbox_root_missing")
    directory = args.private_directory
    external_secret_path(directory / "location-check.token")
    if directory.is_relative_to(root):
        raise ValueError("notion_secret_inside_outbox")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        token = SecretStr(getpass.getpass("Notion runtime REST token (hidden): "))
    notion._secret(token)
    database_id = (
        args.database_id or input("Exact database ID (UUID, not URL): ").strip()
    )
    property_names = {
        role: getattr(args, role + "_property_name") for role in notion.ROLES
    }
    if all(value is None for value in property_names.values()):
        property_names = None
    elif any(value is None for value in property_names.values()):
        raise ValueError("notion_setup_exact_property_names_required")
    async with isolated_client() as client:
        destination, proof = await discover_destination(
            client,
            token,
            database_id,
            source_id=args.data_source_id,
            property_names=property_names,
        )
    identity = uuid.uuid4().hex
    token_file = directory / f"notion-{identity}.token"
    destination_file = directory / f"notion-{identity}.destination.json"
    receipt_file = directory / f"notion-{identity}.setup-receipt.json"
    save_private_token(token_file, token)
    raw = destination.model_dump_json().encode()
    write_local_record(destination_file, raw)
    digest = hashlib.sha256(raw).hexdigest()
    receipt = {
        "schema": "ctcc.notion_setup.v1",
        "verified_at": datetime.now(UTC).isoformat(),
        "destination_file_sha256": digest,
        "readback": proof,
        "http_methods": ["GET"],
        "notion_writes": 0,
        "worker_enabled": False,
        "execution_authority": False,
    }
    write_local_record(
        receipt_file, (json.dumps(receipt, sort_keys=True) + "\n").encode()
    )
    print("Verified real schema; worker remains disabled. Runtime configuration:")
    print("NOTION_OUTBOX_ENABLED=false")
    print(f"NOTION_OUTBOX_ROOT={root}")
    print(f"NOTION_OUTBOX_TOKEN_FILE={token_file}")
    print(f"NOTION_OUTBOX_DESTINATION_FILE={destination_file}")
    print(f"NOTION_OUTBOX_DESTINATION_SHA256={digest}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--outbox-root", type=Path, required=True)
    parser.add_argument("--database-id")
    parser.add_argument("--data-source-id")
    for role in notion.ROLES:
        parser.add_argument("--" + role.replace("_", "-") + "-property-name")
    default = (
        Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local/share"))
        / "CTCC/private-notion"
    )
    parser.add_argument("--private-directory", type=Path, default=default)
    args = parser.parse_args()
    try:
        asyncio.run(configure(args))
    except (KeyboardInterrupt, EOFError):
        print("NOTION_SETUP_CANCELLED")
        raise SystemExit(1) from None
    except Exception:  # noqa: BLE001 -- Never print tokens, remote bodies, URLs or exception details.
        print("NOTION_SETUP_FAILED_CLOSED; no Notion pages created or worker enabled")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
