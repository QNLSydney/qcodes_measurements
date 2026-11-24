import re
from pathlib import Path

import numpy as np
from qcodes.dataset import DataSetProtocol, load_by_id
from qcodes.dataset.export_config import _expand_export_path
from qcodes.parameters import ParamSpecBase

from ..logging import get_logger
from ..plot import pyplot
from .axis import image_axis_tolerance, is_regular_image_axis
from .local.PlotItem import PlotItem
from .measurement import resolve_plot_spec

__all__ = ["save_figure", "append_by_id", "plot_by_id", "plot_by_run", "plot_dataset"]

TITLE_FORMAT = re.compile(
    r"(\w+) \([^)]+\) ?v.?(?:<br>)? ?(\w+) \([^)]+\) ((?:\(id: (?:[\d -]+)\) ?)+)"
)
ID_STR = re.compile(r"(id: [0-9-]+)")

logger = get_logger("plot.plot_tools")


def save_figure(plot, fname, fig_folder=None, *, db_path: str | None = None):
    """Save a PNG in fig_folder or the figures subdirectory of the database export folder.

    db_path is the dataset's SQLite file. If omitted, use the database in QCoDeS config.
    """
    if fig_folder is None:
        fig_folder = Path(_expand_export_path("{db_location}", db_path)) / "figures"

    folder = Path(fig_folder)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{fname}.png"
    print(f"Saving to: {path}")
    plot.export(str(path))


def find_plot_by_paramspec(win: pyplot.PlotWindow, x: ParamSpecBase, y: ParamSpecBase):
    """
    Find a plot matching the given paramspecs in the window.
    """
    plotitems = [item for item in win.items if isinstance(item, PlotItem)]
    for plotitem in plotitems:
        title = plotitem.plot_title
        m = TITLE_FORMAT.match(title)
        if m:
            groups = m.groups()
            if x.name == groups[0] and y.name == groups[1]:
                return plotitem
    return None


