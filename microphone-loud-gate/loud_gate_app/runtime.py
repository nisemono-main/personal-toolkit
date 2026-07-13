"""Always-on microphone routing and limiter runtime for Loud Gate."""

from __future__ import annotations

import ctypes
import logging
import math
import signal
import threading
import time
from typing import Callable

import sounddevice as sd
from ctypes import wintypes

from .audio_engine import AudioEngine, AudioHealthSnapshot, LookaheadLimiter
from .config import MAX_THRESHOLD_DB, MIN_THRESHOLD_DB, LoudGateConfig, save_config
from .devices import (
    hostapi_name,
    list_devices,
    looks_like_virtual_output,
    resolve_device_index,
    resolve_sample_rate,
)


DEFAULT_BLOCK_MS = 10.0
DEFAULT_STREAM_LATENCY_MS = 100.0
STARTUP_RETRY_LIMIT = 3
RETRY_DELAY_SECONDS = 5.0
HEALTH_POLL_SECONDS = 0.25
HEALTH_LOG_INTERVAL_SECONDS = 5.0
HEALTH_RESTART_STREAK = 3

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


def configured_hotkeys(cfg: LoudGateConfig) -> dict[int, tuple[int, int, str]]:
    definitions = (
        (HOTKEY_ID, "mute_hotkey"),
        (HOTKEY_ID_STOP, "stop_hotkey"),
        (HOTKEY_ID_THRESHOLD_DOWN, "threshold_down_hotkey"),
        (HOTKEY_ID_THRESHOLD_UP, "threshold_up_hotkey"),
    )
    bindings = {}
    for hotkey_id, field_name in definitions:
        try:
            bindings[hotkey_id] = parse_hotkey(str(getattr(cfg, field_name)), field_name)
        except (AttributeError, TypeError, ValueError) as exc:
            raise RuntimeError(f"Invalid hotkey configuration: {exc}") from exc

    return bindings


def install_shutdown_handlers(stop_event: threading.Event) -> None:
    def handler(signum, frame):
        stop_event.set()

    signal.signal(signal.SIGINT, handler)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, handler)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, handler)


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
        self._stop_requested = threading.Event()
        self._error: BaseException | None = None
        self._registered_hotkeys: list[int] = []

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="ConfiguredHotkeys", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout=5.0):
            raise RuntimeError("The global hotkey thread did not finish initializing within five seconds.")
        if self._error is not None:
            raise self._error

    def stop(self) -> None:
        thread = self._thread
        if thread is None:
            return
        self._stop_requested.set()
        if self._thread_id is not None and thread.is_alive():
            user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        thread.join(timeout=2.0)
        if thread.is_alive():
            self.logger.warning("The global hotkey thread did not stop within two seconds.")

    def _run(self) -> None:
        try:
            self._thread_id = kernel32.GetCurrentThreadId()
            msg = MSG()
            user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)

            if self._stop_requested.is_set():
                return

            for hotkey_id, (modifiers, virtual_key, label) in self.hotkeys.items():
                if not user32.RegisterHotKey(None, hotkey_id, modifiers, virtual_key):
                    self._error = RuntimeError(
                        f"Could not register {label} ({ctypes.WinError()}). "
                        "Choose an unused combination in config.ini."
                    )
                    return
                self._registered_hotkeys.append(hotkey_id)

            self._ready.set()
            if self._stop_requested.is_set():
                return
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


