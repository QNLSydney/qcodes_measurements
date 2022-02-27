import os
import re
import json
import numpy as np

from qcodes import ParamSpec, load_by_id
from qcodes.data.data_set import DataSet

from ..logging import get_logger
from ..plot import pyplot
from .local.PlotItem import BasePlotItem

__all__ = ["save_figure", "append_by_id", "plot_by_id", "plot_by_run", "plot_dataset"]

TITLE_FORMAT = re.compile(r"(\w+) \([^)]+\) ?v.?(?:<br>)? ?(\w+) \([^)]+\) ((?:\(id: (?:[\d -]+)\) ?)+)")
ID_STR = re.compile(r"(id: [0-9-]+)")

logger = get_logger("plot.plot_tools")

def save_figure(plot, fname, fig_folder=None):
    """
    Save the figure on screen to the given directory, or by default, figures
    """
    if fig_folder is None:
        fig_folder = os.path.join(os.getcwd(), 'figures')

    if not os.path.exists(fig_folder):
        os.makedirs(fig_folder)

    path = os.path.join(fig_folder, '{}.png'.format(fname))
    print("Saving to: {}".format(path))
    plot.export(path)

def find_plot_by_paramspec(win: pyplot.PlotWindow, x: ParamSpec, y: ParamSpec):
    """
    Find a plot matching the given paramspecs in the window.
    """
    plotitems = [item for item in win.items if isinstance(item, BasePlotItem)]
    for plotitem in plotitems:
        title = plotitem.plot_title
        m = TITLE_FORMAT.match(title)
        if m:
            groups = m.groups()
            if x.name == groups[0] and y.name == groups[1]:
                return plotitem
    return None

def plot_dataset(dataset: DataSet, win: pyplot.PlotWindow=None):
    """
    Plot the given dataset.

    If a window is given, then the dataset will be appended into the window,
    attempting to match the PlotItem to the correct dataset using the title.

    If a matching plot is not found, a new plot will be inserted.

    Args:
        dataset [qcodes.DataSet]: The qcodes dataset to plot.
        win Union[pyplot.PlotWindow, None]: The window to plot into, or none if
            we should create a new window.
    """
    if win is None:
        win = pyplot.PlotWindow(title='ID: {}'.format(dataset.run_id))
        appending = False
    elif isinstance(win, pyplot.PlotWindow):
        appending = True
    else:
        raise TypeError(f"Unexpected type for win. Expected pyplot.PlotWindow, got {type(win)}.")

    # Plot each dependant dataset in the data
    data = dataset.get_parameter_data()
    for param, vals in data.items():
        param = dataset.paramspecs[param]
        dep_params = [dataset.paramspecs[p] for p in param._depends_on]

        if len(dep_params) == 1:
            plot = None
            if appending:
                plot = find_plot_by_paramspec(win, dep_params[0], param)
                if plot is None:
                    raise ValueError(f"Failed to find a plot matching the paramters of this sweep in the window.")
                plot.plot_title += f" (id: {dataset.run_id})"
                if not plot.left_axis.checkParamspec(param):
                    raise ValueError(f"Left axis label/units incompatible. "
                                     f"Got: {param}, expecting: {plot.left_axis.label}, {plot.left_axis.units}.")
                if not plot.bot_axis.checkParamspec(dep_params[0]):
                    raise ValueError(f"Bottom axis label/units incompatible. "
                                     f"Got: {dep_params[0]}, expecting: {plot.bot_axis.label}, {plot.bot_axis.units}.")

            if plot is None:
                plot = win.addPlot()
                plot.plot_title = (f"{dep_params[0].name} ({dep_params[0].label}) "
                                   f"v.<br>{param.name} ({param.label}) "
                                   f"(id: {dataset.run_id})")

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
                    logger.error("Unable to unwrap dataset automatically. Unable to infer shape."
                                 f"Inferred shape: {c_data.shape}. (Size: {c_data.size} != {len(dataset)})")
                    continue
                setpoint_x = c_data.index.values
                setpoint_y = c_data.columns.values
                c_data = c_data.values
            else:
                c_data = vals[param.name]
                setpoint_x = vals[dep_params[0].name][:,0]
                setpoint_y = vals[dep_params[1].name][0,:]
            plot = None
            if appending:
                plot = find_plot_by_paramspec(win, dep_params[0], dep_params[1])
                plot.plot_title += f" (id: {dataset.run_id})"
                if not plot.left_axis.checkParamspec(dep_params[1]):
                    raise ValueError(f"Left axis label/units incompatible. "
                                     f"Got: {dep_params[1]}, expecting: {plot.left_axis.label}, {plot.left_axis.units}.")
                if not plot.bot_axis.checkParamspec(dep_params[0]):
                    raise ValueError(f"Bottom axis label/units incompatible. "
                                     f"Got: {dep_params[0]}, expecting: {plot.bot_axis.label}, {plot.bot_axis.units}.")
                histogram = plot.items[0].histogram
                if not histogram.axis.checkParamspec(param):
                    raise ValueError(f"Color axis label/units incompatible. "
                                     f"Got: {param}, expecting: {histogram.axis.label}, {histogram.axis.units}.")

            if plot is None:
                plot = win.addPlot()
                plot.plot_title = (f"{dep_params[0].name} ({dep_params[0].label}) "
                                   f"v.<br>{dep_params[1].name} ({dep_params[1].label}) "
                                   f"(id: {dataset.run_id})")

            if np.isnan(c_data).any(axis=None):
                # Nan in plot
                logger.warning("2D plot has NaN's in it. Ignoring plot")
                continue
            add_image_plot(plot, setpoint_x, setpoint_y, c_data,
                           x=dep_params[0], y=dep_params[1], z=param)
        else:
            raise ValueError("Invalid number of dimensions in dataset. Can only plot 1D or 2D traces.")

    return win

