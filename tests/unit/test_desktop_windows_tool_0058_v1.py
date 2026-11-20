"""Desktop UI/process DATA mechanics; no profile, trade, DB or browser is used."""

import io
import json
import os
import socket
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from starlette.responses import Response

from scripts import desktop_windows_tool_0058_v1 as desktop
from scripts import launch_windows_tool_0058_v1 as launcher


def plan(tmp_path, mode="analysis"):
    return desktop.DesktopLaunchPlanV1(
        tmp_path / "tool-profile.json",
        tmp_path,
        tmp_path / "runtime/Scripts/python.exe",
        "a" * 64,
        "b" * 40,
        mode,
        18282,
        "c" * 64,
        tmp_path / "base/python.exe",
        "d" * 64,
    )


def test_readiness_nonce_is_a_bounded_separate_stdin_message():
    document = {
        "schema": desktop.STARTUP_SCHEMA,
        "nonce": "1" * 64,
        "profile_sha256": "c" * 64,
    }
    raw = desktop.package.canonical(document)
    assert launcher._desktop_startup(io.BytesIO(raw)) == document
    assert len(raw) <= 256


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"x" * 257,
        b"{}",
        b'{"nonce":"credential_must_not_escape"}',
        desktop.package.canonical(
            {"schema": desktop.STARTUP_SCHEMA, "nonce": "A" * 64}
        ),
        desktop.package.canonical(
            {
                "schema": desktop.STARTUP_SCHEMA,
                "nonce": "1" * 64,
                "api_token": "must_not_escape",
            }
        ),
        desktop.package.canonical({"schema": desktop.STARTUP_SCHEMA, "nonce": "1" * 64})
        + b" ",
    ],
)
def test_bad_nonce_never_enters_a_launcher_application(raw):
    with pytest.raises(ValueError) as error:
        launcher._desktop_startup(io.BytesIO(raw))
    assert "must_not_escape" not in str(error.value)


def wrapper(monkeypatch):
    document = {
        "package_root": "synthetic-package",
        "package_identity_sha256": "a" * 64,
        "mode": "analysis",
        "port": 18282,
        "settings": {"api_token": "private_must_not_escape"},
    }
    monkeypatch.setattr(
        launcher.package, "verify", lambda *_: {"source_commit": "b" * 40}
    )

    async def application(scope, receive, send):
        await Response(b"ordinary-application")(scope, receive, send)

    return launcher._DesktopReadiness(application, document, "1" * 64)


@pytest.mark.asyncio
async def test_launcher_only_readiness_contains_exact_nonce_pid_and_public_release_identity(
    monkeypatch,
):
    app = wrapper(monkeypatch)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 1234)),
        base_url="http://127.0.0.1:18282",
    ) as client:
        response = await client.get(desktop.READINESS_PATH)
        ordinary = await client.get("/other")
    assert response.status_code == 200 and ordinary.content == b"ordinary-application"
    assert response.content == desktop.package.canonical(
        {
            "schema": desktop.READINESS_SCHEMA,
            "nonce": "1" * 64,
            "pid": os.getpid(),
            "package_identity_sha256": "a" * 64,
            "source_commit": "b" * 40,
            "mode": "analysis",
            "execution_authority": False,
        }
    )
    assert response.headers["cache-control"] == "no-store"
    assert "access-control-allow-origin" not in response.headers
    assert "private_must_not_escape" not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault",
    ["peer", "host", "origin", "forwarded", "credential", "query", "method", "server"],
)
async def test_readiness_refuses_remote_wronghost_forwarded_or_authenticated_requests(
    monkeypatch, fault
):
    app = wrapper(monkeypatch)
    peer = "192.0.2.1" if fault == "peer" else "127.0.0.1"
    base = "http://localhost:18282" if fault == "server" else "http://127.0.0.1:18282"
    headers = {}
    if fault == "host":
        headers["host"] = "evil.invalid"
    elif fault == "origin":
        headers["origin"] = "https://evil.invalid"
    elif fault == "forwarded":
        headers["x-forwarded-for"] = "127.0.0.1"
    elif fault == "credential":
        headers["authorization"] = "Bearer private_must_not_escape"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=(peer, 1234)), base_url=base
    ) as client:
        response = await client.request(
            "POST" if fault == "method" else "GET",
            desktop.READINESS_PATH + ("?nonce=1" if fault == "query" else ""),
            headers=headers,
        )
    assert response.status_code == 403 and response.json() == {"status": "REFUSED"}
    assert (
        "private_must_not_escape" not in response.text and "1" * 64 not in response.text
    )


