"""Audio-device discovery and validated full-duplex pair selection."""

from __future__ import annotations

from dataclasses import dataclass

import sounddevice as sd

from .config import DEFAULT_SAMPLE_RATE, LoudGateConfig


SUPPORTED_HOSTAPI = "Windows WASAPI"
VB_CABLE_MISSING_WARNING = (
    "VB-Audio CABLE Input was not found as a Windows WASAPI output device. "
    "Install or enable VB-CABLE before starting Loud Gate."
)
UNSUPPORTED_HOSTAPI_WARNING = (
    "Only Windows WASAPI device pairs are supported and checked by Loud Gate. "
    "WDM-KS, DirectSound, MME, and other backends are untested and are used at your own risk."
)


@dataclass(frozen=True, slots=True)
class DevicePair:
    """A full-duplex input/output pair proven openable by one PortAudio stream."""

    input_index: int
    output_index: int
    input_channels: int
    output_channels: int
    sample_rate: int
    hostapi: str


def list_devices() -> list[dict]:
    return list(sd.query_devices())


def normalize_name(name: str) -> str:
    return " ".join(name.lower().split())


def hostapi_name(device: dict) -> str:
    apis = sd.query_hostapis()
    hostapi_index = device.get("hostapi")
    if isinstance(hostapi_index, int) and 0 <= hostapi_index < len(apis):
        return str(apis[hostapi_index]["name"])
    return "unknown"


def is_supported_hostapi(name: str) -> bool:
    return normalize_name(name) == normalize_name(SUPPORTED_HOSTAPI)


def same_hostapi(input_device: dict, output_device: dict) -> bool:
    return input_device.get("hostapi") == output_device.get("hostapi")


def stream_channels(device: dict, want_input: bool) -> int:
    key = "max_input_channels" if want_input else "max_output_channels"
    return max(1, min(2, int(device[key])))


def compatible_device(device: dict, want_input: bool) -> bool:
    key = "max_input_channels" if want_input else "max_output_channels"
    return int(device.get(key) or 0) > 0