def add_line_plot(plot: pyplot.PlotItem, setpoint_x: np.ndarray, data: np.ndarray,
                  x: ParamSpec, y: ParamSpec, title=None):
    # Check that we are given a plot
    if not isinstance(plot, pyplot.PlotItem):
        raise TypeError("Must be given a plot to put image into")
    if title is not None:
        plot.plot_title = title

    # Create line plot
    lplot = plot.plot(setpoint_x=setpoint_x.flatten(), data=data.flatten(), pen='r')

    # Set Axis Labels
    plot.left_axis.paramspec = y
    plot.bot_axis.paramspec = x

    # Give back plot
    return lplot

def add_image_plot(plot: pyplot.PlotItem,
                   setpoint_x: np.ndarray, setpoint_y: np.ndarray, data: np.ndarray,
                   x: ParamSpec, y: ParamSpec, z: ParamSpec, title=None):
    # Check that we are given a plot
    if not isinstance(plot, pyplot.PlotItem):
        raise TypeError("Must be given a plot to put image into")
    if title is not None:
        plot.plot_title = title

    # Create image plot
    implot = plot.plot(setpoint_x=setpoint_x,
                       setpoint_y=setpoint_y,
                       data=data)

    # Set Axis Labels
    plot.left_axis.paramspec = y
    plot.bot_axis.paramspec = x
    implot.histogram.axis.paramspec = z

    # Give back the image plot
    return implot

def plot_by_id(did, save_fig=False, fig_folder=None):
    """
    Generate a plot by the given ID
    """
    ds = load_by_id(did)
    win = plot_dataset(ds)

    # Save the figure by id if requested
    if save_fig:
        save_figure(win, did, fig_folder)

    return win

def append_by_id(win, did):
    """
    Append a 1d trace to a plot
    """
    d = load_by_id(did)
    plot_dataset(d, win)

def plot_by_run(exp, kt, save_fig, fig_folder=None):
    """
    Plot a dataset by exp id
    """
    ds = exp.data_set(kt)
    win = plot_dataset(ds)

    # Save the figure by id if requested
    if save_fig:
        save_figure(win, ds.run_id, fig_folder)

    return win

def add_gate_label(plots, did):
    """
    Add gate labels to a plot
    """
    ds = load_by_id(did)
    json_meta = json.loads(ds.get_metadata('snapshot'))
    sub_dict = json_meta['station']['instruments']['mdac']['submodules']

    label_txt = []
    for ch in range(1, 65):
        ch_str = 'ch{num:02d}'.format(num=ch)
        label = sub_dict[ch_str]['parameters']['voltage']['label']
        v_value = sub_dict[ch_str]['parameters']['voltage']['value']
        if abs(v_value) > 1e-6:
            label_txt.append('{}: {:+.4f}'.format(label, v_value))

    if isinstance(plots, pyplot.PlotWindow):
        plots = plots.items
    elif isinstance(plots, pyplot.PlotItem):
        plots = (plots,)
    else:
        raise TypeError("Either pass a window, or a PlotItem in a window")

    for item in plots:
        if isinstance(item, pyplot.PlotItem):
            txt = item.textbox('<br>'.join(label_txt))
            txt.anchor('br')
            txt.offset = (-10, -50)
        else:
            print("Item is a {}".format(type(item)))


def plot_Wtext(did, save_fig=False, fig_folder=None):
    """
    Plot a dataset by id, appending gate labels automatically
    """
    win = plot_by_id(did, save_fig=False)

    # Add gate label and save if requested
    add_gate_label(win, did)
    if save_fig:
        save_figure(win, did, fig_folder)

    return win

def plot_Wtext_by_run(exp, kt, save_fig=False, fig_folder=None):
    """
    Plot a dataset by it's exp id, appending gate labels automatically
    """
    ds = exp.data_set(kt)
    win = plot_dataset(ds)

    # Add gate label and save if requested
    add_gate_label(win, ds.run_id)
    if save_fig:
        save_figure(win, ds.run_id, fig_folder)

    return win