def run_service(cfg: LoudGateConfig, logger: logging.Logger, verbose: bool) -> None:
    mute_event = threading.Event()
    stop_event = threading.Event()
    hotkeys = configured_hotkeys(cfg)
    try:
        threshold_step_db = float(cfg.threshold_step_db)
    except (AttributeError, TypeError, ValueError) as exc:
        raise RuntimeError("threshold_step_db must be a number in config.ini.") from exc

    if threshold_step_db <= 0:
        raise RuntimeError("threshold_step_db must be greater than zero in config.ini.")

    install_shutdown_handlers(stop_event)

    startup_attempts = 0
    has_started_once = False

    while not stop_event.is_set():
        hotkey: GlobalHotkeyManager | None = None
        startup_complete = False
        phase = "initialization"
        try:
            startup_attempts += 1
            phase = "device discovery"
            devices = list_devices()
            phase = "device resolution"
            in_idx = resolve_device_index(devices, cfg, "input", want_input=True)
            out_idx = resolve_device_index(devices, cfg, "output", want_input=False)

            if in_idx is None or out_idx is None:
                raise RuntimeError(
                    "Saved devices were not found. Re-run the script interactively to reselect them."
                )

            saved_input_idx = cfg.input_device_index
            saved_output_idx = cfg.output_device_index
            if in_idx != saved_input_idx or out_idx != saved_output_idx:
                cfg.input_device_index = in_idx
                cfg.input_device_name = devices[in_idx]["name"]
                cfg.input_device_hostapi = hostapi_name(devices[in_idx])
                cfg.output_device_index = out_idx
                cfg.output_device_name = devices[out_idx]["name"]
                cfg.output_device_hostapi = hostapi_name(devices[out_idx])
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

            phase = "stream configuration"
            input_channels = max(1, min(2, int(devices[in_idx]["max_input_channels"])))
            output_channels = max(1, min(2, int(devices[out_idx]["max_output_channels"])))
            sample_rate = resolve_sample_rate(devices, in_idx, out_idx, input_channels, output_channels)
            block_size = int(round(sample_rate * (DEFAULT_BLOCK_MS / 1000.0)))
            limiter = LookaheadLimiter(
                threshold_db=float(cfg.threshold_db),
                release_ms=float(cfg.release_ms),
                lookahead_ms=float(cfg.lookahead_ms),
                sample_rate=sample_rate,
                block_size=block_size,
            )

            def adjust_threshold(delta_db: float) -> None:
                previous_threshold = cfg.threshold_db
                new_threshold = limiter.adjust_threshold_db(delta_db)
                if not (MIN_THRESHOLD_DB <= new_threshold <= MAX_THRESHOLD_DB):
                    limiter.set_threshold_db(previous_threshold)
                    logger.warning(
                        "Threshold remains at %.1f dBFS; requested value %.1f is outside the supported range.",
                        previous_threshold,
                        new_threshold,
                    )
                    return
                cfg.threshold_db = new_threshold
                try:
                    save_config(cfg)
                except Exception as exc:
                    logger.warning("Failed to save updated threshold: %s", exc)
                step = abs(delta_db)
                direction = "lowered" if delta_db < 0 else "raised"
                logger.info("Threshold %s by %.1f dB -> %.1f dBFS", direction, step, new_threshold)

            phase = "buffer and hotkey setup"
            hotkey = GlobalHotkeyManager(
                mute_event,
                stop_event,
                logger,
                adjust_threshold,
                hotkeys,
                threshold_step_db,
            )
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
            audio = AudioEngine(
                limiter=limiter,
                mute_event=mute_event,
                output_channels=output_channels,
                buffer_capacity_samples=max_buffer_blocks * block_size,
            )

            phase = "hotkey registration"
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
                float(cfg.release_ms),
                float(cfg.lookahead_ms),
            )

            if verbose:
                print(
                    f"Running. {cfg.mute_hotkey} toggles mute. Input = {devices[in_idx]['name']}. "
                    f"Output = {devices[out_idx]['name']}.",
                    flush=True,
                )

            phase = "input stream startup"
            with sd.InputStream(
                samplerate=sample_rate,
                blocksize=block_size,
                dtype="int16",
                channels=input_channels,
                device=in_idx,
                callback=audio.input_callback,
                latency=input_latency,
            ):
                while not stop_event.is_set():
                    ready_samples = audio.buffer.available_samples
                    if ready_samples >= prefill_blocks * block_size:
                        break
                    if stop_event.wait(0.05):
                        break

                if stop_event.is_set():
                    break

                phase = "output stream startup"
                with sd.OutputStream(
                    samplerate=sample_rate,
                    blocksize=block_size,
                    dtype="int16",
                    channels=output_channels,
                    device=out_idx,
                    callback=audio.output_callback,
                    latency=output_latency,
                ):
                    startup_complete = True
                    has_started_once = True
                    startup_attempts = 0
                    logger.info(
                        "Audio streams ready; queue target=%s samples, capacity=%s samples.",
                        prefill_blocks * block_size,
                        audio.buffer.capacity_samples,
                    )

                    target_queue_samples = prefill_blocks * block_size
                    high_water_samples = min(
                        audio.buffer.capacity_samples,
                        target_queue_samples + (2 * block_size),
                    )
                    low_water_samples = max(0, target_queue_samples - (2 * block_size))
                    callback_fault_streak = 0
                    low_queue_streak = 0
                    last_health_log = 0.0

                    while not stop_event.wait(HEALTH_POLL_SECONDS):
                        phase = "audio health monitoring"
                        audio.rebalance_buffer(
                            high_water_samples=high_water_samples,
                            max_drop_samples=block_size,
                        )
                        snapshot = audio.health.consume()
                        queue_samples = audio.buffer.available_samples
                        queue_ratio = queue_samples / float(audio.buffer.capacity_samples)
                        queue_is_low = (
                            not mute_event.is_set()
                            and queue_samples < low_water_samples
                        )
                        low_queue_streak = low_queue_streak + 1 if queue_is_low else 0

                        if snapshot.has_callback_status or snapshot.buffer_underflow_samples:
                            callback_fault_streak += 1
                        else:
                            callback_fault_streak = 0

                        if snapshot.has_events or queue_is_low:
                            now = time.monotonic()
                            if now - last_health_log >= HEALTH_LOG_INTERVAL_SECONDS:
                                logger.warning(
                                    "Audio health: %s; queue=%s/%s samples (%.1f%%).",
                                    _describe_audio_health(snapshot, queue_is_low),
                                    queue_samples,
                                    audio.buffer.capacity_samples,
                                    queue_ratio * 100.0,
                                )
                                last_health_log = now

                        if callback_fault_streak >= HEALTH_RESTART_STREAK:
                            raise RuntimeError(
                                "Audio callbacks reported stream faults for "
                                f"{callback_fault_streak} consecutive health checks: "
                                f"{_describe_audio_health(snapshot, queue_is_low)}"
                            )

                        if low_queue_streak >= HEALTH_RESTART_STREAK:
                            raise RuntimeError(
                                "Audio output queue remained below its low-water mark for "
                                f"{low_queue_streak} consecutive health checks."
                            )

        except KeyboardInterrupt:
            stop_event.set()
            logger.info("Stopped by user.")
            break
        except Exception as exc:
            if stop_event.is_set():
                break

            if startup_complete or has_started_once:
                logger.exception("Runtime failure during %s: %s", phase, exc)
            else:
                logger.exception(
                    "Startup attempt %s/%s failed during %s: %s",
                    startup_attempts,
                    STARTUP_RETRY_LIMIT,
                    phase,
                    exc,
                )
                if startup_attempts >= STARTUP_RETRY_LIMIT:
                    raise RuntimeError(
                        "Audio service could not start after "
                        f"{STARTUP_RETRY_LIMIT} attempts. Last failure during {phase}: {exc}"
                    ) from exc

            if verbose:
                print(f"Audio engine error during {phase}: {exc}", flush=True)
            if stop_event.wait(RETRY_DELAY_SECONDS):
                break
            continue
        finally:
            if hotkey is not None:
                try:
                    hotkey.stop()
                except Exception:
                    logger.exception("Failed to cleanly stop the global hotkey manager.")


def _describe_audio_health(snapshot: AudioHealthSnapshot, queue_is_low: bool) -> str:
    details: list[str] = []
    if snapshot.input_overflows:
        details.append(f"input overflows={snapshot.input_overflows}")
    if snapshot.input_underflows:
        details.append(f"input underflows={snapshot.input_underflows}")
    if snapshot.output_overflows:
        details.append(f"output overflows={snapshot.output_overflows}")
    if snapshot.output_underflows:
        details.append(f"output underflows={snapshot.output_underflows}")
    if snapshot.buffer_underflow_samples:
        details.append(f"silence-filled samples={snapshot.buffer_underflow_samples}")
    if snapshot.dropped_samples:
        details.append(f"buffer-dropped samples={snapshot.dropped_samples}")
    if snapshot.drift_correction_samples:
        details.append(f"clock-drift corrections={snapshot.drift_correction_samples}")
    if queue_is_low:
        details.append("queue below low-water mark")
    return ", ".join(details) if details else "no incidents"
