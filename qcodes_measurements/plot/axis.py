"""Coordinate transforms shared by local and remote image items."""

import sys

import numpy as np
from numpy.typing import ArrayLike


def image_axis_tolerance(values: np.ndarray) -> float:
    """Compare coordinates relative to pixel spacing, allowing float roundoff."""
    spacing = abs(float(values[-1] - values[0])) / max(values.size - 1, 1)
    roundoff = sys.float_info.epsilon * max(abs(float(values[0])), abs(float(values[-1]))) * 8
    return max(spacing * 1e-7, roundoff)


def is_regular_image_axis(values: np.ndarray) -> bool:
    if values.size == 0 or not np.isfinite(values).all():
        return False
    if values.size == 1:
        return True
    step = (values[-1] - values[0]) / (values.size - 1)
    return bool(
        step != 0 and np.allclose(np.diff(values), step, rtol=0, atol=image_axis_tolerance(values))
    )


def image_axis(setpoints: ArrayLike) -> tuple[float, float]:
    """Return pixel origin and spacing, placing pixel centres at the setpoints."""
    values = np.asarray(setpoints)
    if values.ndim != 1 or values.size == 0:
        raise ValueError("Image setpoints must be a non-empty one-dimensional array")
    step = float((values[-1] - values[0]) / (values.size - 1)) if values.size > 1 else 1.0
    if step == 0:
        step = 1.0
    return float(values[0]) - step / 2, step
