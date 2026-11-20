"""Launch one fixed loopback tool from its verified private release profile."""

from __future__ import annotations

import asyncio
import os
import re
import sys
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path

from scripts import windows_tool_0058_v1 as package


def _desktop_startup(stream):
    """A bounded readiness nonce is DATA, never the API token or an Arm."""
    from scripts.desktop_windows_tool_0058_v1 import STARTUP_SCHEMA

    raw = stream.read(257)
    package.need(type(raw) is bytes and 0 < len(raw) <= 256)
    value = package.json.loads(raw)
    package.need(
        type(value) is dict and set(value) == {"schema", "nonce", "profile_sha256"}
    )
    package.need(package.canonical(value) == raw and value["schema"] == STARTUP_SCHEMA)
    package.need(
        type(value["nonce"]) is str
        and re.fullmatch(r"[a-f0-9]{64}", value["nonce"]) is not None
    )
    package.need(
        type(value["profile_sha256"]) is str
        and re.fullmatch(r"[a-f0-9]{64}", value["profile_sha256"]) is not None
    )
    return value


class _DesktopReadiness:
    """Only launcher-wrapped apps expose this closed loopback DATA route."""

    def __init__(self, application, document, nonce):
        from scripts.desktop_windows_tool_0058_v1 import (
            READINESS_PATH,
            READINESS_SCHEMA,
        )

        package.need(
            type(nonce) is str and re.fullmatch(r"[a-f0-9]{64}", nonce) is not None
        )
        identity = package.verify(
            Path(document["package_root"]), document["package_identity_sha256"]
        )
        self.application, self.path, self.port = (
            application,
            READINESS_PATH,
            document["port"],
        )
        package.need(type(self.port) is int and 1024 <= self.port <= 65535)
        self.body = package.canonical(
            {
                "schema": READINESS_SCHEMA,
                "nonce": nonce,
                "pid": os.getpid(),
                "package_identity_sha256": document["package_identity_sha256"],
                "source_commit": identity["source_commit"],
                "mode": document["mode"],
                "execution_authority": False,
            }
        )

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("path") != self.path:
            return await self.application(scope, receive, send)
        from starlette.responses import Response

        headers = scope.get("headers", ())
        shape = (
            type(headers) in (tuple, list)
            and len(headers) <= 64
            and all(
                type(pair) in (tuple, list)
                and len(pair) == 2
                and all(type(part) is bytes for part in pair)
                for pair in headers
            )
        )
        rows = [(key.lower(), value) for key, value in headers] if shape else []
        names = [key for key, _ in rows]
        expected = f"127.0.0.1:{self.port}".encode("ascii")
        origin = b"http://" + expected
        peer = scope.get("client")
        server = scope.get("server")
        valid = (
            shape
            and scope.get("method") == "GET"
            and scope.get("scheme") == "http"
            and scope.get("query_string", b"") == b""
            and type(peer) in (tuple, list)
            and len(peer) == 2
            and peer[0] == "127.0.0.1"
            and type(server) in (tuple, list)
            and tuple(server) == ("127.0.0.1", self.port)
            and names.count(b"host") == 1
            and dict(rows).get(b"host") == expected
            and len(names) == len(set(names))
            and not set(names).intersection(
                {
                    b"forwarded",
                    b"x-forwarded-host",
                    b"x-forwarded-for",
                    b"x-forwarded-proto",
                    b"authorization",
                    b"cookie",
                    b"x-ctcc-token",
                    b"transfer-encoding",
                }
            )
            and dict(rows).get(b"origin", origin) == origin
            and dict(rows).get(b"content-length", b"0") == b"0"
        )
        response = Response(
            self.body if valid else b'{"status":"REFUSED"}',
            status_code=200 if valid else 403,
            media_type="application/json",
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )
        return await response(scope, receive, send)


@contextmanager
def runtime_lock(root):
    """OS-owned byte lock; a crash releases it, no stale-lock deletion needed."""
    path = root / "runtime-operation.lock"
    with path.open("a+b") as stream:
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


async def _require_actual0058():
    from app.core.native_role_witness_0056_v1 import (
        observe_restricted_native_roles_0056_witness_v1,
    )
    from app.demo_automation import native_server_selectors_v2 as selectors
    from app.domain.native_database_profile_v1 import DB0058

    selected = selectors.select_configured_native_demo_factories_v2()
    try:
        package.need(
            await selectors._observe_runtime_database_head_v2(selected) == "0058"
        )
        roles = await observe_restricted_native_roles_0056_witness_v1(
            selected, source=package._SOURCE, profile=DB0058
        )
        package.need(
            roles.observation.database_head == "0058"
            and roles.startup_admission is roles.execution_authority is False
        )
    finally:
        await selectors._dispose_failed_selection_factories_v2(selected)


