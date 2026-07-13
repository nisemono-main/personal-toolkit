"""Sample-accurate limiter and full-duplex callback for Loud Gate."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


DEFAULT_MAX_CALLBACK_FRAMES = 16384
MUTE_RAMP_MS = 5.0


@dataclass(slots=True)
class AudioControls:
    """Small callback-readable control snapshot updated by the hotkey thread."""

    threshold_db: float
    muted: bool = False

    def set_threshold_db(self, threshold_db: float) -> float:
        self.threshold_db = float(threshold_db)
        return self.threshold_db

    def adjust_threshold_db(self, delta_db: float) -> float:
        self.threshold_db = float(self.threshold_db + float(delta_db))
        return self.threshold_db


@dataclass(frozen=True, slots=True)
class AudioHealthSnapshot:
    """Monotonic facts written by the callback and sampled by the control thread."""

    callbacks: int = 0
    frames: int = 0
    input_overflows: int = 0
    input_underflows: int = 0
    output_overflows: int = 0
    output_underflows: int = 0
    callback_errors: int = 0
    oversized_callbacks: int = 0
    clipped_input_callbacks: int = 0

    @property
    def incident_count(self) -> int:
        return (
            self.input_overflows
            + self.input_underflows
            + self.output_overflows
            + self.output_underflows
            + self.callback_errors
            + self.oversized_callbacks
        )

    def since(self, previous: "AudioHealthSnapshot") -> "AudioHealthSnapshot":
        return AudioHealthSnapshot(
            **{
                field_name: max(0, getattr(self, field_name) - getattr(previous, field_name))
                for field_name in self.__dataclass_fields__
            }
        )


class AudioHealth:
    """Single-callback-writer counters that never acquire a callback-side lock."""

    def __init__(self) -> None:
        self.callbacks = 0
        self.frames = 0
        self.input_overflows = 0
        self.input_underflows = 0
        self.output_overflows = 0
        self.output_underflows = 0
        self.callback_errors = 0
        self.oversized_callbacks = 0
        self.clipped_input_callbacks = 0
        self.last_error: BaseException | None = None

    def report_status(self, status) -> None:
        if status is None:
            return
        for status_name in (
            "input_overflow",
            "input_underflow",
            "output_overflow",
            "output_underflow",
        ):
            if bool(getattr(status, status_name, False)):
                counter_name = f"{status_name}s"
                setattr(self, counter_name, getattr(self, counter_name) + 1)

    def snapshot(self) -> AudioHealthSnapshot:
        return AudioHealthSnapshot(
            callbacks=self.callbacks,
            frames=self.frames,
            input_overflows=self.input_overflows,
            input_underflows=self.input_underflows,
            output_overflows=self.output_overflows,
            output_underflows=self.output_underflows,
            callback_errors=self.callback_errors,
            oversized_callbacks=self.oversized_callbacks,
            clipped_input_callbacks=self.clipped_input_callbacks,
        )


class LookaheadLimiter:
    """Sample-accurate lookahead peak limiter independent of callback boundaries."""

    def __init__(
        self,
        controls: AudioControls,
        release_ms: float,
        lookahead_ms: float,
        sample_rate: int,
        max_callback_frames: int = DEFAULT_MAX_CALLBACK_FRAMES,
    ) -> None:
        if sample_rate <= 0:
            raise ValueError("sample_rate must be greater than zero.")
        if release_ms < 0 or lookahead_ms < 0:
            raise ValueError("release_ms and lookahead_ms cannot be negative.")
        if max_callback_frames <= 0:
            raise ValueError("max_callback_frames must be greater than zero.")

        self.controls = controls
        self.release_ms = float(release_ms)
        self.lookahead_ms = float(lookahead_ms)
        self.sample_rate = int(sample_rate)
        self.max_callback_frames = int(max_callback_frames)
        self.lookahead_samples = max(
            0,
            int(round(self.sample_rate * self.lookahead_ms / 1000.0)),
        )

        if self.release_ms == 0:
            self._release_coefficient = 0.0
        else:
            self._release_coefficient = math.exp(
                -1.0 / (self.sample_rate * self.release_ms / 1000.0)
            )

        workspace_size = self.lookahead_samples + self.max_callback_frames
        self._signal_history = np.zeros(self.lookahead_samples, dtype=np.float32)
        self._absolute_history = np.zeros(self.lookahead_samples, dtype=np.float32)
        self._signal_workspace = np.zeros(workspace_size, dtype=np.float32)
        self._absolute_workspace = np.zeros(workspace_size, dtype=np.float32)
        self._window_peaks = np.zeros(self.max_callback_frames, dtype=np.float32)
        self._gain_workspace = np.ones(self.max_callback_frames, dtype=np.float32)
        self._current_gain = 1.0
        self._mute_gain = 0.0 if controls.muted else 1.0
        self._mute_step = 1.0 / max(
            1,
            int(round(self.sample_rate * MUTE_RAMP_MS / 1000.0)),
        )
        self.last_input_peak = 0.0

    def process_into(
        self,
        source: np.ndarray,
        destination: np.ndarray,
        muted: bool,
    ) -> None:
        frames = int(source.shape[0])
        if frames != int(destination.shape[0]):
            raise ValueError("source and destination must have the same frame count.")
        if frames > self.max_callback_frames:
            raise ValueError(
                f"Callback supplied {frames} frames; capacity is {self.max_callback_frames}."
            )
        if frames == 0:
            return

        lookahead = self.lookahead_samples
        signal = self._signal_workspace[: lookahead + frames]
        absolute = self._absolute_workspace[: lookahead + frames]
        if lookahead:
            np.copyto(signal[:lookahead], self._signal_history)
            np.copyto(absolute[:lookahead], self._absolute_history)

        current_signal = signal[lookahead:]
        np.copyto(current_signal, source, casting="unsafe")
        np.nan_to_num(current_signal, copy=False, nan=0.0, posinf=1.0, neginf=-1.0)
        if muted:
            current_signal.fill(0.0)

        current_absolute = absolute[lookahead:]
        np.abs(current_signal, out=current_absolute)
        self.last_input_peak = float(np.max(current_absolute))

        windows = np.lib.stride_tricks.sliding_window_view(
            absolute,
            lookahead + 1,
        )
        peaks = self._window_peaks[:frames]
        np.max(windows, axis=1, out=peaks)

        threshold_linear = 10.0 ** (float(self.controls.threshold_db) / 20.0)
        release = self._release_coefficient
        current_gain = self._current_gain
        mute_gain = self._mute_gain
        mute_target = 0.0 if muted else 1.0
        mute_step = self._mute_step
        gains = self._gain_workspace[:frames]

        for index in range(frames):
            peak = float(peaks[index])
            target_gain = 1.0 if peak <= threshold_linear else threshold_linear / peak
            if target_gain < current_gain:
                current_gain = target_gain
            else:
                current_gain = release * current_gain + (1.0 - release) * target_gain

            if mute_gain < mute_target:
                mute_gain = min(mute_target, mute_gain + mute_step)
            elif mute_gain > mute_target:
                mute_gain = max(mute_target, mute_gain - mute_step)
            gains[index] = current_gain * mute_gain

        self._current_gain = current_gain
        self._mute_gain = mute_gain
        np.multiply(signal[:frames], gains, out=destination)

        if lookahead:
            np.copyto(self._signal_history, signal[frames : frames + lookahead])
            np.copyto(self._absolute_history, absolute[frames : frames + lookahead])


class AudioEngine:
    """Own the limiter and the single full-duplex PortAudio callback."""

    def __init__(
        self,
        limiter: LookaheadLimiter,
        controls: AudioControls,
        input_channel: int,
        output_channels: int,
    ) -> None:
        if input_channel < 0:
            raise ValueError("input_channel cannot be negative.")
        if output_channels <= 0:
            raise ValueError("output_channels must be greater than zero.")
        self.limiter = limiter
        self.controls = controls
        self.input_channel = int(input_channel)
        self.output_channels = int(output_channels)
        self.health = AudioHealth()

    def callback(self, indata, outdata, frames, time_info, status) -> None:
        self.health.callbacks += 1
        self.health.frames += int(frames)
        self.health.report_status(status)

        try:
            if frames > self.limiter.max_callback_frames:
                self.health.oversized_callbacks += 1
                outdata.fill(0.0)
                return
            if self.input_channel >= indata.shape[1]:
                raise RuntimeError(
                    f"Configured input channel {self.input_channel} is unavailable; "
                    f"stream supplied {indata.shape[1]} channels."
                )

            raw_input = indata[:, self.input_channel]
            processed_output = outdata[:, 0]
            self.limiter.process_into(
                raw_input,
                processed_output,
                bool(self.controls.muted),
            )
            if self.limiter.last_input_peak >= 0.999:
                self.health.clipped_input_callbacks += 1
            if outdata.shape[1] > 1:
                outdata[:, 1:] = processed_output[:, None]

        except BaseException as exc:
            outdata.fill(0.0)
            self.health.callback_errors += 1
            self.health.last_error = exc
