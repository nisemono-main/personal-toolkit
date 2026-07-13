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
    drift_correction_samples: int = 0

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
                self.drift_correction_samples,
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
        "drift_correction_samples",
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

    def report_drift_correction(self, sample_count: int) -> None:
        if sample_count <= 0:
            return
        with self._lock:
            self._counters["drift_correction_samples"] += int(sample_count)

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
    ) -> None:
        if output_channels <= 0:
            raise ValueError("output_channels must be greater than zero.")
        self.limiter = limiter
        self.mute_event = mute_event
        self.output_channels = int(output_channels)
        self.buffer = PcmRingBuffer(buffer_capacity_samples)
        self.health = AudioHealth()
        self._output_was_muted = False

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
        read_samples = self.buffer.read_into(mono_output)
        self.health.report_buffer_underflow(mono_output.size - read_samples)
        if outdata.shape[1] > 1:
            outdata[:, 1:] = mono_output[:, None]

    def rebalance_buffer(self, high_water_samples: int, max_drop_samples: int) -> int:
        """Apply a small elastic correction when independent device clocks drift apart."""

        available = self.buffer.available_samples
        excess = max(0, available - int(high_water_samples))
        if excess <= 0:
            return 0

        dropped = self.buffer.discard_oldest(min(excess, int(max_drop_samples)))
        self.health.report_drift_correction(dropped)
        return dropped
