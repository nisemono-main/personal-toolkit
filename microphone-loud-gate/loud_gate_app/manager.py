"""Tk-based setup and control window for the independent Loud Gate runtime."""

from __future__ import annotations

import copy
import os
import queue
import subprocess
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Any

from .config import (
    CONFIG_VERSION,
    ConfigError,
    LoudGateConfig,
    config_path,
    default_config,
    load_config,
    log_path,
    save_config,
)
from .devices import (
    hostapi_name,
    list_devices,
    looks_like_virtual_output,
    resolve_pair_format,
    wasapi_input_devices,
    wasapi_output_devices,
)
from .ipc import IpcError, request_runtime
from .runtime import configured_hotkeys
from .startup import TASK_NAME, install_startup_task, launch_runtime, uninstall_startup_task


class HotkeyCapture(ttk.Entry):
    """Capture the next real keyboard combination instead of accepting typed text."""

    _ALIASES = {
        "BACKSPACE": "Backspace",
        "DELETE": "Delete",
        "END": "End",
        "ESCAPE": "Esc",
        "HOME": "Home",
        "INSERT": "Insert",
        "LEFT": "Left",
        "PAGEDOWN": "PageDown",
        "PRIOR": "PageUp",
        "RETURN": "Enter",
        "RIGHT": "Right",
        "SPACE": "Space",
        "TAB": "Tab",
        "UP": "Up",
        "DOWN": "Down",
    }

    def __init__(self, parent, variable: tk.StringVar):
        super().__init__(parent, textvariable=variable, width=26)
        self.bind("<KeyPress>", self._capture, add="+")
        self.bind("<<Paste>>", lambda _event: "break")
        self.bind("<<Cut>>", lambda _event: "break")
        self.configure(cursor="xterm")

    def _capture(self, event) -> str:
        keysym = str(event.keysym or "").upper()
        if keysym in {
            "SHIFT_L",
            "SHIFT_R",
            "CONTROL_L",
            "CONTROL_R",
            "ALT_L",
            "ALT_R",
            "WIN_L",
            "WIN_R",
            "META_L",
            "META_R",
        }:
            return "break"

        key = self._key_token(keysym)
        if key is None:
            return "break"

        modifiers: list[str] = []
        if event.state & 0x0004:
            modifiers.append("Ctrl")
        if event.state & 0x0008:
            modifiers.append("Alt")
        if event.state & 0x0001:
            modifiers.append("Shift")
        if event.state & 0x0040:
            modifiers.append("Win")

        value = "+".join([*modifiers, key])
        self.delete(0, tk.END)
        self.insert(0, value)
        return "break"

    @classmethod
    def _key_token(cls, keysym: str) -> str | None:
        if keysym in cls._ALIASES:
            return cls._ALIASES[keysym]
        if keysym.startswith("F") and keysym[1:].isdigit():
            number = int(keysym[1:])
            if 1 <= number <= 24:
                return f"F{number}"
        if len(keysym) == 1 and keysym.isalnum():
            return keysym.upper()
        return None