def plot_dataset(
    dataset: DataSetProtocol,
    win: pyplot.PlotWindow | None = None,
    params: list[str] | None = None,
):
    """
    Plot the given dataset.

    If a window is given, then the dataset will be appended into the window,
    attempting to match the PlotItem to the correct dataset using the title.

    If a matching plot is not found, a new plot will be inserted.

    Args:
        dataset [qcodes.DataSet]: The qcodes dataset to plot.
        win Optional[pyplot.PlotWindow]: The window to plot into, or none if
            we should create a new window.
    """
    if win is None:
        win = pyplot.PlotWindow(title=f"ID: {dataset.run_id}")
        appending = False
    elif isinstance(win, pyplot.PlotWindow):
        appending = True
    else:
        raise TypeError(f"Unexpected type for win. Expected pyplot.PlotWindow, got {type(win)}.")

    # Plot each dependant dataset in the data
    data = dataset.get_parameter_data()
    for param, vals in data.items():
        # Check if we want to plot this item
        if params is not None and param not in params:
            continue  # This is not an item we want to plot
        param = dataset.description.interdeps[param]
        dep_params = dataset.description.interdeps.dependencies.get(param, ())
        if dataset.description.shapes is not None and dep_params:
            try:
                spec = resolve_plot_spec(dataset.description, param)
            except ValueError as exc:
                logger.warning("%s; skipping its plot", exc)
                continue
            dep_params = spec.axes

        if len(dep_params) == 1:
            plot = None
            if appending:
                plot = find_plot_by_paramspec(win, dep_params[0], param)
                if plot is None:
                    raise ValueError(
                        "Failed to find a plot matching the paramters of this sweep in the window."
                    )
                plot.plot_title += f" (id: {dataset.run_id})"
                if plot.left_axis is not None and not plot.left_axis.checkParamspec(param):
                    raise ValueError(
                        f"Left axis label/units incompatible. "
                        f"Got: {param}, expecting: {plot.left_axis.label}, {plot.left_axis.units}."
                    )
                if plot.bot_axis is not None and not plot.bot_axis.checkParamspec(dep_params[0]):
                    raise ValueError(
                        f"Bottom axis label/units incompatible. "
                        f"Got: {dep_params[0]}, expecting: {plot.bot_axis.label}, {plot.bot_axis.units}."
                    )

            if plot is None:
                plot = win.addPlot()
                plot.plot_title = (
                    f"{dep_params[0].name} ({dep_params[0].label}) "
                    f"v.<br>{param.name} ({param.label}) "
                    f"(id: {dataset.run_id})"
                )

            c_data = vals[param.name]
            if np.isnan(c_data).all(axis=None):
                # No data in plot
                continue
            add_line_plot(plot, vals[dep_params[0].name], c_data, x=dep_params[0], y=param)
        elif len(dep_params) == 2:
            # Check if we are loading an old-style plot with no shape information
            if dataset.description.shapes is None:
                logger.info("No shape info. Falling back to old-style shape inference.")
                # No dataset description available. Use pandas to unwrap shape
                data = dataset.to_pandas_dataframe_dict(param.name)[param.name]
                c_data = data.unstack().droplevel(0, axis=1)
                if c_data.size != len(data):
                    logger.error(
                        "Unable to infer shape %r: size %s != %s",
                        c_data.shape,
                        c_data.size,
                        dataset.number_of_results,
                    )
                    continue
                setpoint_x = c_data.index.to_numpy()
                setpoint_y = c_data.columns.to_numpy()
                c_data = c_data.to_numpy()
            else:
                c_data = vals[param.name]
                shape = dataset.description.shapes.get(param.name)
                if shape is None or c_data.size != np.prod(shape):
                    logger.warning("Incomplete or inconsistent image shape for %s", param.name)
                    continue
                c_data = c_data.reshape(shape)
                setpoint_x = vals[dep_params[0].name][:, 0]
                setpoint_y = vals[dep_params[1].name][0, :]
                if not np.allclose(
                    vals[dep_params[0].name],
                    setpoint_x[:, None],
                    rtol=0,
                    atol=image_axis_tolerance(setpoint_x),
                ) or not np.allclose(
                    vals[dep_params[1].name],
                    setpoint_y[None, :],
                    rtol=0,
                    atol=image_axis_tolerance(setpoint_y),
                ):
                    logger.warning(
                        "Changing or invalid image grid for %s; skipping its plot", param.name
                    )
                    continue
            if not is_regular_image_axis(setpoint_x) or not is_regular_image_axis(setpoint_y):
                logger.warning("Nonuniform image coordinates for %s; skipping its plot", param.name)
                continue
            plot = None
            if appending:
                plot = find_plot_by_paramspec(win, dep_params[0], dep_params[1])
                if plot is None:
                    raise ValueError("Failed to find matching plot to append")
                plot.plot_title += f" (id: {dataset.run_id})"
                if plot.left_axis is not None and not plot.left_axis.checkParamspec(dep_params[1]):
                    raise ValueError(
                        f"Left axis label/units incompatible. "
                        f"Got: {dep_params[1]}, expecting: {plot.left_axis.label}, {plot.left_axis.units}."
                    )
                if plot.bot_axis is not None and not plot.bot_axis.checkParamspec(dep_params[0]):
                    raise ValueError(
                        f"Bottom axis label/units incompatible. "
                        f"Got: {dep_params[0]}, expecting: {plot.bot_axis.label}, {plot.bot_axis.units}."
                    )
                histogram = plot.items[0].histogram
                if not histogram.axis.checkParamspec(param):
                    raise ValueError(
                        f"Color axis label/units incompatible. "
                        f"Got: {param}, expecting: {histogram.axis.label}, {histogram.axis.units}."
                    )

            if plot is None:
                plot = win.addPlot()
                plot.plot_title = (
                    f"{dep_params[0].name} ({dep_params[0].label}) "
                    f"v.<br>{dep_params[1].name} ({dep_params[1].label}) "
                    f"(id: {dataset.run_id})"
                )

            add_image_plot(
                plot,
                setpoint_x,
                setpoint_y,
                c_data,
                x=dep_params[0],
                y=dep_params[1],
                z=param,
            )
        else:
            raise ValueError(
                "Invalid number of dimensions in dataset. Can only plot 1D or 2D traces."
            )

    return win


def add_line_plot(
    plot: pyplot.PlotItem,
    setpoint_x: np.ndarray,
    data: np.ndarray,
    x: ParamSpecBase,
    y: ParamSpecBase,
    title=None,
):
    # Check that we are given a plot
    if not isinstance(plot, pyplot.PlotItem):
        raise TypeError("Must be given a plot to put image into")
    if title is not None:
        plot.plot_title = title

    # Create line plot
    lplot = plot.plot(setpoint_x=setpoint_x.flatten(), data=data.flatten(), pen="r")

    # Set Axis Labels
    if plot.left_axis is not None:
        plot.left_axis.paramspec = y
    if plot.bot_axis is not None:
        plot.bot_axis.paramspec = x

    # Give back plot
    return lplot


