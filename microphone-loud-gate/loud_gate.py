"""
loud_gate.py

Windows microphone gate for a real mic -> VB-Cable style routing setup.

What it does:
- Starts automatically at logon via a scheduled task you install once
- Keeps running and retries if the audio stack errors out
- Uses configurable global hotkeys to mute, stop, and adjust the limiter threshold
- Uses a lookahead peak limiter so the first transient cannot slip through

Typical routing:
  [Real microphone] -> this script -> [virtual playback/output device] -> apps use the matching capture/input side

Install:
  pip install numpy sounddevice
  python loud_gate.py --install-startup

First run without config:
  python loud_gate.py
  It will show only likely mic inputs and virtual-audio outputs, then save
  the config and exit.

Start it manually after setup:
  python loud_gate.py --run
"""

from __future__ import annotations

import argparse
import configparser
import ctypes
import logging
import math
import os
import subprocess
import signal
import sys
import threading
from collections import deque
from pathlib import Path
from typing import Callable

import numpy as np
import sounddevice as sd
from ctypes import wintypes


APP_NAME = "loud-gate"
TASK_NAME = "LoudGateMicRouter"
STARTUP_LAUNCHER_NAME = "startup_launcher.vbs"
CONFIG_VERSION = 5
CONFIG_SECTION = "loud-gate"
HOTKEY_CONFIG_SECTION = "hotkeys"

DEFAULT_THRESHOLD_DB = -18.0
DEFAULT_RELEASE_MS = 150.0
DEFAULT_LOOKAHEAD_MS = 25.0
DEFAULT_BLOCK_MS = 10.0
DEFAULT_STREAM_LATENCY_MS = 100.0
DEFAULT_SAMPLE_RATE = 44100

HOTKEY_ID = 1
HOTKEY_ID_STOP = 2
HOTKEY_ID_THRESHOLD_DOWN = 3
HOTKEY_ID_THRESHOLD_UP = 4
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_ALT = 0x0001
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32


class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD),
        ("pt", wintypes.POINT),
    ]


def app_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home())
    return Path(base) / APP_NAME


def config_path() -> Path:
    return app_dir() / "config.ini"


def log_path() -> Path:
    return app_dir() / "loud_gate.log"


def ensure_app_dir() -> Path:
    path = app_dir()
    path.mkdir(parents=True, exist_ok=True)
    return path


def default_config() -> dict:
    return {
        "version": CONFIG_VERSION,
        "input_device_index": None,
        "input_device_name": None,
        "input_device_hostapi": None,
        "output_device_index": None,
        "output_device_name": None,
        "output_device_hostapi": None,
        "threshold_db": DEFAULT_THRESHOLD_DB,
        "release_ms": DEFAULT_RELEASE_MS,
        "lookahead_ms": DEFAULT_LOOKAHEAD_MS,
        "mute_hotkey": "F13",
        "stop_hotkey": "CTRL+SHIFT+F13",
        "threshold_down_hotkey": "F14",
        "threshold_up_hotkey": "CTRL+F14",
        "threshold_step_db": 5.0,
    }


HOTKEY_MODIFIERS = {
    "ALT": MOD_ALT,
    "CTRL": MOD_CONTROL,
    "CONTROL": MOD_CONTROL,
    "SHIFT": MOD_SHIFT,
    "WIN": MOD_WIN,
    "WINDOWS": MOD_WIN,
    "META": MOD_WIN,
}

HOTKEY_NAMED_KEYS = {
    "BACKSPACE": 0x08,
    "TAB": 0x09,
    "ENTER": 0x0D,
    "RETURN": 0x0D,
    "PAUSE": 0x13,
    "CAPSLOCK": 0x14,
    "ESC": 0x1B,
    "ESCAPE": 0x1B,
    "SPACE": 0x20,
    "PAGEUP": 0x21,
    "PAGEDOWN": 0x22,
    "END": 0x23,
    "HOME": 0x24,
    "LEFT": 0x25,
    "UP": 0x26,
    "RIGHT": 0x27,
    "DOWN": 0x28,
    "INSERT": 0x2D,
    "DELETE": 0x2E,
}


