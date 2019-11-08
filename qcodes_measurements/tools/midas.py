import logging as log
import numpy as np

from collections import Iterable

from qcodes.instrument.visa import VisaInstrument
from qcodes.dataset.measurements import Measurement

from ..plot import pyplot, plot_tools
from qcodes_measurements.tools.measure import _run_functions

run_id = 0

def midasLinear1d(param_set, start, stop, num_points, delay, *param_meas,
             append=None, save=True,
             atstart=None, ateach=None, atend=None,
             wallcontrol=None, wallcontrol_slope=None,
             setback=False):
    """
    """
    global run_id
    # Set up a plotting window
    if append is None or not append:
        win = pyplot.PlotWindow()
        win.win_title = 'ID: '
        win.resize(1500,1000) #was 1000,600 originally
    elif isinstance(append, pyplot.PlotWindow):
        # Append to the given window
        win = append
    elif isinstance(append, bool):
        # Append to the last trace if true
        win = pyplot.PlotWindow.getWindows()[-1]
    else:
        raise ValueError("Unknown argument to append. Either give a plot window"
                         " or true to append to the last plot")

    # Register setpoints
    meas = Measurement()
    meas.register_parameter(param_set)
    param_set.post_delay = delay
    set_points = np.linspace(start, stop, num_points)

    # Keep track of data and plots
    output = []
    data = []
    plots = []

    # Run @start functions
    _run_functions(atstart)

    # Register each of the sweep parameters and set up a plot window for them
    for p, parameter in enumerate(param_meas):
        print(parameter, param_set)
        meas.register_parameter(parameter, setpoints=(param_set,))
        output.append([parameter, None])

        # Create plot window
        if append is not None and append:
            plot = win.items[0]
        else:
            col = p % 4
            row = p // 4
            plot = win.addPlot(title="%s (%s) v.<br>%s (%s)" %
                               (param_set.full_name, param_set.label,
                                parameter.full_name, parameter.label),
                                row=row, col=col)

        # Figure out if we have 1d or 2d data
        if getattr(parameter, 'shape', None):
            # If we have 2d data, we need to know its length
            shape = parameter.shape[0]
            set_points_y = parameter.setpoints[0]

            # Create data array
            data.append(np.ndarray((num_points, shape)))
            cm = pyplot.ColorMap.get_color_map("plasma")
            plotdata = pyplot.ImageItem(setpoint_x=set_points + p * 50e6 + 7e9,
                                            setpoint_y=set_points_y,
                                            name=parameter.name,
                                            colormap=cm)
            plot.addItem(plotdata)
        else:
            # Create data arrays
            data.append(np.full(num_points, np.nan))
            set_points_y = None
            plotdata = pyplot.PlotDataItem(setpoint_x=set_points, pen=(255,0,0),
                                           name=parameter.name)
            plot.addItem(plotdata)

        # Update axes
        if set_points_y is not None:
            plot.update_axes(param_set, parameter, param_y_setpoint=True)
            #plotdata.update_histogram_axis(parameter)
        else:
            plot.update_axes(param_set, parameter)
        plots.append(plotdata)

    if wallcontrol is not None:
        wallcontrol_start = wallcontrol.get()
        step = (stop-start)/num_points
    # Then, run the actual sweep
    for i, set_point in enumerate(set_points):
        if wallcontrol is not None:
            wallcontrol.set(wallcontrol_start + i*step*wallcontrol_slope)
        param_set.set(set_point)
        _run_functions(ateach)
        for p, parameter in enumerate(param_meas):
            output[p][1] = parameter.get()
            if getattr(parameter, 'shape', None) is not None:
                data[p][i,:] = output[p][1] # Update 2D data
                if i == 0:
                    data[p][1:] = (np.min(output[p][1]) +
                                    np.max(output[p][1]))/2
            else:
                data[p][i] = output[p][1] # Update 1D data

            # Update live plots
            plots[p].update(data[p])

    if wallcontrol is not None:
        wallcontrol.set(wallcontrol_start)

    if setback:
        param_set.set(start)

    _run_functions(atend)
    np.save("midas_run_save_{}".format(run_id), data)
    run_id += 1
    if save:
        plot_tools.save_figure(win, 1)
    return (win)  # can use plot_by_id(dataid)
