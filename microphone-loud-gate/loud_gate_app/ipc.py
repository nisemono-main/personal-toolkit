"""Small authenticated local control channel for the Loud Gate runtime."""

from __future__ import annotations

import ctypes
from multiprocessing.connection import Client, Listener
import os
import secrets
import threading
from typing import Any

from .config import app_dir, ensure_app_dir


PIPE_NAME = r"\\.\pipe\LoudGateRuntime"
PROTOCOL_VERSION = 1
AUTHKEY_FILE = "runtime.token"


class IpcError(RuntimeError):
    """Raised when the manager cannot communicate with the runtime."""


def authkey() -> bytes:
    """Use a per-user token so unrelated local processes cannot issue commands."""

    ensure_app_dir()
    path = app_dir() / AUTHKEY_FILE
    try:
        value = path.read_bytes()
    except FileNotFoundError:
        value = secrets.token_bytes(32)
        try:
            with path.open("xb") as file:
                file.write(value)
        except FileExistsError:
            value = path.read_bytes()
    if len(value) < 16:
        raise IpcError(f"The runtime control token at {path} is invalid.")
    return value


def request_runtime(command: str, **payload: Any) -> dict[str, Any]:
    """Send one request to the per-user runtime and return its response."""

    request = {"protocol": PROTOCOL_VERSION, "command": command, **payload}
    try:
        if os.name == "nt":
            if not ctypes.windll.kernel32.WaitNamedPipeW(PIPE_NAME, 500):
                raise IpcError(
                    "The Loud Gate runtime is not reachable. Start it with Run or install autorun."
                )
        connection = Client(PIPE_NAME, family="AF_PIPE", authkey=authkey())
        try:
            connection.send(request)
            response = connection.recv()
        finally:
            connection.close()
    except IpcError:
        raise
    except (EOFError, OSError, PermissionError) as exc:
        raise IpcError(
            "The Loud Gate runtime is not reachable. Start it with Run or install autorun."
        ) from exc

    if not isinstance(response, dict):
        raise IpcError("The Loud Gate runtime returned an invalid response.")
    return response


class RuntimeIpcServer:
    """Serve one-request-at-a-time control calls without touching audio buffers."""

    def __init__(self, handler):
        self._handler = handler
        self._listener: Listener | None = None
        self._thread = None

    def start(self) -> None:
        self._listener = Listener(PIPE_NAME, family="AF_PIPE", authkey=authkey())
        self._thread = threading.Thread(target=self._serve, name="RuntimeIpc", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        listener = self._listener
        self._listener = None
        if listener is not None:
            listener.close()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)

    def _serve(self) -> None:
        listener = self._listener
        if listener is None:
            return
        while self._listener is listener:
            try:
                connection = listener.accept()
            except (OSError, EOFError):
                break
            try:
                request = connection.recv()
                response = self._dispatch(request)
                connection.send(response)
            except (EOFError, OSError):
                pass
            except Exception as exc:
                try:
                    connection.send({"ok": False, "error": str(exc)})
                except (OSError, EOFError):
                    pass
            finally:
                connection.close()

    def _dispatch(self, request: object) -> dict[str, Any]:
        if not isinstance(request, dict) or request.get("protocol") != PROTOCOL_VERSION:
            return {"ok": False, "error": "Unsupported Loud Gate control protocol."}
        command = request.get("command")
        if not isinstance(command, str):
            return {"ok": False, "error": "The control request has no command."}
        result = self._handler(command, request)
        if not isinstance(result, dict):
            return {"ok": True, "result": result}
        return {"ok": True, **result}