def parse_hotkey(value: str, field_name: str) -> tuple[int, int, str]:
    """Parse a human-readable hotkey such as ``Ctrl+Shift+F13``."""

    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must contain a key such as F13 or Ctrl+Shift+F13.")

    tokens = [token for token in value.replace("+", " ").upper().split() if token]
    modifiers = MOD_NOREPEAT
    virtual_key: int | None = None

    for token in tokens:
        if token in HOTKEY_MODIFIERS:
            modifiers |= HOTKEY_MODIFIERS[token]
            continue

        if virtual_key is not None:
            raise ValueError(f"{field_name} contains more than one key: {value!r}.")

        if token in HOTKEY_NAMED_KEYS:
            virtual_key = HOTKEY_NAMED_KEYS[token]
        elif len(token) == 1 and token.isalnum():
            virtual_key = ord(token)
        elif token.startswith("F") and token[1:].isdigit() and 1 <= int(token[1:]) <= 24:
            virtual_key = 0x70 + int(token[1:]) - 1
        else:
            raise ValueError(
                f"Unsupported key in {field_name}: {token!r}. Use F1-F24, A-Z, 0-9, or a named key."
            )

    if virtual_key is None:
        raise ValueError(f"{field_name} must include a non-modifier key.")

    return modifiers, virtual_key, "+".join(tokens)


def configured_hotkeys(cfg: dict) -> dict[int, tuple[int, int, str]]:
    definitions = (
        (HOTKEY_ID, "mute_hotkey"),
        (HOTKEY_ID_STOP, "stop_hotkey"),
        (HOTKEY_ID_THRESHOLD_DOWN, "threshold_down_hotkey"),
        (HOTKEY_ID_THRESHOLD_UP, "threshold_up_hotkey"),
    )
    bindings = {}
    for hotkey_id, field_name in definitions:
        try:
            bindings[hotkey_id] = parse_hotkey(str(cfg[field_name]), field_name)
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError(f"Invalid hotkey configuration: {exc}") from exc

    return bindings


def load_config() -> dict | None:
    path = config_path()
    if not path.exists():
        return None

    parser = configparser.ConfigParser(interpolation=None)
    try:
        with path.open("r", encoding="utf-8") as file:
            parser.read_file(file)
    except Exception as exc:
        raise RuntimeError(f"Failed to load config from {path}: {exc}") from exc

    if not parser.has_section(CONFIG_SECTION):
        raise RuntimeError(f"Config section [{CONFIG_SECTION}] is missing from {path}.")

    data = parser[CONFIG_SECTION]
    hotkey_data = parser[HOTKEY_CONFIG_SECTION] if parser.has_section(HOTKEY_CONFIG_SECTION) else {}
    try:
        version = data.getint("version")
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"Invalid config version in {path}.") from exc

    if version != CONFIG_VERSION:
        return None

    cfg = default_config()
    for key, default in cfg.items():
        source = hotkey_data if key.endswith("_hotkey") or key == "threshold_step_db" else data
        if key not in source:
            continue

        raw = source.get(key, raw=True)
        if key.endswith("_device_index"):
            cfg[key] = None if raw == "" else int(raw)
        elif raw == "" and default is None:
            cfg[key] = None
        elif isinstance(default, float):
            cfg[key] = float(raw)
        elif isinstance(default, int):
            cfg[key] = int(raw)
        else:
            cfg[key] = raw

    return cfg


def save_config(cfg: dict) -> None:
    ensure_app_dir()
    path = config_path()
    tmp = path.with_suffix(".tmp")
    parser = configparser.ConfigParser(interpolation=None)
    parser[CONFIG_SECTION] = {
        key: "" if value is None else str(value)
        for key, value in sorted(cfg.items())
        if not (key.endswith("_hotkey") or key == "threshold_step_db")
    }
    parser[HOTKEY_CONFIG_SECTION] = {
        key: "" if value is None else str(value)
        for key, value in sorted(cfg.items())
        if key.endswith("_hotkey") or key == "threshold_step_db"
    }
    with tmp.open("w", encoding="utf-8", newline="") as file:
        parser.write(file)
    tmp.replace(path)


def setup_logging(verbose: bool) -> logging.Logger:
    ensure_app_dir()
    logger = logging.getLogger("loud_gate")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    logger.propagate = False

    file_handler = logging.FileHandler(log_path(), encoding="utf-8")
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    )
    logger.addHandler(file_handler)

    if verbose:
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(stream_handler)

    return logger


def is_windows_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def relaunch_as_admin(extra_args: list[str]) -> None:
    script = str(Path(__file__).resolve())
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


def list_devices() -> list[dict]:
    return list(sd.query_devices())


def compatible_device(device: dict, want_input: bool) -> bool:
    return device["max_input_channels"] > 0 if want_input else device["max_output_channels"] > 0


def normalize_name(name: str) -> str:
    return " ".join(name.lower().split())


def hostapi_name(device: dict) -> str:
    apis = sd.query_hostapis()
    hostapi_index = device.get("hostapi")
    if isinstance(hostapi_index, int) and 0 <= hostapi_index < len(apis):
        return str(apis[hostapi_index]["name"])
    return "unknown"