class ManagerApp:
    """Own UI/configuration concerns while leaving audio to another process."""

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Loud Gate Manager")
        self.root.geometry("760x540")
        self.root.resizable(False, False)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

        self.cfg: LoudGateConfig | None = None
        self.devices: list[dict] = []
        self.input_options: list[tuple[int, dict]] = []
        self.output_options: list[tuple[int, dict]] = []
        self._work_queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._status_request_in_flight = False
        self._device_request_in_flight = False
        self._closed = False
        self._warned_non_vb_output: str | None = None

        self.input_var = tk.StringVar(value="Discovering WASAPI inputs…")
        self.output_var = tk.StringVar(value="Discovering WASAPI outputs…")
        self.input_channel_var = tk.StringVar(value="0")
        self.threshold_var = tk.StringVar(value="-18.0")
        self.release_var = tk.StringVar(value="150.0")
        self.lookahead_var = tk.StringVar(value="25.0")
        self.mute_hotkey_var = tk.StringVar(value="F13")
        self.stop_hotkey_var = tk.StringVar(value="CTRL+SHIFT+F13")
        self.threshold_down_var = tk.StringVar(value="F14")
        self.threshold_up_var = tk.StringVar(value="CTRL+F14")
        self.threshold_step_var = tk.StringVar(value="5.0")
        self.status_var = tk.StringVar(value="Stopped")
        self.metrics_var = tk.StringVar(value="CPU: —   Latency: —")
        self.input_route_var = tk.StringVar(value="Input: —")
        self.output_route_var = tk.StringVar(value="Output: —")
        self.health_var = tk.StringVar(value="")
        self.autorun_var = tk.StringVar(value="Autorun: checking…")
        self.config_var = tk.StringVar(value=f"Config: {config_path()}")
        self._indicator: tk.Canvas | None = None
        self._input_combo: ttk.Combobox | None = None
        self._output_combo: ttk.Combobox | None = None
        self._input_channel_spin: ttk.Spinbox | None = None
        self._health_label: ttk.Label | None = None

        self._build_ui()
        self._load_initial_config()
        self.refresh_devices()
        self._poll_queue()
        self._poll_runtime()
        self._refresh_autorun_label()

    def _build_ui(self) -> None:
        root_frame = ttk.Frame(self.root, padding=14)
        root_frame.pack(fill="both", expand=True)

        header = ttk.Frame(root_frame)
        header.pack(fill="x", pady=(0, 12))
        self._indicator = tk.Canvas(header, width=22, height=22, highlightthickness=0)
        self._indicator.grid(row=0, column=0, rowspan=3, padx=(0, 8))
        self._set_indicator("#d33b3b")

        ttk.Label(header, textvariable=self.status_var, font=("Segoe UI", 11, "bold")).grid(
            row=0, column=1, sticky="w"
        )
        ttk.Label(header, textvariable=self.metrics_var).grid(
            row=0, column=2, sticky="w", padx=(18, 0)
        )
        ttk.Label(header, textvariable=self.input_route_var).grid(
            row=1, column=1, columnspan=2, sticky="w"
        )
        ttk.Label(header, textvariable=self.output_route_var).grid(
            row=2, column=1, columnspan=2, sticky="w"
        )

        notebook = ttk.Notebook(root_frame)
        notebook.pack(fill="both", expand=True)
        audio_tab = ttk.Frame(notebook, padding=12)
        hotkey_tab = ttk.Frame(notebook, padding=12)
        startup_tab = ttk.Frame(notebook, padding=12)
        notebook.add(audio_tab, text="Audio")
        notebook.add(hotkey_tab, text="Hotkeys")
        notebook.add(startup_tab, text="Startup")

        audio_tab.columnconfigure(1, weight=1)
        self._input_combo = ttk.Combobox(
            audio_tab, textvariable=self.input_var, state="readonly", width=72
        )
        self._row(audio_tab, 0, "Input device", self._input_combo)
        self._output_combo = ttk.Combobox(
            audio_tab, textvariable=self.output_var, state="readonly", width=72
        )
        self._row(audio_tab, 1, "Output device", self._output_combo)
        self._input_channel_spin = ttk.Spinbox(
            audio_tab, from_=0, to=1, textvariable=self.input_channel_var, width=8
        )
        self._row(audio_tab, 2, "Input channel (zero-based)", self._input_channel_spin)
        self._row(audio_tab, 3, "Threshold / ceiling (dBFS)", self._entry(audio_tab, self.threshold_var))
        self._row(audio_tab, 4, "Release (ms)", self._entry(audio_tab, self.release_var))
        self._row(audio_tab, 5, "Lookahead (ms)", self._entry(audio_tab, self.lookahead_var))
        ttk.Label(
            audio_tab,
            text="Both selections must be Windows WASAPI endpoints that can open together. "
            "VB-CABLE is recommended for application microphone routing; other WASAPI outputs are untested.",
            justify="left",
            wraplength=650,
        ).grid(row=6, column=0, columnspan=2, sticky="w", pady=(12, 0))
        self._health_label = ttk.Label(
            audio_tab, textvariable=self.health_var, foreground="#a33b00", justify="left"
        )
        self._health_label.grid(row=7, column=0, columnspan=2, sticky="w", pady=(10, 0))
        self._health_label.grid_remove()

        hotkey_tab.columnconfigure(1, weight=1)
        hotkey_rows = (
            ("Mute", self.mute_hotkey_var),
            ("Stop runtime", self.stop_hotkey_var),
            ("Lower threshold", self.threshold_down_var),
            ("Raise threshold", self.threshold_up_var),
            ("Threshold step (dB)", self.threshold_step_var),
        )
        for row, (label, variable) in enumerate(hotkey_rows):
            widget = (
                self._entry(hotkey_tab, variable)
                if label == "Threshold step (dB)"
                else HotkeyCapture(hotkey_tab, variable)
            )
            self._row(hotkey_tab, row, label, widget)
        ttk.Label(
            hotkey_tab,
            text="Click a hotkey field, then press the actual key combination. "
            "Typing and pasting are intentionally ignored. Use F1–F24, letters, digits, or named keys.",
            justify="left",
            wraplength=650,
        ).grid(row=len(hotkey_rows), column=0, columnspan=2, sticky="w", pady=(14, 0))

        startup_tab.columnconfigure(0, weight=1)
        ttk.Label(
            startup_tab,
            text="Autorun starts only the background runtime. Closing this manager never stops it.\n"
            "The generated VBS launcher keeps startup hidden and displays a failure dialog if runtime startup fails.",
            justify="left",
        ).grid(row=0, column=0, sticky="w", pady=(0, 14))
        ttk.Label(startup_tab, textvariable=self.autorun_var).grid(row=1, column=0, sticky="w")
        startup_buttons = ttk.Frame(startup_tab)
        startup_buttons.grid(row=2, column=0, sticky="w", pady=(12, 0))
        ttk.Button(startup_buttons, text="Install autorun", command=self.install_autorun).pack(
            side="left", padx=(0, 8)
        )
        ttk.Button(startup_buttons, text="Remove autorun", command=self.remove_autorun).pack(
            side="left", padx=(0, 8)
        )
        ttk.Button(startup_buttons, text="Open log", command=self.open_log).pack(side="left")

        action_bar = ttk.Frame(root_frame)
        action_bar.pack(fill="x", pady=(12, 0))
        ttk.Button(action_bar, text="Save", command=self.save_settings).pack(side="left")
        ttk.Button(action_bar, text="Run", command=self.run_runtime).pack(side="left", padx=(8, 0))
        ttk.Button(action_bar, text="Stop", command=self.stop_runtime).pack(side="left", padx=(8, 0))
        ttk.Label(
            action_bar,
            text="Save changes, then Stop and Run to apply them to an active runtime.",
            foreground="#666666",
        ).pack(side="left", padx=(14, 0))

        ttk.Label(root_frame, textvariable=self.config_var, foreground="#666666").pack(
            anchor="w", pady=(8, 0)
        )

    @staticmethod
    def _entry(parent, variable: tk.StringVar) -> ttk.Entry:
        return ttk.Entry(parent, textvariable=variable, width=26)

    @staticmethod
    def _row(parent, row: int, label: str, widget) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=(0, 12), pady=5)
        widget.grid(row=row, column=1, sticky="ew", pady=5)

    def _set_indicator(self, color: str) -> None:
        if self._indicator is None:
            return
        self._indicator.delete("all")
        self._indicator.create_oval(3, 3, 19, 19, fill=color, outline=color)

    def _load_initial_config(self) -> None:
        try:
            self.cfg = load_config() or default_config()
        except ConfigError as exc:
            self.cfg = default_config()
            messagebox.showerror("Loud Gate configuration", str(exc), parent=self.root)
        self._copy_config_to_form()

    def _copy_config_to_form(self) -> None:
        cfg = self.cfg or default_config()
        self.input_channel_var.set(str(cfg.input_channel))
        self.threshold_var.set(str(cfg.threshold_db))
        self.release_var.set(str(cfg.release_ms))
        self.lookahead_var.set(str(cfg.lookahead_ms))
        self.mute_hotkey_var.set(cfg.mute_hotkey)
        self.stop_hotkey_var.set(cfg.stop_hotkey)
        self.threshold_down_var.set(cfg.threshold_down_hotkey)
        self.threshold_up_var.set(cfg.threshold_up_hotkey)
        self.threshold_step_var.set(str(cfg.threshold_step_db))

    def refresh_devices(self) -> None:
        if self._device_request_in_flight or self._closed:
            return
        self._device_request_in_flight = True
        threading.Thread(target=self._discover_devices, name="DeviceDiscovery", daemon=True).start()
        self.root.after(5000, self.refresh_devices)

    def _discover_devices(self) -> None:
        try:
            devices = list_devices()
            self._work_queue.put(
                ("devices", (devices, wasapi_input_devices(devices), wasapi_output_devices(devices)))
            )
        except Exception as exc:
            self._work_queue.put(("error", f"Device discovery failed: {exc}"))

    def _apply_devices(
        self,
        devices: list[dict],
        input_options: list[tuple[int, dict]],
        output_options: list[tuple[int, dict]],
    ) -> None:
        old_input_name = self._selected_option_name(self.input_options, self._input_combo)
        old_output_name = self._selected_option_name(self.output_options, self._output_combo)
        self.devices = devices
        self.input_options = input_options
        self.output_options = output_options
        self._set_device_combo(
            self._input_combo,
            self.input_options,
            old_input_name or (self.cfg.input_device_name if self.cfg else None),
            "No WASAPI input devices found",
            True,
        )
        self._set_device_combo(
            self._output_combo,
            self.output_options,
            old_output_name or (self.cfg.output_device_name if self.cfg else None),
            "No WASAPI output devices found",
            False,
        )
        self._device_request_in_flight = False

    @staticmethod
    def _selected_option_name(
        options: list[tuple[int, dict]], combo: ttk.Combobox | None
    ) -> str | None:
        if combo is None:
            return None
        index = combo.current()
        if 0 <= index < len(options):
            return str(options[index][1]["name"])
        return None

    def _set_device_combo(
        self,
        combo: ttk.Combobox | None,
        options: list[tuple[int, dict]],
        desired_name: str | None,
        empty_label: str,
        want_input: bool,
    ) -> None:
        if combo is None:
            return
        labels = [self._device_label(index, device, want_input) for index, device in options]
        combo.configure(values=labels, state="readonly" if labels else "disabled")
        selected = None
        if desired_name:
            normalized = desired_name.strip().lower()
            for index, (_, device) in enumerate(options):
                if str(device["name"]).strip().lower() == normalized:
                    selected = index
                    break
        if selected is not None:
            combo.current(selected)
        elif desired_name:
            combo.set(f"Configured device not found: {desired_name}")
        elif labels:
            combo.set("Select a device")
        else:
            combo.set(empty_label)
        if want_input and self._input_channel_spin is not None:
            maximum = 0
            if selected is not None:
                maximum = max(0, min(1, int(options[selected][1].get("max_input_channels") or 1) - 1))
            self._input_channel_spin.configure(to=maximum)

    @staticmethod
    def _device_label(index: int, device: dict, want_input: bool) -> str:
        api = hostapi_name(device)
        channel_key = "max_input_channels" if want_input else "max_output_channels"
        channels = int(device.get(channel_key) or 0)
        return f"{device['name']}  [{api}; {channels} channel(s); index {index}]"

    def _selected_device(self, combo: ttk.Combobox | None, options: list[tuple[int, dict]], label: str):
        if combo is None:
            raise ConfigError(f"{label} selector is unavailable.")
        selected = combo.current()
        if selected < 0 or selected >= len(options):
            raise ConfigError(f"Select a valid {label.lower()} before saving.")
        return options[selected]

    def _form_config(self) -> LoudGateConfig:
        input_index, input_device = self._selected_device(
            self._input_combo, self.input_options, "Input device"
        )
        output_index, output_device = self._selected_device(
            self._output_combo, self.output_options, "Output device"
        )
        cfg = copy.copy(self.cfg or default_config())
        try:
            cfg.input_channel = int(self.input_channel_var.get())
            cfg.threshold_db = float(self.threshold_var.get())
            cfg.release_ms = float(self.release_var.get())
            cfg.lookahead_ms = float(self.lookahead_var.get())
            cfg.threshold_step_db = float(self.threshold_step_var.get())
        except ValueError as exc:
            raise ConfigError(f"Audio values must be valid numbers: {exc}") from exc

        preferred_rate = cfg.sample_rate if cfg.has_device_selection else None
        pair = resolve_pair_format(
            self.devices,
            input_index,
            output_index,
            preferred_rate=preferred_rate,
        )
        if pair is None:
            raise ConfigError(
                "The selected WASAPI input/output pair could not be opened together. "
                "Check Windows formats and exclusive-mode ownership, then choose another pair."
            )
        if not 0 <= cfg.input_channel < pair.input_channels:
            raise ConfigError(
                f"Input channel must be between 0 and {pair.input_channels - 1} for this input."
            )

        cfg.version = CONFIG_VERSION
        cfg.input_device_index = input_index
        cfg.input_device_name = str(input_device["name"])
        cfg.input_device_hostapi = pair.hostapi
        cfg.output_device_index = output_index
        cfg.output_device_name = str(output_device["name"])
        cfg.output_device_hostapi = pair.hostapi
        cfg.sample_rate = pair.sample_rate
        cfg.mute_hotkey = self.mute_hotkey_var.get().strip()
        cfg.stop_hotkey = self.stop_hotkey_var.get().strip()
        cfg.threshold_down_hotkey = self.threshold_down_var.get().strip()
        cfg.threshold_up_hotkey = self.threshold_up_var.get().strip()
        cfg.validate(require_devices=True)
        try:
            configured_hotkeys(cfg)
        except RuntimeError as exc:
            raise ConfigError(str(exc)) from exc
        return cfg

    def save_settings(self, *, show_success: bool = True) -> bool:
        try:
            cfg = self._form_config()
            save_config(cfg)
            self.cfg = cfg
            self.config_var.set(f"Config: {config_path()}")
            if not looks_like_virtual_output(cfg.output_device_name or ""):
                if self._warned_non_vb_output != cfg.output_device_name:
                    self._warned_non_vb_output = cfg.output_device_name
                    messagebox.showwarning(
                        "Non-VB-CABLE output selected",
                        "This WASAPI output was validated as openable, but it is not recognized as VB-CABLE. "
                        "Routing microphone audio to other endpoints is untested and at your own risk.",
                        parent=self.root,
                    )
            if show_success:
                self._show_info("Saved", "Configuration saved. Stop and Run to apply changes to a running runtime.")
            return True
        except (ConfigError, OSError, ValueError) as exc:
            self._show_error("Cannot save configuration", str(exc))
            return False

    def run_runtime(self) -> None:
        if not self.save_settings(show_success=False):
            return
        try:
            response = request_runtime("ping")
            if response.get("ok"):
                self._show_info(
                    "Already running",
                    "The runtime is already running. Stop it first, then press Run to apply saved settings.",
                )
                return
        except IpcError:
            pass
        try:
            launch_runtime()
        except OSError as exc:
            self._show_error("Runtime launch failed", str(exc))
            return
        self.status_var.set("Starting")
        self._set_indicator("orange")

    def stop_runtime(self) -> None:
        try:
            response = request_runtime("stop")
            if not response.get("ok", True):
                raise IpcError(str(response.get("error", "The runtime rejected the stop request.")))
        except IpcError:
            self._apply_runtime_unavailable()

    def install_autorun(self) -> None:
        try:
            install_startup_task()
            self._refresh_autorun_label()
            self._show_info("Autorun installed", f"Task Scheduler task '{TASK_NAME}' starts the runtime at logon.")
        except (OSError, RuntimeError) as exc:
            self._show_error("Autorun installation failed", str(exc))

    def remove_autorun(self) -> None:
        try:
            uninstall_startup_task()
            self._refresh_autorun_label()
        except (OSError, RuntimeError) as exc:
            self._show_error("Autorun removal failed", str(exc))

    def _refresh_autorun_label(self) -> None:
        result = subprocess.run(
            ["schtasks", "/Query", "/TN", TASK_NAME],
            capture_output=True,
            text=True,
            check=False,
        )
        installed = result.returncode == 0
        self.autorun_var.set(f"Autorun: {'installed' if installed else 'not installed'} ({TASK_NAME})")

    def open_log(self) -> None:
        try:
            log_path().parent.mkdir(parents=True, exist_ok=True)
            if not log_path().exists():
                log_path().write_text("Loud Gate has not written a runtime log yet.\n", encoding="utf-8")
            os.startfile(str(log_path()))
        except OSError as exc:
            self._show_error("Cannot open log", str(exc))

    def _poll_runtime(self) -> None:
        if not self._status_request_in_flight and not self._closed:
            self._status_request_in_flight = True
            threading.Thread(target=self._get_status, name="RuntimeStatus", daemon=True).start()
        if not self._closed:
            self.root.after(1000, self._poll_runtime)

    def _get_status(self) -> None:
        try:
            response = request_runtime("status")
            self._work_queue.put(("status", response))
        except IpcError as exc:
            self._work_queue.put(("status_error", str(exc)))

    def _poll_queue(self) -> None:
        try:
            while True:
                kind, value = self._work_queue.get_nowait()
                if kind == "devices":
                    self._apply_devices(*value)
                elif kind == "status":
                    self._status_request_in_flight = False
                    self._apply_status(value)
                elif kind == "status_error":
                    self._status_request_in_flight = False
                    self._apply_runtime_unavailable()
                elif kind == "error":
                    self._device_request_in_flight = False
                    self._show_error("Loud Gate", str(value))
        except queue.Empty:
            pass
        if not self._closed:
            self.root.after(100, self._poll_queue)

    def _apply_status(self, response: dict[str, Any]) -> None:
        if not response.get("ok", False):
            self._apply_runtime_unavailable()
            return
        status = response.get("status") or {}
        state = str(status.get("state", "unknown"))
        display_state = {
            "running": "Running",
            "starting": "Starting",
            "restarting": "Starting",
            "stopping": "Stopping",
            "stopped": "Stopped",
            "error": "Error",
        }.get(state, "Stopped")
        colors = {
            "running": "#22a447",
            "starting": "orange",
            "restarting": "orange",
            "stopping": "orange",
            "error": "#d33b3b",
        }
        self._set_indicator(colors.get(state, "#d33b3b"))
        self.status_var.set(display_state)

        cpu = float(status.get("cpu_load") or 0.0) * 100.0
        latency = status.get("latency_ms")
        cpu_text = f"{cpu:.1f}%" if state == "running" else "—"
        latency_text = f"{float(latency):.1f} ms" if latency is not None and state == "running" else "—"
        self.metrics_var.set(f"CPU: {cpu_text}   Latency: {latency_text}")
        self.input_route_var.set(f"Input: {status.get('input_device') or '—'}")
        self.output_route_var.set(f"Output: {status.get('output_device') or '—'}")

        health = status.get("last_health") or ""
        if status.get("error"):
            health = f"Error: {status['error']}"
        if health and self._health_label is not None:
            self.health_var.set(health)
            self._health_label.grid()
        elif self._health_label is not None:
            self._health_label.grid_remove()

    def _apply_runtime_unavailable(self) -> None:
        self._set_indicator("#d33b3b")
        self.status_var.set("Stopped")
        self.metrics_var.set("CPU: —   Latency: —")
        self.input_route_var.set("Input: —")
        self.output_route_var.set("Output: —")
        if self._health_label is not None:
            self._health_label.grid_remove()

    def close(self) -> None:
        self._closed = True
        self.root.destroy()

    def _show_info(self, title: str, message: str) -> None:
        messagebox.showinfo(title, message, parent=self.root)

    def _show_error(self, title: str, message: str) -> None:
        messagebox.showerror(title, message, parent=self.root)


def run_manager() -> int:
    root = tk.Tk()
    ManagerApp(root)
    root.mainloop()
    return 0
