"""Interactive selection of a validated full-duplex device pair."""

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
    UNSUPPORTED_HOSTAPI_WARNING,
    VB_CABLE_MISSING_WARNING,
    DevicePair,
    list_devices,
    relevant_device_pairs,
    relevant_virtual_cable_outputs,
)


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


def pick_pair(pairs: list[DevicePair], devices: list[dict]) -> DevicePair:
    if not pairs:
        raise RuntimeError(
            "No Windows WASAPI microphone/VB-CABLE pair could be opened. "
            "Other audio backends are untested and are used at your own risk."
        )

    print("\nValidated full-duplex microphone routes:\n")
    for menu_index, pair in enumerate(pairs):
        print(
            f"  [{menu_index}] {devices[pair.input_index]['name']} -> "
            f"{devices[pair.output_index]['name']}  "
            f"({pair.hostapi}; {pair.sample_rate} Hz; device indexes "
            f"{pair.input_index}/{pair.output_index})"
        )
    print()

    while True:
        raw = input("Select a route [0]: ").strip()
        if raw == "":
            return pairs[0]
        if raw.isdigit() and 0 <= int(raw) < len(pairs):
            return pairs[int(raw)]
        print("Enter a route number from the list above.")


def pick_input_channel(channel_count: int, default: int) -> int:
    if channel_count == 1:
        return 0
    default = default if 0 <= default < channel_count else 0
    while True:
        raw = input(
            f"Input channel [default: {default}; available: 0-{channel_count - 1}]: "
        ).strip()
        if raw == "":
            return default
        if raw.isdigit() and 0 <= int(raw) < channel_count:
            return int(raw)
        print(f"Enter a channel from 0 to {channel_count - 1}.")


def interactive_setup(existing: LoudGateConfig | None = None) -> LoudGateConfig:
    cfg = existing if existing is not None else default_config()
    devices = list_devices()
    if not relevant_virtual_cable_outputs(devices):
        print(f"\nWARNING: {VB_CABLE_MISSING_WARNING}")
        print(f"WARNING: {UNSUPPORTED_HOSTAPI_WARNING}\n")
    pair = pick_pair(relevant_device_pairs(devices), devices)

    print(
        "\nThe selected route was opened as one full-duplex stream. "
        "Applications should use CABLE Output as their microphone.\n"
    )
    input_channel = pick_input_channel(pair.input_channels, cfg.input_channel)

    cfg.version = CONFIG_VERSION
    cfg.input_device_index = pair.input_index
    cfg.input_device_name = str(devices[pair.input_index]["name"])
    cfg.input_device_hostapi = pair.hostapi
    cfg.input_channel = input_channel
    cfg.output_device_index = pair.output_index
    cfg.output_device_name = str(devices[pair.output_index]["name"])
    cfg.output_device_hostapi = pair.hostapi
    cfg.sample_rate = pair.sample_rate
    cfg.threshold_db = ask_float(
        "Ceiling / threshold in dBFS",
        float(cfg.threshold_db),
        minimum=MIN_THRESHOLD_DB,
        maximum=MAX_THRESHOLD_DB,
    )
    cfg.release_ms = ask_float("Release time in ms", float(cfg.release_ms), minimum=0.0)
    cfg.lookahead_ms = ask_float(
        "Lookahead time in ms",
        float(cfg.lookahead_ms),
        minimum=0.0,
    )
    cfg.validate(require_devices=True)
    return cfg
