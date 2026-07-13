"""Windows startup-task integration for Loud Gate."""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
from pathlib import Path

from .config import app_dir, ensure_app_dir, log_path


TASK_NAME = "LoudGateMicRouter"
STARTUP_LAUNCHER_NAME = "startup_launcher.vbs"


def entrypoint_path() -> Path:
    return Path(__file__).resolve().parents[1] / "loud_gate.py"


def is_windows_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def relaunch_as_admin(extra_args: list[str]) -> None:
    script = str(entrypoint_path())
    params = subprocess.list2cmdline([script, *extra_args])
    rc = ctypes.windll.shell32.ShellExecuteW(
        None,
        "runas",
        sys.executable,
        params,
        None,
        1,
    )
    if rc <= 32:
        raise RuntimeError("Failed to relaunch elevated.")


def startup_launcher_path() -> Path:
    return app_dir() / STARTUP_LAUNCHER_NAME


def _vbs_literal(value: str) -> str:
    """Return a VBScript string literal with embedded quotes escaped."""

    return '"' + value.replace('"', '""') + '"'


def write_startup_launcher() -> Path:
    """Create a hidden launcher that reports a non-zero service exit visibly."""

    ensure_app_dir()
    launcher = startup_launcher_path()
    python_exe = str(Path(sys.executable).resolve())
    script = str(entrypoint_path())
    command = f"{_vbs_literal(python_exe)} {_vbs_literal(script)} --run --quiet"
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


def install_startup_task() -> None:
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
            "HIGHEST",
            "/F",
            "/TR",
            command,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Failed to create startup task: {result.stderr.strip() or result.stdout.strip()}"
        )


def uninstall_startup_task() -> None:
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
        raise RuntimeError(
            f"Failed to remove startup task: {message or 'unknown error'}"
        )

    try:
        startup_launcher_path().unlink()
    except FileNotFoundError:
        pass
