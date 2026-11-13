"""Explicit Windows localhost snapshot-only launcher; no Docker or DB runtime."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from scripts import prepare_local_analysis_installation as installation


class OfflineSnapshotLaunchRefusal(ValueError):
    """Fixed refusal without paths, token, or environment values."""


def _need(value: bool) -> None:
    if value is not True:
        raise OfflineSnapshotLaunchRefusal(
            "offline_snapshot_launcher_refused"
        ) from None


def verified_launch_inputs(root: Path) -> tuple[bytes, str, int]:
    """Reuse the owned installation identity and read back only its local token."""
    _need(isinstance(root, Path) and root.is_absolute())
    checked = installation.verify(root)
    _need(checked["status"] == "READY_TO_START_ANALYSIS_ONLY")
    metadata = json.loads(installation._read_regular(root / "installation.json", 4096))
    token = installation._read_regular(root / "api_token", 4096)
    _need(
        type(metadata) is dict
        and type(metadata.get("secret_sha256")) is dict
        and installation._sha(token) == metadata["secret_sha256"].get("api_token")
        and checked["installation_root"] == str(root)
        and checked["dashboard_url"]
        == f"http://127.0.0.1:{metadata.get('host_port')}/dashboard"
    )
    return token, metadata["source_commit"], metadata["host_port"]


def build_verified_app(root: Path):
    token, source_commit, port = verified_launch_inputs(root)
    # Import the analysis code only after exact source and private file checks.
    from app.api.offline_snapshot_only import build_offline_snapshot_only_app

    return (
        build_offline_snapshot_only_app(
            token=token, source_commit=source_commit, port=port
        ),
        source_commit,
        port,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the CTCC offline snapshot viewer on 127.0.0.1 only"
    )
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument("--check", type=Path)
    choice.add_argument("--serve", type=Path)
    args = parser.parse_args(argv)
    try:
        root = (args.check or args.serve).absolute()
        app, source_commit, port = build_verified_app(root)
    except Exception:  # noqa: BLE001 -- fixed refusal, never reveal private values
        print('{"status":"REFUSED","code":"offline_snapshot_launcher_refused"}')
        return 1
    url = f"http://127.0.0.1:{port}/dashboard"
    if args.check is not None:
        print(
            json.dumps(
                {
                    "status": "READY_TO_LAUNCH_OFFLINE_SNAPSHOT_ONLY",
                    "url": url,
                    "source_commit": source_commit,
                    "execution_authority": False,
                },
                separators=(",", ":"),
            )
        )
        return 0
    print(
        json.dumps(
            {
                "status": "VERIFIED_READY_TO_BIND",
                "url": url,
                "execution_authority": False,
            },
            separators=(",", ":"),
        ),
        flush=True,
    )
    try:
        import uvicorn

        uvicorn.run(
            app,
            host="127.0.0.1",
            port=port,
            workers=1,
            reload=False,
            proxy_headers=False,
            access_log=False,
            log_level="critical",
            log_config=None,
            lifespan="off",
            timeout_keep_alive=2,
        )
    except Exception:  # noqa: BLE001 -- fixed refusal, no private values in output
        print('{"status":"REFUSED","code":"offline_snapshot_bind_refused"}')
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
