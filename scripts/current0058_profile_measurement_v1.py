"""Retain source-bound actual isolated DB0057 role/catalog observations only."""

from __future__ import annotations

import os
import re
from datetime import UTC, datetime
from pathlib import Path

from scripts import current0058_hermetic_contract_v1 as contract
from scripts.evidence_json import read_evidence_json

ENV = "CTCC_DB0058_PROFILE_EVIDENCE_DIR"
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
        header[0] == ["0058"]
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
            profile=profiles.DB0058,
        )
        graph = await session.scalar(text(inherited.actor_catalog_sql(profiles.DB0058)))
        native = inherited.observe_writer_0056(graph, profile=profiles.DB0058)
        g12_observation = g12.observe_catalog_0057(
            graph["g12_0057"],
            owner=graph["issued"]["owner"],
            writer=oids[0],
            profile=profiles.DB0058,
        )
    receipt = {
        "schema": "ctcc.current0058.profile_measurement.v1",
        "source": source,
        "case": case,
        "database_head": "0058",
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
        "native_closure_body_sha256": native["native_closure_body_sha256"],
        "native_submission_body_sha256": native["native_submission_body_sha256"],
        "native_protective_flat_body_sha256": native[
            "native_protective_flat_body_sha256"
        ],
        "native_protective_indexes_sha256": native["native_protective_indexes_sha256"],
        "actual_protective_catalog_sha256": contract.digest(
            contract.canonical(graph["native_protective58"])
        ),
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
    from app.domain import native_protective_hold_schema_v58 as protective
    from app.domain import native_submission_binding_schema_v58 as submission

    contract._need(type(raw) is bytes and 0 < len(raw) <= 32_768)
    contract._need(case in {name for _, name in contract.CASES})
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
        "native_closure_body_sha256",
        "native_submission_body_sha256",
        "native_protective_flat_body_sha256",
        "native_protective_indexes_sha256",
        "actual_protective_catalog_sha256",
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
        value["schema"] == "ctcc.current0058.profile_measurement.v1"
        and value["source"] == source
        and value["case"] == case
        and value["database_head"] == "0058"
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
                "native_closure_body_sha256",
                "native_submission_body_sha256",
                "native_protective_flat_body_sha256",
                "native_protective_indexes_sha256",
                "actual_protective_catalog_sha256",
            )
        )
    )
    contract._need(
        value["native_closure_body_sha256"] == protective.CURRENT_BODY_SHA256
        and value["native_protective_flat_body_sha256"]
        == protective.PREDICATE_BODY_SHA256
        and value["native_submission_body_sha256"] == submission.CURRENT_BODY_SHA256
    )
    return value


def _verify_custody_result(result):
    keys = {
        "database_head",
        "writer_oid",
        "catalog_sha256",
        "transcript_sha256",
        "claim_sha256",
        "publication_sha256",
        "stages",
        "claim_count",
        "publication_count",
        "synthetic_private_owner_handoff",
        "qualification_performed",
        "execution_authority",
    }
    contract._need(
        type(result) is dict
        and set(result) == keys
        and result["database_head"] == "0058"
    )
    contract._need(
        type(result["writer_oid"]) is int and 0 < result["writer_oid"] < 2**32
    )
    contract._need(
        all(
            type(result[key]) is str
            and re.fullmatch(r"[a-f0-9]{64}", result[key]) is not None
            for key in (
                "catalog_sha256",
                "transcript_sha256",
                "claim_sha256",
                "publication_sha256",
            )
        )
    )
    contract._need(
        all(
            type(result[key]) is int and result[key] == 1
            for key in ("claim_count", "publication_count")
        )
        and result["synthetic_private_owner_handoff"] is True
        and result["qualification_performed"] is result["execution_authority"] is False
    )
    expected = (
        "claim_write",
        "claim_read",
        "claim_read",
        "publication_write",
        "claim_read",
        "publication_read",
    )
    stages = result["stages"]
    contract._need(type(stages) is list and len(stages) == len(expected))
    previous, transactions = None, set()
    for stage, name in zip(stages, expected, strict=True):
        contract._need(
            type(stage) is dict
            and set(stage)
            == {
                "stage",
                "database_head",
                "writer_oid",
                "backend_pid",
                "transaction_id",
                "started_at",
                "completed_at",
            }
        )
        contract._need(
            stage["stage"] == name
            and stage["database_head"] == "0058"
            and type(stage["writer_oid"]) is int
            and stage["writer_oid"] == result["writer_oid"]
            and type(stage["backend_pid"]) is int
            and 0 < stage["backend_pid"] < 2**31
        )
        xid = stage["transaction_id"]
        contract._need(
            type(xid) is str
            and re.fullmatch(r"[1-9][0-9]{0,19}", xid) is not None
            and xid not in transactions
        )
        times = []
        for key in ("started_at", "completed_at"):
            contract._need(type(stage[key]) is str and 20 <= len(stage[key]) <= 40)
            at = datetime.fromisoformat(stage[key])
            contract._need(at.tzinfo is UTC)
            times.append(at)
        contract._need(
            times[0] <= times[1] and (previous is None or previous <= times[0])
        )
        previous = times[1]
        transactions.add(xid)
    return result


def verify_custody_measurement(raw, *, source):
    contract._need(type(raw) is bytes and 0 < len(raw) <= 32_768)
    value = read_evidence_json(raw)
    contract._need(
        type(value) is dict
        and set(value)
        == {
            "schema",
            "source",
            "case",
            "database_head",
            "custody",
            "execution_authority",
            "trading_acceptance",
        }
        and contract.canonical(value) == raw
    )
    contract._need(
        value["schema"] == "ctcc.current0058.g12_custody_component.v1"
        and value["source"] == source
        and value["case"] == contract.CUSTODY_CASE[1]
        and value["database_head"] == "0058"
        and value["execution_authority"] is value["trading_acceptance"] is False
    )
    _verify_custody_result(value["custody"])
    return value


async def record_custody_if_requested(result, *, case):
    output = os.environ.get(ENV)
    if output is None:
        return
    contract._need(output == "/validation-results" and case == contract.CUSTODY_CASE[1])
    _verify_custody_result(result)
    identity = read_evidence_json(_IDENTITY.read_bytes())
    files_raw = _FILES.read_bytes()
    source = contract.source_identity(
        identity, read_evidence_json(files_raw), files_raw
    )
    value = {
        "schema": "ctcc.current0058.g12_custody_component.v1",
        "source": source,
        "case": case,
        "database_head": "0058",
        "custody": result,
        "execution_authority": False,
        "trading_acceptance": False,
    }
    raw = contract.canonical(value)
    verify_custody_measurement(raw, source=source)
    root = Path(output)
    contract._need(root.is_dir() and not root.is_symlink())
    path = root / Path(contract.CUSTODY_REPORT).name
    with path.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    contract._need(path.read_bytes() == raw)
