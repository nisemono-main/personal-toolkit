"""Real-time-oriented limiter and PCM buffering primitives for Loud Gate."""

from __future__ import annotations

import math
import threading
from collections import deque
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True, slots=True)
class AudioHealthSnapshot:
    """Callback and buffer incidents collected without doing I/O in callbacks."""

    input_overflows: int = 0
    input_underflows: int = 0
    output_overflows: int = 0
    output_underflows: int = 0
    buffer_underflow_samples: int = 0
    dropped_samples: int = 0

    @property
    def callback_status_events(self) -> int:
        return (
            self.input_overflows
            + self.input_underflows
            + self.output_overflows
            + self.output_underflows
        )

    @property
    def has_callback_status(self) -> bool:
        return self.callback_status_events > 0

    @property
    def has_events(self) -> bool:
        return self.has_callback_status or any(
            (
                self.buffer_underflow_samples,
                self.dropped_samples,
            )
        )


class AudioHealth:
    """Thread-safe counters for facts that must leave the real-time callbacks."""

    _COUNTER_NAMES = (
        "input_overflows",
        "input_underflows",
        "output_overflows",
        "output_underflows",
        "buffer_underflow_samples",
        "dropped_samples",
    )

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters = {name: 0 for name in self._COUNTER_NAMES}

    def report_callback_status(self, direction: str, status) -> None:
        if direction not in {"input", "output"} or status is None:
            return

        with self._lock:
            for status_name, counter_name in (
                ("overflow", f"{direction}_overflows"),
                ("underflow", f"{direction}_underflows"),
            ):
                if bool(getattr(status, f"{direction}_{status_name}", False)):
                    self._counters[counter_name] += 1

    def report_buffer_underflow(self, sample_count: int) -> None:
        if sample_count <= 0:
            return
        with self._lock:
            self._counters["buffer_underflow_samples"] += int(sample_count)

    def report_dropped_samples(self, sample_count: int) -> None:
        if sample_count <= 0:
            return
        with self._lock:
            self._counters["dropped_samples"] += int(sample_count)

    def consume(self) -> AudioHealthSnapshot:
        with self._lock:
            snapshot = AudioHealthSnapshot(**self._counters)
            for name in self._COUNTER_NAMES:
                self._counters[name] = 0
            return snapshot


class PcmRingBuffer:
    """Bounded single-producer/single-consumer mono PCM sample buffer.

    The input callback is the producer and the output callback is the consumer.
    A narrow lock protects the small NumPy copy and index-update region because
    NumPy may release the interpreter lock during those copies. The expensive
    limiter work happens outside this lock. When full, the oldest samples are
    discarded to keep latency bounded and favor current microphone audio.
    """

    def __init__(self, capacity_samples: int) -> None:
        if capacity_samples <= 0:
            raise ValueError("capacity_samples must be greater than zero.")
        self._storage = np.zeros(int(capacity_samples), dtype=np.int16)
        self._capacity = int(capacity_samples)
        self._read_index = 0
        self._write_index = 0
        self._size = 0
        self._lock = threading.Lock()

    @property
    def capacity_samples(self) -> int:
        return self._capacity

    @property
    def available_samples(self) -> int:
        with self._lock:
            return self._size

    def clear(self) -> None:
        with self._lock:
            self._read_index = self._write_index
            self._size = 0

    def write(self, samples: np.ndarray) -> tuple[int, int]:
        values = np.asarray(samples, dtype=np.int16)
        if values.ndim != 1 or values.size == 0:
            return 0, 0

        with self._lock:
            discarded = 0
            if values.size > self._capacity:
                discarded += int(values.size - self._capacity)
                values = values[-self._capacity :]

            count = int(values.size)
            overflow = max(0, self._size + count - self._capacity)
            if overflow:
                discarded += overflow
                self._read_index = (self._read_index + overflow) % self._capacity
                self._size -= overflow

            first = min(count, self._capacity - self._write_index)
            self._storage[self._write_index : self._write_index + first] = values[:first]
            remaining = count - first
            if remaining:
                self._storage[:remaining] = values[first:]

            self._write_index = (self._write_index + count) % self._capacity
            self._size += count
            return count, discarded

    def discard_oldest(self, sample_count: int) -> int:
        """Discard a bounded amount of old audio for elastic clock-drift control."""

        if sample_count <= 0:
            return 0
        with self._lock:
            discarded = min(int(sample_count), self._size)
            self._read_index = (self._read_index + discarded) % self._capacity
            self._size -= discarded
            return discarded

    def read_into(self, destination: np.ndarray) -> int:
        """Read into a caller-owned 1-D destination and zero-fill any shortfall."""

        if destination.ndim != 1:
            raise ValueError("destination must be a one-dimensional PCM view.")

        with self._lock:
            destination.fill(0)
            count = min(int(destination.size), self._size)
            if count == 0:
                return 0

            first = min(count, self._capacity - self._read_index)
            destination[:first] = self._storage[self._read_index : self._read_index + first]
            remaining = count - first
            if remaining:
                destination[first:count] = self._storage[:remaining]

            self._read_index = (self._read_index + count) % self._capacity
            self._size -= count
            return count


