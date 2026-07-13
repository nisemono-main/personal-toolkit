"""Audio-device discovery, matching, and stream capability checks."""

from __future__ import annotations

import sounddevice as sd

from .config import DEFAULT_SAMPLE_RATE, LoudGateConfig


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


def resolve_device_index(
    devices: list[dict], cfg: LoudGateConfig, key: str, want_input: bool
) -> int | None:
    idx_key = f"{key}_device_index"
    name_key = f"{key}_device_name"
    hostapi_key = f"{key}_device_hostapi"
    stored_idx = getattr(cfg, idx_key)
    stored_name = getattr(cfg, name_key)
    stored_hostapi = getattr(cfg, hostapi_key)
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

    raise RuntimeError("No common sample rate found for the selected input/output devices.")
