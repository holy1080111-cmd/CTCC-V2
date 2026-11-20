"""User-picked pinned desktop launch; no credentials, Arm or configuration wizard.

The batch file lives in the exact archived source directory. Its isolated
pythonw interpreter opens only a native private-profile picker. The existing
profile remains the independent package trust pin. No token is passed to a
process, an environment variable, a URL, a log or the readiness endpoint.
"""

from __future__ import annotations

import ctypes
import os
import re
import secrets
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.error import URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

if __package__ in (None, ""):
    # -I deliberately removes script/cwd discovery. Only this defining source
    # root is added; read_profile then proves the external exact-package pin.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import windows_tool_0058_v1 as package

READINESS_PATH = "/_ctcc/desktop-ready/v1"
READINESS_SCHEMA = "ctcc.windows_tool.desktop_readiness.v1"
STARTUP_SCHEMA = "ctcc.windows_tool.desktop_startup.v1"
_BRIDGE = "import runpy,sys;root,module=sys.argv[1:3];sys.path.insert(0,root);sys.argv=[module,*sys.argv[3:]];runpy.run_module(module,run_name='__main__')"
_ENV_NAMES = frozenset(
    (
        "SYSTEMROOT",
        "WINDIR",
        "PATH",
        "TEMP",
        "TMP",
        "USERPROFILE",
        "APPDATA",
        "LOCALAPPDATA",
    )
)


class BrowserNotOpened(ValueError):
    def __init__(self, mode, port):
        _need(
            mode in ("analysis", "demo", "live")
            and type(port) is int
            and 1024 <= port <= 65535
        )
        self.url = f"http://127.0.0.1:{port}/" + ("" if mode == "live" else "dashboard")
        super().__init__("ctcc_desktop_browser_not_opened")


@dataclass(frozen=True, slots=True, repr=False)
class DesktopLaunchPlanV1:
    profile: Path
    root: Path
    interpreter: Path
    package_identity_sha256: str
    source_commit: str
    mode: str
    port: int
    profile_sha256: str
    execution_authority: bool = field(default=False, init=False)


def _need(value):
    if value is not True:
        raise ValueError("ctcc_desktop_launch_refused") from None


def _select_profile():
    """A real user selection, never automatic discovery or remembered choice."""
    from ctypes import wintypes as w

    _need(sys.platform == "win32")

    class OpenFileName(ctypes.Structure):
        _fields_ = [
            ("lStructSize", w.DWORD),
            ("hwndOwner", w.HWND),
            ("hInstance", w.HINSTANCE),
            ("lpstrFilter", w.LPCWSTR),
            ("lpstrCustomFilter", w.LPWSTR),
            ("nMaxCustFilter", w.DWORD),
            ("nFilterIndex", w.DWORD),
            ("lpstrFile", w.LPWSTR),
            ("nMaxFile", w.DWORD),
            ("lpstrFileTitle", w.LPWSTR),
            ("nMaxFileTitle", w.DWORD),
            ("lpstrInitialDir", w.LPCWSTR),
            ("lpstrTitle", w.LPCWSTR),
            ("Flags", w.DWORD),
            ("nFileOffset", w.WORD),
            ("nFileExtension", w.WORD),
            ("lpstrDefExt", w.LPCWSTR),
            ("lCustData", w.LPARAM),
            ("lpfnHook", ctypes.c_void_p),
            ("lpTemplateName", w.LPCWSTR),
            ("pvReserved", ctypes.c_void_p),
            ("dwReserved", w.DWORD),
            ("FlagsEx", w.DWORD),
        ]

    api = ctypes.WinDLL("comdlg32", use_last_error=True)
    api.GetOpenFileNameW.argtypes = [ctypes.POINTER(OpenFileName)]
    api.GetOpenFileNameW.restype = w.BOOL
    api.CommDlgExtendedError.argtypes = []
    api.CommDlgExtendedError.restype = w.DWORD
    filename = ctypes.create_unicode_buffer(32768)
    filters = ctypes.create_unicode_buffer(
        "CTCC settings (tool-profile.json)\0tool-profile.json\0JSON (*.json)\0*.json\0\0"
    )
    value = OpenFileName()
    value.lStructSize = ctypes.sizeof(value)
    value.lpstrFile, value.nMaxFile = ctypes.cast(filename, w.LPWSTR), len(filename)
    value.lpstrFilter = ctypes.cast(filters, w.LPCWSTR)
    value.nFilterIndex = 1
    value.lpstrInitialDir = str(Path.home() / "AppData/Local")
    value.lpstrTitle = "CTCC：選擇這個版本的私人設定檔"
    # Existing file/path, Explorer UI, no cwd mutation and no Recent Documents.
    value.Flags = 0x1000 | 0x800 | 0x80000 | 0x8 | 0x2000000
    if not api.GetOpenFileNameW(ctypes.byref(value)):
        _need(api.CommDlgExtendedError() == 0)
        return None
    return Path(filename.value)


