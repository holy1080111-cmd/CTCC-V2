"""Retain a pinned, label-free D/V research package and its two source rosters."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.domain.source_primitives import decode
from app.mie.validation.artifact import SHA256_RE
from app.mie.validation.development_validation_source_collection_v1 import _root_context
from app.mie.validation.prospective_devval_campaign_plan_v1 import _MAX_MANIFEST_BYTES
from app.mie.validation.prospective_devval_campaign_replay_v1 import _read_root
from app.mie.validation.prospective_devval_campaign_run_v1 import _retain
from app.mie.validation.prospective_devval_candidate_trials_v1 import (
    DevvalCandidateSourceV1,
)
from app.mie.validation.prospective_devval_global_companion_schedule_v1 import (
    _MAX_LEGACY_CONFIG_BYTES,
    _MAX_SCHEDULE_BYTES,
)
from app.mie.validation.prospective_devval_legacy_calibration_policy_v1 import (
    _MAX_POLICY_BYTES,
)
from app.mie.validation.prospective_devval_research_package_v1 import (
    _MAX_PACKAGE_BYTES,
    DevvalResearchPackageInputsV1,
    compile_devval_research_package_v1,
)
from app.trade_evidence.native_private_root_v2 import require_private_evidence_base_v2
from app.trade_qualification.public_market_collector import PublicMarketCollectionPolicy
from scripts.compile_devval_research_blueprint import _Parser
from scripts.devval_campaign_source import verify_source_checkpoint

_MAX_MARKET_POLICY_BYTES = 32 * 1024


def _pin(value: str) -> str:
    if SHA256_RE.fullmatch(value) is None:
        raise argparse.ArgumentTypeError("invalid SHA256")
    return value


def _arguments(arguments):
    parser = _Parser(description=__doc__, allow_abbrev=False)
    for name in (
        "source-archive",
        "blueprint",
        "scorer-config",
        "calibration-policy",
        "market-policy",
        "output-directory",
    ):
        parser.add_argument(f"--{name}", type=Path, required=True)
    for name in ("source-archive", "blueprint", "calibration-policy", "market-policy"):
        parser.add_argument(f"--{name}-sha256", type=_pin, required=True)
    parser.add_argument("--decision-delay-seconds", type=int, required=True)
    return parser.parse_args(arguments)


def _read(path: Path, maximum: int) -> bytes:
    path = path.absolute()
    with _read_root(path.parent) as directory:
        return directory.read(path.name, maximum)


def _compile(args):
    checkpoint = verify_source_checkpoint(
        Path(__file__).resolve().parents[1],
        args.source_archive.absolute(),
        args.source_archive_sha256,
    )
    source = DevvalCandidateSourceV1(
        commit=checkpoint["expected_source_commit"],
        tree=checkpoint["expected_source_tree"],
        manifest_sha256=checkpoint["expected_source_manifest_sha256"],
    )
    market_raw = _read(args.market_policy, _MAX_MARKET_POLICY_BYTES)
    decode(market_raw, _MAX_MARKET_POLICY_BYTES)
    market_policy = PublicMarketCollectionPolicy.model_validate_json(
        market_raw, strict=True
    )
    return compile_devval_research_package_v1(
        DevvalResearchPackageInputsV1(
            blueprint_bytes=_read(args.blueprint, _MAX_MANIFEST_BYTES),
            expected_blueprint_sha256=args.blueprint_sha256,
            scorer_config_bytes=_read(args.scorer_config, _MAX_LEGACY_CONFIG_BYTES),
            calibration_policy_bytes=_read(args.calibration_policy, _MAX_POLICY_BYTES),
            expected_calibration_policy_sha256=args.calibration_policy_sha256,
            market_policy=market_policy,
            expected_market_policy_sha256=args.market_policy_sha256,
            expected_source_checkpoint=source,
            expected_source_archive_sha256=args.source_archive_sha256,
            decision_delay_seconds=args.decision_delay_seconds,
        )
    )


def _retain_package(args, compiled):
    output = args.output_directory.absolute()
    require_private_evidence_base_v2(str(output))
    files = (
        ("campaign.json", compiled.campaign, _MAX_MANIFEST_BYTES),
        ("companion_schedule.json", compiled.companion_schedule, _MAX_SCHEDULE_BYTES),
        ("package.json", compiled.package, _MAX_PACKAGE_BYTES),
    )
    with _root_context(output) as directory:
        if directory.names():
            raise ValueError("research package requires an empty evidence directory")
        for name, artifact, limit in files:
            _retain(directory, name, artifact.payload, limit)
        if set(directory.names()) != {name for name, _, _ in files} or any(
            directory.read(name, limit) != artifact.payload
            for name, artifact, limit in files
        ):
            raise ValueError("research package readback changed")


def main(arguments=None) -> int:
    try:
        args = _arguments(arguments)
        compiled = _compile(args)
        _retain_package(args, compiled)
    except (Exception, KeyboardInterrupt):  # noqa: BLE001 - Keep private paths and bytes out of stderr.
        print("devval_research_package_command_refused", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "schema": "ctcc.mie.devval_research_package_command.v1",
                "package_sha256": compiled.package.sha256,
                "campaign_sha256": compiled.campaign.sha256,
                "companion_schedule_sha256": compiled.companion_schedule.sha256,
                "pre_window_publication_verified": False,
                "independent_availability_verified": False,
                "predictive_oos_eligible": False,
                "execution_authority": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