class Headers:
    def get(self, name, default=None):
        return default

    def get_content_type(self):
        return "application/json"


class ReadyResponse:
    status = 200
    headers = Headers()

    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def read(self, maximum):
        assert maximum == 4097
        return self.body


@pytest.mark.parametrize(
    "fault",
    [None, "nonce", "pid", "package", "commit", "mode", "extra", "bool", "whitespace"],
)
def test_parent_rechecks_native_pid_and_exact_canonical_readiness_before_browser(
    tmp_path, monkeypatch, fault
):
    value = plan(tmp_path)
    record = {
        "schema": desktop.READINESS_SCHEMA,
        "nonce": "1" * 64,
        "pid": 1234,
        "package_identity_sha256": value.package_identity_sha256,
        "source_commit": value.source_commit,
        "mode": value.mode,
        "execution_authority": False,
    }
    if fault:
        key, changed = {
            "nonce": ("nonce", "2" * 64),
            "pid": ("pid", 999),
            "package": ("package_identity_sha256", "c" * 64),
            "commit": ("source_commit", "c" * 40),
            "mode": ("mode", "live"),
            "extra": ("passed", True),
            "bool": ("execution_authority", 0),
            "whitespace": ("schema", desktop.READINESS_SCHEMA),
        }[fault]
        record[key] = changed
    body = desktop.package.canonical(record) + (b" " if fault == "whitespace" else b"")
    monkeypatch.setattr(desktop, "_listening_pid", lambda _: 1234)

    def opener(*handlers):
        assert type(handlers[0]) is desktop.ProxyHandler and handlers[0].proxies == {}
        assert type(handlers[1]) is desktop._NoRedirect

        def open_request(request, timeout):
            assert request.full_url == "http://127.0.0.1:18282" + desktop.READINESS_PATH
            assert "1" * 64 not in request.full_url and timeout == 0.5
            return ReadyResponse(body)

        return SimpleNamespace(open=open_request)

    monkeypatch.setattr(desktop, "build_opener", opener)
    if fault:
        with pytest.raises(ValueError):
            desktop._ready(value, "1" * 64, 1234)
    else:
        assert desktop._ready(value, "1" * 64, 1234) is True


def test_other_process_on_port_refuses_before_any_http_probe(tmp_path, monkeypatch):
    monkeypatch.setattr(desktop, "_listening_pid", lambda _: 999)
    monkeypatch.setattr(
        desktop, "build_opener", lambda *_: pytest.fail("foreign listener was probed")
    )
    with pytest.raises(ValueError):
        desktop._ready(plan(tmp_path), "1" * 64, 1234)


class ChildInput:
    def __init__(self):
        self.parts = []
        self.closed = False

    def write(self, part):
        self.parts.append(part)

    def flush(self):
        pass

    def close(self):
        self.closed = True


class Child:
    pid = 1234

    def __init__(self):
        self.stdin = ChildInput()
        self.terminated = False

    def poll(self):
        return 0 if self.terminated else None

    def terminate(self):
        self.terminated = True

    def wait(self, timeout):
        assert timeout == 5
        return 0


