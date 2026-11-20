"""Local interactive private setup. No secret argument or secret stdout exists."""

from __future__ import annotations

import ctypes
import getpass
import re
import secrets
import sys
from pathlib import Path
from uuid import uuid4

from scripts import local_analysis_private_dir as acl
from scripts import windows_tool_0058_v1 as package


def create_private_root():
    """Apply the reviewed owner/System-only ACL only to one NEW tool leaf."""
    package.need(sys.platform == "win32")
    base = (Path.home() / "AppData/Local").absolute()
    package._directory(base)
    kernel, security = acl.api()
    filesystem = ctypes.create_unicode_buffer(32)
    package.need(
        kernel.GetDriveTypeW(base.anchor) == 3
        and bool(
            kernel.GetVolumeInformationW(
                base.anchor, None, 0, None, None, None, filesystem, len(filesystem)
            )
        )
        and filesystem.value == "NTFS"
    )
    user = acl.current_user(kernel, security)
    package.need(user != "S-1-5-18")
    path = base / ("ctcc-tool-" + uuid4().hex)
    descriptor = ctypes.c_void_p()
    package.need(
        bool(
            security.ConvertStringSecurityDescriptorToSecurityDescriptorW(
                f"O:{user}D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;{user})",
                1,
                ctypes.byref(descriptor),
                None,
            )
        )
    )
    try:
        attributes = acl._SecurityAttributes(
            ctypes.sizeof(acl._SecurityAttributes), descriptor, False
        )
        package.need(bool(kernel.CreateDirectoryW(str(path), ctypes.byref(attributes))))
    finally:
        kernel.LocalFree(descriptor)
    acl.verify_acl(kernel, security, path, user)
    package.need(not any(path.iterdir()))
    return path


def _secret(label):
    value = getpass.getpass(label + "（不顯示）：")
    package.need(
        bool(value) and len(value) <= 8192 and "\n" not in value and "\r" not in value
    )
    return value


def _path(label):
    path = Path(input(label + "：")).absolute()
    package.need(path.resolve(strict=True) == path)
    return path


def _new_notion_outbox(root):
    from app.trade_evidence.native_private_root_v2 import (
        mkdir_private_evidence_child_v2,
    )
    from app.trade_qualification.account_native_proof_storage import _root_context

    with _root_context(root) as directory:
        mkdir_private_evidence_child_v2(directory, "notion-outbox")
    return root / "notion-outbox"


def _link_existing_notion(values, root):
    """Optional existing private locators for one NEW profile; no remote IO."""
    values.update(notion_outbox_enabled=False, submission_outbox_enabled=False)
    selected = input(
        "報告同步：1 略過，2 連結這台電腦已核對的 Notion 設定（預設 1）："
    ).strip()
    package.need(selected in ("", "1", "2"))
    if selected != "2":
        return
    destination_file = _path("既有 Notion 目的地設定檔路徑")
    expected = input("這份目的地設定檔已核對的 SHA256：").strip().lower()
    package.need(re.fullmatch(r"[a-f0-9]{64}", expected) is not None)
    token_file = _path("既有 Notion 權杖檔路徑（不是權杖內容）")
    package.need(destination_file != token_file)
    from app.trade_evidence import notion_binding as binding
    from app.trade_evidence import storage

    # Close each private-file lease before the next independent read; the
    # destination and token may share one protected parent on Windows.
    with binding.private_file(destination_file) as fd:
        raw = storage._read_fd(fd, 16384)
    package.need(package.sha(raw) == expected)
    # This frozen model verifies exactly four distinct property IDs/names and
    # their title/rich_text roles. The independent worker must verify real GET
    # binding afresh when it is explicitly activated.
    binding.load_destination(destination_file, expected)
    binding.load_private_token(token_file)
    outbox = _new_notion_outbox(root)
    values.update(
        notion_outbox_destination_file=str(destination_file),
        notion_outbox_destination_sha256=expected,
        notion_outbox_token_file=str(token_file),
        notion_outbox_root=str(outbox),
        submission_outbox_root=str(outbox),
    )
    print("既有設定已連結；同步程式與報告佇列仍關閉，沒有送出任何內容。")


