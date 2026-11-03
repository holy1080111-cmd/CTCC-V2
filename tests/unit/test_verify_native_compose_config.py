"""Pure synthetic effective-config refusals; no Docker or secrets read."""

import importlib.util
import unittest
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[2] / "scripts/verify_native_compose_config.py"
SPEC = importlib.util.spec_from_file_location("verify_native_compose_config", SOURCE)
assert SPEC is not None and SPEC.loader is not None
verifier = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verifier)


class NativeComposeConfigChecks(unittest.TestCase):
    def base(self):
        image = "ctcc-native-reviewed:exact-source"
        ident = "abcdef123456"
        base = {"networks": {"native_internal": None}, "ports": []}
        api = {
            **base,
            "image": image,
            "pull_policy": "never",
            "entrypoint": ["/bin/sh", "-ec"],
            "command": "export DATABASE_URL=$(cat /run/secrets/native_writer_dsn); exec uvicorn app.main:app",
            "container_name": f"ctcc-native-{ident}-api",
            "profiles": ["native-runtime"],
            "secrets": [
                "native_writer_dsn",
                "native_custody_dsn",
                "native_frame_dsn",
                "native_api_token",
            ],
            "environment": {
                "ENVIRONMENT": "production",
                "TRADING_MODE": "okx_demo",
                "CTCC_NATIVE_ONLY_LIFESPAN_V1": "true",
                "WEB_CONCURRENCY": "1",
                "OKX_DEMO_ENABLED": "true",
                **dict.fromkeys(verifier._FLAGS, "false"),
            },
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
                    "secrets": ["native_owner_dsn"],
                },
                "native_role_provision": {
                    **base,
                    "image": image,
                    "pull_policy": "never",
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

    def test_exact_no_execution_merge(self):
        document, ident, image = self.base()
        self.assertEqual(verifier.verify(document, ident, image)["status"], "PASS")

    def test_inherited_or_broadened_api_refused(self):
        for path, value in (
            (("services", "api", "build"), {"context": "."}),
            (("services", "api", "ports"), ["8100:8000"]),
            (("services", "api", "env_file"), [".env"]),
            (("services", "api", "secrets"), ["native_writer_dsn", "native_owner_dsn"]),
            (("services", "api", "networks"), {"default": None}),
            (("services", "api", "environment", "OKX_DEMO_ALLOW_ORDER_WRITES"), "true"),
            (("services", "postgres", "environment", "POSTGRES_PASSWORD"), "synthetic"),
        ):
            with self.subTest(path=path):
                document, ident, image = self.base()
                cursor = document
                for key in path[:-1]:
                    cursor = cursor[key]
                cursor[path[-1]] = value
                with self.assertRaises(ValueError):
                    verifier.verify(document, ident, image)


if __name__ == "__main__":
    unittest.main()
