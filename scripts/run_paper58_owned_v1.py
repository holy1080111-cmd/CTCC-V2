"""Verified Windows package entry point for the owned persistent Paper app."""

from __future__ import annotations

import json
import os
from pathlib import Path

from scripts import windows_tool_0058_v1 as package
from scripts.container_startup_0058_v1 import _EXCHANGE_SECRETS, _FALSE
from scripts.evidence_json import read_evidence_json

_SCHEMA = "ctcc.paper58.fresh_owned_windows_profile.v1"
_PROFILE_KEYS = frozenset(
    {
        "schema",
        "package_root",
        "package_identity_sha256",
        "port",
        "api_token_file",
        "owner_database_url_file",
        "actor_database_url_file",
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
                *_EXCHANGE_SECRETS,
                "CTCC_WINDOWS_TOOL_CONFIG_FILE",
                "NOTION_API_TOKEN",
                "SUBMISSION_OUTBOX_TOKEN_FILE",
                "NOTION_OUTBOX_TOKEN_FILE",
            )
        )
    )


def _secret(path, private_read):
    content = private_read(Path(path))
    package.need(0 < len(content) <= 4096)
    value = content.decode("utf-8")
    package.need(value == value.strip() and not any(ord(char) < 32 for char in value))
    return value


def build_verified_app(profile: Path):
    _require_closed_environment()
    from app.config.windows_tool_profile_0058_v1 import _private

    exact = _private(profile)
    document = read_evidence_json(exact)
    package.need(type(document) is dict and set(document) == _PROFILE_KEYS)
    package.need(document["schema"] == _SCHEMA)
    root = Path(document["package_root"])
    package.need(
        root.is_absolute() and root / "source" == Path(__file__).resolve().parents[1]
    )
    identity = package.verify(root, document["package_identity_sha256"])
    package.verify_dependencies(root)
    token = _secret(document["api_token_file"], _private)
    owner_url = _secret(document["owner_database_url_file"], _private)
    actor_url = _secret(document["actor_database_url_file"], _private)
    package.need(_private(profile) == exact)

    from app.database.native_pg_tls_v1 import (
        reviewed_native_pg_tls_route_v1,
        verified_native_pg_tls_context_v1,
    )

    package.need(
        all(
            reviewed_native_pg_tls_route_v1(value).password
            for value in (owner_url, actor_url)
        )
    )
    ca_file = Path(document["ca_file"])
    verified_native_pg_tls_context_v1(ca_file, document["ca_sha256"])

    from app.api.paper58_owned_http_v1 import build_owned_paper58_app_v1

    port = document["port"]
    app = build_owned_paper58_app_v1(
        owner_database_url=owner_url,
        actor_database_url=actor_url,
        ca_file=ca_file,
        ca_sha256=document["ca_sha256"],
        token=token,
        port=port,
    )
    return app, identity["source_commit"], port


def main(argv=None):
    parser = package.PrivateParser(description="Run CTCC Paper at 127.0.0.1")
    parser.add_argument("--profile", required=True, type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    try:
        app, commit, port = build_verified_app(args.profile)
        if args.check:
            print(
                json.dumps(
                    {
                        "status": "PAPER_PROFILE_VERIFIED",
                        "source_commit": commit,
                        "database_recovery_verified": False,
                        "execution_authority": False,
                    }
                )
            )
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
        package.need(server.started is True)
    except (Exception, SystemExit):  # noqa: BLE001 - private file and credential error stays local
        print('{"status":"REFUSED","code":"paper58_launch_refused"}')
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