def can_open_callback_stream(device_index: int, want_input: bool, channels: int, samplerate: int) -> bool:
    def input_cb(indata, frames, time_info, status):
        pass

    def output_cb(outdata, frames, time_info, status):
        outdata.fill(0)

    try:
        if want_input:
            with sd.InputStream(
                device=device_index,
                samplerate=samplerate,
                channels=channels,
                dtype="int16",
                callback=input_cb,
            ):
                return True
        with sd.OutputStream(
            device=device_index,
            samplerate=samplerate,
            channels=channels,
            dtype="int16",
            callback=output_cb,
        ):
            return True
    except Exception:
        return False


def stream_channels(device: dict, want_input: bool) -> int:
    key = "max_input_channels" if want_input else "max_output_channels"
    return max(1, min(2, int(device[key])))


def stream_rate_candidates(device: dict) -> list[int]:
    rates: list[int] = []
    for rate in (
        int(round(device.get("default_samplerate") or DEFAULT_SAMPLE_RATE)),
        DEFAULT_SAMPLE_RATE,
        48000,
    ):
        if rate not in rates:
            rates.append(rate)
    return rates


def device_can_open_callback(devices: list[dict], device_index: int, want_input: bool) -> bool:
    if not (0 <= device_index < len(devices)):
        return False

    device = devices[device_index]
    if not compatible_device(device, want_input):
        return False

    channels = stream_channels(device, want_input)
    return any(
        can_open_callback_stream(device_index, want_input, channels, rate)
        for rate in stream_rate_candidates(device)
    )


def looks_like_physical_mic(name: str) -> bool:
    n = normalize_name(name)
    include = any(token in n for token in ("microphone", "mic", "headset", "capture"))
    exclude = any(
        token in n
        for token in (
            "cable",
            "vb-audio",
            "virtual cable",
            "point",
            "steam",
            "stereo mix",
            "primary sound",
            "sound mapper",
            "speakers",
            "output",
        )
    )
    return include and not exclude


def looks_like_virtual_cable_output(name: str) -> bool:
    n = normalize_name(name)
    include = any(token in n for token in ("cable input", "virtual cable"))
    exclude = any(
        token in n
        for token in (
            "cable output",
            "point",
            "steam",
            "stereo mix",
            "capture",
            "line in",
            "microphone",
            "mic",
        )
    )
    return include and not exclude


def looks_like_virtual_output(name: str) -> bool:
    return looks_like_virtual_cable_output(name)


def hostapi_rank(device: dict) -> int:
    api = normalize_name(hostapi_name(device))
    if "wdm-ks" in api:
        return 0
    if "wasapi" in api:
        return 1
    if "directsound" in api:
        return 2
    if "mme" in api:
        return 3
    return 4


def sort_candidates(candidates: list[tuple[int, dict]]) -> list[tuple[int, dict]]:
    def rank(item: tuple[int, dict]) -> tuple[int, str, int]:
        idx, device = item
        return hostapi_rank(device), normalize_name(str(device["name"])), idx

    return sorted(candidates, key=rank)


def dedupe_candidates_by_name(candidates: list[tuple[int, dict]]) -> list[tuple[int, dict]]:
    best_by_name: dict[str, tuple[int, dict]] = {}
    for idx, device in candidates:
        key = normalize_name(str(device["name"]))
        current = best_by_name.get(key)
        if current is None or hostapi_rank(device) < hostapi_rank(current[1]):
            best_by_name[key] = (idx, device)
    return list(best_by_name.values())


def mic_name_rank(device: dict) -> int:
    n = normalize_name(str(device["name"]))
    if "maonocaster" in n:
        return 0
    if "headset microphone" in n:
        return 1
    if "microphone" in n:
        return 2
    if "mic" in n:
        return 3
    return 4


def relevant_physical_mic_inputs(devices: list[dict]) -> list[tuple[int, dict]]:
    filtered: list[tuple[int, dict]] = []

    for idx, device in enumerate(devices):
        if not compatible_device(device, True):
            continue
        name = str(device["name"])
        if not looks_like_physical_mic(name):
            continue
        if not device_can_open_callback(devices, idx, True):
            continue
        filtered.append((idx, device))

    if filtered:
        unique = dedupe_candidates_by_name(filtered)
        return sorted(
            unique,
            key=lambda item: (
                mic_name_rank(item[1]),
                hostapi_rank(item[1]),
                normalize_name(str(item[1]["name"])),
                item[0],
            ),
        )

    for idx, device in enumerate(devices):
        if not compatible_device(device, True):
            continue
        name = str(device["name"])
        filtered.append((idx, device))
    unique = dedupe_candidates_by_name(filtered)
    return sorted(
        unique,
        key=lambda item: (
            mic_name_rank(item[1]),
            hostapi_rank(item[1]),
            normalize_name(str(item[1]["name"])),
            item[0],
        ),
    )