def _plan(profile):
    from app.config.windows_tool_profile_0058_v1 import _private, read_profile

    _need(sys.platform == "win32" and type(profile) is type(Path()))
    _need(
        profile.is_absolute() and re.fullmatch(r"[A-Za-z]:", profile.drive) is not None
    )
    profile = profile.absolute()
    original = _private(profile)
    document = read_profile(profile)
    root = Path(document["package_root"])
    _need(root / "source" == package._SOURCE)
    expected = root / "runtime/Scripts"
    _need(
        Path(sys.executable).resolve()
        in (expected / "pythonw.exe", expected / "python.exe")
    )
    package.verify_dependencies(root)
    identity = package.verify(root, document["package_identity_sha256"])
    _need(_private(profile) == original)
    interpreter = expected / "python.exe"
    package.regular(interpreter, 20_000_000)
    # Discard the settings/token mapping. Only safe launch metadata crosses
    # this boundary, while the child independently rereads the private file.
    return DesktopLaunchPlanV1(
        profile,
        root,
        interpreter,
        document["package_identity_sha256"],
        identity["source_commit"],
        document["mode"],
        document["port"],
        package.sha(original),
    )


def _require_free_port(port):
    _need(sys.platform == "win32")
    _need(type(port) is int and 1024 <= port <= 65535)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        probe.bind(("127.0.0.1", port))


def _listening_pid(port):
    """Actual IPv4 listener ownership; JSON from another PID never suffices."""
    from ctypes import wintypes as w

    _need(sys.platform == "win32")
    api = ctypes.WinDLL("iphlpapi", use_last_error=True)
    api.GetExtendedTcpTable.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(w.DWORD),
        w.BOOL,
        w.ULONG,
        w.ULONG,
        w.ULONG,
    ]
    api.GetExtendedTcpTable.restype = w.ULONG
    size = w.DWORD()
    result = api.GetExtendedTcpTable(
        None, ctypes.byref(size), False, socket.AF_INET, 3, 0
    )
    _need(result == 122 and 4 <= size.value <= 1_000_000)
    buffer = ctypes.create_string_buffer(size.value)
    _need(
        api.GetExtendedTcpTable(buffer, ctypes.byref(size), False, socket.AF_INET, 3, 0)
        == 0
    )

    class Row(ctypes.Structure):
        _fields_ = [
            (name, w.DWORD)
            for name in (
                "state",
                "address",
                "port",
                "remote_address",
                "remote_port",
                "pid",
            )
        ]

    count = w.DWORD.from_buffer(buffer).value
    _need(count <= 16384 and 4 + count * ctypes.sizeof(Row) <= size.value)
    found = []
    for index in range(count):
        row = Row.from_buffer(buffer, 4 + index * ctypes.sizeof(Row))
        if (
            row.state == 2
            and row.address == int.from_bytes(socket.inet_aton("127.0.0.1"), "little")
            and socket.ntohs(row.port & 0xFFFF) == port
        ):
            _need(row.pid > 0)
            found.append(row.pid)
    _need(len(found) <= 1)
    return found[0] if found else None


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, url):
        return None