def peak_dbfs(samples: np.ndarray) -> float:
    peak = float(np.max(np.abs(samples))) if samples.size else 0.0
    return 20.0 * np.log10(peak + 1e-12)


class LookaheadLimiter:
    """Peak limiter with a short lookahead and release smoothing."""

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
        self.segment_size = max(1, int(round(self.sample_rate * 0.001)))
        self.segment_seconds = self.segment_size / float(self.sample_rate)
        self.lookahead_segments = max(
            1,
            math.ceil(self.lookahead_ms / (self.segment_seconds * 1000.0)),
        )

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

    def _target_gain_db(self, peak_db: float, threshold_db: float) -> float:
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

        # Read the control value once per callback rather than taking a lock
        # once for every 1 ms lookahead segment.
        threshold_db = self.get_threshold_db()
        output = np.empty_like(block)
        output_offset = 0
        offset = 0
        total = int(block.shape[0])

        while offset < total:
            end = min(total, offset + self.segment_size)
            segment = block[offset:end]
            segment_size = end - offset
            offset = end

            self.pending_segments.append(segment.copy())
            self.pending_peaks.append(peak_dbfs(segment))

            if len(self.pending_segments) <= self.lookahead_segments:
                output[output_offset : output_offset + segment_size] = 0.0
                output_offset += segment_size
                continue

            window_peak_db = max(self.pending_peaks)
            target_gain_db = self._target_gain_db(window_peak_db, threshold_db)

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

            copy_count = min(segment_size, int(out_segment.size))
            output[output_offset : output_offset + copy_count] = out_segment[:copy_count] * gain
            if copy_count < segment_size:
                output[output_offset + copy_count : output_offset + segment_size] = 0.0
            output_offset += segment_size

        return output


