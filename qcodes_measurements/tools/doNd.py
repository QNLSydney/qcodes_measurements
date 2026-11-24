"""
Wrappers around qcodes.dataset doNd functions that live-plot
data during the sweep.
"""

import functools
import inspect
import itertools
import re
import warnings
from collections.abc import Sequence
from dataclasses import dataclass, field
from threading import RLock
from typing import Any

import numpy as np
import qcodes.dataset
from qcodes import config
from qcodes.dataset.data_set import DataSet
from qcodes.parameters import MultiParameter, ParameterBase, ParamSpecBase

from ..logging import get_logger
from ..plot import ImageItem, PlotDataItem, PlotItem, PlotWindow, TableWidget
from ..plot.axis import image_axis_tolerance, is_regular_image_axis
from ..plot.measurement import PlotSpec, resolve_plot_spec
from ..plot.plot_tools import save_figure


class _GlobalState:
    def __init__(self):
        self.current: LivePlotWindow | None = None


# Get access to module level variables
this = _GlobalState()
this.current = None
logger = get_logger("tools.doNd")


# Utility functions for parsing parameter names from plot titles
_param = r"(\w+)\s+\([^)]+\)"
_single_id = r"(\d+)(?:-(\d+))?(?:, (?=\d))?"
_id = r"(\(id:\s+(?:\d+(?:-\d+)?(?:, (?=\d))?)+\))"
_id_re = re.compile(_single_id, re.IGNORECASE)
_plot_title_re = re.compile(
    r"(" + _param + r"\s+v\.(?:<br>|\s)+" + _param + r")\s+" + _id,
    re.MULTILINE | re.IGNORECASE,
)
_single_param_title_re = re.compile(r"(" + _param + r")\s*" + _id, re.MULTILINE)


def _get_window(append, size=(1000, 600)):
    """
    Return a handle to a plot window to use for this plot.
    If append is False, create a new plot window, otherwise return
    a handle to the given window, or the last created window.

    Args:
        append (Union[bool, PlotWindow]): If true, return the last
        created plot window, if PlotWindow, return that window, otherwise
        a new window will be created.

        size (Tuple[int, int]): The size in px of the new plot window. If append
        is not false, this parameter has no effect.
    """
    # Set up a plotting window
    if append is None or append is False:
        win = PlotWindow()
        win.win_title = "ID: "
        win.resize(*size)
    elif isinstance(append, PlotWindow):
        # Append to the given window
        win = append
    elif isinstance(append, bool):
        windows = PlotWindow.getWindows()
        if not windows:
            return _get_window(False, size)
        win = windows[-1]
    else:
        raise ValueError(
            "Unknown argument to append. Either give a plot window"
            " or true to append to the last plot"
        )
    return win


def _explode_ids(ids_str: str) -> tuple[int, ...]:
    """
    Explode a list of ids from a plot title into a list of all
    ids.
    """
    ids: list[int] = []
    for match in _id_re.finditer(ids_str):
        start, stop = match.groups()
        if stop is None:
            ids.append(int(start))
        else:
            ids.extend(range(int(start), int(stop) + 1))
    return tuple(ids)


def _reduce_ids(ids: Sequence[int]):
    if not ids:
        return []
    strings = []
    i = 1
    r = 0
    while i < len(ids):
        if ids[i] == ids[i - 1] + 1:
            i += 1
        else:
            if i - 1 == r:
                strings.append(f"{ids[r]}")
            else:
                strings.append(f"{ids[r]}-{ids[i - 1]}")
            r = i
            i += 1
    if i - 1 == r:
        strings.append(f"{ids[r]}")
    else:
        strings.append(f"{ids[r]}-{ids[i - 1]}")
    return strings


def _parse_title(title) -> tuple[str, tuple[str, ...], tuple[int, ...]]:
    match = _plot_title_re.fullmatch(title)
    if not match:
        # Might be a single title re
        match = _single_param_title_re.fullmatch(title)
        if not match:
            raise ValueError(f"Can't parse title: {title}")
        paramstr, param_name, ids = match.groups()
        ids = _explode_ids(ids)
        return (paramstr, (param_name,), ids)
    paramstr, param1_name, param2_name, ids = match.groups()
    ids = _explode_ids(ids)
    return (paramstr, (param1_name, param2_name), ids)