def add_image_plot(
    plot: pyplot.PlotItem,
    setpoint_x: np.ndarray,
    setpoint_y: np.ndarray,
    data: np.ndarray,
    x: ParamSpecBase,
    y: ParamSpecBase,
    z: ParamSpecBase,
    title=None,
):
    # Check that we are given a plot
    if not isinstance(plot, pyplot.PlotItem):
        raise TypeError("Must be given a plot to put image into")
    if title is not None:
        plot.plot_title = title

    # Create image plot
    implot = plot.plot(setpoint_x=setpoint_x, setpoint_y=setpoint_y, data=data)

    # Set Axis Labels
    if plot.left_axis is not None:
        plot.left_axis.paramspec = y
    if plot.bot_axis is not None:
        plot.bot_axis.paramspec = x
    if implot.histogram.axis is not None:
        implot.histogram.axis.paramspec = z

    # Give back the image plot
    return implot


def plot_by_id(did, params=None, save_fig=False, fig_folder=None):
    """
    Generate a plot by the given ID

    Args:
        did[int]: Dataset ID to load
        params[Optional[List[str]]]: List of parameters to plot
        save_fig[Bool]: Save figure after plotting
        fig_folder[Optional[str]]: Location to save figure
    """
    ds = load_by_id(did)
    win = plot_dataset(ds, params=params)

    # Save the figure by id if requested
    if save_fig:
        save_figure(win, ds.run_id, fig_folder, db_path=ds.path_to_db)

    return win


def append_by_id(win, did, params=None, save_fig=False, fig_folder=None):
    """
    Append a 1d trace to a plot
    Args:
        win[PlotWindow]: Window to append plots to
        did[int]: Dataset ID to load
        params[Optional[List[str]]]: List of parameters to plot
        save_fig[Bool]: Save figure after plotting
        fig_folder[Optional[str]]: Location to save figure
    """
    ds = load_by_id(did)
    plot_dataset(ds, win, params=params)

    # Save the figure by id if requested
    if save_fig:
        save_figure(win, ds.run_id, fig_folder, db_path=ds.path_to_db)


def plot_by_run(exp, kt, params=None, save_fig=False, fig_folder=None):
    """
    Plot a dataset by exp id
    """
    ds = exp.data_set(kt)
    win = plot_dataset(ds, params=params)

    # Save the figure by id if requested
    if save_fig:
        save_figure(win, ds.run_id, fig_folder, db_path=ds.path_to_db)

    return win


def add_gate_label(plots, did):
    """
    Add gate labels to a plot
    """
    _add_gate_label(plots, load_by_id(did))


def _add_gate_label(plots, dataset: DataSetProtocol):
    snapshot = dataset.snapshot or {}
    instruments = snapshot.get("station", {}).get("instruments", {})
    channels = instruments.get("mdac", {}).get("submodules", {})

    label_txt = []
    for ch in range(1, 65):
        ch_str = f"ch{ch:02d}"
        voltage = channels.get(ch_str, {}).get("parameters", {}).get("voltage", {})
        v_value = voltage.get("value")
        if isinstance(v_value, (int, float)) and abs(v_value) > 1e-6:
            label = voltage.get("label", ch_str)
            label_txt.append(f"{label}: {v_value:+.4f}")
    if not label_txt:
        return

    if isinstance(plots, pyplot.PlotWindow):
        plots = plots.items
    elif isinstance(plots, pyplot.PlotItem):
        plots = (plots,)
    else:
        raise TypeError("Either pass a window, or a PlotItem in a window")

    for item in plots:
        if isinstance(item, pyplot.PlotItem):
            txt = item.textbox("<br>".join(label_txt))
            txt.anchor("br")
            txt.offset = (-10, -50)
        else:
            print(f"Item is a {type(item)}")


def plot_Wtext(did, save_fig=False, fig_folder=None):
    """
    Plot a dataset by id, appending gate labels automatically
    """
    ds = load_by_id(did)
    win = plot_dataset(ds)

    # Add gate label and save if requested
    _add_gate_label(win, ds)
    if save_fig:
        save_figure(win, ds.run_id, fig_folder, db_path=ds.path_to_db)

    return win


def plot_Wtext_by_run(exp, kt, save_fig=False, fig_folder=None):
    """
    Plot a dataset by it's exp id, appending gate labels automatically
    """
    ds = exp.data_set(kt)
    win = plot_dataset(ds)

    # Add gate label and save if requested
    _add_gate_label(win, ds)
    if save_fig:
        save_figure(win, ds.run_id, fig_folder, db_path=ds.path_to_db)

    return win