class AudioEngine:
    """Own limiter state and callbacks for the input/output stream pair."""

    def __init__(
        self,
        limiter: LookaheadLimiter,
        mute_event: threading.Event,
        output_channels: int,
        buffer_capacity_samples: int,
        target_buffer_samples: int | None = None,
        max_clock_adjustment: float = 0.005,
    ) -> None:
        if output_channels <= 0:
            raise ValueError("output_channels must be greater than zero.")
        if target_buffer_samples is not None and target_buffer_samples <= 0:
            raise ValueError("target_buffer_samples must be greater than zero when provided.")
        if not 0.0 <= max_clock_adjustment < 0.5:
            raise ValueError("max_clock_adjustment must be between zero and 0.5.")
        self.limiter = limiter
        self.mute_event = mute_event
        self.output_channels = int(output_channels)
        self.buffer = PcmRingBuffer(buffer_capacity_samples)
        self.health = AudioHealth()
        self._target_buffer_samples = target_buffer_samples
        self._max_clock_adjustment = float(max_clock_adjustment)
        self._resampler_capacity = max(8192, self.limiter.block_size * 2)
        self._resampler_source = np.zeros(self._resampler_capacity, dtype=np.int16)
        self._resampler_source_float = np.empty(self._resampler_capacity, dtype=np.float32)
        self._resampler_output_positions = np.arange(
            self._resampler_capacity,
            dtype=np.float32,
        )
        self._resampler_positions = np.empty(self._resampler_capacity, dtype=np.float32)
        self._resampler_left_positions = np.empty(self._resampler_capacity, dtype=np.float32)
        self._resampler_fractions = np.empty(self._resampler_capacity, dtype=np.float32)
        self._resampler_left_indices = np.empty(self._resampler_capacity, dtype=np.intp)
        self._resampler_right_indices = np.empty(self._resampler_capacity, dtype=np.intp)
        self._resampler_left_values = np.empty(self._resampler_capacity, dtype=np.float32)
        self._resampler_right_values = np.empty(self._resampler_capacity, dtype=np.float32)
        self._resampler_output = np.empty(self._resampler_capacity, dtype=np.float32)
        self._output_was_muted = False

    def _ensure_resampler_capacity(self, frames: int, source_count: int) -> None:
        required = max(int(frames), int(source_count))
        if required <= self._resampler_capacity:
            return

        self._resampler_capacity = required
        self._resampler_source = np.zeros(required, dtype=np.int16)
        self._resampler_source_float = np.empty(required, dtype=np.float32)
        self._resampler_output_positions = np.arange(required, dtype=np.float32)
        self._resampler_positions = np.empty(required, dtype=np.float32)
        self._resampler_left_positions = np.empty(required, dtype=np.float32)
        self._resampler_fractions = np.empty(required, dtype=np.float32)
        self._resampler_left_indices = np.empty(required, dtype=np.intp)
        self._resampler_right_indices = np.empty(required, dtype=np.intp)
        self._resampler_left_values = np.empty(required, dtype=np.float32)
        self._resampler_right_values = np.empty(required, dtype=np.float32)
        self._resampler_output = np.empty(required, dtype=np.float32)

    def _clock_ratio(self, available_samples: int) -> float:
        if self._target_buffer_samples is None:
            return 1.0

        relative_error = (
            float(available_samples - self._target_buffer_samples)
            / float(self._target_buffer_samples)
        )
        correction = max(
            -self._max_clock_adjustment,
            min(self._max_clock_adjustment, relative_error * 0.02),
        )
        return 1.0 + correction

    def input_callback(self, indata, frames, time_info, status) -> None:
        self.health.report_callback_status("input", status)

        muted = self.mute_event.is_set()
        incoming = np.asarray(indata[:, 0], dtype=np.float32) / 32768.0
        processed = self.limiter.process(incoming, muted)
        pcm = np.clip(np.rint(processed * 32767.0), -32768, 32767).astype(np.int16)

        if muted:
            self.buffer.clear()
        _, dropped_samples = self.buffer.write(pcm)
        self.health.report_dropped_samples(dropped_samples)

    def output_callback(self, outdata, frames, time_info, status) -> None:
        self.health.report_callback_status("output", status)

        if self.mute_event.is_set():
            self.buffer.clear()
            self._output_was_muted = True
            outdata.fill(0)
            return

        if self._output_was_muted:
            self.buffer.clear()
            self._output_was_muted = False

        mono_output = outdata[:, 0]
        frames = int(mono_output.size)
        if frames <= 0:
            return
        ratio = self._clock_ratio(self.buffer.available_samples)
        source_count = max(2, int(round(frames * ratio)))
        self._ensure_resampler_capacity(frames, source_count)

        if source_count == frames:
            read_samples = self.buffer.read_into(mono_output)
        else:
            source = self._resampler_source[:source_count]
            read_samples = self.buffer.read_into(source)
            source_float = self._resampler_source_float[:source_count]
            np.copyto(source_float, source, casting="unsafe")

            if frames == 1:
                self._resampler_output[0] = source_float[0]
            else:
                positions = self._resampler_positions[:frames]
                left_positions = self._resampler_left_positions[:frames]
                np.multiply(
                    self._resampler_output_positions[:frames],
                    (source_count - 1) / float(frames - 1),
                    out=positions,
                )
                np.floor(positions, out=left_positions)
                self._resampler_left_indices[:frames] = left_positions
                np.subtract(positions, left_positions, out=self._resampler_fractions[:frames])

                left_indices = self._resampler_left_indices[:frames]
                right_indices = self._resampler_right_indices[:frames]
                right_indices[:] = left_indices
                right_indices += 1
                np.minimum(right_indices, source_count - 1, out=right_indices)
                np.take(
                    source_float,
                    left_indices,
                    out=self._resampler_left_values[:frames],
                )
                np.take(
                    source_float,
                    right_indices,
                    out=self._resampler_right_values[:frames],
                )
                np.subtract(
                    self._resampler_right_values[:frames],
                    self._resampler_left_values[:frames],
                    out=self._resampler_output[:frames],
                )
                np.multiply(
                    self._resampler_output[:frames],
                    self._resampler_fractions[:frames],
                    out=self._resampler_output[:frames],
                )
                np.add(
                    self._resampler_left_values[:frames],
                    self._resampler_output[:frames],
                    out=self._resampler_output[:frames],
                )

            np.rint(self._resampler_output[:frames], out=self._resampler_output[:frames])
            np.clip(
                self._resampler_output[:frames],
                -32768,
                32767,
                out=self._resampler_output[:frames],
            )
            np.copyto(mono_output, self._resampler_output[:frames], casting="unsafe")

        self.health.report_buffer_underflow(
            max(0, source_count - read_samples)
        )
        if outdata.shape[1] > 1:
            outdata[:, 1:] = mono_output[:, None]
