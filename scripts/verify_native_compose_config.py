"""Redacted effective-Compose verifier; reads config JSON from stdin only.

This does not invoke Docker, open DSN files, or make an acceptance claim.
The caller must inspect actual containers/secret mounts separately.
"""

from __future__ import annotations

import argparse
import json
import re
import sys

_ID = re.compile(r"[0-9a-f]{12}\Z")
_FLAGS = (
    "AUTO_TRADE",
    "LIVE_TRADING",
    "PAPER_PERSISTENCE_ENABLED",
    "PAPER_AUTO_TICKS",
    "PAPER_AUTO_EXECUTION",
    "OKX_WS_ENABLED",
    "OKX_DEMO_ALLOW_ORDER_WRITES",
    "OKX_DEMO_AUTO_EXECUTION",
    "OKX_DEMO_AUTO_RECONCILE_ON_START",
    "OKX_DEMO_OBSERVABILITY_ENABLED",
    "OKX_DEMO_SOAK_ENABLED",
    "OKX_DEMO_SOAK_ALLOW_EXECUTE",
    "OKX_DEMO_CONTINUOUS_SESSION_ENABLED",
    "OKX_LIVE_ENABLED",
    "OKX_LIVE_ALLOW_ORDER_WRITES",
    "OKX_LIVE_AUTO_EXECUTION",
    "OKX_LIVE_AUTO_RECONCILE_ON_START",
    "NOTION_OUTBOX_ENABLED",
    "SUBMISSION_OUTBOX_ENABLED",
)
_RUNTIME_ENV = {
    "ENVIRONMENT": "production",
    "TRADING_MODE": "okx_demo",
    "CTCC_NATIVE_ONLY_LIFESPAN_V1": "true",
    "WEB_CONCURRENCY": "1",
    "OKX_DEMO_ENABLED": "true",
    "READINESS_REQUIRE_DATABASE": "true",
    "READINESS_REQUIRE_REDIS": "true",
    **dict.fromkeys(_FLAGS, "false"),
}
_OWNER_ENV = {
    "ENVIRONMENT": "production",
    "TRADING_MODE": "analysis_only",
    **dict.fromkeys(
        ("AUTO_TRADE", "LIVE_TRADING", "OKX_DEMO_ENABLED", "OKX_LIVE_ENABLED"), "false"
    ),
}
_RUNTIME_SCRIPT = """\
DATABASE_URL="$(cat /run/secrets/native_writer_dsn)"
OKX_DEMO_NATIVE_CUSTODY_DATABASE_URL="$(cat /run/secrets/native_custody_dsn)"
OKX_DEMO_NATIVE_FRAME_READER_DATABASE_URL="$(cat /run/secrets/native_frame_dsn)"
API_TOKEN="$(cat /run/secrets/native_api_token)"
export DATABASE_URL OKX_DEMO_NATIVE_CUSTODY_DATABASE_URL OKX_DEMO_NATIVE_FRAME_READER_DATABASE_URL API_TOKEN
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1"""
_MIGRATE_SCRIPT = """\
DATABASE_URL="$(cat /run/secrets/native_owner_dsn)"
export DATABASE_URL
exec alembic upgrade head"""
_APP_FIELDS = {
    "command",
    "depends_on",
    "entrypoint",
    "environment",
    "image",
    "networks",
    "pull_policy",
    "restart",
    "secrets",
    # Some Compose versions keep empty reset fields in the rendered model.
    "build",
    "env_file",
    "ports",
}
_RUNTIME_FIELDS = _APP_FIELDS | {
    "container_name",
    "healthcheck",
    "logging",
    "profiles",
    "security_opt",
}
_RUNTIME_HEALTHCHECK = [
    "CMD",
    "python",
    "-c",
    "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/readiness', timeout=3)",
]


def _need(value):
    if value is not True:
        raise ValueError("native_compose_merge_denied")


def _secrets(service):
    names = set()
    for item in service.get("secrets", []):
        if type(item) is str:
            name = item
        else:
            _need(type(item) is dict and set(item) <= {"source", "target"})
            name = item.get("source")
            _need(item.get("target", name) in (name, f"/run/secrets/{name}"))
        _need(type(name) is str and name not in names)
        names.add(name)
    return names