def relevant_virtual_cable_outputs(devices: list[dict]) -> list[tuple[int, dict]]:
    filtered: list[tuple[int, dict]] = []

    for idx, device in enumerate(devices):
        if not compatible_device(device, False):
            continue
        name = str(device["name"])
        if not looks_like_virtual_cable_output(name):
            continue
        if not device_can_open_callback(devices, idx, False):
            continue
        filtered.append((idx, device))

    return sort_candidates(dedupe_candidates_by_name(filtered))


def print_device_candidates(title: str, candidates: list[tuple[int, dict]]) -> None:
    print(f"\n{title}\n")
    for i, (idx, device) in enumerate(candidates):
        direction = "input" if device["max_input_channels"] > 0 else "output"
        print(f"  [{idx}] {device['name']}  ({hostapi_name(device)}; {direction}; menu {i})")
    print()


def pick_device(prompt: str, devices: list[tuple[int, dict]], want_input: bool) -> int:
    if not devices:
        raise RuntimeError("No matching devices were found.")

    while True:
        default_hint = f" [default: {devices[0][0]}]" if len(devices) == 1 else ""
        raw = input(f"{prompt}{default_hint}: ").strip()
        if raw == "" and len(devices) == 1:
            idx, device = devices[0]
            print(f"Selected: {device['name']}  ({hostapi_name(device)})")
            return idx
        if not raw.isdigit():
            print("Please enter a device index or menu number from the list above.")
            continue
        choice = int(raw)

        for original_index, device in devices:
            if original_index == choice:
                return original_index

        if 0 <= choice < len(devices):
            original_index, device = devices[choice]
        else:
            print("Out of range, try again.")
            continue

        if not compatible_device(device, want_input):
            print("That device does not have the right channels. Pick another one.")
            continue
        return original_index


def ask_float(prompt: str, default: float) -> float:
    while True:
        raw = input(f"{prompt} [{default}]: ").strip()
        if raw == "":
            return default
        try:
            return float(raw)
        except ValueError:
            print("Please enter a valid number.")


def interactive_setup(existing: dict | None = None) -> dict:
    cfg = default_config()
    if existing:
        cfg.update(existing)

    devices = list_devices()

    mic_candidates = relevant_physical_mic_inputs(devices)
    virtual_out_candidates = relevant_virtual_cable_outputs(devices)

    print_device_candidates("Physical microphone inputs:", mic_candidates)
    print_device_candidates("Virtual cable playback outputs:", virtual_out_candidates)
    print(
        "Pick the real microphone as the input.\n"
        "Pick the VB-Cable playback/output side below. Windows apps use the matching capture side as the mic source.\n"
    )

    in_idx = pick_device("Enter the device index of your REAL microphone", mic_candidates, want_input=True)
    if not virtual_out_candidates:
        raise RuntimeError(
            "No VB-Cable playback/output devices were found. Make sure the device you want is exposed as "
            "a render/output endpoint such as CABLE Input."
        )

    out_idx = pick_device(
        "Enter the device index of your VB-CABLE playback/output device",
        virtual_out_candidates,
        want_input=False,
    )

    out_name = devices[out_idx]["name"]
    if not looks_like_virtual_output(out_name):
        print(
            "\nWarning: the selected output does not look like a VB-Cable playback endpoint.\n"
            f"Selected: {out_name}\n"
            "The script should write to CABLE Input / playback, and apps should use CABLE Output / recording as the mic.\n"
        )
        confirm = input("Continue with this output anyway? [y/N]: ").strip().lower()
        if confirm not in ("y", "yes"):
            raise SystemExit("Setup cancelled.")

    threshold_db = ask_float("Ceiling / threshold in dBFS", float(cfg["threshold_db"]))
    release_ms = ask_float("Release time in ms", float(cfg["release_ms"]))

    cfg.update(
        {
            "version": CONFIG_VERSION,
            "input_device_index": in_idx,
            "input_device_name": devices[in_idx]["name"],
            "input_device_hostapi": hostapi_name(devices[in_idx]),
            "output_device_index": out_idx,
            "output_device_name": devices[out_idx]["name"],
            "output_device_hostapi": hostapi_name(devices[out_idx]),
            "threshold_db": threshold_db,
            "release_ms": release_ms,
        }
    )
    return cfg