def process_fixture(tmp_path, monkeypatch, *, browser=True):
    value = plan(tmp_path)
    process, observed, browsers = Child(), [], []
    monkeypatch.setattr(desktop.sys, "platform", "win32")
    monkeypatch.setattr(
        desktop.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False
    )
    monkeypatch.setattr(desktop, "_require_free_port", lambda _: None)
    monkeypatch.setattr(desktop, "_plan", lambda _: value)
    monkeypatch.setattr(desktop, "_listening_pid", lambda _: 1234)
    monkeypatch.setattr(desktop, "_ready", lambda *_: True)
    monkeypatch.setattr(desktop, "_owned_process_image", lambda *_, **__: 123456)
    monkeypatch.setattr(desktop.secrets, "token_hex", lambda _: "1" * 64)
    monkeypatch.setenv("OKX_LIVE_API_SECRET", "credential_must_not_escape")
    monkeypatch.setenv("CTCC_API_TOKEN", "credential_must_not_escape")
    monkeypatch.setenv("HTTP_PROXY", "http://credential_must_not_escape")
    monkeypatch.setenv("__PYVENV_LAUNCHER__", "foreign_launcher_must_not_escape")

    def create(command, **kwargs):
        observed.append((command, kwargs))
        return process

    monkeypatch.setattr(desktop.subprocess, "Popen", create)
    import webbrowser

    monkeypatch.setattr(
        webbrowser, "open", lambda url, **_: browsers.append(url) or browser
    )
    return value, process, observed, browsers


def test_owned_hidden_child_has_no_secret_or_nonce_in_arguments_environment_logs_or_url(
    tmp_path, monkeypatch
):
    value, process, observed, browsers = process_fixture(tmp_path, monkeypatch)
    desktop.launch(value)
    command, options = observed[0]
    assert "credential_must_not_escape" not in str((command, options, browsers))
    assert "foreign_launcher_must_not_escape" not in str((command, options, browsers))
    assert command[0] == str(value.base_interpreter)
    assert options["env"]["__PYVENV_LAUNCHER__"] == str(value.interpreter)
    assert "1" * 64 not in str((command, options, browsers))
    assert options["stdout"] is options["stderr"] is desktop.subprocess.DEVNULL
    assert options["creationflags"] == 0x08000000
    assert "--desktop-readiness-stdin" in command and "--profile" in command
    assert json.loads(b"".join(process.stdin.parts)) == {
        "schema": desktop.STARTUP_SCHEMA,
        "nonce": "1" * 64,
        "profile_sha256": "c" * 64,
    }
    assert process.stdin.closed and not process.terminated
    assert browsers == ["http://127.0.0.1:18282/dashboard"]


def test_port_collision_does_not_spawn_or_open_a_browser(tmp_path, monkeypatch):
    value, _, observed, browsers = process_fixture(tmp_path, monkeypatch)

    def collision(_):
        raise OSError("in use")

    monkeypatch.setattr(desktop, "_require_free_port", collision)
    with pytest.raises(OSError):
        desktop.launch(value)
    assert not observed and not browsers


def test_caller_constructed_plan_cannot_select_another_interpreter(
    tmp_path, monkeypatch
):
    value, _, observed, browsers = process_fixture(tmp_path, monkeypatch)
    forged = replace(value, interpreter=tmp_path / "foreign.exe")
    with pytest.raises(ValueError):
        desktop.launch(forged)
    assert not observed and not browsers


def test_selected_private_profile_drift_refuses_before_process_start(
    tmp_path, monkeypatch
):
    value, _, observed, browsers = process_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(
        desktop, "_plan", lambda _: replace(value, profile_sha256="d" * 64)
    )
    with pytest.raises(ValueError):
        desktop.launch(value)
    assert not observed and not browsers


def test_failed_new_child_is_stopped_without_touching_an_existing_process(
    tmp_path, monkeypatch
):
    value, process, _, browsers = process_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(desktop, "_ready", lambda *_: False)
    samples = iter((0, 56))
    monkeypatch.setattr(desktop.time, "monotonic", lambda: next(samples))
    with pytest.raises(ValueError):
        desktop.launch(value)
    assert process.terminated and not browsers


def test_browser_failure_keeps_verified_ready_child_and_reports_only_local_url(
    tmp_path, monkeypatch
):
    value, process, _, _ = process_fixture(tmp_path, monkeypatch, browser=False)
    with pytest.raises(desktop.BrowserNotOpened) as error:
        desktop.launch(value)
    assert (
        error.value.url == "http://127.0.0.1:18282/dashboard" and not process.terminated
    )


