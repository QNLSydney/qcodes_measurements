import os
import re
import json
import pandas as pd

from qcodes import DataSet, ParamSpec, load_by_id

from ..plot import pyplot

__all__ = ["save_figure", "append_by_id", "plot_by_id", "plot_by_run", "plot_dataset"]

TITLE_FORMAT = re.compile(r"(\w+) \(\w+\) ?v.?(?:<br>)? ?(\w+) \(\w+\) \(id: ([\d -]+)\)")

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

def append_by_id(win, did, param_name=None, force=False):
    """
    Append a 1d trace to a plot
    """
    d = load_by_id(did)

    # If there is more than one parameter taken, we need to give the
    # name of the parameter to append
    if len(d.dependent_parameters) != 1 and param_name is not None:
        raise ValueError("Can only append a single parameter, but there are"
                         " multiple parameters in dataset. Must give parameter"
                         " name to plot.")

    # Pull out the correct parameter and data items
    if param_name is None:
        param_name = d.dependent_parameters[0].name
        param = d.paramspecs[param_name]
    else:
        try:
            param = d.paramspecs[param_name]
        except KeyError:
            raise KeyError(f"Cannot find {param_name} in data. "
                        f"Available parameters are: {', '.join(d.paramspecs.keys())}")
    data = d.get_data_as_pandas_dataframe(param_name)[param_name].unstack()


    # Figure out dimensionality of data
    if len(param.depends_on_) == 1:
        left_axis = param
        bot_axis = d.param_spec[param.depends_on_[0].name]
        c_axis = None
    elif len(param.depends_on_) == 2:
        left_axis = d.param_spec[param.depends_on_[0].name]
        bot_axis = d.param_spec[param.depends_on_[1].name]
        c_axis = param
        data.columns = data.columns.dropindex()

    # Assume that the plot is the first one in the window
    plot = win.items[0]

    # Sanity Check: Are axis labels the same?
    if not force:
        assert plot.left_axis.label == left_axis.get('label', "")
        assert plot.left_axis.units == left_axis.get('unit', "A.U.")
        assert plot.bot_axis.label == bot_axis.get('label', "")
        assert plot.bot_axis.units == bot_axis.get('unit', "A.U.")
        if c_axis is not None:
            assert plot.items[0].histogram.axis.label == c_axis.get('label', '')
            assert plot.items[0].histogram.axis.unit == c_axis.get('unit', 'A.U.')

    # Do the plot
    if c_axis is None:
        plot.plot(setpoint_x=data.index, data=data.values, pen='r')
    else:
        plot.plot(setpoint_x=data.index, setpoint_y=data.columns, data=data)

    # Update window titles
    win.win_title += ", {}".format(did)
    plot.plot_title += " (id: {})".format(did)

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
    win = pyplot.PlotWindow(title='ID: {}'.format(dataset.run_id))

    # Plot each dependant dataset in the data
    data = dataset.get_data_as_pandas_dataframe(*dataset.dependent_parameters)
    for param in data:
        param = dataset.paramspecs[param.name]
        dep_params = [dataset.paramspecs[p.name] for p in param._depends_on]
        plot = win.addPlot()

        if len(dep_params) == 1:
            plot.plot_title = (f"{dep_params[0].name} ({dep_params[0].label}) "
                               f"v.<br>{param.name} ({param.label}) "
                               f"(id: {dataset.run_id})")
            c_data = data[param]
            if c_data.isna().all():
                # No data in plot
                continue
            add_line_plot(plot, c_data, x=dep_params[0], y=param)
        elif len(dep_params) == 2:
            plot.plot_title = (f"{dep_params[0].name} ({dep_params[0].label}) "
                               f"v.<br>{dep_params[1].name} ({dep_params[1].label}) "
                               f"(id: {dataset.run_id})")
            c_data = data[param].unstack().droplevel(0, axis=1)
            if c_data.isna().all():
                # No data in plot
                continue
            add_image_plot(plot, c_data, x=dep_params[0], y=dep_params[1], z=param)
        else:
            raise ValueError("Invalid number of dimensions in dataset. Can only plot 1D or 2D traces.")

    return win

def add_line_plot(plot: pyplot.PlotItem, data: pd.DataFrame,
                  x: ParamSpec, y: ParamSpec, title=None):
    # Check that we are given a plot
    if not isinstance(plot, pyplot.PlotItem):
        raise TypeError("Must be given a plot to put image into")
    if title is not None:
        plot.plot_title = title

    # Create line plot
    lplot = plot.plot(setpoint_x=data.index.values, data=data[x.name].values, pen='r')

    # Set Axis Labels
    plot.left_axis.paramspec = y
    plot.bot_axis.paramspec = x

    # Give back plot
    return lplot

def add_image_plot(plot: pyplot.PlotItem, data: pd.DataFrame,
                   x: ParamSpec, y: ParamSpec, z: ParamSpec, title=None):
    # Check that we are given a plot
    if not isinstance(plot, pyplot.PlotItem):
        raise TypeError("Must be given a plot to put image into")
    if title is not None:
        plot.plot_title = title

    # Create image plot
    implot = plot.plot(setpoint_x=data.index.values,
                       setpoint_y=data.columns.levels[1].values,
                       data=data.values)

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