def resolve_device_index(devices: list[dict], cfg: dict, key: str, want_input: bool) -> int | None:
    idx_key = f"{key}_device_index"
    name_key = f"{key}_device_name"
    hostapi_key = f"{key}_device_hostapi"
    stored_idx = cfg.get(idx_key)
    stored_name = cfg.get(name_key)
    stored_hostapi = cfg.get(hostapi_key)
    stored_hostapi_norm = normalize_name(stored_hostapi) if isinstance(stored_hostapi, str) else None

    def matches(device: dict) -> bool:
        if stored_name and device["name"] != stored_name:
            return False
        if stored_hostapi_norm and normalize_name(hostapi_name(device)) != stored_hostapi_norm:
            return False
        return True

    if isinstance(stored_idx, int) and 0 <= stored_idx < len(devices):
        device = devices[stored_idx]
        if compatible_device(device, want_input) and matches(device) and device_can_open_callback(devices, stored_idx, want_input):
            return stored_idx

    if stored_name:
        for i, device in enumerate(devices):
            if not compatible_device(device, want_input):
                continue
            if matches(device) and device_can_open_callback(devices, i, want_input):
                return i

        lowered = stored_name.lower()
        for i, device in enumerate(devices):
            if not compatible_device(device, want_input):
                continue
            if lowered in device["name"].lower():
                if stored_hostapi_norm and normalize_name(hostapi_name(device)) != stored_hostapi_norm:
                    continue
                if not device_can_open_callback(devices, i, want_input):
                    continue
                return i

    return None


def resolve_sample_rate(
    devices: list[dict],
    in_idx: int,
    out_idx: int,
    input_channels: int,
    output_channels: int,
) -> int:
    candidates: list[int] = []
    for rate in (
        DEFAULT_SAMPLE_RATE,
        int(round(devices[in_idx].get("default_samplerate") or DEFAULT_SAMPLE_RATE)),
        int(round(devices[out_idx].get("default_samplerate") or DEFAULT_SAMPLE_RATE)),
        48000,
    ):
        if rate not in candidates:
            candidates.append(rate)

    for rate in candidates:
        try:
            if can_open_callback_stream(in_idx, True, input_channels, rate) and can_open_callback_stream(out_idx, False, output_channels, rate):
                return rate
        except Exception:
            continue

    raise RuntimeError(
        "No common sample rate found for the selected input/output devices."
    )


def startup_launcher_path() -> Path:
    return app_dir() / STARTUP_LAUNCHER_NAME


def write_startup_launcher() -> Path:
    ensure_app_dir()
    launcher = startup_launcher_path()
    python_exe = str(Path(sys.executable).resolve())
    script = str(Path(__file__).resolve())
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
    command = subprocess.list2cmdline(
        [wscript, "//B", "//NoLogo", str(launcher)]
    )
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


def install_shutdown_handlers(stop_event: threading.Event) -> None:
    def handler(signum, frame):
        stop_event.set()

    signal.signal(signal.SIGINT, handler)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, handler)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, handler)


def peak_dbfs(samples: np.ndarray) -> float:
    peak = float(np.max(np.abs(samples))) if samples.size else 0.0
    return 20.0 * np.log10(peak + 1e-12)


class GlobalHotkeyManager:
    def __init__(
        self,
        mute_event: threading.Event,
        stop_event: threading.Event,
        logger: logging.Logger,
        adjust_threshold: Callable[[float], None],
        hotkeys: dict[int, tuple[int, int, str]],
        threshold_step_db: float,
    ):
        self.mute_event = mute_event
        self.stop_event = stop_event
        self.logger = logger
        self.adjust_threshold = adjust_threshold
        self.hotkeys = hotkeys
        self.threshold_step_db = float(threshold_step_db)
        self._thread: threading.Thread | None = None
        self._thread_id: int | None = None
        self._ready = threading.Event()
        self._error: BaseException | None = None
        self._registered_hotkeys: list[int] = []

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="ConfiguredHotkeys", daemon=True)
        self._thread.start()
        self._ready.wait()
        if self._error is not None:
            raise self._error

    def stop(self) -> None:
        if self._thread_id is not None:
            user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def _run(self) -> None:
        try:
            self._thread_id = kernel32.GetCurrentThreadId()
            msg = MSG()
            user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)

            for hotkey_id, (modifiers, virtual_key, label) in self.hotkeys.items():
                if not user32.RegisterHotKey(None, hotkey_id, modifiers, virtual_key):
                    self._error = RuntimeError(
                        f"Could not register {label} ({ctypes.WinError()}). "
                        "Choose an unused combination in config.ini."
                    )
                    return
                self._registered_hotkeys.append(hotkey_id)

            self._ready.set()
            self.logger.info(
                "Hotkeys registered: mute=%s, stop=%s, threshold down=%s, threshold up=%s, step=%.1f dB.",
                self.hotkeys[HOTKEY_ID][2],
                self.hotkeys[HOTKEY_ID_STOP][2],
                self.hotkeys[HOTKEY_ID_THRESHOLD_DOWN][2],
                self.hotkeys[HOTKEY_ID_THRESHOLD_UP][2],
                self.threshold_step_db,
            )

            while True:
                result = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if result == 0:
                    break
                if result == -1:
                    self._error = ctypes.WinError()
                    break
                if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID:
                    if self.mute_event.is_set():
                        self.mute_event.clear()
                        self.logger.info("%s: mic unmuted", self.hotkeys[HOTKEY_ID][2])
                    else:
                        self.mute_event.set()
                        self.logger.info("%s: mic muted", self.hotkeys[HOTKEY_ID][2])
                elif msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID_THRESHOLD_DOWN:
                    self.adjust_threshold(-self.threshold_step_db)
                elif msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID_THRESHOLD_UP:
                    self.adjust_threshold(self.threshold_step_db)
                elif msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID_STOP:
                    self.stop_event.set()
                    self.logger.info("%s: stopping service", self.hotkeys[HOTKEY_ID_STOP][2])
                    break
        except BaseException as exc:
            self._error = exc
        finally:
            self._ready.set()
            for hotkey_id in reversed(self._registered_hotkeys):
                user32.UnregisterHotKey(None, hotkey_id)