def _shell_script(service, expected):
    _need(service.get("entrypoint") == ["/bin/sh", "-ec"])
    # Compose can re-escape dollars when serializing its effective model.
    # Either serialization must still contain one exact script argument.
    _need(service.get("command") in ([expected], [expected.replace("$", "$$")]))


def verify(document, deployment_id, image_ref):
    _need(type(document) is dict and type(document.get("services")) is dict)
    _need(document.get("name") == f"ctcc-native-{deployment_id}")
    services = document["services"]
    _need(
        set(services)
        == {"api", "native_migrate", "native_role_provision", "postgres", "redis"}
    )
    network = document.get("networks", {}).get("native_internal", {})
    _need(network.get("internal") is True)
    for name in services:
        service = services[name]
        _need(set(service.get("networks", {})) == {"native_internal"})
        _need(not service.get("ports"))
    for name in ("api", "native_migrate", "native_role_provision"):
        service = services[name]
        _need(service.get("image") == image_ref)
        _need(service.get("pull_policy") == "never")
        _need(not service.get("build"))
        _need(not service.get("env_file"))
    api = services["api"]
    migrate = services["native_migrate"]
    provision = services["native_role_provision"]
    _need(set(api) <= _RUNTIME_FIELDS)
    _need(set(migrate) <= _APP_FIELDS)
    _need(set(provision) <= _APP_FIELDS | {"profiles"})
    _shell_script(api, _RUNTIME_SCRIPT)
    _shell_script(migrate, _MIGRATE_SCRIPT)
    _need(
        provision.get("entrypoint")
        == ["python", "-B", "-m", "scripts.provision_native_roles_v1"]
    )
    _need(provision.get("command") == ["--provision-reviewed-native-roles"])
    _need(api.get("healthcheck", {}).get("test") == _RUNTIME_HEALTHCHECK)
    _need(api.get("security_opt") == ["no-new-privileges:true"])
    _need(api.get("container_name") == f"ctcc-native-{deployment_id}-api")
    _need(api.get("profiles") == ["native-runtime"])
    _need(
        _secrets(api)
        == {
            "native_writer_dsn",
            "native_custody_dsn",
            "native_frame_dsn",
            "native_api_token",
        }
    )
    _need(_secrets(migrate) == {"native_owner_dsn"})
    _need(provision.get("profiles") == ["native-role-setup"])
    _need(
        _secrets(provision)
        == {
            "native_owner_dsn",
            "native_writer_dsn",
            "native_custody_dsn",
            "native_frame_dsn",
        }
    )
    for name in ("postgres", "redis"):
        _need(
            services[name].get("container_name")
            == f"ctcc-native-{deployment_id}-{name}"
        )
    postgres_env = services["postgres"].get("environment", {})
    _need("POSTGRES_PASSWORD" not in postgres_env)
    _need(
        postgres_env.get("POSTGRES_PASSWORD_FILE")
        == "/run/secrets/native_postgres_init_password"
    )
    _need(_secrets(services["postgres"]) == {"native_postgres_init_password"})
    _need(api.get("environment") == _RUNTIME_ENV)
    _need(migrate.get("environment") == provision.get("environment") == _OWNER_ENV)
    return {
        "status": "PASS",
        "profile": "native_no_execution_merge_v1",
        "execution_authority": False,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--deployment-id", required=True)
    parser.add_argument("--image-ref", required=True)
    args = parser.parse_args()
    if _ID.fullmatch(args.deployment_id) is None or not args.image_ref:
        print('{"status":"FAIL","code":"native_compose_input_invalid"}')
        return 1
    try:
        raw = sys.stdin.buffer.read(2_000_001)
        _need(0 < len(raw) <= 2_000_000)
        result = verify(json.loads(raw), args.deployment_id, args.image_ref)
    except Exception:  # noqa: BLE001 - malformed merged input emits a fixed refusal
        print('{"status":"FAIL","code":"native_compose_merge_denied"}')
        return 1
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
