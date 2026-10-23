"""Coordinate transforms shared by local and remote image items."""

import numpy as np
from numpy.typing import ArrayLike


def image_axis(setpoints: ArrayLike) -> tuple[float, float]:
    """Return pixel origin and spacing, placing pixel centres at the setpoints."""
    values = np.asarray(setpoints)
    if values.ndim != 1 or values.size == 0:
        raise ValueError("Image setpoints must be a non-empty one-dimensional array")
    step = float((values[-1] - values[0]) / (values.size - 1)) if values.size > 1 else 1.0
    if step == 0:
        step = 1.0
    return float(values[0]) - step / 2, step