class LookaheadLimiter:
    def __init__(
        self,
        threshold_db: float,
        release_ms: float,
        lookahead_ms: float,
        sample_rate: int,
        block_size: int,
    ) -> None:
        self._threshold_lock = threading.Lock()
        self._threshold_db = float(threshold_db)
        self.release_ms = float(release_ms)
        self.lookahead_ms = float(lookahead_ms)
        self.sample_rate = int(sample_rate)
        self.block_size = int(block_size)
        self.block_seconds = self.block_size / float(self.sample_rate)
        self.segment_size = max(1, int(round(self.sample_rate * 0.001)))
        self.segment_seconds = self.segment_size / float(self.sample_rate)
        self.lookahead_segments = max(1, math.ceil(self.lookahead_ms / (self.segment_seconds * 1000.0)))

        if self.release_ms <= 0:
            self.release_coeff = 0.0
        else:
            self.release_coeff = math.exp(-self.segment_seconds / (self.release_ms / 1000.0))

        self.pending_segments: deque[np.ndarray] = deque()
        self.pending_peaks: deque[float] = deque()
        self.current_gain_db = 0.0

    def get_threshold_db(self) -> float:
        with self._threshold_lock:
            return float(self._threshold_db)

    def set_threshold_db(self, threshold_db: float) -> float:
        with self._threshold_lock:
            self._threshold_db = float(threshold_db)
            return self._threshold_db

    def adjust_threshold_db(self, delta_db: float) -> float:
        with self._threshold_lock:
            self._threshold_db = float(self._threshold_db + float(delta_db))
            return self._threshold_db

    def _target_gain_db(self, peak_db: float) -> float:
        threshold_db = self.get_threshold_db()
        if peak_db <= threshold_db:
            return 0.0
        return threshold_db - peak_db

    def process(self, in_block: np.ndarray, muted: bool) -> np.ndarray:
        block = np.asarray(in_block, dtype=np.float32)
        if block.size == 0:
            return np.zeros_like(block)

        if muted:
            self.pending_segments.clear()
            self.pending_peaks.clear()
            self.current_gain_db = 0.0
            return np.zeros_like(block)

        outputs: list[np.ndarray] = []
        offset = 0
        total = int(block.shape[0])

        while offset < total:
            end = min(total, offset + self.segment_size)
            segment = block[offset:end]
            offset = end

            self.pending_segments.append(segment.copy())
            self.pending_peaks.append(peak_dbfs(segment))

            if len(self.pending_segments) <= self.lookahead_segments:
                outputs.append(np.zeros_like(segment))
                continue

            window_peak_db = max(self.pending_peaks)
            target_gain_db = self._target_gain_db(window_peak_db)

            if target_gain_db < self.current_gain_db:
                self.current_gain_db = target_gain_db
            else:
                self.current_gain_db = (
                    self.release_coeff * self.current_gain_db
                    + (1.0 - self.release_coeff) * target_gain_db
                )

            out_segment = self.pending_segments.popleft()
            self.pending_peaks.popleft()

            gain = 10.0 ** (self.current_gain_db / 20.0)
            outputs.append((out_segment * gain).astype(np.float32, copy=False))

        if not outputs:
            return np.zeros_like(block)
        return np.concatenate(outputs).astype(np.float32, copy=False)


