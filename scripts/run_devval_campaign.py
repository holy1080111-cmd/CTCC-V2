"""Explicit operator entry point for prospective D/V source collection."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import ssl
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.database.repositories.public_receipt_post_read_observation import (
    PublicReceiptPostReadRepository,
)
from app.database.repositories.public_receipt_witness import (
    PublicReceiptWitnessRepository,
)
from app.database.repositories.public_runtime_bundle_custody import (
    RuntimeBundleCustodyRepository,
)
from app.domain.source_primitives import canonical, decode, sha
from app.mie.validation.development_validation_source_collection_v1 import _root_context
from app.mie.validation.prospective_devval_campaign_batch_v1 import _MAX_BATCH_BYTES
from app.mie.validation.prospective_devval_campaign_custody_v1 import (
    DevvalCampaignCustodyRepositoryV1,
)
from app.mie.validation.prospective_devval_campaign_plan_v1 import (
    _MAX_MANIFEST_BYTES,
    ProspectiveDevvalCampaignPlanV1,
    freeze_prospective_devval_campaign_v1,
    verify_prospective_devval_campaign_v1,
)
from app.mie.validation.prospective_devval_campaign_replay_v1 import (
    _read_root,
    replay_prospective_devval_campaign_v1,
)
from app.mie.validation.prospective_devval_campaign_run_v1 import (
    _retain,
    run_prospective_devval_campaign_v1,
)
from app.mie.validation.prospective_devval_campaign_study_v1 import (
    _MAX_STUDY_BYTES,
    DevvalCampaignStudyPlanV1,
    _prepare_study,
)
from app.trade_evidence.native_private_root_v2 import require_private_evidence_base_v2
from scripts.devval_campaign_source import (
    DevvalSourceCheckpointError,
    verify_source_checkpoint,
)

_CAMPAIGN_ENV = "CTCC_DEVVAL_CAMPAIGN_DATABASE_URL"
_SOURCE_ENVS = (
    "CTCC_DEVVAL_WITNESS_DATABASE_URL",
    "CTCC_DEVVAL_POST_READ_DATABASE_URL",
    "CTCC_DEVVAL_RUNTIME_CUSTODY_DATABASE_URL",
)
_MODES = ("freeze", "publish", "acknowledge", "observe", "collect", "replay")


class CampaignCommandError(ValueError):
    """Fixed operator refusal without payload, credential, or endpoint details."""


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise CampaignCommandError("devval_campaign_command_invalid")


def _arguments(arguments):
    parser = _Parser(description=__doc__, allow_abbrev=False)
    parser.add_argument("mode", choices=_MODES)
    parser.add_argument("--source-archive", required=True, type=Path)
    parser.add_argument("--source-archive-sha256", required=True)
    for name in (
        "plan",
        "output",
        "campaign",
        "ca-file",
        "root",
        "batch-output-root",
        "study-plan",
        "study-output-root",
    ):
        parser.add_argument(f"--{name}", type=Path)
    for name in (
        "campaign-sha256",
        "root-binding-sha256",
        "completion-sha256",
        "study-plan-sha256",
    ):
        parser.add_argument(f"--{name}")
    for name in ("database-oid", "login-oid", "publisher-oid", "observer-oid"):
        parser.add_argument(f"--{name}", type=int)
    args = parser.parse_args(arguments)

    mode = args.mode
    used = (
        {"plan", "output"}
        if mode == "freeze"
        else {
            "campaign_sha256",
            "ca_file",
            "database_oid",
            "login_oid",
            "publisher_oid",
        }
    )
    if mode in ("publish", "acknowledge", "observe", "collect"):
        used.add("campaign")
    if mode in ("collect", "replay"):
        used.update(("root", "observer_oid"))
    if mode == "replay":
        used.add("completion_sha256")
        if args.batch_output_root is not None:
            used.add("batch_output_root")
        if args.study_plan is not None:
            used.update(("study_plan", "study_plan_sha256", "study_output_root"))
    if mode == "observe" and args.root_binding_sha256 is not None:
        used.update(("root_binding_sha256", "observer_oid"))
    present = {name for name, value in vars(args).items() if value is not None}
    if present != used | {"mode", "source_archive", "source_archive_sha256"}:
        raise CampaignCommandError("devval_campaign_command_invalid")
    if any(
        not re.fullmatch(r"[0-9a-f]{64}", vars(args)[name])
        for name in present
        if name.endswith("_sha256")
    ) or any(
        not 0 < vars(args)[name] < 2**32 for name in present if name.endswith("_oid")
    ):
        raise CampaignCommandError("devval_campaign_command_invalid")
    return args


def _read(path: Path) -> bytes:
    path = path.absolute()
    with _read_root(path.parent) as directory:
        return directory.read(path.name, _MAX_MANIFEST_BYTES)


def _freeze(args, checkpoint) -> dict:
    raw = _read(args.plan)
    decode(raw, _MAX_MANIFEST_BYTES)
    plan = ProspectiveDevvalCampaignPlanV1.model_validate_json(raw)
    if (
        plan.declared_source_commit != checkpoint["expected_source_commit"]
        or plan.declared_source_tree != checkpoint["expected_source_tree"]
        or plan.manifest_sha256 != checkpoint["expected_source_manifest_sha256"]
    ):
        raise CampaignCommandError("devval_campaign_source_changed")
    frozen = freeze_prospective_devval_campaign_v1(plan)
    output = args.output.absolute()
    require_private_evidence_base_v2(str(output.parent))
    with _root_context(output.parent) as directory:
        pin = _retain(directory, output.name, frozen.payload, _MAX_MANIFEST_BYTES)
    return {"campaign_sha256": pin, "planned_rows": plan.expected_rows}


def _url(name):
    raw = os.environ.get(name)
    if not raw:
        raise CampaignCommandError("devval_campaign_database_settings_missing")
    url = make_url(raw)
    if (
        url.drivername != "postgresql+asyncpg"
        or not url.host
        or not url.username
        or not url.database
        or url.query
        or not 1 <= (url.port or 5432) <= 65535
        or url.port == 0
    ):
        raise CampaignCommandError("devval_campaign_database_settings_invalid")
    return url


def _database(url):
    return (url.host.lower(), url.port or 5432, url.database)


@asynccontextmanager
async def _factories(args):
    mode = args.mode
    campaign = _url(_CAMPAIGN_ENV)
    sources = (
        tuple(_url(name) for name in _SOURCE_ENVS)
        if mode
        in (
            "collect",
            "replay",
        )
        else ()
    )
    if sources and (
        _database(sources[0]) != _database(sources[1])
        or sources[0].username == sources[1].username
        or _database(campaign) in {_database(url) for url in sources}
        or len({(*_database(url), url.username) for url in sources}) != len(sources)
    ):
        raise CampaignCommandError("devval_campaign_database_roles_overlap")
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    tls.load_verify_locations(cafile=str(args.ca_file))
    engines = []
    try:
        for url in (campaign, *sources):
            engines.append(
                create_async_engine(
                    url,
                    poolclass=NullPool,
                    connect_args={
                        "ssl": tls,
                        "port": url.port or 5432,
                        "password": url.password if url.password is not None else "",
                    },
                    hide_parameters=True,
                )
            )
        factories = tuple(
            async_sessionmaker(engine, autoflush=False, expire_on_commit=False)
            for engine in engines
        )
        if mode == "publish":
            role = "publisher"
        elif mode == "acknowledge" or (
            mode == "observe" and args.root_binding_sha256 is None
        ):
            role = "observer"
        else:
            role = "collector"
        repository = DevvalCampaignCustodyRepositoryV1(
            factories[0],
            role=role,
            host=campaign.host,
            port=campaign.port or 5432,
            database=campaign.database,
            login=campaign.username,
            expected_database_oid=args.database_oid,
            expected_login_oid=args.login_oid,
            expected_publisher_role_oid=args.publisher_oid,
            expected_observer_role_oid=args.observer_oid,
        )
        yield repository, factories[1:]
    finally:
        try:
            async with asyncio.timeout(10):
                results = await asyncio.gather(
                    *(engine.dispose() for engine in engines),
                    return_exceptions=True,
                )
        except TimeoutError:
            raise CampaignCommandError(
                "devval_campaign_database_cleanup_uncertain"
            ) from None
        if any(isinstance(result, BaseException) for result in results):
            raise CampaignCommandError("devval_campaign_database_cleanup_uncertain")


async def _run(args, checkpoint):
    mode = args.mode
    pin = args.campaign_sha256
    payload = None
    study_plan = None
    if mode != "replay":
        payload = _read(args.campaign)
        verify_prospective_devval_campaign_v1(
            payload, expected_campaign_sha256=pin, **checkpoint
        )
    if args.study_plan is not None:
        raw_study = _read(args.study_plan)
        decode(raw_study, _MAX_MANIFEST_BYTES)
        study_plan = DevvalCampaignStudyPlanV1.model_validate_json(raw_study)
        if (
            study_plan.campaign_sha256 != pin
            or study_plan.canonical_sha256() != args.study_plan_sha256
        ):
            raise CampaignCommandError("devval_campaign_study_plan_changed")
        if (
            args.batch_output_root is not None
            and args.batch_output_root.absolute() == args.study_output_root.absolute()
        ):
            raise CampaignCommandError("devval_campaign_study_output_conflict")
    common = {"expected_campaign_sha256": pin, **checkpoint}
    async with _factories(args) as (repository, sources):
        if mode == "publish":
            commit = await repository.publish(payload, **common)
            return {"campaign_sha256": pin, "publication_xid": commit.publication_xid}
        if mode == "acknowledge":
            readback = await repository.acknowledge(payload, **common)
            return _publication_receipt(pin, readback)
        if mode == "observe":
            if args.root_binding_sha256 is None:
                return _publication_receipt(
                    pin, await repository.read(payload, **common)
                )
            readback, claim = await repository.observe_dispatch(
                payload, root_binding_sha256=args.root_binding_sha256, **common
            )
            return {
                **_publication_receipt(pin, readback),
                "dispatch_xid": claim.dispatch_xid,
                "root_binding_sha256": claim.root_binding_sha256,
            }

        witness = PublicReceiptWitnessRepository(sources[0])
        observation = PublicReceiptPostReadRepository(sources[1], witness)
        providers = {
            "root": args.root.absolute(),
            "publication_repository": repository,
            "witness_repository": witness,
            "observation_repository": observation,
        }
        if mode == "collect":
            capture = await run_prospective_devval_campaign_v1(
                campaign_payload=payload,
                custody_session_factory=sources[2],
                **providers,
                **common,
            )
            return {
                "campaign_sha256": pin,
                "completion_sha256": capture.receipt_sha256,
                "rows": capture.row_count,
            }
        replay = await replay_prospective_devval_campaign_v1(
            expected_completion_sha256=args.completion_sha256,
            custody_repository=RuntimeBundleCustodyRepository(sources[2]),
            include_batch=args.batch_output_root is not None or study_plan is not None,
            **providers,
            **common,
        )
        result = {
            "campaign_sha256": pin,
            "completion_sha256": replay.completion_sha256,
            "row_inventory_sha256": replay.row_inventory_sha256,
            "development_rows": replay.development_rows,
            "validation_rows": replay.validation_rows,
        }
    if study_plan is not None:
        study = _prepare_study(
            replay.batch, plan=study_plan, expected_plan_sha256=args.study_plan_sha256
        )
        result.update(_publish_study(args.study_output_root.absolute(), study))
    if args.batch_output_root is not None:
        result.update(_publish_batch(args.batch_output_root.absolute(), replay.batch))
    return result


def _publish_batch(root, batch):
    if batch is None:
        raise CampaignCommandError("devval_campaign_batch_missing")
    receipt_pin = _publish_replay_artifact(
        root,
        "batch",
        batch,
        _MAX_BATCH_BYTES,
        {
            "schema": "ctcc.mie.devval_campaign_batch_publication.v1",
            "batch_sha256": batch.sha256,
            "batch_bytes": len(batch.payload),
            "campaign_sha256": batch.contract.campaign_sha256,
            "completion_sha256": batch.contract.completion_sha256,
            "row_inventory_sha256": batch.contract.row_inventory_sha256,
            "predictive_oos_eligible": False,
            "execution_authority": False,
        },
    )
    return {"batch_sha256": batch.sha256, "batch_publication_sha256": receipt_pin}


def _publish_study(root, study):
    receipt_pin = _publish_replay_artifact(
        root,
        "study",
        study,
        _MAX_STUDY_BYTES,
        {
            "schema": "ctcc.mie.devval_campaign_study_publication.v1",
            "study_sha256": study.sha256,
            "study_bytes": len(study.payload),
            "study_plan_sha256": study.contract.study_plan_sha256,
            "batch_sha256": study.contract.batch_sha256,
            "completion_sha256": study.contract.completion_sha256,
            "predictive_oos_eligible": False,
            "execution_authority": False,
        },
    )
    return {"study_sha256": study.sha256, "study_publication_sha256": receipt_pin}


def _publish_replay_artifact(root, name, artifact, maximum, facts):
    if sha(artifact.payload) != artifact.sha256:
        raise CampaignCommandError(f"devval_campaign_{name}_changed")
    require_private_evidence_base_v2(str(root))
    filename = f"{name}-v1.json"
    receipt = canonical(facts)
    with _root_context(root) as directory:
        if directory.names():
            raise CampaignCommandError(f"devval_campaign_{name}_output_not_empty")
        if _retain(directory, filename, artifact.payload, maximum) != artifact.sha256:
            raise CampaignCommandError(f"devval_campaign_{name}_changed")
        receipt_pin = _retain(directory, "receipt-v1.json", receipt, 4096)
        _retain(
            directory,
            "receipt-v1.sha256",
            receipt_pin.encode("ascii") + b"\n",
            65,
        )
        if set(directory.names()) != {
            filename,
            "receipt-v1.json",
            "receipt-v1.sha256",
        }:
            raise CampaignCommandError(f"devval_campaign_{name}_output_changed")
    return receipt_pin


def _publication_receipt(pin, readback):
    return {
        "campaign_sha256": pin,
        "publication_xid": readback.publication_xid,
        "acknowledgement_xid": readback.acknowledgement_xid,
        "readback_xid": readback.readback_xid,
    }


def main(arguments=None) -> int:
    try:
        args = _arguments(arguments)
        checkpoint = verify_source_checkpoint(
            Path(__file__).resolve().parents[1],
            args.source_archive.absolute(),
            args.source_archive_sha256,
        )
        result = (
            _freeze(args, checkpoint)
            if args.mode == "freeze"
            else asyncio.run(_run(args, checkpoint))
        )
    except (CampaignCommandError, DevvalSourceCheckpointError) as error:
        print(str(error), file=sys.stderr)
        return 1
    except (Exception, KeyboardInterrupt):  # noqa: BLE001 - Private role/paths never escape.
        print("devval_campaign_stage_unresolved", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "schema": "ctcc.mie.devval_campaign_command.v1",
                "operation": args.mode,
                **result,
                "predictive_oos_eligible": False,
                "execution_authority": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
