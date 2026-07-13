"""Windows startup-task integration for Loud Gate."""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
from pathlib import Path

from .config import app_dir, ensure_app_dir


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


def write_startup_launcher() -> Path:
    ensure_app_dir()
    launcher = startup_launcher_path()
    python_exe = str(Path(sys.executable).resolve())
    script = str(entrypoint_path())
    vbs = (
        'Set shell = CreateObject("WScript.Shell")\n'
        f'shell.Run Chr(34) & "{python_exe}" & Chr(34) & " " & Chr(34) & "{script}" & Chr(34) & " --run --quiet", 0, False\n'
    )
    launcher.write_text(vbs, encoding="utf-8")
    return launcher


def install_startup_task() -> None:
    launcher = write_startup_launcher()
    system_root = os.environ.get("SystemRoot")
    if not system_root:
        raise RuntimeError("SystemRoot is not available; cannot locate Windows Script Host.")

    wscript = str(Path(system_root) / "System32" / "wscript.exe")
    command = subprocess.list2cmdline([wscript, "//B", "//NoLogo", str(launcher)])
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