def run_service(cfg: dict, logger: logging.Logger, verbose: bool) -> None:
    mute_event = threading.Event()
    stop_event = threading.Event()
    hotkeys = configured_hotkeys(cfg)
    try:
        threshold_step_db = float(cfg["threshold_step_db"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("threshold_step_db must be a number in config.ini.") from exc

    if threshold_step_db <= 0:
        raise RuntimeError("threshold_step_db must be greater than zero in config.ini.")

    install_shutdown_handlers(stop_event)

    while not stop_event.is_set():
        try:
            devices = list(sd.query_devices())
            in_idx = resolve_device_index(devices, cfg, "input", want_input=True)
            out_idx = resolve_device_index(devices, cfg, "output", want_input=False)

            if in_idx is None or out_idx is None:
                raise RuntimeError(
                    "Saved devices were not found. Re-run the script interactively to reselect them."
                )

            saved_input_idx = cfg.get("input_device_index")
            saved_output_idx = cfg.get("output_device_index")
            if in_idx != saved_input_idx or out_idx != saved_output_idx:
                cfg["input_device_index"] = in_idx
                cfg["input_device_name"] = devices[in_idx]["name"]
                cfg["input_device_hostapi"] = hostapi_name(devices[in_idx])
                cfg["output_device_index"] = out_idx
                cfg["output_device_name"] = devices[out_idx]["name"]
                cfg["output_device_hostapi"] = hostapi_name(devices[out_idx])
                try:
                    save_config(cfg)
                    logger.info(
                        "Resolved saved devices to input=%s (%s), output=%s (%s) and updated config.",
                        in_idx,
                        devices[in_idx]["name"],
                        out_idx,
                        devices[out_idx]["name"],
                    )
                except Exception as exc:
                    logger.warning("Resolved devices but failed to save config: %s", exc)

            if not looks_like_virtual_output(devices[out_idx]["name"]):
                logger.warning(
                    "Selected output '%s' does not look like a VB-Cable playback device.",
                    devices[out_idx]["name"],
                )

            input_channels = max(1, min(2, int(devices[in_idx]["max_input_channels"])))
            output_channels = max(1, min(2, int(devices[out_idx]["max_output_channels"])))
            sample_rate = resolve_sample_rate(devices, in_idx, out_idx, input_channels, output_channels)
            block_size = int(round(sample_rate * (DEFAULT_BLOCK_MS / 1000.0)))
            limiter = LookaheadLimiter(
                threshold_db=float(cfg["threshold_db"]),
                release_ms=float(cfg["release_ms"]),
                lookahead_ms=float(cfg["lookahead_ms"]),
                sample_rate=sample_rate,
                block_size=block_size,
            )

            def adjust_threshold(delta_db: float) -> None:
                new_threshold = limiter.adjust_threshold_db(delta_db)
                cfg["threshold_db"] = new_threshold
                try:
                    save_config(cfg)
                except Exception as exc:
                    logger.warning("Failed to save updated threshold: %s", exc)
                step = abs(delta_db)
                direction = "lowered" if delta_db < 0 else "raised"
                logger.info("Threshold %s by %.1f dB -> %.1f dBFS", direction, step, new_threshold)

            hotkey = GlobalHotkeyManager(
                mute_event,
                stop_event,
                logger,
                adjust_threshold,
                hotkeys,
                threshold_step_db,
            )
            pending_blocks: deque[np.ndarray] = deque()
            buffer_lock = threading.Lock()
            lookahead_output_blocks = max(1, math.ceil(limiter.lookahead_ms / DEFAULT_BLOCK_MS))
            max_buffer_blocks = max(16, lookahead_output_blocks * 6 + 4)
            prefill_blocks = max(4, lookahead_output_blocks + 2)
            input_latency = max(
                float(devices[in_idx].get("default_high_input_latency") or 0.0),
                DEFAULT_STREAM_LATENCY_MS / 1000.0,
            )
            output_latency = max(
                float(devices[out_idx].get("default_high_output_latency") or 0.0),
                DEFAULT_STREAM_LATENCY_MS / 1000.0,
            )
            last_pcm = np.zeros(block_size, dtype=np.int16)

            hotkey.start()
            if stop_event.is_set():
                break

            logger.info(
                "Running with input='%s', output='%s', sample_rate=%s, block_size=%s, in_channels=%s, out_channels=%s, threshold=%.1f dBFS, release=%.1f ms, lookahead=%.1f ms",
                devices[in_idx]["name"],
                devices[out_idx]["name"],
                sample_rate,
                block_size,
                input_channels,
                output_channels,
                limiter.get_threshold_db(),
                float(cfg["release_ms"]),
                float(cfg["lookahead_ms"]),
            )

            if verbose:
                print(
                    f"Running. F13 toggles mute. Input = {devices[in_idx]['name']}. "
                    f"Output = {devices[out_idx]['name']}.",
                    flush=True,
                )

            def input_callback(indata, frames, time_info, status):
                if status:
                    # Callback must stay real-time safe; surface device issues through the outer retry loop.
                    pass
                incoming = np.asarray(indata[:, 0], dtype=np.float32) / 32768.0
                processed = limiter.process(incoming, mute_event.is_set())
                pcm = np.clip(np.rint(processed * 32767.0), -32768, 32767).astype(np.int16)
                with buffer_lock:
                    pending_blocks.append(pcm)
                    while len(pending_blocks) > max_buffer_blocks:
                        pending_blocks.popleft()

            def output_callback(outdata, frames, time_info, status):
                nonlocal last_pcm
                if status:
                    # Callback must stay real-time safe; surface device issues through the outer retry loop.
                    pass
                with buffer_lock:
                    pcm = pending_blocks.popleft() if pending_blocks else None

                if pcm is None:
                    pcm = last_pcm
                else:
                    last_pcm = pcm

                if pcm.shape[0] != frames:
                    if pcm.shape[0] > frames:
                        pcm = pcm[:frames]
                    else:
                        padded = np.empty(frames, dtype=np.int16)
                        padded[: pcm.shape[0]] = pcm
                        pad_value = int(pcm[-1]) if pcm.size else 0
                        if pcm.shape[0] < frames:
                            padded[pcm.shape[0] :] = pad_value
                        pcm = padded

                last_pcm = pcm.copy()
                out_block = np.repeat(pcm[:, None], output_channels, axis=1)
                outdata[:] = out_block

            with sd.InputStream(
                samplerate=sample_rate,
                blocksize=block_size,
                dtype="int16",
                channels=input_channels,
                device=in_idx,
                callback=input_callback,
                latency=input_latency,
            ):
                while not stop_event.is_set():
                    with buffer_lock:
                        ready = len(pending_blocks)
                    if ready >= prefill_blocks:
                        break
                    if stop_event.wait(0.05):
                        break

                with sd.OutputStream(
                    samplerate=sample_rate,
                    blocksize=block_size,
                    dtype="int16",
                    channels=output_channels,
                    device=out_idx,
                    callback=output_callback,
                    latency=output_latency,
                ):
                    while not stop_event.wait(0.25):
                        pass

        except KeyboardInterrupt:
            stop_event.set()
            logger.info("Stopped by user.")
            break
        except Exception as exc:
            if stop_event.is_set():
                break
            logger.exception("Runtime error: %s", exc)
            if verbose:
                print(f"Audio engine error: {exc}", flush=True)
            if stop_event.wait(5.0):
                break
            continue
        finally:
            hotkey.stop()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Windows lookahead mic limiter with configurable global hotkeys.")
    parser.add_argument("--setup", action="store_true", help="Re-run interactive device setup.")
    parser.add_argument("--run", action="store_true", help="Run without prompting. Requires saved config.")
    parser.add_argument(
        "--install-startup",
        action="store_true",
        help="Create a logon scheduled task so the script starts automatically.",
    )
    parser.add_argument(
        "--uninstall-startup",
        action="store_true",
        help="Remove the logon scheduled task.",
    )
    parser.add_argument("--verbose", action="store_true", help="Print live status to the console.")
    parser.add_argument("--quiet", action="store_true", help="Suppress console status output.")
    return parser.parse_args()


def main() -> int:
    if os.name != "nt":
        raise SystemExit("This script is Windows-only.")

    args = parse_args()
    did_setup = False

    if args.install_startup or args.uninstall_startup:
        if not is_windows_admin():
            relaunch_as_admin(sys.argv[1:])
            return 0

    if args.uninstall_startup:
        uninstall_startup_task()
        print(f"Removed scheduled task: {TASK_NAME}")
        return 0

    existing = load_config()
    cfg = existing

    if args.setup or cfg is None:
        if not sys.stdin.isatty():
            raise SystemExit(
                "Interactive setup requires a console. Run the script once from PowerShell to set up devices."
        )
        cfg = interactive_setup(existing)
        save_config(cfg)
        print(f"Saved config to {config_path()}")
        did_setup = True

    if args.install_startup:
        install_startup_task()
        print(f"Installed startup task: {TASK_NAME}")
        if not args.run:
            return 0

    verbose = args.verbose or (sys.stdout.isatty() and not args.quiet)
    logger = setup_logging(verbose)
    logger.info("Config file: %s", config_path())

    if did_setup and not args.run:
        print(f"Setup complete. Run `python .\\loud_gate.py --run` to start it now, or use `--install-startup` to launch automatically.")
        return 0

    run_service(cfg, logger, verbose)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
