"""Evidence-only trend calculations; sparse or counter-reset data is not a baseline."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Sample:
    observed_at: datetime
    used_bytes: int


@dataclass(frozen=True)
class Trend:
    bytes_per_second: float
    seconds_to_threshold: float | None
    baseline_seconds: float
    sample_count: int


def forecast(
    samples: list[Sample], *, capacity_bytes: int,
    threshold_fraction: float = 0.80, minimum_baseline_seconds: int = 300,
) -> Trend:
    if type(capacity_bytes) is not int or capacity_bytes <= 0:
        raise ValueError("Capacity must be a positive byte count.")
    if not math.isfinite(threshold_fraction) or not 0 < threshold_fraction < 1:
        raise ValueError("The preventive threshold must be a finite fraction below capacity.")
    if type(minimum_baseline_seconds) is not int or minimum_baseline_seconds < 300:
        raise ValueError("At least five minutes of real baseline is required.")
    if len(samples) < 6:
        raise ValueError("At least six distinct real observations are required.")
    for sample in samples:
        if sample.observed_at.tzinfo is None or type(sample.used_bytes) is not int or not 0 <= sample.used_bytes <= capacity_bytes:
            raise ValueError("Every sample needs an aware timestamp and valid observed byte count.")
    times = [(sample.observed_at - samples[0].observed_at).total_seconds() for sample in samples]
    if any(right <= left or right - left > 120 for left, right in zip(times, times[1:])):
        raise ValueError("Out-of-order, duplicate or excessively gapped observations cannot form a baseline.")
    if times[-1] < minimum_baseline_seconds:
        raise ValueError("The observed baseline is too short.")
    if any(right.used_bytes < left.used_bytes for left, right in zip(samples, samples[1:])):
        raise ValueError("A reset, cleanup or nonmonotonic series requires a new growth baseline.")
    mean_time = sum(times) / len(times)
    mean_used = sum(sample.used_bytes for sample in samples) / len(samples)
    numerator = sum((stamp - mean_time) * (sample.used_bytes - mean_used) for stamp, sample in zip(times, samples))
    denominator = sum((stamp - mean_time) ** 2 for stamp in times)
    slope = numerator / denominator
    remaining = capacity_bytes * threshold_fraction - samples[-1].used_bytes
    eta = 0.0 if remaining <= 0 else None if slope <= 0 else remaining / slope
    return Trend(slope, eta, times[-1], len(samples))
