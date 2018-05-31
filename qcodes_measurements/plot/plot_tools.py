from math import ceil
import warnings
import re, os, time, json
import numpy as np

from qcodes.dataset.experiment_container import load_by_id
from qcodes.dataset.data_export import get_data_by_id, get_shaped_data_by_runid

from ..plot import pyplot

def save_figure(plot, id, fig_folder=None):
    """
    Save the figure on screen to the given directory, or by default, figures
    """
    if fig_folder is None:
        fig_folder = os.path.join(os.getcwd(), 'figures')

    if not os.path.exists(fig_folder):
        os.makedirs(fig_folder)

    path = os.path.join(fig_folder, '{}.png'.format(id))
    print("Saving to: {}".format(path))
    plot.export(path)
    time.sleep(1)

def append_by_id(win, id, param_name=None, force=False):
    """
    Append a 1d trace to a plot
    """
    data = get_shaped_data_by_runid(id)
    
    # If there is more than one parameter taken, we need to give the 
    # name of the parameter to append
    if len(data) != 1 and param_name is not None:
        raise ValueError("Can only append a single parameter, but there are"
                         " multiple parameters in dataset. Must give parameter"
                         " name to plot.")

    # Pull out the correct data item
    if param_name is None:
        data = data[0]
    else:
        num_data = len(data)
        data_names = []
        for i in range(num_data):
            data_name = data[i][-1]['name']
            if data_name == param_name:
                data = data[i]
            data_names.append(data_name)
        else:
            raise ValueError("Cannot find {} in data."
                             " Available names are: {}".format(param_name,
                                                               ", ".join(data_names)))
    # Assume that the plot is the first one in the window
    plot = win.items[0]
    
    # Sanity Check: Are axis labels the same?
    if not force:
        assert(plot.left_axis.label == data[1]['data'].get('label', ""))
        assert(plot.left_axis.units == data[1]['data'].get('unit', "A.U."))
        assert(plot.bot_axis.label == data[0]['data'].get('label', ""))
        assert(plot.bot_axis.units == data[0]['data'].get('unit', "A.U."))
    

    # Do the plot
    plot.plot(setpoint_x=data[0]['data'], data=data[1]['data'], pen='r')

    # Update window titles
    win.win_title += ", {}".format(id)
    plot.plot_title += " (id: {})".format(id)

def add_gate_label(plots, id):
    """
    Add gate labels to a plot
    """
    ds = load_by_id(id)
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

def add_line_plot(plot, x, y, title=None):
    # Check that we are given a plot
    if not isinstance(plot, pyplot.PlotItem):
        raise TypeError("Must be given a plot to put image into")
    if title is not None:
        plot.plot_title = title

    # Create line plot
    lplot = plot.plot(x['data'], data=y['data'], pen='r')

    # Set Axis Labels
    plot.left_axis.label = y.get('label', "")
    plot.left_axis.units = y.get('unit', "A.U.")
    plot.bot_axis.label  = x.get('label', "")
    plot.bot_axis.units  = x.get('unit', "A.U.")

    # Give back plot
    return lplot

def add_image_plot(plot, x, y, z, title=None):
    # Check that we are given a plot
    if not isinstance(plot, pyplot.PlotItem):
        raise TypeError("Must be given a plot to put image into")
    if title is not None:
        plot.plot_title = title

    # Create image plot
    implot = plot.plot(setpoint_x=x['data'], setpoint_y=y['data'], data=z['data'])

    # Set Axis Labels
    plot.left_axis.label   = y.get('label', "")
    plot.left_axis.units   = y.get('unit', "A.U.")
    plot.bot_axis.label    = x.get('label', "")
    plot.bot_axis.units    = x.get('unit', "A.U.")
    implot.histogram.label = z.get('label', "")
    implot.histogram.units = z.get('unit', "A.U.")

    # Give back the image plot
    return implot

def plot_by_id(id, save_fig=False, fig_folder=None):
    """
    Generate a plot by the given ID
    """
    win = pyplot.PlotWindow(title='ID: {}'.format(id))
    data = get_shaped_data_by_runid(id)

    # Plot each line in the data
    for data_num, plot_data in enumerate(data):
        plot = win.addPlot()

        if len(plot_data) == 2:
            plot.plot_title = "{} (id: {})".format(plot_data[0]['label'], id)
            x, y = plot_data
            if np.all(np.isnan(y['data'])):
                # No data in plot
                continue

            lplot = add_line_plot(plot, x, y)
        elif len(plot_data) == 3:
            plot.plot_title = "{} v {} (id: {})".format(plot_data[0]['label'],
                                                        plot_data[1]['label'],
                                                        id)
            x, y, z = plot_data
            if np.all(np.isnan(z['data'])):
                # No data in 2d plot
                continue
            # Reshape data to expected format
            z['data'] = np.nan_to_num(z['data']).T

            implot = add_image_plot(plot, x, y, z)
        else:
            raise ValueError("Invalid number of datas")

    # Save the figure by id if requested
    if save_fig:
        save_figure(win, id, fig_folder)
        
    return win

def plot_by_run(exp, kt, save_fig, fig_folder=None):
    """
    Plot a dataset by exp id
    """
    ds = exp.data_set(kt)
    return plot_by_id(ds.run_id, save_fig, fig_folder)

def plot_Wtext(id, save_fig=False, fig_folder=None):
    """
    Plot a dataset by id, appending gate labels automatically
    """
    win = plot_by_id(id, save_fig=False)

    # Add gate label and save if requested
    add_gate_label(win, id)
    if save_fig:
        save_figure(win, id, fig_folder)
        
    return win

def plot_Wtext_by_run(exp, kt, save_fig=False, fig_folder=None):
    """
    Plot a dataset by it's exp id, appending gate labels automatically
    """
    ds = exp.data_set(kt)
    return plot_Wtext(ds.run_id, save_fig, fig_folder)

def find_by_id(id):
    """
    Find the plotwindow that contains the given ID
    """
    win = pyplot.PlotWindow.find_by_id(id)
    return win