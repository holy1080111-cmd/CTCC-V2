"""Inspect retained MIE inputs using only pinned offline diagnostics."""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from pathlib import Path

from app.domain.source_primitives import sha
from app.mie.contracts import MieShadowTrace
from app.mie.validation import cash_pnl_concentration_v1, cost_stress_v1
from scripts.evidence_json import read_evidence_json
from scripts.verify_gate3_development_validation_groups import SHA256_RE, _read_bounded

_MAX_TRACE_BYTES = 4 * 1024 * 1024
_NO_ACCEPTANCE = {
    "admission": "DENY",
    "gate3_accepted": False,
    "gate4_accepted": False,
    "predictive_oos_accepted": False,
    "economic_oos_accepted": False,
    "independent_preregistration_verified": False,
    "source_authenticity_verified": False,
    "real_trade_sample_verified": False,
    "execution_authority": False,
}


class _PrivateParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ValueError("offline_mie_diagnostic_refused")


def _read_pinned(path: Path, pin: str, limit: int) -> bytes:
    path = path.absolute()
    if (
        SHA256_RE.fullmatch(pin) is None
        or path.anchor.startswith(("\\", "//"))
        or ".." in path.parts
    ):
        raise ValueError("offline_mie_input_refused")
    if os.name == "nt":
        from ctypes import windll

        if windll.kernel32.GetDriveTypeW(path.anchor) == 4:
            raise ValueError("offline_mie_input_refused")
    for part in (*reversed(path.parents), path):
        info = part.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("offline_mie_input_refused")
    raw = _read_bounded(path, limit=limit)
    if sha(raw) != pin:
        raise ValueError("offline_mie_input_pin_differs")
    return raw


def _inspect_shadow(args: argparse.Namespace) -> dict[str, object]:
    raw = _read_pinned(args.trace, args.trace_sha256, _MAX_TRACE_BYTES)
    retained = read_evidence_json(raw)
    trace = MieShadowTrace.model_validate(retained)
    # Retained trace fields must survive the typed replay without defaulted IDs
    # or other missing data. Object key order and JSON whitespace are immaterial.
    canonical = json.dumps(
        retained,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    replay_sha256 = trace.replay_sha256()
    if sha(canonical) != replay_sha256:
        raise ValueError("offline_mie_trace_not_complete")
    return {"replay_sha256": replay_sha256, "trace_authority": trace.authority}


def _evaluate(args: argparse.Namespace) -> dict[str, object]:
    if args.mode == "economic":
        report = cash_pnl_concentration_v1.evaluate_cash_pnl_concentration_v1(
            _read_pinned(
                args.plan, args.plan_sha256, cash_pnl_concentration_v1._MAX_PLAN
            ),
            _read_pinned(
                args.cohort, args.cohort_sha256, cash_pnl_concentration_v1._MAX_COHORT
            ),
            expected_plan_sha256=args.plan_sha256,
            expected_cohort_sha256=args.cohort_sha256,
        )
    else:
        report = cost_stress_v1.evaluate_cost_stress_v1(
            _read_pinned(args.plan, args.plan_sha256, cost_stress_v1._MAX_PLAN_BYTES),
            _read_pinned(args.path, args.path_sha256, cost_stress_v1._MAX_PATH_BYTES),
            expected_plan_sha256=args.plan_sha256,
            expected_path_sha256=args.path_sha256,
        )
    return {
        "report_sha256": report.sha256,
        "report": read_evidence_json(report.payload),
    }


def main(argv: list[str] | None = None) -> int:
    parser = _PrivateParser(description=__doc__, allow_abbrev=False)
    modes = parser.add_subparsers(dest="mode", required=True)
    for mode, inputs in (
        ("shadow", ("trace",)),
        ("economic", ("plan", "cohort")),
        ("cost-stress", ("plan", "path")),
    ):
        command = modes.add_parser(mode, allow_abbrev=False)
        for name in inputs:
            command.add_argument(f"--{name}", type=Path, required=True)
            command.add_argument(f"--{name}-sha256", required=True)

    try:
        args = parser.parse_args(argv)
        output = _inspect_shadow(args) if args.mode == "shadow" else _evaluate(args)
    except (Exception, KeyboardInterrupt):  # noqa: BLE001 -- do not echo private inputs.
        print('{"admission":"DENY","status":"REFUSED"}', file=sys.stderr)
        return 1

    print(
        json.dumps(
            {
                "schema": "ctcc.mie.offline_diagnostic.v1",
                "mode": args.mode,
                "status": "DIAGNOSTIC_COMPLETED",
                **_NO_ACCEPTANCE,
                **output,
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
