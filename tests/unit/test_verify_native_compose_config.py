"""Synthetic effective-config admission and refusal cases."""

import pytest

from scripts import verify_native_compose_config as verifier


def _base_merge():
    image = "ctcc-native-reviewed:exact-source"
    ident = "abcdef123456"
    base = {"networks": {"native_internal": None}, "ports": []}
    api = {
        **base,
        "image": image,
        "pull_policy": "never",
        "entrypoint": ["/bin/sh", "-ec"],
        "command": [verifier._RUNTIME_SCRIPT.replace("$", "$$")],
        "container_name": f"ctcc-native-{ident}-api",
        "profiles": ["native-runtime"],
        "secrets": [
            "native_writer_dsn",
            "native_custody_dsn",
            "native_frame_dsn",
            "native_api_token",
        ],
        "environment": dict(verifier._RUNTIME_ENV),
        "healthcheck": {"test": list(verifier._RUNTIME_HEALTHCHECK)},
        "security_opt": ["no-new-privileges:true"],
    }
    document = {
        "name": f"ctcc-native-{ident}",
        "networks": {"native_internal": {"internal": True}},
        "services": {
            "api": api,
            "native_migrate": {
                **base,
                "image": image,
                "pull_policy": "never",
                "entrypoint": ["/bin/sh", "-ec"],
                "command": [verifier._MIGRATE_SCRIPT],
                "environment": dict(verifier._OWNER_ENV),
                "secrets": ["native_owner_dsn"],
            },
            "native_role_provision": {
                **base,
                "image": image,
                "pull_policy": "never",
                "entrypoint": [
                    "python",
                    "-B",
                    "-m",
                    "scripts.provision_native_roles_v1",
                ],
                "command": ["--provision-reviewed-native-roles"],
                "environment": dict(verifier._OWNER_ENV),
                "profiles": ["native-role-setup"],
                "secrets": [
                    "native_owner_dsn",
                    "native_writer_dsn",
                    "native_custody_dsn",
                    "native_frame_dsn",
                ],
            },
            "postgres": {
                **base,
                "container_name": f"ctcc-native-{ident}-postgres",
                "environment": {
                    "POSTGRES_PASSWORD_FILE": "/run/secrets/native_postgres_init_password"
                },
                "secrets": ["native_postgres_init_password"],
            },
            "redis": {
                **base,
                "container_name": f"ctcc-native-{ident}-redis",
            },
        },
    }
    return document, ident, image


def test_exact_no_execution_merge():
    document, ident, image = _base_merge()
    assert verifier.verify(document, ident, image)["status"] == "PASS"


@pytest.mark.parametrize(
    ("path", "value"),
    [
        pytest.param(("services", "api", "build"), {"context": "."}, id="local-build"),
        pytest.param(("services", "api", "ports"), ["8100:8000"], id="published-port"),
        pytest.param(("services", "api", "env_file"), [".env"], id="host-env-file"),
        pytest.param(
            ("services", "api", "secrets"),
            ["native_writer_dsn", "native_owner_dsn"],
            id="owner-secret-in-api",
        ),
        pytest.param(
            ("services", "api", "networks"), {"default": None}, id="external-network"
        ),
        pytest.param(
            ("services", "api", "environment", "OKX_DEMO_ALLOW_ORDER_WRITES"),
            "true",
            id="order-writes-enabled",
        ),
        pytest.param(
            ("services", "postgres", "environment", "POSTGRES_PASSWORD"),
            "synthetic",
            id="inline-db-password",
        ),
        pytest.param(
            ("services", "api", "command"),
            ["printf injected\n" + verifier._RUNTIME_SCRIPT],
            id="modified-runtime-command",
        ),
        pytest.param(
            ("services", "native_migrate", "command"),
            ["export", "DATABASE_URL=$$(cat /run/secrets/native_owner_dsn)"],
            id="truncated-migration-command",
        ),
        pytest.param(
            ("services", "api", "volumes"),
            [
                {
                    "type": "bind",
                    "source": "/synthetic-host",
                    "target": "/synthetic-mount",
                }
            ],
            id="unexpected-bind-mount",
        ),
        pytest.param(
            ("services", "api", "environment", "NOTION_TOKEN"),
            "synthetic",
            id="unexpected-token-environment",
        ),
        pytest.param(
            ("services", "api", "healthcheck", "test"),
            ["CMD-SHELL", "true"],
            id="changed-runtime-healthcheck",
        ),
        pytest.param(
            ("services", "native_migrate", "secrets"),
            [{"source": "native_owner_dsn", "target": "different"}],
            id="changed-secret-target",
        ),
    ],
)
def test_inherited_or_broadened_api_refused(path, value):
    document, ident, image = _base_merge()
    cursor = document
    for key in path[:-1]:
        cursor = cursor[key]
    cursor[path[-1]] = value
    with pytest.raises(ValueError, match="native_compose_merge_denied"):
        verifier.verify(document, ident, image)