def _ready(plan, nonce, pid):
    owner = _listening_pid(plan.port)
    if owner is None:
        return False
    _need(owner == pid)
    opener = build_opener(ProxyHandler({}), _NoRedirect())
    request = Request(
        f"http://127.0.0.1:{plan.port}{READINESS_PATH}",
        headers={"Accept": "application/json", "Accept-Encoding": "identity"},
    )
    with opener.open(request, timeout=0.5) as response:
        _need(
            response.status == 200
            and response.headers.get("Content-Encoding", "identity") == "identity"
        )
        _need(response.headers.get_content_type() == "application/json")
        raw = response.read(4097)
    _need(0 < len(raw) <= 4096)
    expected = {
        "schema": READINESS_SCHEMA,
        "nonce": nonce,
        "pid": pid,
        "package_identity_sha256": plan.package_identity_sha256,
        "source_commit": plan.source_commit,
        "mode": plan.mode,
        "execution_authority": False,
    }
    _need(raw == package.canonical(expected))
    return True


def launch(plan):
    """Only the new owned child can cause a browser to open; no auto-activation."""
    _need(type(plan) is DesktopLaunchPlanV1 and sys.platform == "win32")
    # A caller-created plan cannot select an interpreter or change the private
    # profile after its first verification. The child repeats the same pin.
    _need(_plan(plan.profile) == plan)
    _require_free_port(plan.port)
    nonce = secrets.token_hex(32)
    environment = {
        name: value for name, value in os.environ.items() if name.upper() in _ENV_NAMES
    }
    command = [
        str(plan.interpreter),
        "-I",
        "-B",
        "-c",
        _BRIDGE,
        str(plan.root / "source"),
        "scripts.launch_windows_tool_0058_v1",
        "--profile",
        str(plan.profile),
        "--serve",
        "--desktop-readiness-stdin",
    ]
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=plan.root,
        env=environment,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    ready_child = False
    try:
        _need(
            type(process.pid) is int and process.pid > 0 and process.stdin is not None
        )
        message = package.canonical(
            {
                "schema": STARTUP_SCHEMA,
                "nonce": nonce,
                "profile_sha256": plan.profile_sha256,
            }
        )
        process.stdin.write(message)
        process.stdin.flush()
        process.stdin.close()
        deadline = time.monotonic() + 55
        while time.monotonic() < deadline:
            _need(process.poll() is None)
            try:
                ready = _ready(plan, nonce, process.pid)
            except (URLError, TimeoutError, ConnectionError):
                ready = False
            if ready:
                import webbrowser

                # Recheck native PID immediately before opening a fixed URL.
                _need(
                    process.poll() is None and _listening_pid(plan.port) == process.pid
                )
                suffix = "/" if plan.mode == "live" else "/dashboard"
                url = f"http://127.0.0.1:{plan.port}{suffix}"
                ready_child = True
                try:
                    opened = webbrowser.open(url, new=2, autoraise=True)
                except Exception:  # noqa: BLE001 -- retain a genuinely ready child; never expose browser internals.
                    opened = False
                if opened is not True:
                    raise BrowserNotOpened(plan.mode, plan.port) from None
                return
            time.sleep(0.2)
        _need(False)
    finally:
        if not ready_child and process.poll() is None:
            # Only this newly created child is terminated. No existing process,
            # service, DB, journal or protection is selected or changed.
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                _need(False)


def _message(text):
    _need(sys.platform == "win32")
    api = ctypes.WinDLL("user32", use_last_error=True)
    api.MessageBoxW.argtypes = [
        ctypes.c_void_p,
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        ctypes.c_uint,
    ]
    api.MessageBoxW.restype = ctypes.c_int
    api.MessageBoxW(None, text, "CTCC", 0x30)


def main():
    try:
        selected = _select_profile()
        if selected is None:
            return 0
        launch(_plan(selected))
        return 0
    except BrowserNotOpened as error:
        _message("工具已啟動，但瀏覽器未能自動開啟。\n請在瀏覽器開啟：" + error.url)
        return 1
    except Exception:  # noqa: BLE001 -- no private path, settings or error text is displayed/logged.
        _message(
            "設定或啟動核對未通過，工具尚未啟用。\n請確認已安裝此版本，並選擇此版本的私人設定檔。\n既有交易與保護單不會因此變更。"
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