def build_tool(document):
    from app.config.settings import get_settings

    settings = get_settings()
    if document["mode"] == "analysis":
        from app.api.offline_snapshot_only import build_offline_snapshot_only_app

        identity = package.verify(
            Path(document["package_root"]), document["package_identity_sha256"]
        )
        return build_offline_snapshot_only_app(
            token=settings.api_token_value.encode(),
            source_commit=identity["source_commit"],
            port=document["port"],
        )
    if document["mode"] == "live":
        # The dedicated app stays usable with NOT_CONFIGURED and cannot import
        # the general API/legacy Live write routes as a fallback.
        from app.api.native_live_manual_v1 import app

        return app
    from app.main import app

    original = app.router.lifespan_context

    @asynccontextmanager
    async def actual0058_lifespan(instance):
        # Genuine SQL head, full catalog and roles BEFORE signed recovery. A
        # label/descriptor is insufficient. Main independently repeats startup.
        async with asyncio.timeout(45):
            await _require_actual0058()
        async with original(instance):
            package.need(
                instance.state.native_manual_demo_database_head == "0058"
                and instance.state.native_manual_demo_recovery_confirmed is True
            )
            yield

    app.router.lifespan_context = actual0058_lifespan
    return app


def configure_process(profile):
    from app.config.windows_tool_profile_0058_v1 import ENVIRONMENT_NAME, read_profile

    # No inherited runtime route or private Demo .env may select Live custody.
    os.environ[ENVIRONMENT_NAME] = str(profile)
    document = read_profile(profile)
    runtime = document["live_runtime"]
    os.environ.pop("CTCC_NATIVE_LIVE_RUNTIME_CONFIG_FILE", None)
    if runtime is not None:
        os.environ["CTCC_NATIVE_LIVE_RUNTIME_CONFIG_FILE"] = runtime["path"]
    from app.config.settings import get_settings

    package.need(get_settings.cache_info().currsize == 0)
    get_settings()
    return document


def main(arguments=None):
    parser = package.PrivateParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--profile", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--serve", action="store_true")
    parser.add_argument("--desktop-readiness-stdin", action="store_true")
    args = parser.parse_args(arguments)
    try:
        package.need(not args.desktop_readiness_stdin or args.serve)
        startup = (
            _desktop_startup(sys.stdin.buffer) if args.desktop_readiness_stdin else None
        )
        profile = args.profile.absolute()
        if startup is not None:
            from app.config.windows_tool_profile_0058_v1 import _private

            package.need(package.sha(_private(profile)) == startup["profile_sha256"])
        document = configure_process(profile)
        if startup is not None:
            package.need(package.sha(_private(profile)) == startup["profile_sha256"])
        root = Path(document["package_root"])
        package.need(package._SOURCE == root / "source")
        # The isolated interpreter is mandatory; host packages never silently
        # substitute for the separately installed exact Windows lock.
        package.need(
            Path(sys.executable).resolve() == root / "runtime/Scripts/python.exe"
            and Path(sys.prefix).resolve() == root / "runtime"
        )
        package.verify_dependencies(root)
        with runtime_lock(root):
            print(
                package.canonical(
                    {
                        "status": "LOCAL_CONFIGURATION_VERIFIED",
                        "mode": document["mode"],
                        "url": f"http://127.0.0.1:{document['port']}/dashboard"
                        if document["mode"] != "live"
                        else f"http://127.0.0.1:{document['port']}/",
                        "database_head": "NOT_OBSERVED",
                        "execution_authority": False,
                        "arm_restored": False,
                    }
                ).decode(),
                flush=True,
            )
            if args.check:
                return 0
            import uvicorn

            application = build_tool(document)
            if startup is not None:
                application = _DesktopReadiness(application, document, startup["nonce"])
            uvicorn.run(
                application,
                host="127.0.0.1",
                port=document["port"],
                workers=1,
                reload=False,
                proxy_headers=False,
                access_log=False,
                log_config=None,
                log_level="critical",
                lifespan="off" if document["mode"] == "analysis" else "on",
                timeout_keep_alive=2,
            )
        return 0
    except Exception:  # noqa: BLE001 -- provider/private exception text is not a UI.
        print('{"status":"REFUSED","code":"windows_tool_0058_launch_refused"}')
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