def _compatible_plot_item(
    win: PlotWindow, p_bot: ParamSpecBase, p_left: ParamSpecBase | None = None
) -> PlotItem | None:
    """
    Returns a compatible plot item if found
    """
    if p_left is not None:
        axes = (p_bot.name, p_left.name)
    else:
        axes = (p_bot.name,)
    for item in win.items:
        if isinstance(item, PlotItem):
            _, params, _ = _parse_title(item.plot_title)
            if params == axes:
                return item
    return None


def _stackable_plot_item(win, bottom, left):
    """Find a line plot with matching physical axes, allowing different output names."""
    for item in win.items:
        if not isinstance(item, PlotItem):
            continue
        traces = item.traces
        if not traces or any(not isinstance(trace, PlotDataItem) for trace in traces):
            continue
        if item.bot_axis.checkParamspec(bottom) and item.left_axis.checkParamspec(left):
            return item
    return None


def _register_subscriber():
    """
    Register live plotting in the qcodes config object.
    """
    if "qcm" not in config.subscription.subscribers:
        logger.info("Registering qcm as a default subscriber")
        config.subscription.subscribers["qcm"] = {
            "factory": "qcodes_measurements.tools.doNd.subscriber",
            "factory_kwargs": {},
            "subscription_kwargs": {
                "min_wait": 10,
                # Poll the live cache even before buffered results reach SQL.
                "min_count": 0,
                "callback_kwargs": {},
            },
        }
    if "qcm" not in config.subscription.default_subscribers:
        config.subscription.default_subscribers.append("qcm")


@dataclass
class _ImageProgress:
    axes: list[np.ndarray | None] = field(default_factory=lambda: [None, None])
    validated_count: int = 0


@dataclass(frozen=False)
class LivePlotWindow:
    plot_window: PlotWindow | None
    stack: bool = False
    append: bool = False
    dataset: DataSet | None = None
    datacount: dict[str, int] = field(default_factory=dict)
    table_items: dict[str, list[str]] | None = None
    plot_items: dict[str, PlotDataItem | ImageItem] = field(default_factory=dict)
    plot_params: list[ParameterBase] | None = None
    plot_param_names: set[str] | None = None
    annotation: str | None = None
    table_column: int = 0
    plot_specs: dict[str, PlotSpec] = field(default_factory=dict)
    sweep_axes: dict[str, np.ndarray] = field(default_factory=dict)
    constant_setpoints: set[str] = field(default_factory=set)
    image_progress: dict[str, _ImageProgress] = field(default_factory=dict)
    disabled_params: set[str] = field(default_factory=set)
    lock: Any = field(default_factory=RLock, repr=False)


def do_nothing(new_data, data_len, state):
    """
    Function that does nothing
    """
    return


def _read_plot_cache(dataset, *, in_flight):
    """Read once per frame; live cache reads never query SQL.

    QCoDeS 0.61 has no public populated-count accessor. Keep its private
    per-tree counters here: SQL rows and finite-value counts are not sample
    counts, particularly for array parameters and measured NaNs.
    """
    cache = dataset.cache
    if in_flight and cache.live is not True:
        return {}, {}
    data = cache.data()
    counts = cache._write_status.copy()  # pylint: disable=protected-access
    shapes = dataset.description.shapes or {}
    for name, tree in data.items():
        values = tree[name]
        count = counts.get(name)
        if values.shape != shapes.get(name):
            # Growing/overflowed arrays are unpadded; QCoDeS may report
            # either zero or None as their write status after appending.
            count = values.size
        elif count is None:
            # A shaped but entirely unmeasured tree is already NaN-padded.
            # With unknown shapes, QCoDeS instead grows the actual arrays.
            count = 0
        counts[name] = min(count, values.size)
    return data, counts


