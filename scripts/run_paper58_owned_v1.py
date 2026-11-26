"""Verified Windows package entry point for the owned persistent Paper app."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from scripts import windows_tool_0058_v1 as package
from scripts.container_startup_0058_v1 import _FALSE
from scripts.evidence_json import read_evidence_json

_SCHEMA = "ctcc.paper58.fresh_owned_windows_profile.v1"
_PROFILE_FILE = "paper-profile.json"
_SECRET_FILES = {
    "api_token_file": "api_token",
    "owner_database_url_file": "paper_owner_database_url",
    "actor_database_url_file": "paper_actor_database_url",
}
_PROFILE_KEYS = frozenset(
    {
        "schema",
        "package_root",
        "package_identity_sha256",
        "port",
        *_SECRET_FILES,
        "secret_files_sha256",
        "ca_file",
        "ca_sha256",
    }
)


def _require_closed_environment():
    environment = {name.upper(): value for name, value in os.environ.items()}
    package.need(environment.get("TRADING_MODE", "analysis_only") == "analysis_only")
    package.need(all(environment.get(name, "false") == "false" for name in _FALSE))
    package.need(
        all(
            not environment.get(name)
            for name in (
                "CTCC_WINDOWS_TOOL_CONFIG_FILE",
                "CTCC_NATIVE_LIVE_RUNTIME_CONFIG_FILE",
                "NOTION_API_TOKEN",
            )
        )
    )
    package.need(
        all(
            not value or name in _FALSE and value == "false"
            for name, value in environment.items()
            if name.startswith(("OKX_", "NOTION_OUTBOX_", "SUBMISSION_OUTBOX_"))
        )
    )


def _require_installed_runtime(root):
    package.need(
        Path(sys.executable).resolve() == root / "runtime/Scripts/python.exe"
        and Path(sys.prefix).resolve() == root / "runtime"
    )


def _secret_text(content):
    package.need(0 < len(content) <= 4096)
    value = content.decode("utf-8")
    package.need(value == value.strip() and not any(ord(char) < 32 for char in value))
    return value


def _read_secrets(profile, document, private_read):
    hashes = document["secret_files_sha256"]
    package.need(type(hashes) is dict and set(hashes) == set(_SECRET_FILES))
    values = {}
    for key, name in _SECRET_FILES.items():
        path = profile.parent / name
        package.need(document[key] == str(path))
        content = private_read(path)
        package.need(package.sha(content) == hashes[key])
        values[key] = _secret_text(content)
    return values


def _require_pg_inputs(owner_url, actor_url, ca_file, ca_sha256):
    from app.core.owned_paper58_runtime_v1 import _routes
    from app.database.native_pg_tls_v1 import verified_native_pg_tls_context_v1

    owner, actor = _routes(owner_url, actor_url)
    package.need(bool(owner.password) and bool(actor.password))
    verified_native_pg_tls_context_v1(ca_file, ca_sha256)


def build_verified_app(profile: Path):
    _require_closed_environment()
    from app.config.windows_tool_profile_0058_v1 import _private

    exact = _private(profile)
    document = read_evidence_json(exact)
    package.need(type(document) is dict and set(document) == _PROFILE_KEYS)
    package.need(document["schema"] == _SCHEMA and profile.name == _PROFILE_FILE)
    root = Path(document["package_root"])
    package.need(
        root.is_absolute() and root / "source" == Path(__file__).resolve().parents[1]
    )
    identity = package.verify(root, document["package_identity_sha256"])
    _require_installed_runtime(root)
    package.verify_dependencies(root)
    values = _read_secrets(profile, document, _private)
    owner_url = values["owner_database_url_file"]
    actor_url = values["actor_database_url_file"]
    package.need(_private(profile) == exact)
    ca_file = Path(document["ca_file"])
    _require_pg_inputs(owner_url, actor_url, ca_file, document["ca_sha256"])

    from app.api.paper58_owned_http_v1 import build_owned_paper58_app_v1

    port = document["port"]
    app = build_owned_paper58_app_v1(
        owner_database_url=owner_url,
        actor_database_url=actor_url,
        ca_file=ca_file,
        ca_sha256=document["ca_sha256"],
        token=values["api_token_file"],
        port=port,
    )
    return app, identity["source_commit"], port


def main(argv=None):
    parser = package.PrivateParser(description="Run CTCC Paper at 127.0.0.1")
    parser.add_argument("--profile", required=True, type=Path)
    operation = parser.add_mutually_exclusive_group(required=True)
    operation.add_argument("--check", action="store_true")
    operation.add_argument("--serve", action="store_true")
    args = parser.parse_args(argv)
    try:
        from scripts.launch_windows_tool_0058_v1 import runtime_lock

        root = Path(__file__).resolve().parents[2]
        _require_installed_runtime(root)
        with runtime_lock(root):
            app, commit, port = build_verified_app(args.profile)
            print(
                json.dumps(
                    {
                        "status": "PAPER_PROFILE_VERIFIED",
                        "dashboard_url": f"http://127.0.0.1:{port}/dashboard/local-analysis",
                        "source_commit": commit,
                        "database_recovery_verified": False,
                        "execution_authority": False,
                    }
                ),
                flush=True,
            )
            if args.check:
                return 0

            import uvicorn

            server = uvicorn.Server(
                uvicorn.Config(
                    app,
                    host="127.0.0.1",
                    port=port,
                    workers=1,
                    reload=False,
                    lifespan="on",
                    proxy_headers=False,
                    access_log=False,
                    log_level="critical",
                    log_config=None,
                    timeout_keep_alive=2,
                )
            )
            server.run()
            package.need(
                server.started is True and server.lifespan.shutdown_failed is False
            )
    except (Exception, SystemExit):  # noqa: BLE001 - private file and credential error stays local
        print('{"status":"REFUSED","code":"paper58_launch_refused"}')
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