def looks_like_physical_mic(name: str) -> bool:
    normalized = normalize_name(name)
    included = any(token in normalized for token in ("microphone", "mic", "headset", "capture"))
    excluded = any(
        token in normalized
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
    return included and not excluded


def looks_like_virtual_cable_output(name: str) -> bool:
    normalized = normalize_name(name)
    included = any(token in normalized for token in ("cable input", "virtual cable"))
    excluded = any(
        token in normalized
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
    return included and not excluded


def looks_like_virtual_output(name: str) -> bool:
    return looks_like_virtual_cable_output(name)


def hostapi_rank_name(name: str) -> int:
    normalized = normalize_name(name)
    if "wasapi" in normalized:
        return 0
    if "wdm-ks" in normalized:
        return 1
    if "directsound" in normalized:
        return 2
    if "mme" in normalized:
        return 3
    return 4


def hostapi_rank(device: dict) -> int:
    return hostapi_rank_name(hostapi_name(device))


def _unique_candidates(candidates: list[tuple[int, dict]]) -> list[tuple[int, dict]]:
    unique: dict[tuple[str, str], tuple[int, dict]] = {}
    for index, device in candidates:
        key = (normalize_name(str(device["name"])), normalize_name(hostapi_name(device)))
        unique.setdefault(key, (index, device))
    return list(unique.values())


def _mic_name_rank(device: dict) -> int:
    normalized = normalize_name(str(device["name"]))
    if "maonocaster" in normalized:
        return 0
    if "headset microphone" in normalized:
        return 1
    if "microphone" in normalized:
        return 2
    if "mic" in normalized:
        return 3
    return 4


def relevant_physical_mic_inputs(devices: list[dict]) -> list[tuple[int, dict]]:
    candidates = [
        (index, device)
        for index, device in enumerate(devices)
        if compatible_device(device, True)
        and is_supported_hostapi(hostapi_name(device))
        and looks_like_physical_mic(str(device["name"]))
    ]
    if not candidates:
        candidates = [
            (index, device)
            for index, device in enumerate(devices)
            if compatible_device(device, True)
            and is_supported_hostapi(hostapi_name(device))
        ]
    return sorted(
        _unique_candidates(candidates),
        key=lambda item: (
            _mic_name_rank(item[1]),
            hostapi_rank(item[1]),
            normalize_name(str(item[1]["name"])),
            item[0],
        ),
    )


def relevant_virtual_cable_outputs(devices: list[dict]) -> list[tuple[int, dict]]:
    candidates = [
        (index, device)
        for index, device in enumerate(devices)
        if compatible_device(device, False)
        and is_supported_hostapi(hostapi_name(device))
        and looks_like_virtual_cable_output(str(device["name"]))
    ]
    return sorted(
        _unique_candidates(candidates),
        key=lambda item: (
            hostapi_rank(item[1]),
            normalize_name(str(item[1]["name"])),
            item[0],
        ),
    )


def _rate_candidates(
    input_device: dict,
    output_device: dict,
    preferred_rate: int | None,
) -> list[int]:
    candidates: list[int] = []
    for rate in (
        preferred_rate,
        DEFAULT_SAMPLE_RATE,
        int(round(input_device.get("default_samplerate") or DEFAULT_SAMPLE_RATE)),
        int(round(output_device.get("default_samplerate") or DEFAULT_SAMPLE_RATE)),
        48000,
    ):
        if rate is not None and rate not in candidates:
            candidates.append(int(rate))
    return candidates


def can_open_full_duplex_stream(
    input_index: int,
    output_index: int,
    input_channels: int,
    output_channels: int,
    sample_rate: int,
) -> bool:
    """Open and close the exact pair without starting audio callbacks."""

    def callback(indata, outdata, frames, time_info, status):
        outdata.fill(0)

    stream = None
    try:
        stream = sd.Stream(
            device=(input_index, output_index),
            channels=(input_channels, output_channels),
            samplerate=sample_rate,
            blocksize=0,
            dtype=("float32", "float32"),
            latency=("high", "high"),
            callback=callback,
        )
        return True
    except Exception:
        return False
    finally:
        if stream is not None:
            stream.close()


def resolve_pair_format(
    devices: list[dict],
    input_index: int,
    output_index: int,
    preferred_rate: int | None = None,
) -> DevicePair | None:
    input_device = devices[input_index]
    output_device = devices[output_index]
    if (
        not same_hostapi(input_device, output_device)
        or not is_supported_hostapi(hostapi_name(input_device))
    ):
        return None

    input_channels = stream_channels(input_device, True)
    output_channels = stream_channels(output_device, False)
    for sample_rate in _rate_candidates(input_device, output_device, preferred_rate):
        if can_open_full_duplex_stream(
            input_index,
            output_index,
            input_channels,
            output_channels,
            sample_rate,
        ):
            return DevicePair(
                input_index=input_index,
                output_index=output_index,
                input_channels=input_channels,
                output_channels=output_channels,
                sample_rate=sample_rate,
                hostapi=hostapi_name(input_device),
            )
    return None


def relevant_device_pairs(devices: list[dict]) -> list[DevicePair]:
    pairs: list[DevicePair] = []
    for input_index, input_device in relevant_physical_mic_inputs(devices):
        for output_index, output_device in relevant_virtual_cable_outputs(devices):
            if (
                not same_hostapi(input_device, output_device)
                or not is_supported_hostapi(hostapi_name(input_device))
            ):
                continue
            pair = resolve_pair_format(devices, input_index, output_index)
            if pair is not None:
                pairs.append(pair)
    return sorted(
        pairs,
        key=lambda pair: (
            hostapi_rank_name(pair.hostapi),
            _mic_name_rank(devices[pair.input_index]),
            normalize_name(str(devices[pair.input_index]["name"])),
            pair.input_index,
            pair.output_index,
        ),
    )


def _stored_device_candidates(
    devices: list[dict],
    stored_index: int | None,
    stored_name: str | None,
    stored_hostapi: str | None,
    want_input: bool,
) -> list[int]:
    if not stored_name:
        return []

    normalized_name = normalize_name(stored_name)
    normalized_api = normalize_name(stored_hostapi or "")
    candidates: list[int] = []
    for index, device in enumerate(devices):
        if not compatible_device(device, want_input):
            continue
        if not is_supported_hostapi(hostapi_name(device)):
            continue
        candidate_name = normalize_name(str(device["name"]))
        if candidate_name != normalized_name and normalized_name not in candidate_name:
            continue
        candidates.append(index)

    return sorted(
        candidates,
        key=lambda index: (
            0 if index == stored_index else 1,
            0 if normalize_name(hostapi_name(devices[index])) == normalized_api else 1,
            hostapi_rank(devices[index]),
            index,
        ),
    )


def resolve_device_pair(devices: list[dict], cfg: LoudGateConfig) -> DevicePair | None:
    """Resolve saved names to a same-backend pair and validate the exact stream."""

    input_candidates = _stored_device_candidates(
        devices,
        cfg.input_device_index,
        cfg.input_device_name,
        cfg.input_device_hostapi,
        True,
    )
    output_candidates = _stored_device_candidates(
        devices,
        cfg.output_device_index,
        cfg.output_device_name,
        cfg.output_device_hostapi,
        False,
    )

    candidate_pairs: list[tuple[int, int, int, int]] = []
    for input_index in input_candidates:
        for output_index in output_candidates:
            if not same_hostapi(devices[input_index], devices[output_index]):
                continue
            api_rank = hostapi_rank(devices[input_index])
            index_penalty = int(input_index != cfg.input_device_index) + int(
                output_index != cfg.output_device_index
            )
            candidate_pairs.append((api_rank, index_penalty, input_index, output_index))

    for _, _, input_index, output_index in sorted(candidate_pairs):
        pair = resolve_pair_format(
            devices,
            input_index,
            output_index,
            preferred_rate=cfg.sample_rate,
        )
        if pair is not None:
            return pair
    return None
