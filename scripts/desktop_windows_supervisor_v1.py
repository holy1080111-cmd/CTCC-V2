"""Native controls for one retained desktop child; no HTTP trading authority."""

from __future__ import annotations

import ctypes
import sys

from scripts import desktop_windows_tool_0058_v1 as desktop


def _confirm_stop(window):
    api = ctypes.WinDLL("user32", use_last_error=True)
    api.MessageBoxW.argtypes = [
        ctypes.c_void_p,
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        ctypes.c_uint,
    ]
    api.MessageBoxW.restype = ctypes.c_int
    return (
        api.MessageBoxW(
            window,
            "要停止這次啟動的本機工具嗎？\n\n"
            "這只會停止本機程式，不會平倉、取消交易所保護單或清除交易紀錄。\n"
            "若當下仍有操作未完成，下次開啟必須重新核對結果。",
            "CTCC：停止本機工具",
            0x4 | 0x20 | 0x100,
        )
        == 6
    )


def _command(owner, command, window):
    desktop._session(owner)
    if command == 100:
        desktop.reopen_owned_desktop_v1(owner)
        return False
    if command == 101 and _confirm_stop(window):
        desktop.stop_owned_desktop_v1(owner)
        return True
    return False


def supervise_owned_desktop_v1(owner):
    """Keep the original handle; never reconnect by a saved PID or port."""
    from ctypes import wintypes as w

    desktop._need(sys.platform == "win32")
    process = desktop._session(owner)[0]
    user = ctypes.WinDLL("user32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    gdi = ctypes.WinDLL("gdi32", use_last_error=True)
    result_type = ctypes.c_ssize_t
    procedure = ctypes.WINFUNCTYPE(result_type, w.HWND, w.UINT, w.WPARAM, w.LPARAM)

    class WindowClass(ctypes.Structure):
        _fields_ = [
            ("style", w.UINT),
            ("procedure", procedure),
            ("class_extra", ctypes.c_int),
            ("window_extra", ctypes.c_int),
            ("instance", w.HINSTANCE),
            ("icon", w.HANDLE),
            ("cursor", w.HANDLE),
            ("background", w.HANDLE),
            ("menu", w.LPCWSTR),
            ("name", w.LPCWSTR),
        ]

    signatures = {
        "RegisterClassW": ([ctypes.POINTER(WindowClass)], w.ATOM),
        "UnregisterClassW": ([w.LPCWSTR, w.HINSTANCE], w.BOOL),
        "CreateWindowExW": (
            [
                w.DWORD,
                w.LPCWSTR,
                w.LPCWSTR,
                w.DWORD,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                w.HWND,
                w.HMENU,
                w.HINSTANCE,
                ctypes.c_void_p,
            ],
            w.HWND,
        ),
        "DefWindowProcW": ([w.HWND, w.UINT, w.WPARAM, w.LPARAM], result_type),
        "DestroyWindow": ([w.HWND], w.BOOL),
        "ShowWindow": ([w.HWND, ctypes.c_int], w.BOOL),
        "UpdateWindow": ([w.HWND], w.BOOL),
        "IsWindow": ([w.HWND], w.BOOL),
        "SetTimer": (
            [w.HWND, ctypes.c_size_t, w.UINT, ctypes.c_void_p],
            ctypes.c_size_t,
        ),
        "KillTimer": ([w.HWND, ctypes.c_size_t], w.BOOL),
        "PostQuitMessage": ([ctypes.c_int], None),
        "GetMessageW": ([ctypes.POINTER(w.MSG), w.HWND, w.UINT, w.UINT], ctypes.c_int),
        "TranslateMessage": ([ctypes.POINTER(w.MSG)], w.BOOL),
        "DispatchMessageW": ([ctypes.POINTER(w.MSG)], result_type),
        "SendMessageW": ([w.HWND, w.UINT, w.WPARAM, w.LPARAM], result_type),
    }
    for name, (arguments, returned) in signatures.items():
        function = getattr(user, name)
        function.argtypes, function.restype = arguments, returned
    kernel.GetModuleHandleW.argtypes, kernel.GetModuleHandleW.restype = (
        [w.LPCWSTR],
        w.HMODULE,
    )
    gdi.GetStockObject.argtypes, gdi.GetStockObject.restype = [ctypes.c_int], w.HANDLE
    instance = kernel.GetModuleHandleW(None)
    class_name = "CTCCOwnedDesktopSupervisorV1"

    @procedure
    def callback(window, message, wp, lp):
        try:
            if message == 0x111 and wp & 0xFFFF in (100, 101):
                if _command(owner, wp & 0xFFFF, window):
                    user.DestroyWindow(window)
                return 0
            if message == 0x10:
                if _command(owner, 101, window):
                    user.DestroyWindow(window)
                return 0
            if message == 0x113:
                if process.poll() is not None:
                    desktop._SESSIONS.pop(owner, None)
                    user.DestroyWindow(window)
                return 0
            if message == 2:
                user.KillTimer(window, 1)
                user.PostQuitMessage(0)
                return 0
        except BaseException:  # noqa: BLE001 -- no private exception text may escape a native callback.
            desktop._message(
                "本機工具的核對或操作未完成。\n頁面不會改接其他程式；請保留交易紀錄並重新核對。"
            )
            return 0
        return user.DefWindowProcW(window, message, wp, lp)

    definition = WindowClass()
    definition.procedure, definition.instance = callback, instance
    definition.background, definition.name = 6, class_name
    desktop._need(bool(instance and user.RegisterClassW(ctypes.byref(definition))))
    window = None
    try:
        window = user.CreateWindowExW(
            0,
            class_name,
            "CTCC 工具",
            0x00CA0000,
            -2147483648,
            -2147483648,
            570,
            235,
            None,
            None,
            instance,
            None,
        )
        desktop._need(bool(window))
        controls = (
            (
                "STATIC",
                (
                    "工具已在這台電腦啟動。\n關閉瀏覽器分頁後，可從這裡再次開啟。\n"
                    "結束使用時，請按「停止本機工具」。"
                ),
                0,
                20,
                20,
                525,
                85,
            ),
            ("BUTTON", "重新開啟頁面", 100, 20, 125, 230, 38),
            ("BUTTON", "停止本機工具", 101, 285, 125, 230, 38),
        )
        font = gdi.GetStockObject(17)
        for kind, text, identifier, x, y, width, height in controls:
            child = user.CreateWindowExW(
                0,
                kind,
                text,
                0x50000000 | (0x10000 if identifier else 0),
                x,
                y,
                width,
                height,
                window,
                identifier,
                instance,
                None,
            )
            desktop._need(bool(child))
            user.SendMessageW(child, 0x30, font, 1)
        desktop._need(user.SetTimer(window, 1, 1000, None) == 1)
        user.ShowWindow(window, 4)
        user.UpdateWindow(window)
        message = w.MSG()
        while True:
            state = user.GetMessageW(ctypes.byref(message), None, 0, 0)
            desktop._need(state >= 0)
            if state == 0:
                break
            user.TranslateMessage(ctypes.byref(message))
            user.DispatchMessageW(ctypes.byref(message))
    finally:
        if window and user.IsWindow(window):
            user.DestroyWindow(window)
        desktop._need(bool(user.UnregisterClassW(class_name, instance)))
