"""Read back two distinct exact-source DB0057 smoke clusters; no full union."""

from __future__ import annotations

import argparse
from pathlib import Path

from scripts import current0057_hermetic_contract as contract


def verify_pair(artifacts: Path, *, commit: str, tree: str):
    contract._need(artifacts.is_dir() and not artifacts.is_symlink())
    rows = []
    for path in sorted(artifacts.iterdir()):
        if not path.is_dir():
            continue
        contract._need(not path.is_symlink())
        report = path / "ctcc-g12-smoke"
        if not (report / "identity.json").is_file():
            continue
        raw = contract._read(path, "ctcc-source.tar", maximum=128 * 1024 * 1024)
        observed = contract.verify_smoke(report, raw, commit=commit, tree=tree)
        rows.append(
            {
                "artifact": path.name,
                "archive_sha256": contract.digest(raw),
                "readback": observed,
            }
        )
    contract._need(len(rows) == 2)
    contract._need(
        rows[0]["archive_sha256"] == rows[1]["archive_sha256"]
        and rows[0]["readback"]["source"] == rows[1]["readback"]["source"]
    )
    contract._need(
        rows[0]["readback"]["cluster_system_identifier_sha256"]
        != rows[1]["readback"]["cluster_system_identifier_sha256"]
    )
    contract._need(
        all(
            rows[0]["readback"][key] == rows[1]["readback"][key]
            for key in ("inherited_native_definitions_sha256", "g12_definitions_sha256")
        )
    )
    return {
        "schema": "ctcc.current0057.g12_smoke_pair_readback.v1",
        "source": rows[0]["readback"]["source"],
        "clusters": rows,
        "g12_smoke_pair": "PASS",
        "hermetic_regression": "NOT_RUN",
        "component_validation": "NOT_RUN",
        "execution_authority": False,
        "trading_acceptance": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--tree", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = verify_pair(args.artifacts, commit=args.commit, tree=args.tree)
    raw = contract.canonical(result)
    with args.output.open("xb") as stream:
        stream.write(raw)
    contract._need(args.output.read_bytes() == raw)
    print("DB0057_G12_SMOKE_PAIR=PASS")
    print("HERMETIC_REGRESSION=NOT_RUN")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