def test_picker_cancel_never_autoselects_a_profile_or_starts_a_process(monkeypatch):
    monkeypatch.setattr(desktop, "_select_profile", lambda: None)
    monkeypatch.setattr(
        desktop, "_plan", lambda _: pytest.fail("profile automatically chosen")
    )
    monkeypatch.setattr(
        desktop, "launch", lambda _: pytest.fail("process automatically started")
    )
    assert desktop.main() == 0


def test_private_configuration_error_is_never_reflected_into_ui(monkeypatch):
    def refused():
        raise ValueError("credential_must_not_escape")

    messages = []
    monkeypatch.setattr(desktop, "_select_profile", refused)
    monkeypatch.setattr(desktop, "_message", messages.append)
    assert desktop.main() == 1
    assert messages and "credential_must_not_escape" not in str(messages)


def test_native_windows_listener_pid_and_collision_are_measured_without_an_application():
    if sys.platform != "win32":
        with pytest.raises(ValueError):
            desktop._listening_pid(18282)
        return
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        assert desktop._listening_pid(port) == os.getpid()
        with pytest.raises(OSError):
            desktop._require_free_port(port)


def test_native_windows_process_handle_image_birth_and_hash_are_observed(tmp_path):
    if sys.platform != "win32":
        with pytest.raises(ValueError):
            desktop._owned_process_image(
                SimpleNamespace(poll=lambda: None), plan(tmp_path)
            )
        return
    import ctypes

    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.GetCurrentProcess.argtypes, api.GetCurrentProcess.restype = [], ctypes.c_void_p
    process = SimpleNamespace(
        poll=lambda: None, pid=os.getpid(), _handle=api.GetCurrentProcess()
    )
    image = __import__("pathlib").Path(sys._base_executable).resolve()
    value = replace(
        plan(tmp_path),
        base_interpreter=image,
        base_interpreter_sha256=desktop.package.sha(
            desktop.package.regular(image, 20_000_000)
        ),
    )
    stamp = desktop._owned_process_image(process, value)
    assert desktop._owned_process_image(process, value, stamp) == stamp
    with pytest.raises(ValueError):
        desktop._owned_process_image(process, value, stamp + 1)
    with pytest.raises(ValueError):
        desktop._owned_process_image(
            process, replace(value, base_interpreter_sha256="f" * 64)
        )
    with pytest.raises(ValueError):
        desktop._owned_process_image(
            process, replace(value, base_interpreter=tmp_path / "foreign.exe")
        )
    with pytest.raises(ValueError):
        desktop._owned_process_image(
            SimpleNamespace(
                poll=lambda: None, pid=os.getpid() + 1, _handle=process._handle
            ),
            value,
        )


def test_direct_base_image_drift_refuses_before_stdin_or_browser(tmp_path, monkeypatch):
    value, process, _, browsers = process_fixture(tmp_path, monkeypatch)

    def reject(*_):
        raise ValueError("image_changed")

    monkeypatch.setattr(desktop, "_owned_process_image", reject)
    with pytest.raises(ValueError):
        desktop.launch(value)
    assert process.terminated and process.stdin.parts == [] and browsers == []


def test_supervisor_reopens_only_original_still_owned_verified_child(
    tmp_path, monkeypatch
):
    value, process, _, browsers = process_fixture(tmp_path, monkeypatch)
    owner = desktop.launch(value)
    desktop.reopen_owned_desktop_v1(owner)
    assert len(browsers) == 2 and process.terminated is False
    monkeypatch.setattr(desktop, "_listening_pid", lambda _: 999)
    with pytest.raises(ValueError):
        desktop.reopen_owned_desktop_v1(owner)
    assert len(browsers) == 2 and process.terminated is False


def test_supervisor_rechecks_profile_before_reopening(tmp_path, monkeypatch):
    value, process, _, browsers = process_fixture(tmp_path, monkeypatch)
    owner = desktop.launch(value)
    monkeypatch.setattr(
        desktop, "_plan", lambda _: replace(value, profile_sha256="f" * 64)
    )
    with pytest.raises(ValueError):
        desktop.reopen_owned_desktop_v1(owner)
    assert len(browsers) == 1 and process.terminated is False


