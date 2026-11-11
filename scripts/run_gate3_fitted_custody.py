"""Explicit restricted-role custody for one fitted prospective candidate.

Run publish, acknowledge, and observe as separate operator invocations with
their own directly granted PostgreSQL logins. This command never reads a
holdout, reserves an evaluation, contacts an exchange, or trades.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import ssl
import sys
from pathlib import Path

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.domain.source_primitives import canonical, sha
from app.mie.validation.artifact import (
    FrozenGate3Artifact,
    _verify,
    verify_prospective_preregistration,
)
from app.mie.validation.gate3_fitted_candidate_custody_v1 import (
    FittedCandidateInputsV1,
    Gate3FittedCandidateCustodyV1,
    Gate3FittedCustodyError,
)
from app.mie.validation.prospective_devval_campaign_batch_v1 import (
    _MAX_BATCH_BYTES,
    DevvalCampaignBatchV1,
)
from app.mie.validation.prospective_devval_campaign_replay_v1 import _read_root
from app.mie.validation.prospective_devval_campaign_run_v1 import _retain
from app.mie.validation.prospective_devval_campaign_study_v1 import (
    _MAX_STUDY_BYTES,
    DevvalCampaignStudyV1,
)
from app.mie.validation.prospective_devval_candidate_trials_v1 import (
    DevvalCandidateTrialsV1,
    DevvalLegacyScoresV1,
)
from app.mie.validation.prospective_devval_fitted_binding_v1 import (
    _MAX_FITTED_BINDING_BYTES,
    _MAX_TRIAL_BYTES,
    DevvalFittedCandidateBindingV1,
)
from app.trade_evidence.native_private_root_v2 import require_private_evidence_base_v2
from scripts.compile_devval_research_blueprint import _read_research_blueprint
from scripts.devval_campaign_source import verify_source_checkpoint
from scripts.run_devval_campaign import _read

_DATABASE_ENV = "CTCC_GATE3_FITTED_CUSTODY_DATABASE_URL"
_PIN = re.compile(r"[0-9a-f]{64}\Z")
_MAX_LEGACY_BYTES = 16 * 1024 * 1024
_MAX_RECEIPT_BYTES = 16_384


class FittedCustodyCommandError(ValueError):
    """Fixed operator refusal without evidence, URL, or secret values."""


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise FittedCustodyCommandError("gate3_fitted_command_invalid")


def _arguments(arguments):
    parser = _Parser(description=__doc__, allow_abbrev=False)
    parser.add_argument("mode", choices=("publish", "acknowledge", "observe"))
    for name in (
        "source-archive",
        "binding",
        "batch",
        "study",
        "trials",
        "legacy",
        "preregistration",
        "ca-file",
        "output",
    ):
        parser.add_argument(f"--{name}", type=Path, required=True)
    for name in (
        "source-archive-sha256",
        "binding-sha256",
        "batch-sha256",
        "study-sha256",
        "trials-sha256",
        "legacy-sha256",
        "preregistration-sha256",
        "ca-sha256",
    ):
        parser.add_argument(f"--{name}", required=True)
    parser.add_argument("--ack-sha256")
    parser.add_argument("--research-blueprint", type=Path)
    parser.add_argument("--research-blueprint-sha256")
    for name in ("database-host", "database-name", "login-name"):
        parser.add_argument(f"--{name}", required=True)
    parser.add_argument("--database-port", required=True, type=int)
    for name in (
        "database-oid",
        "login-oid",
        "publisher-oid",
        "observer-oid",
        "evaluator-oid",
    ):
        parser.add_argument(f"--{name}", required=True, type=int)
    args = parser.parse_args(arguments)
    inputs = (
        args.source_archive,
        args.binding,
        args.batch,
        args.study,
        args.trials,
        args.legacy,
        args.preregistration,
        args.ca_file,
        *((args.research_blueprint,) if args.research_blueprint is not None else ()),
    )
    if (
        (args.mode == "observe") != (args.ack_sha256 is not None)
        or (args.research_blueprint is None) != (args.research_blueprint_sha256 is None)
        or any(
            type(value) is not str or _PIN.fullmatch(value) is None
            for name, value in vars(args).items()
            if name.endswith("sha256") and value is not None
        )
        or any(
            type(value) is not int or not 0 < value < 2**32
            for name, value in vars(args).items()
            if name.endswith("oid")
        )
        or not 1 <= args.database_port <= 65535
        or any(
            not value or value.strip() != value
            for value in (args.database_host, args.database_name, args.login_name)
        )
        or any(
            not isinstance(value, Path) or not value.is_absolute()
            for value in (*inputs, args.output)
        )
    ):
        raise FittedCustodyCommandError("gate3_fitted_command_invalid")
    role_oids = {args.publisher_oid, args.observer_oid, args.evaluator_oid}
    if (
        len(role_oids) != 3
        or (args.mode == "publish" and args.login_oid != args.publisher_oid)
        or (args.mode == "acknowledge" and args.login_oid != args.observer_oid)
        or (args.mode == "observe" and args.login_oid in role_oids)
    ):
        raise FittedCustodyCommandError("gate3_fitted_command_role_invalid")
    if len({value.resolve() for value in inputs}) != len(inputs) or any(
        value.resolve() == args.output.resolve() for value in inputs
    ):
        raise FittedCustodyCommandError("gate3_fitted_command_path_conflict")
    return args


def _artifact(path: Path, pin: str, contract, maximum: int):
    raw = _read(path, maximum)
    checked = _verify(raw, expected_sha256=pin, contract_type=contract)
    return FrozenGate3Artifact(payload=raw, sha256=pin, contract=checked)


def _inputs(args, checkpoint) -> FittedCandidateInputsV1:
    binding = _artifact(
        args.binding,
        args.binding_sha256,
        DevvalFittedCandidateBindingV1,
        _MAX_FITTED_BINDING_BYTES,
    )
    batch = _artifact(
        args.batch, args.batch_sha256, DevvalCampaignBatchV1, _MAX_BATCH_BYTES
    )
    if batch.contract.research_blueprint_sha256 != args.research_blueprint_sha256:
        raise FittedCustodyCommandError("gate3_fitted_research_blueprint_changed")
    blueprint = (
        _read_research_blueprint(
            args.research_blueprint, args.research_blueprint_sha256
        )
        if args.research_blueprint is not None
        else None
    )
    study = _artifact(
        args.study, args.study_sha256, DevvalCampaignStudyV1, _MAX_STUDY_BYTES
    )
    trials = _artifact(
        args.trials,
        args.trials_sha256,
        DevvalCandidateTrialsV1,
        _MAX_TRIAL_BYTES,
    )
    legacy = _artifact(
        args.legacy,
        args.legacy_sha256,
        DevvalLegacyScoresV1,
        _MAX_LEGACY_BYTES,
    ).contract
    preregistration = verify_prospective_preregistration(
        _read(args.preregistration, 1_048_576),
        expected_sha256=args.preregistration_sha256,
    )
    source = binding.contract.source_checkpoint
    if (source.commit, source.tree, source.manifest_sha256) != (
        checkpoint["expected_source_commit"],
        checkpoint["expected_source_tree"],
        checkpoint["expected_source_manifest_sha256"],
    ) or binding.contract.source_archive_sha256 != args.source_archive_sha256:
        raise FittedCustodyCommandError("gate3_fitted_source_changed")
    return FittedCandidateInputsV1(
        binding=binding,
        expected_binding_sha256=args.binding_sha256,
        batch=batch,
        study=study,
        trial_result=trials,
        legacy_scores=legacy,
        preregistration=preregistration,
        expected_preregistration_sha256=args.preregistration_sha256,
        source_archive_sha256=args.source_archive_sha256,
        research_blueprint=blueprint,
    )


def _database_url(args):
    raw = os.environ.get(_DATABASE_ENV)
    if not raw:
        raise FittedCustodyCommandError("gate3_fitted_database_settings_missing")
    url = make_url(raw)
    if (
        url.drivername != "postgresql+asyncpg"
        or not url.host
        or not url.username
        or not url.database
        or url.query
        or not 1 <= (url.port or 5432) <= 65535
        or url.port == 0
        or (url.host, url.port or 5432, url.database, url.username)
        != (args.database_host, args.database_port, args.database_name, args.login_name)
    ):
        raise FittedCustodyCommandError("gate3_fitted_database_settings_invalid")
    return url


def _ca_context(path: Path, expected_sha256: str) -> ssl.SSLContext:
    payload = _read(path, 1_048_576)
    if sha(payload) != expected_sha256:
        raise FittedCustodyCommandError("gate3_fitted_ca_invalid")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.load_verify_locations(cadata=payload.decode("ascii"))
    if context.verify_mode != ssl.CERT_REQUIRED or not context.check_hostname:
        raise FittedCustodyCommandError("gate3_fitted_ca_invalid")
    return context


async def _execute(args, inputs):
    url = _database_url(args)
    tls = _ca_context(args.ca_file, args.ca_sha256)
    engine = create_async_engine(
        url,
        poolclass=NullPool,
        connect_args={
            "ssl": tls,
            "port": url.port or 5432,
            "password": url.password if url.password is not None else "",
        },
        hide_parameters=True,
    )
    try:
        factory = async_sessionmaker(engine, autoflush=False, expire_on_commit=False)
        role = {
            "publish": "publisher",
            "acknowledge": "observer",
            "observe": "reader",
        }[args.mode]
        repository = Gate3FittedCandidateCustodyV1(
            factory,
            role=role,
            expected_host=args.database_host,
            expected_port=args.database_port,
            expected_database_name=args.database_name,
            expected_login_name=args.login_name,
            expected_database_oid=args.database_oid,
            expected_login_oid=args.login_oid,
            expected_publisher_oid=args.publisher_oid,
            expected_observer_oid=args.observer_oid,
            expected_evaluator_oid=args.evaluator_oid,
        )
        if args.mode == "publish":
            result = await repository.publish(inputs)
            return {
                "seal_sha256": result.seal_sha256,
                "binding_sha256": result.binding_sha256,
                "publication_xid": result.publication_xid,
                "publisher_oid": result.publisher_oid,
            }
        if args.mode == "acknowledge":
            result = await repository.acknowledge(inputs)
        else:
            result = await repository.read_acknowledged(
                inputs, expected_ack_sha256=args.ack_sha256
            )
        return {
            "seal_sha256": result.seal_sha256,
            "binding_sha256": result.binding_sha256,
            "publication_xid": result.publication_xid,
            "acknowledgement_xid": result.acknowledgement_xid,
            "ack_sha256": result.ack_sha256,
            "publisher_oid": result.publisher_oid,
            "observer_oid": result.observer_oid,
        }
    finally:
        try:
            async with asyncio.timeout(10):
                await engine.dispose()
        except Exception:  # noqa: BLE001 - Never expose connection details.
            raise FittedCustodyCommandError(
                "gate3_fitted_database_cleanup_uncertain"
            ) from None


def _require_unused_output(args):
    require_private_evidence_base_v2(str(args.output.parent))
    with _read_root(args.output.parent) as directory:
        if args.output.name in directory.names():
            raise FittedCustodyCommandError("gate3_fitted_receipt_already_exists")


def _retain_result(args, result):
    output = args.output
    require_private_evidence_base_v2(str(output.parent))
    document = {
        "schema": "ctcc.mie.gate3_fitted_custody_command.v1",
        "operation": args.mode,
        "source_archive_sha256": args.source_archive_sha256,
        **result,
        "independent_custody_verified": False,
        "predictive_oos_eligible": False,
        "execution_authority": False,
    }
    raw = canonical(document)
    if len(raw) > _MAX_RECEIPT_BYTES:
        raise FittedCustodyCommandError("gate3_fitted_receipt_size")
    with _read_root(output.parent) as directory:
        pin = _retain(directory, output.name, raw, _MAX_RECEIPT_BYTES)
    return pin


def main(arguments=None) -> int:
    try:
        args = _arguments(arguments)
        checkpoint = verify_source_checkpoint(
            Path(__file__).resolve().parents[1],
            args.source_archive,
            args.source_archive_sha256,
        )
        inputs = _inputs(args, checkpoint)
        _require_unused_output(args)
        result = asyncio.run(_execute(args, inputs))
        receipt_sha256 = _retain_result(args, result)
    except (FittedCustodyCommandError, Gate3FittedCustodyError) as error:
        print(str(error), file=sys.stderr)
        return 1
    except (Exception, KeyboardInterrupt):  # noqa: BLE001 - No private details in output.
        print("gate3_fitted_stage_unresolved", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "schema": "ctcc.mie.gate3_fitted_command_result.v1",
                "operation": args.mode,
                "receipt_sha256": receipt_sha256,
                "predictive_oos_eligible": False,
                "execution_authority": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
