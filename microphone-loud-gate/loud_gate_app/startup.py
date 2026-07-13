"""Windows startup-task integration for Loud Gate."""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
from pathlib import Path
from ctypes import wintypes

from .config import app_dir, ensure_app_dir, log_path


TASK_NAME = "LoudGateMicRouter"
STARTUP_LAUNCHER_NAME = "startup_launcher.vbs"
SEE_MASK_NOCLOSEPROCESS = 0x00000040
INFINITE = 0xFFFFFFFF


class SHELLEXECUTEINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("fMask", ctypes.c_ulong),
        ("hwnd", wintypes.HWND),
        ("lpVerb", ctypes.c_wchar_p),
        ("lpFile", ctypes.c_wchar_p),
        ("lpParameters", ctypes.c_wchar_p),
        ("lpDirectory", ctypes.c_wchar_p),
        ("nShow", ctypes.c_int),
        ("hInstApp", wintypes.HINSTANCE),
        ("lpIDList", ctypes.c_void_p),
        ("lpClass", ctypes.c_wchar_p),
        ("hkeyClass", wintypes.HKEY),
        ("dwHotKey", wintypes.DWORD),
        ("hIcon", wintypes.HANDLE),
        ("hProcess", wintypes.HANDLE),
    ]


def entrypoint_path() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve()
    return Path(__file__).resolve().parents[1] / "loud_gate.py"


def application_command(*extra_args: str) -> list[str]:
    """Build a command that works from source and from a frozen one-file exe."""

    if getattr(sys, "frozen", False):
        return [str(Path(sys.executable).resolve()), *extra_args]
    return [sys.executable, str(entrypoint_path()), *extra_args]


def _run_elevated(extra_args: list[str]) -> None:
    """Run one maintenance operation through UAC and wait for its result."""

    command = application_command(*extra_args)
    executable, *parameters = command
    info = SHELLEXECUTEINFO()
    info.cbSize = ctypes.sizeof(SHELLEXECUTEINFO)
    info.fMask = SEE_MASK_NOCLOSEPROCESS
    info.lpVerb = "runas"
    info.lpFile = executable
    info.lpParameters = subprocess.list2cmdline(parameters)
    info.lpDirectory = str(entrypoint_path().parent)
    info.nShow = 1

    shell32 = ctypes.windll.shell32
    kernel32 = ctypes.windll.kernel32
    if not shell32.ShellExecuteExW(ctypes.byref(info)):
        error = ctypes.WinError()
        raise RuntimeError(f"Windows could not start the elevated maintenance task: {error}")

    try:
        kernel32.WaitForSingleObject(info.hProcess, INFINITE)
        exit_code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(exit_code)):
            raise RuntimeError(f"Windows could not read the elevated task result: {ctypes.WinError()}")
    finally:
        kernel32.CloseHandle(info.hProcess)

    if exit_code.value != 0:
        raise RuntimeError(f"The elevated maintenance task failed with exit code {exit_code.value}.")


def startup_launcher_path() -> Path:
    return app_dir() / STARTUP_LAUNCHER_NAME


def _vbs_literal(value: str) -> str:
    """Return a VBScript string literal with embedded quotes escaped."""

    return '"' + value.replace('"', '""') + '"'


def write_startup_launcher() -> Path:
    """Create a hidden launcher that reports a non-zero service exit visibly."""

    ensure_app_dir()
    launcher = startup_launcher_path()
    command = subprocess.list2cmdline(
        application_command("--runtime", "--background", "--autorun")
    )
    log_file = _vbs_literal(str(log_path()))
    vbs = (
        "On Error Resume Next\n"
        'Set shell = CreateObject("WScript.Shell")\n'
        f"command = {_vbs_literal(command)}\n"
        "exitCode = shell.Run(command, 0, True)\n"
        "errorNumber = Err.Number\n"
        "errorDescription = Err.Description\n"
        "On Error GoTo 0\n"
        "If errorNumber <> 0 Then\n"
        '    message = "Windows Script Host could not launch Loud Gate." & vbCrLf & _\n'
        '              "Error: " & errorDescription & vbCrLf & _\n'
        f'              "See the log for details: " & {log_file}\n'
        '    MsgBox message, 16, "Loud Gate startup failed"\n'
        "ElseIf exitCode <> 0 Then\n"
        '    message = "Loud Gate could not start from Windows startup." & vbCrLf & _\n'
        '              "Exit code: " & exitCode & vbCrLf & _\n'
        f'              "See the log for the original error: " & {log_file}\n'
        '    MsgBox message, 16, "Loud Gate startup failed"\n'
        "End If\n"
    )
    temporary = launcher.with_suffix(".tmp")
    temporary.write_text(vbs, encoding="utf-8", newline="")
    temporary.replace(launcher)
    return launcher


def launch_runtime() -> subprocess.Popen:
    """Start the independent runtime process without inheriting a console window."""

    command = application_command("--runtime", "--background")
    creation_flags = (
        getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
        | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
        | getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    )
    return subprocess.Popen(
        command,
        cwd=str(entrypoint_path().parent),
        creationflags=creation_flags,
        close_fds=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def install_startup_task(*, elevated: bool = False) -> None:
    launcher = write_startup_launcher()
    system_root = os.environ.get("SystemRoot")
    if not system_root:
        raise RuntimeError("SystemRoot is not available; cannot locate Windows Script Host.")

    wscript = str(Path(system_root) / "System32" / "wscript.exe")
    command = subprocess.list2cmdline([wscript, "//NoLogo", str(launcher)])
    result = subprocess.run(
        [
            "schtasks",
            "/Create",
            "/TN",
            TASK_NAME,
            "/SC",
            "ONLOGON",
            "/RL",
            "LIMITED",
            "/F",
            "/TR",
            command,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip()
        if not elevated and "access is denied" in message.lower():
            _run_elevated(["--install-startup", "--elevated"])
            return
        raise RuntimeError(
            f"Failed to create startup task: {message}"
        )


def uninstall_startup_task(*, elevated: bool = False) -> None:
    result = subprocess.run(
        ["schtasks", "/Delete", "/TN", TASK_NAME, "/F"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "").strip()
        if message and "cannot find" in message.lower():
            try:
                startup_launcher_path().unlink()
            except FileNotFoundError:
                pass
            return
        if not elevated and "access is denied" in message.lower():
            _run_elevated(["--uninstall-startup", "--elevated"])
            return
        raise RuntimeError(
            f"Failed to remove startup task: {message or 'unknown error'}"
        )

    try:
        startup_launcher_path().unlink()
    except FileNotFoundError:
        pass