def configure(package_root, identity, mode, port):
    from app.config.settings import Settings
    from app.config.windows_tool_profile_0058_v1 import SCHEMA, _values
    from app.trade_evidence.native_private_root_v2 import (
        mkdir_private_evidence_child_v2,
    )

    package.verify(package_root, identity)
    package.need(package_root / "source" == package._SOURCE)
    package.verify_dependencies(package_root)
    package.need(
        mode in ("analysis", "demo", "live")
        and type(port) is int
        and 1024 <= port <= 65535
    )
    package.need(sys.stdin.isatty() and sys.stderr.isatty())
    root = create_private_root()
    token = secrets.token_urlsafe(48)
    values = {"api_token": token}
    runtime = None
    if mode == "demo":
        for name, label in (
            ("database_url", "Demo 限權寫入角色連線"),
            ("okx_demo_native_custody_database_url", "Demo custody 角色連線"),
            ("okx_demo_native_frame_reader_database_url", "Demo frame-reader 角色連線"),
            ("okx_demo_api_key", "Demo API key"),
            ("okx_demo_api_secret", "Demo API secret"),
            ("okx_demo_api_passphrase", "Demo API passphrase"),
        ):
            values[name] = _secret(label)
        ca = _path("已核對的 PostgreSQL CA 檔案")
        values["ctcc_native_pg_tls_ca_file"] = str(ca)
        values["ctcc_native_pg_tls_ca_sha256"] = package.sha(package.regular(ca, 65536))
        from app.trade_qualification.account_native_proof_storage import _root_context

        with _root_context(root) as directory:
            mkdir_private_evidence_child_v2(directory, "demo-evidence")
        evidence = root / "demo-evidence"
        values["okx_demo_native_evidence_root"] = str(evidence)
        # Native Control config/qualification owns risk, not legacy score or
        # continuous scheduler switches. Defaults stay conservative and OFF.
        values["okx_demo_allowed_symbols"] = "BTC-USDT-SWAP,ETH-USDT-SWAP"
        values["okx_demo_scan_symbols"] = values["okx_demo_allowed_symbols"]
        selected_profile = input(
            "Demo 策略設定：1 標準，2 加入 HighVol Momentum（預設 1）："
        ).strip()
        package.need(selected_profile in ("", "1", "2"))
        values["ctcc_demo_high_vol_momentum_v2"] = selected_profile == "2"
        print("策略設定只會寫入新設定檔；仍需完整對帳、空倉檢查與人工啟動。")
        _link_existing_notion(values, root)
    if mode == "live":
        print("Live 預設關閉。尚未配置專用 Live 角色及金鑰時，直接按 Enter。")
        filename = input("已審核的 Live 私人 runtime 設定檔路徑（可空白）：")
        if filename:
            from app.config.windows_tool_profile_0058_v1 import _private

            path = Path(filename).absolute()
            runtime = {"path": str(path), "sha256": package.sha(_private(path))}
            for name, label in (
                ("okx_live_api_key", "Live API key"),
                ("okx_live_api_secret", "Live API secret"),
                ("okx_live_api_passphrase", "Live API passphrase"),
                ("okx_live_expected_uid", "Live exact account UID"),
                ("okx_live_expected_main_uid", "Live main UID"),
            ):
                values[name] = _secret(label)
            region = input("Live 註冊區域 global / eea：")
            package.need(region in ("global", "eea"))
            values["okx_live_rest_base_url"] = {
                "global": "https://openapi.okx.com",
                "eea": "https://eea.okx.com",
            }[region]
    document = {
        "schema": SCHEMA,
        "package_root": str(package_root),
        "package_identity_sha256": identity,
        "mode": mode,
        "port": port,
        "settings": values,
        "live_runtime": runtime,
    }
    # Validation only. This does not import a runtime, create a database engine,
    # open an account, initialize counters, recover, clear a stop or grant Arm.
    Settings(_env_file=None, **_values(Settings, document))
    profile = root / "tool-profile.json"
    package._new(profile, package.canonical(document))
    package._new(root / "api_token", token.encode())
    print(
        package.canonical(
            {
                "status": "PRIVATE_CONFIGURATION_CREATED",
                "profile_file": str(profile),
                "token_file": str(root / "api_token"),
                "token_printed": False,
                "execution_authority": False,
                "database_observed": False,
            }
        ).decode()
    )
    return profile


def main(arguments=None):
    parser = package.PrivateParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--package", required=True, type=Path)
    parser.add_argument("--identity-sha256", required=True)
    parser.add_argument("--mode", required=True, choices=("analysis", "demo", "live"))
    parser.add_argument("--port", required=True, type=int)
    args = parser.parse_args(arguments)
    try:
        configure(args.package.absolute(), args.identity_sha256, args.mode, args.port)
        return 0
    except Exception:  # noqa: BLE001 -- never echo private user input.
        print('{"status":"REFUSED","code":"windows_tool_0058_configuration_refused"}')
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
