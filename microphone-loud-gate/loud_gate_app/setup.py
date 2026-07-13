"""Interactive device and limiter setup for Loud Gate."""

from __future__ import annotations

import math

from .config import (
    CONFIG_VERSION,
    MAX_THRESHOLD_DB,
    MIN_THRESHOLD_DB,
    LoudGateConfig,
    default_config,
)
from .devices import (
    compatible_device,
    hostapi_name,
    list_devices,
    looks_like_virtual_output,
    relevant_physical_mic_inputs,
    relevant_virtual_cable_outputs,
)


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


def ask_float(
    prompt: str,
    default: float,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    while True:
        raw = input(f"{prompt} [{default}]: ").strip()
        if raw == "":
            return default
        try:
            value = float(raw)
        except ValueError:
            print("Please enter a valid number.")
            continue
        if not math.isfinite(value):
            print("Please enter a finite number.")
            continue
        if minimum is not None and value < minimum:
            print(f"Please enter a value greater than or equal to {minimum}.")
            continue
        if maximum is not None and value > maximum:
            print(f"Please enter a value less than or equal to {maximum}.")
            continue
        return value


def interactive_setup(existing: LoudGateConfig | None = None) -> LoudGateConfig:
    cfg = existing if existing is not None else default_config()

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

    cfg.version = CONFIG_VERSION
    cfg.input_device_index = in_idx
    cfg.input_device_name = devices[in_idx]["name"]
    cfg.input_device_hostapi = hostapi_name(devices[in_idx])
    cfg.output_device_index = out_idx
    cfg.output_device_name = devices[out_idx]["name"]
    cfg.output_device_hostapi = hostapi_name(devices[out_idx])
    cfg.threshold_db = ask_float(
        "Ceiling / threshold in dBFS",
        float(cfg.threshold_db),
        minimum=MIN_THRESHOLD_DB,
        maximum=MAX_THRESHOLD_DB,
    )
    cfg.release_ms = ask_float("Release time in ms", float(cfg.release_ms), minimum=0.0)
    cfg.validate(require_devices=True)
    return cfg
