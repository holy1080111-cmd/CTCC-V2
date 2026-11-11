"""Compile a pinned prospective D/V research blueprint before source capture."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from app.mie.validation.artifact import _verify
from app.mie.validation.development_validation_source_collection_v1 import _root_context
from app.mie.validation.prospective_devval_campaign_plan_v1 import _MAX_MANIFEST_BYTES
from app.mie.validation.prospective_devval_campaign_replay_v1 import _read_root
from app.mie.validation.prospective_devval_campaign_run_v1 import _retain
from app.mie.validation.prospective_devval_research_blueprint_v1 import (
    DevvalResearchBlueprintV1,
    compile_research_campaign,
)
from app.trade_evidence.native_private_root_v2 import require_private_evidence_base_v2
from scripts.devval_campaign_source import (
    DevvalSourceCheckpointError,
    verify_source_checkpoint,
)


class ResearchBlueprintCommandError(ValueError):
    """Redacted refusal at the compiler boundary."""


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ResearchBlueprintCommandError(
            "devval_research_blueprint_arguments_invalid"
        )


def _read_research_blueprint(path, expected_sha256):
    path = path.absolute()
    with _read_root(path.parent) as directory:
        raw = directory.read(path.name, _MAX_MANIFEST_BYTES)
    return _verify(
        raw,
        expected_sha256=expected_sha256,
        contract_type=DevvalResearchBlueprintV1,
    )


def _arguments(arguments):
    parser = _Parser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--source-archive", type=Path, required=True)
    parser.add_argument("--source-archive-sha256", required=True)
    parser.add_argument("--blueprint", type=Path, required=True)
    parser.add_argument("--blueprint-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(arguments)
    if any(
        re.fullmatch(r"[0-9a-f]{64}", pin) is None
        for pin in (args.source_archive_sha256, args.blueprint_sha256)
    ):
        raise ResearchBlueprintCommandError(
            "devval_research_blueprint_arguments_invalid"
        )
    return args


def _compile(args, checkpoint):
    blueprint = _read_research_blueprint(args.blueprint, args.blueprint_sha256)
    plan = compile_research_campaign(blueprint)
    if (
        plan.declared_source_commit != checkpoint["expected_source_commit"]
        or plan.declared_source_tree != checkpoint["expected_source_tree"]
        or plan.manifest_sha256 != checkpoint["expected_source_manifest_sha256"]
    ):
        raise ResearchBlueprintCommandError("devval_research_blueprint_source_changed")
    output = args.output.absolute()
    require_private_evidence_base_v2(str(output.parent))
    with _root_context(output.parent) as directory:
        pin = _retain(
            directory, output.name, plan.canonical_json_bytes(), _MAX_MANIFEST_BYTES
        )
    return {
        "schema": "ctcc.mie.devval_research_blueprint_command.v1",
        "blueprint_sha256": args.blueprint_sha256,
        "campaign_plan_sha256": pin,
        "planned_rows": plan.expected_rows,
        "computational_only": True,
        "campaign_published": False,
        "predictive_oos_eligible": False,
        "execution_authority": False,
    }


def main(arguments=None) -> int:
    try:
        args = _arguments(arguments)
        checkpoint = verify_source_checkpoint(
            Path(__file__).resolve().parents[1],
            args.source_archive.absolute(),
            args.source_archive_sha256,
        )
        result = _compile(args, checkpoint)
    except (ResearchBlueprintCommandError, DevvalSourceCheckpointError) as error:
        print(str(error), file=sys.stderr)
        return 1
    except (Exception, KeyboardInterrupt):  # noqa: BLE001 - Keep private paths and bytes out of stderr.
        print("devval_research_blueprint_unresolved", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
