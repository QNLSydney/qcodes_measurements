"""Resolve dataset shapes for plotting without reading instrument parameters."""

from collections.abc import Collection
from dataclasses import dataclass

from qcodes.dataset.descriptions.rundescriber import RunDescriber
from qcodes.parameters import ParamSpecBase


@dataclass(frozen=True)
class PlotSpec:
    """An acquisition's plotting view; the stored dataset shape is unchanged."""

    axes: tuple[ParamSpecBase, ...]
    shape: tuple[int, ...] | None


def resolve_plot_spec(
    description: RunDescriber,
    parameter: ParamSpecBase,
    constant_setpoints: Collection[str] = (),
) -> PlotSpec:
    axes = description.interdeps.dependencies.get(parameter, ())
    shapes = description.shapes
    shape = shapes.get(parameter.name) if shapes is not None else None
    if shape is not None:
        if any(size < 1 for size in shape):
            raise ValueError(f"Invalid plot shape {shape} for {parameter.name}")
        # QCoDeS represents a standalone scalar as (1,), rather than ().
        if not axes and shape == (1,):
            return PlotSpec((), ())
        if len(shape) != len(axes):
            raise ValueError(
                f"Shape {shape} does not match the {len(axes)} coordinate axes of {parameter.name}"
            )
        dimensions = [
            (axis, size)
            for axis, size in zip(axes, shape, strict=True)
            if axis.name not in constant_setpoints or size != 1
        ]
        axes = tuple(axis for axis, _ in dimensions)
        shape = tuple(size for _, size in dimensions)
    elif constant_setpoints:
        axes = tuple(axis for axis in axes if axis.name not in constant_setpoints)

    if not axes and shape is None and parameter.type == "array":
        raise ValueError(f"Cannot resolve a standalone array plot for {parameter.name}")

    if len(axes) > 2:
        raise ValueError(f"Cannot plot {parameter.name} with {len(axes)} dimensions")
    if len(axes) == 2 and shape is None:
        raise ValueError(f"Cannot resolve a 2D plot shape for {parameter.name}")
    return PlotSpec(axes, shape)
