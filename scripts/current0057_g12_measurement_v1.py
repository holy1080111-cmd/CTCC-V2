"""Retain source-bound actual isolated DB0057 role/catalog observations only."""

from __future__ import annotations

import os
import re
from pathlib import Path

from scripts import current0057_hermetic_contract as contract
from scripts.evidence_json import read_evidence_json

ENV = "CTCC_DB0057_G12_EVIDENCE_DIR"
_IDENTITY = Path("/ctcc-run-identity.json")
_FILES = Path("/ctcc-source-files.json")
_OWNER = """
SELECT ARRAY(SELECT version_num::text FROM public.alembic_version),
       pg_catalog.current_setting('server_version_num'),
       (SELECT system_identifier::text FROM pg_catalog.pg_control_system()),
       EXISTS(SELECT 1 FROM pg_catalog.pg_stat_ssl WHERE pid=pg_catalog.pg_backend_pid() AND ssl)
"""


async def record_profile_if_requested(owner, selected, *, case):
    output = os.environ.get(ENV)
    if output is None:
        return
    from sqlalchemy import text

    contract._need(
        output == "/validation-results" and case in {name for _, name in contract.CASES}
    )
    root = Path(output)
    contract._need(
        root.is_dir()
        and not root.is_symlink()
        and _IDENTITY.is_file()
        and _FILES.is_file()
    )
    identity = read_evidence_json(_IDENTITY.read_bytes())
    files_raw = _FILES.read_bytes()
    if type(identity) is dict and identity.get("source_contract_head") == "0058":
        from scripts.historical0057_on_current0058_observation_v1 import (
            historical_source_identity,
        )

        source = historical_source_identity(
            identity, read_evidence_json(files_raw), files_raw
        )
    else:
        source = contract.source_identity(
            identity, read_evidence_json(files_raw), files_raw
        )
    from app.domain import collector_current_0056_inherited_catalog_v1 as inherited
    from app.domain import g12_custody_catalog_0057_v1 as g12
    from app.domain import native_database_profile_v1 as profiles
    from scripts.current0057_native_g12_acl_v1 import (
        observe_current0057_g12_writer_profile_v1,
    )

    async with owner.connect() as connection:
        header = tuple((await connection.execute(text(_OWNER))).one())
    contract._need(
        header[0] == ["0057"]
        and header[1] == "170011"
        and type(header[2]) is str
        and re.fullmatch(r"[1-9][0-9]{0,19}", header[2]) is not None
        and header[3] is True
    )
    factories = (
        selected.writer_factory,
        selected.custody_factory,
        selected.frame_reader_factory,
    )
    names = tuple(factory.kw["bind"].url.username for factory in factories)
    oids = []
    for factory in factories:
        async with factory() as session, session.begin():
            await session.execute(text("SET TRANSACTION READ ONLY"))
            observed = tuple(
                (
                    await session.execute(
                        text(
                            "SELECT r.oid::bigint,current_user=session_user,EXISTS(SELECT 1 FROM pg_catalog.pg_stat_ssl WHERE pid=pg_catalog.pg_backend_pid() AND ssl) FROM pg_catalog.pg_roles r WHERE r.rolname=current_user"
                        )
                    )
                ).one()
            )
            contract._need(
                type(observed[0]) is int
                and observed[0] > 0
                and observed[1:] == (True, True)
            )
            oids.append(observed[0])
    contract._need(len(set(oids)) == 3)
    async with factories[0]() as session, session.begin():
        for setting in (
            "SET TRANSACTION READ ONLY",
            "SET LOCAL search_path TO pg_catalog,public,pg_temp",
            "SET LOCAL TIME ZONE 'UTC'",
            "SET LOCAL synchronous_commit='on'",
            "SET LOCAL lock_timeout='1000ms'",
            "SET LOCAL statement_timeout='10000ms'",
        ):
            await session.execute(text(setting))
        acl = await observe_current0057_g12_writer_profile_v1(
            await session.connection(),
            writer=names[0],
            custody=names[1],
            frame_reader=names[2],
            expected_writer_oid=oids[0],
        )
        graph = await session.scalar(text(inherited.actor_catalog_sql(profiles.DB0057)))
        native = inherited.observe_writer_0056(graph, profile=profiles.DB0057)
        g12_observation = g12.observe_catalog_0057(
            graph["g12_0057"], owner=graph["issued"]["owner"], writer=oids[0]
        )
    receipt = {
        "schema": "ctcc.current0057.g12_profile_measurement.v1",
        "source": source,
        "case": case,
        "database_head": "0057",
        "server_version_num": header[1],
        "cluster_system_identifier_sha256": contract.digest(header[2].encode("ascii")),
        "actual_tls": True,
        "role_oids": dict(
            zip(("writer", "custody", "frame_reader"), oids, strict=True)
        ),
        "grant_statements": acl["grant_statements"],
        "acl_shape_sha256": acl["acl_shape_sha256"],
        "actual_g12_catalog_sha256": g12_observation["catalog_sha256"],
        "g12_definitions_sha256": g12_observation["definitions_sha256"],
        "inherited_native_definitions_sha256": native["native_definitions_sha256"],
        "startup_admission": False,
        "execution_authority": False,
        "trading_acceptance": False,
    }
    raw = contract.canonical(receipt)
    verify_measurement(raw, source=source, case=case)
    index = tuple(name for _, name in contract.CASES).index(case)
    path = root / Path(contract.REPORTS[index]).name
    with path.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    contract._need(path.read_bytes() == raw)


def verify_measurement(raw, *, source, case):
    contract._need(type(raw) is bytes and 0 < len(raw) <= 16_384)
    value = read_evidence_json(raw)
    expected = {
        "schema",
        "source",
        "case",
        "database_head",
        "server_version_num",
        "cluster_system_identifier_sha256",
        "actual_tls",
        "role_oids",
        "grant_statements",
        "acl_shape_sha256",
        "actual_g12_catalog_sha256",
        "g12_definitions_sha256",
        "inherited_native_definitions_sha256",
        "startup_admission",
        "execution_authority",
        "trading_acceptance",
    }
    contract._need(
        type(value) is dict
        and set(value) == expected
        and contract.canonical(value) == raw
    )
    contract._need(
        value["schema"] == "ctcc.current0057.g12_profile_measurement.v1"
        and value["source"] == source
        and value["case"] == case
        and value["database_head"] == "0057"
        and value["server_version_num"] == "170011"
        and value["actual_tls"] is True
    )
    contract._need(
        type(value["role_oids"]) is dict
        and set(value["role_oids"]) == {"writer", "custody", "frame_reader"}
        and len(set(value["role_oids"].values())) == 3
        and all(type(v) is int and 0 < v < 2**32 for v in value["role_oids"].values())
    )
    contract._need(
        type(value["grant_statements"]) is int
        and value["grant_statements"] == 44
        and all(
            value[k] is False
            for k in ("startup_admission", "execution_authority", "trading_acceptance")
        )
    )
    contract._need(
        all(
            type(value[k]) is str
            and re.fullmatch(r"[a-f0-9]{64}", value[k]) is not None
            for k in (
                "cluster_system_identifier_sha256",
                "acl_shape_sha256",
                "actual_g12_catalog_sha256",
                "g12_definitions_sha256",
                "inherited_native_definitions_sha256",
            )
        )
    )
    return value
