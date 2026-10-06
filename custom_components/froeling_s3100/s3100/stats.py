"""Connection and protocol statistics."""

from __future__ import annotations

import time
from collections import Counter, deque
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class RollingStats:
    """Min/avg/max over the most recent samples."""

    samples: deque[float] = field(default_factory=lambda: deque(maxlen=200))
    count: int = 0

    def add(self, value: float) -> None:
        """Record a sample."""
        self.samples.append(value)
        self.count += 1

    def as_dict(self) -> dict[str, float | int | None]:
        """Summary in milliseconds."""
        if not self.samples:
            return {"count": self.count, "last_ms": None, "min_ms": None, "avg_ms": None, "max_ms": None}
        return {
            "count": self.count,
            "last_ms": round(self.samples[-1] * 1000, 1),
            "min_ms": round(min(self.samples) * 1000, 1),
            "avg_ms": round(sum(self.samples) / len(self.samples) * 1000, 1),
            "max_ms": round(max(self.samples) * 1000, 1),
        }


@dataclass(slots=True)
class Statistics:
    """Counters describing the health of the link."""

    connects: int = 0
    connect_failures: int = 0
    reconnects: int = 0
    timeouts: int = 0
    login_attempts: int = 0
    sessions_ready: int = 0
    checksum_errors: int = 0
    discarded_bytes: int = 0
    decode_errors: int = 0
    nacks_received: int = 0
    stale_frames: int = 0
    bytes_rx: int = 0
    bytes_tx: int = 0
    frames_rx: Counter[str] = field(default_factory=Counter)
    frames_tx: Counter[str] = field(default_factory=Counter)
    ack_latency: RollingStats = field(default_factory=RollingStats)
    m1_interval: RollingStats = field(default_factory=RollingStats)
    catalog_load_seconds: float | None = None
    last_frame_time: float | None = None
    connected_since: float | None = None
    last_error: str | None = None

    def mark_frame(self) -> None:
        """Remember when the last frame arrived."""
        self.last_frame_time = time.time()

    def as_dict(self) -> dict[str, Any]:
        """Plain representation for diagnostics."""
        return {
            "connects": self.connects,
            "connect_failures": self.connect_failures,
            "reconnects": self.reconnects,
            "timeouts": self.timeouts,
            "login_attempts": self.login_attempts,
            "sessions_ready": self.sessions_ready,
            "checksum_errors": self.checksum_errors,
            "discarded_bytes": self.discarded_bytes,
            "decode_errors": self.decode_errors,
            "nacks_received": self.nacks_received,
            "stale_frames": self.stale_frames,
            "bytes_rx": self.bytes_rx,
            "bytes_tx": self.bytes_tx,
            "frames_rx": dict(sorted(self.frames_rx.items())),
            "frames_tx": dict(sorted(self.frames_tx.items())),
            "ack_latency": self.ack_latency.as_dict(),
            "m1_interval": self.m1_interval.as_dict(),
            "catalog_load_seconds": self.catalog_load_seconds,
            "last_frame_time": self.last_frame_time,
            "connected_since": self.connected_since,
            "last_error": self.last_error,
        }