def _update_image_axes(current, name, plotitem, spec, tree, count):
    """Infer the full grid once, then validate newly acquired coordinates.

    Never shrink an image to its acquired prefix. Known sweep grids are
    installed at setup; other axes are extrapolated from two coordinates,
    or taken directly from the first complete array/row when available.
    """
    shape = spec.shape
    assert shape is not None and len(shape) == 2
    progress = current.image_progress.get(name)
    if progress is None:
        progress = _ImageProgress()
        current.image_progress[name] = progress
    coordinates = [tree[axis.name].reshape(shape) for axis in spec.axes]
    changed = False
    for dim, length in enumerate(shape):
        if progress.axes[dim] is not None:
            continue
        available = (count - 1) // shape[1] + 1 if dim == 0 else min(count, shape[1])
        observed = coordinates[dim][:available, 0] if dim == 0 else coordinates[dim][0, :available]
        if available >= length:
            axis = observed.copy()
        elif available >= 2:
            axis = observed[0] + np.arange(length) * (observed[1] - observed[0])
        else:
            continue
        if not is_regular_image_axis(axis):
            raise ValueError(f"Nonuniform or invalid image coordinates for {spec.axes[dim].name}")
        progress.axes[dim] = axis
        if dim == 0:
            plotitem.setpoint_x = axis
            plotitem.no_xscale = False
        else:
            plotitem.setpoint_y = axis
            plotitem.no_yscale = False
        changed = True

    x_axis, y_axis = progress.axes
    if x_axis is not None and y_axis is not None:
        # Validate just the acquired suffix. In particular, catch a spectrum
        # whose intrinsic setpoints change between outer sweep points.
        start = progress.validated_count
        width = shape[1]
        x_tolerance = image_axis_tolerance(x_axis)
        y_tolerance = image_axis_tolerance(y_axis)
        for row in range(start // width, (count - 1) // width + 1):
            first = max(start - row * width, 0)
            stop = min(count - row * width, width)
            if not np.allclose(
                coordinates[0][row, first:stop], x_axis[row], rtol=0, atol=x_tolerance
            ) or not np.allclose(
                coordinates[1][row, first:stop], y_axis[first:stop], rtol=0, atol=y_tolerance
            ):
                raise ValueError(f"Nonuniform or changing image grid for {name}")
        progress.validated_count = count
    if changed:
        plotitem.rescale()


def update_plots(new_data=None, data_len=None, state=None, *, live=None):
    """Poll the live cache, including results not yet flushed to SQL."""
    current = live if live is not None else this.current
    if current is None or current.dataset is None:
        return
    # QCoDeS can invoke the completion callback while its subscriber thread
    # is still active. Serialize our display state and transport operations.
    with current.lock:
        _update_plots(current, in_flight=new_data is not None)


def _update_plots(current, *, in_flight):
    dataset = current.dataset
    data_cache, counts = _read_plot_cache(dataset, in_flight=in_flight)
    table_changed = False
    table_counts = {}
    items = itertools.chain(current.plot_items.items(), (current.table_items or {}).items())
    for name, plotitem in items:
        if name in current.disabled_params:
            continue
        count = counts.get(name, 0)
        if count == 0 or count == current.datacount.get(name):
            continue
        tree = data_cache[name]
        values = tree[name]
        if current.table_items is not None and name in current.table_items:
            val = values.flat[0]
            if isinstance(val, (float, np.floating)):
                val = np.format_float_scientific(val)
            else:
                val = str(val)
            # Each run contributes one column, even if an update is retried.
            column = current.table_column
            row = current.table_items[name]
            row.extend([""] * max(column + 1 - len(row), 0))
            row[column] = val
            table_counts[name] = count
            table_changed = True
            continue

        spec = current.plot_specs.get(name)
        if spec is None:
            spec = resolve_plot_spec(dataset.description, dataset.description.interdeps[name])
            current.plot_specs[name] = spec
        if isinstance(plotitem, PlotDataItem):
            plotitem.setData(tree[spec.axes[0].name].ravel()[:count], values.ravel()[:count])
        elif isinstance(plotitem, ImageItem):
            shape = spec.shape
            assert shape is not None
            if values.size != shape[0] * shape[1]:
                logger.warning("Image shape changed for %s; stopping its live plot", name)
                current.disabled_params.add(name)
                continue
            try:
                _update_image_axes(current, name, plotitem, spec, tree, count)
            except ValueError as exc:
                logger.warning("%s; stopping its live plot", exc)
                current.disabled_params.add(name)
                continue
            data = values.reshape(shape).copy()
            # Preserve the full image and its visible acquisition frontier.
            # Fill only future samples; measured NaNs remain measured NaNs.
            if count < data.size:
                measured = data.ravel()[:count]
                mean = measured.mean()
                if not np.isfinite(mean):
                    finite = measured[np.isfinite(measured)]
                    mean = finite.mean() if finite.size else np.nan
                data.ravel()[count:] = mean
            plotitem.update(data)
        else:
            continue
        current.datacount[name] = count

    if current.table_items and table_changed:
        n_items = max(len(row) for row in current.table_items.values())
        for row in current.table_items.values():
            row.extend([""] * (n_items - len(row)))
        table = current.plot_window.table
        if table is not None:
            col_titles = table.getHorizontalHeaders()
            if len(col_titles) < n_items:
                col_titles.append(str(dataset.run_id))
            table.setData(current.table_items)
            table.setHorizontalHeaderLabels(col_titles)
            current.datacount.update(table_counts)
        else:
            logger.error("Trying to fill in table that doesn't exist!")


def subscriber(dataset, **kwargs):
    """
    Attach a plot window to the dataset and supply an update
    method that will update the live plots.
    """
    current = this.current

    # First, check if we actually want to do anything. If not, we return
    # a blank function
    if current is None or current.plot_window is None:
        logger.info("Live plotting disabled for %s.", dataset.run_id)
        return do_nothing

    # Update the plot title
    window_run_ids = _explode_ids(f"({current.plot_window.win_title})")
    if not window_run_ids:
        window_run_ids = (dataset.run_id,)
    else:
        window_run_ids = window_run_ids + (dataset.run_id,)
    run_id_str = ", ".join(_reduce_ids(window_run_ids))
    current.plot_window.win_title = f"ID: {run_id_str}"
    current.table_column = len(window_run_ids) - 1

    # Otherwise, register parameters into the window
    current.dataset = dataset
    win = current.plot_window
    win.run_id = dataset.run_id
    run_desc = dataset.description
    params = run_desc.interdeps
    shapes = run_desc.shapes
    if current.plot_params is None:
        current.plot_param_names = set(params.names)
    else:
        current.plot_param_names = {
            name
            for parameter in current.plot_params
            for name in (
                parameter.full_names
                if isinstance(parameter, MultiParameter)
                else (parameter.register_name,)
            )
        }

    for param in params.top_level_parameters:
        name = param.name
        if name not in current.plot_param_names:
            logger.info(
                "Parameter %s not in list of plot parameters %r",
                name,
                current.plot_param_names,
            )
            continue

        try:
            spec = resolve_plot_spec(run_desc, param, current.constant_setpoints)
        except ValueError as exc:
            logger.warning("%s; skipping its live plot", exc)
            continue
        current.plot_specs[name] = spec
        if not spec.axes:
            logger.info("Adding 0D parameter %s", name)
            if win.table is None:
                table = TableWidget(sortable=False)
                scene = win.scene()
                assert scene is not None
                t_widget = scene.addWidget(table)
                t_widget.setMinimumSize(300, 0)
                win.addItem(t_widget)
                current.table_items = {}
            elif current.table_items is None:
                current.table_items = win.table.getData()
            assert win.table is not None
            if name not in current.table_items:
                if current.table_items:
                    nVals = len(next(iter(current.table_items.values())))
                else:
                    nVals = 0
                current.table_items[name] = [""] * nVals
            win.table.setHorizontalHeaderLabels(list(str(s) for s in window_run_ids))
        elif len(spec.axes) == 1:
            logger.info(
                "Adding 1D parameter %s with shape %r",
                name,
                shapes[name] if shapes is not None else None,
            )
            bot_axis = spec.axes[0]

            # If we need to stack or append, find the right plot
            plotitem = None
            if current.stack:
                plotitem = _stackable_plot_item(win, bot_axis, param)
            elif current.append:
                plotitem = _compatible_plot_item(win, bot_axis, param)
                if plotitem is None:
                    logger.warning(
                        "Append requested but appropriate plotitem not found. Making a new one."
                    )

            # Couldn't find an appropriate plotitem - make a new one
            if plotitem is None:
                plotitem = win.addPlot(
                    name=name,
                    title=(
                        f"{bot_axis.name} ({bot_axis.label}) v.<br>"
                        f"{param.name} ({param.label}) "
                        f"(id: {run_id_str})"
                    ),
                )
                plotitem.bot_axis.paramspec = bot_axis
                plotitem.left_axis.paramspec = param
            else:
                # Update ID string
                paramstr, _, _ = _parse_title(plotitem.plot_title)
                plotitem.plot_title = f"{paramstr} (id: {run_id_str})"
            # Add new trace to the plot
            plotdata = plotitem.plot(setpoint_x=[], pen=(255, 0, 0), name=param.name)
            current.plot_items[param.name] = plotdata
        elif len(spec.axes) == 2:
            logger.info(
                "Adding 2D parameter %s with shape %r",
                name,
                shapes[name] if shapes is not None else None,
            )
            bot_axis, left_axis = spec.axes

            plotitem = None
            if current.stack:
                logger.warning("Can't stack 2D param %r. Will create a new plot instead.", name)
            if current.append:
                plotitem = _compatible_plot_item(win, bot_axis, left_axis)
                if plotitem is None:
                    logger.warning(
                        "Append requested but appropriate plotitem not found. Making a new one."
                    )

            # Couldn't find an appropriate plotitem - make a new one
            if plotitem is None:
                plotitem = win.addPlot(
                    name=name,
                    title=(
                        f"{bot_axis.name} ({bot_axis.label}) v.<br>"
                        f"{left_axis.name} ({left_axis.label}) "
                        f"(id: {run_id_str})"
                    ),
                )
                plotitem.bot_axis.paramspec = bot_axis
                plotitem.left_axis.paramspec = left_axis
            else:
                # Update ID string
                paramstr, _, _ = _parse_title(plotitem.plot_title)
                plotitem.plot_title = f"{paramstr} (id: {run_id_str})"

            # Keep the full requested sweep extent from the outset. Intrinsic
            # array coordinates arrive with the first measurement; unknown
            # scalar axes retain full-length provisional scaling until then.
            assert spec.shape is not None
            axes = [current.sweep_axes.get(axis.name) for axis in spec.axes]
            plotdata = plotitem.plot(
                setpoint_x=(axes[0] if axes[0] is not None else np.linspace(0, 1, spec.shape[0])),
                setpoint_y=(axes[1] if axes[1] is not None else np.linspace(0, 1, spec.shape[1])),
                name=name,
            )
            plotdata.histogram.axis.paramspec = param
            plotdata.no_xscale = axes[0] is None
            plotdata.no_yscale = axes[1] is None
            current.image_progress[name] = _ImageProgress(axes=axes)
            current.plot_items[name] = plotdata
        else:
            logger.warning(
                "Trying to plot a dataset with more than 2 dimensions. "
                "Will not create plot for this item"
            )

    # Add annotation to the plot if requested
    if current.annotation is not None and win.items:
        win.items[0].textbox(current.annotation)

    return functools.partial(update_plots, live=current)


def _live_plot(wrapped):
    """
    Wrap do*d functions from qcodes and augment them with the capability for live
    plotting.

    Extend the docstring and signature to include the new parameters so that the help
    documentation still works.
    """
    # Make sure qcm is registered
    _register_subscriber()
    old_signature = inspect.signature(wrapped)

    WRAPPER_ASSIGNMENTS = ("__module__", "__name__", "__qualname__", "__doc__")

    @functools.wraps(wrapped, assigned=WRAPPER_ASSIGNMENTS)
    def wrapped_function(
        *args: Any,
        plot: bool = True,
        plot_params: list[ParameterBase] | None = None,
        append: bool | PlotWindow | None = False,
        annotation: str | None = None,
        save: bool = True,
        stack: bool = False,
        **kwargs: Any,
    ):
        if kwargs.get("dataset_dependencies") is not None:
            raise ValueError("dataset_dependencies is not supported by these measurement wrappers")
        # Validate and bind before opening a window. Binding also handles sweep
        # definitions supplied by keyword rather than position.
        bound = old_signature.bind(*args, **kwargs)
        if any(isinstance(param, (list, tuple)) for param in bound.arguments.get("param_meas", ())):
            raise ValueError("Grouped measurement parameters are not supported by these wrappers")
        if wrapped in (qcodes.dataset.do0d, qcodes.dataset.do1d) and "squeeze" in kwargs:
            raise TypeError("squeeze is controlled by QCoDeS do0d/do1d and cannot be supplied")
        kwargs["do_plot"] = False
        if plot and kwargs.get("in_memory_cache") is False:
            warnings.warn(
                "in_memory_cache=False disables in-flight plotting; plots update at completion",
                UserWarning,
                stacklevel=2,
            )

        sweep_axes = {}
        if plot and wrapped is qcodes.dataset.do1d:
            sweep_axes[bound.arguments["param_set"].register_name] = np.linspace(
                bound.arguments["start"], bound.arguments["stop"], bound.arguments["num_points"]
            )
        elif plot and wrapped is qcodes.dataset.do2d:
            for suffix in ("1", "2"):
                sweep_axes[bound.arguments[f"param_set{suffix}"].register_name] = np.linspace(
                    bound.arguments[f"start{suffix}"],
                    bound.arguments[f"stop{suffix}"],
                    bound.arguments[f"num_points{suffix}"],
                )

        # Get the plot window if requested
        if plot:
            win = _get_window(append)
        elif append:
            raise RuntimeError("Append requested despite plot being False.")
        else:
            win = None

        previous = this.current
        current = LivePlotWindow(
            plot_window=win,
            append=(append is not False and append is not None),
            stack=stack,
            plot_params=plot_params,
            annotation=annotation,
            sweep_axes=sweep_axes,
            constant_setpoints={
                parameter.register_name for parameter in kwargs.get("additional_setpoints", ())
            },
        )

        this.current = current
        ret_val = None
        try:
            ret_val = wrapped(*args, **kwargs)
        finally:
            # QCoDeS has flushed the final results before returning or raising.
            # Reset global state even if plotting or exporting fails.
            this.current = previous
            try:
                update_plots(live=current)
            except Exception:  # pylint: disable=broad-exception-caught
                # Plot failures must not hide a measurement exception.
                logger.exception("Failed to update final live plot")
            # On failure, use the dataset captured by the live subscriber.
            if win is not None and save:
                dataset = ret_val[0] if ret_val is not None else current.dataset
                if dataset is not None:
                    try:
                        save_figure(win, dataset.run_id, db_path=dataset.path_to_db)
                    except Exception:  # pylint: disable=broad-exception-caught
                        # Export failures must not hide a measurement exception.
                        logger.exception("Failed to save figure %s", dataset.run_id)
                else:
                    logger.warning("No dataset available to save the figure")

            # QCoDeS returns tuples of matplotlib axes; older callers may use lists.
            if win is not None and ret_val is not None:
                ret_val = (ret_val[0], [win], ret_val[2])

        # Return the sweep result
        return ret_val

    # Update the docstring to include the new parameters
    wrapped_function.__doc__ = f"""
    Wrapped instance of {wrapped.__name__} which enables live plotting during a measurement.

    Args:
        plot (Optional[bool]): Whether or not live plotting should be performed
        on this dataset.

        plot_params (Optional[list[ParameterBase]]):  Which parameters to plot

        annotation (Optional[str]): Add an annotation to the plot window

        append (Optional[bool | PlotWindow]): If this parameter is not false, the
        trace will be appended to an existing window. Either the plot window should
        be given or the last plot window will be used.

        stack (Optional[bool]): Stack the plots on a single axis, rather than creating a
        new plot for each item.

        save (Optional[bool]): Save the figure to the database export folder under figures/.

    Original Docstring
    ------------------
    {wrapped.__doc__}
    """

    new_signature = inspect.signature(wrapped_function, follow_wrapped=False)
    combined_parameters = dict(old_signature.parameters.items()) | dict(
        new_signature.parameters.items()
    )
    combined_parameters.pop("do_plot", None)
    combined_parameters.pop("args", None)
    # QCoDeS do0d/do1d forward their options through **kwargs. Keep that
    # discoverable, after the keyword-only plotting options.
    var_kwargs = combined_parameters.pop("kwargs", None)
    if "kwargs" in old_signature.parameters and var_kwargs is not None:
        combined_parameters["kwargs"] = old_signature.parameters["kwargs"]
    combined_signature = inspect.Signature(
        tuple(combined_parameters.values()),
        return_annotation=old_signature.return_annotation,
    )
    # Mypy does not recognize `__signature__` attribute of a function. see python/mypy#12472
    wrapped_function.__signature__ = combined_signature  # type: ignore

    return wrapped_function


# Create wrapped doNd functions
do0d = _live_plot(qcodes.dataset.do0d)
do1d = _live_plot(qcodes.dataset.do1d)
do2d = _live_plot(qcodes.dataset.do2d)