def test_caller_created_session_cannot_reopen_or_stop_a_process():
    forged = desktop.DesktopSessionV1()
    with pytest.raises(ValueError):
        desktop.reopen_owned_desktop_v1(forged)
    with pytest.raises(ValueError):
        desktop.stop_owned_desktop_v1(forged)


@pytest.mark.parametrize("confirmed", [False, True])
def test_native_supervisor_stop_requires_explicit_confirmation(
    tmp_path, monkeypatch, confirmed
):
    from scripts import desktop_windows_supervisor_v1 as supervisor

    value, process, _, _ = process_fixture(tmp_path, monkeypatch)
    owner = desktop.launch(value)
    monkeypatch.setattr(supervisor, "_confirm_stop", lambda _: confirmed)
    assert supervisor._command(owner, 101, 123) is confirmed
    assert process.terminated is confirmed
    if confirmed:
        with pytest.raises(ValueError):
            desktop.stop_owned_desktop_v1(owner)


def test_main_supervises_only_returned_owned_child(tmp_path, monkeypatch):
    from scripts import desktop_windows_supervisor_v1 as supervisor

    value, process, _, _ = process_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(desktop, "_select_profile", lambda: value.profile)
    owners = []
    monkeypatch.setattr(supervisor, "supervise_owned_desktop_v1", owners.append)
    assert desktop.main() == 0
    assert len(owners) == 1 and desktop._session(owners[0])[0] is process


def test_supervisor_failure_never_claims_ready_child_was_not_started(
    tmp_path, monkeypatch
):
    from scripts import desktop_windows_supervisor_v1 as supervisor

    value, process, _, _ = process_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(desktop, "_select_profile", lambda: value.profile)

    def fail(_):
        raise ValueError("private_must_not_escape")

    messages = []
    monkeypatch.setattr(supervisor, "supervise_owned_desktop_v1", fail)
    monkeypatch.setattr(desktop, "_message", messages.append)
    assert desktop.main() == 1 and process.terminated is False
    assert "本機工具已啟動" in str(messages) and "private_must_not_escape" not in str(
        messages
    )


def test_actual_windows_direct_base_protocol_preserves_venv_and_native_listener_pid(
    tmp_path,
):
    if sys.platform != "win32":
        with pytest.raises(ValueError):
            desktop._require_free_port(18368)
        return
    output = tmp_path / "public-process-proof.json"
    code = (
        "import json,os,socket,sys;from pathlib import Path;"
        "s=socket.socket();s.bind(('127.0.0.1',0));s.listen(1);"
        "Path(sys.argv[1]).write_text(json.dumps({'pid':os.getpid(),"
        "'port':s.getsockname()[1],'executable_exact':sys.executable==sys.argv[2],"
        "'prefix_exact':sys.prefix==sys.argv[3],'version':list(sys.version_info[:3])}));"
        "sys.stdin.buffer.read(1)"
    )
    base = Path(sys._base_executable).resolve()
    environment = {
        name: value
        for name, value in os.environ.items()
        if name.upper() in desktop._ENV_NAMES
    }
    environment["__PYVENV_LAUNCHER__"] = sys.executable
    child = subprocess.Popen(
        [str(base), "-I", "-B", "-c", code, str(output), sys.executable, sys.prefix],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW,
        env=environment,
    )
    try:
        deadline = time.monotonic() + 10
        while not output.exists() and time.monotonic() < deadline:
            assert child.poll() is None
            time.sleep(0.02)
        actual = json.loads(output.read_bytes())
        assert actual["pid"] == child.pid == desktop._listening_pid(actual["port"])
        assert actual["executable_exact"] is actual["prefix_exact"] is True
        assert actual["version"] == list(sys.version_info[:3])
        value = replace(
            plan(tmp_path),
            base_interpreter=base,
            base_interpreter_sha256=desktop.package.sha(
                desktop.package.regular(base, 20_000_000)
            ),
        )
        assert desktop._owned_process_image(child, value) > 0
    finally:
        if child.poll() is None:
            child.terminate()
        child.wait(timeout=5)
