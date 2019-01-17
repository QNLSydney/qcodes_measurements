import logging as log
from inspect import signature

import numpy as np

from qcodes.instrument.visa import VisaInstrument
from qcodes.dataset.measurements import Measurement

from .. import pyplot, plot_tools

def _flush_buffers(*params):
    """
    If possible, flush the VISA buffer of the instrument of the
    provided parameters. The params can be instruments as well.
    This ensures there is no stale data read off...

    Supposed to be called inside linearNd like so:
    _flush_buffers(inst_set, *inst_meas)
    """

    for param in params:
        if hasattr(param, '_instrument'):
            inst = param._instrument
        elif isinstance(param, VisaInstrument):
            inst = param
        else:
            inst = None

        if inst is not None and hasattr(inst, 'visa_handle'):
            status_code = inst.visa_handle.clear()
            if status_code is not None:
                log.warning("Cleared visa buffer on "
                            "{} with status code {}".format(inst.name,
                                                            status_code))

def _run_function(function, param_vals=None):
    """
    Run a function, passing param_vals as an optional tuple of (*(param, param_val))
    Note: This function assumes we've already unwrapped lists using _run_functions.
    """
    if callable(function):
        sig = signature(function)
        if len(sig.parameters) == 1:
            if param_vals is not None:
                function(param_vals)
            else:
                raise RuntimeError("Function expects parameter values but none were provided")
        else:
            function()
    else:
        raise TypeError("_run_function expects a function")

def _run_functions(functions, param_vals=None, err_name="functions"):
    """
    Run a function or list of functions
    """
    if functions is not None:
        if callable(functions):
            _run_function(functions, param_vals)
        else:
            try:
                if all(callable(x) for x in functions):
                    for func in functions:
                        _run_function(func, param_vals)
                else:
                    raise TypeError()
            except TypeError:
                raise TypeError("{} must be a function or a list of functions".format(err_name))

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
    if append is None or not append:
        win = pyplot.PlotWindow()
        win.win_title = 'ID: '
        win.resize(*size)
    elif isinstance(append, pyplot.PlotWindow):
        # Append to the given window
        win = append
    elif isinstance(append, bool):
        # Append to the last trace if true
        win = pyplot.PlotWindow.getWindows()[-1]
    else:
        raise ValueError("Unknown argument to append. Either give a plot window"
                         " or true to append to the last plot")
    return win

def do0d(*param_meas,
         plot=True, append=None, stack=False, legend=False,
         save=True, atstart=None, ateach=None, atend=None):
    """
    Run a sweep of a single parameter, between start and stop, with a delay after settings
    the point given by delay.

    Args:
        *param_meas (Iterable[Parameter]): A list of the parameters to be measured at each of the
        set points. If any of the parameters given are ArrayParameters then a 2D sweep will be
        taken on that parameter, using the setpoints given in that ArrayParamter.
        Note: At the current time, there is an assumption that the setpoints do NOT change during
        a measurement, and that points are uniformly distributed for the purposes of plotting.
        If the points are not uniformly distributed, data is correctly saved, however the live
        plot will be distorted.

        plot (Optional[bool]): If this value is set to False, the trace will not be live plotted.

        append (Optional[Union[bool, PlotWindow]]): If this parameter is not false, the trace
        will be appended to an existing window. Either the plot window should be given, or the
        last plot window created will be used.

        stack (Optional[bool]): If this parameter is given, all parameters are stacked over
        each other on a single plot, otherwise separate plots are created for each measured parameter.

        legend (Optional[bool]): If true, a legend is added to each plot item.

        save (Optional[bool]): If this value is True, the plot window at the end will be saved
        as a png to the figures folder, with the trace id as the filename.

        atstart (Optional[Union[Callable,Iterable[Callable]]]): A function or list of functions
        to be run before the measurement is started. The functions will be run BEFORE the parameters
        are inserted into the measurement, hence if some parameters require setup before they are run,
        they can be inserted here.

        ateach (Optional[Union[Callable,Iterable[Callable]]]): A function or list of functions
        to be run after each time the sweep parameter is set. These functions will be run AFTER
        the delay, and so is suitable if an instrument requires a call to capture a trace before
        the parameter can be read.

        atend (Optional[Union[Callable,Iterable[Callable]]]): A function or list of functions
        to be run at the end of a trace. This is run AFTER the data is saved into the database,
        and after parameters are set back to their starting points (if setback is True), and
        can therefore be used to read the data that was taken and potentially do some post analysis.

    Returns:
        (id, win): ID is the trace id of the saved wave, win is a handle to the plot window that was created
        for the purposes of liveplotting.

    """

    _flush_buffers(*param_meas)
    if plot:
        win = _get_window(append)
    else:
        win = None

    # Register setpoints
    meas = Measurement()

    # Keep track of data and plots
    output = []
    data = []
    plots = []
    table = None
    table_items = {}

    # Run @start functions
    _run_functions(atstart)

    # Register each of the sweep parameters and set up a plot window for them
    for p, parameter in enumerate(param_meas):
        meas.register_parameter(parameter)
        output.append([parameter, None])

        if plot:
            # Figure out if we have 1d or 2d data
            shape = getattr(parameter, 'shape', None)
            if shape is not None and shape != tuple():
                set_points = parameter.setpoints[0]
                data.append(np.ndarray((parameter.shape[0],)))
            else:
                set_points = None

            # Create plot window
            if set_points is not None:
                if append is not None and append:
                    plotitem = win.items[0]
                elif stack and win.items:
                    plotitem = win.items[0]
                    plotitem.plot_title += f" {parameter.full_name}"
                else:
                    plotitem = win.addPlot(name=parameter.full_name,
                                           title="%s" %
                                           (parameter.full_name))
                    if legend:
                        plotitem.addLegend()

                # Add data into the plot window
                plotdata = plotitem.plot(setpoint_x=set_points,
                                         pen=(255, 0, 0),
                                         name=parameter.full_name)
                plots.append(plotdata)
                plotitem.update_axes(parameter, parameter, param_x_setpoint=True)
            else:
                if table is None:
                    table = pyplot.TableWidget(sortable=False)
                    t_widget = win.scene().addWidget(table)
                    t_widget.setMinimumSize(300, 0)
                    win.addItem(t_widget)
                table_items[parameter.full_name] = (0,)

    with meas.run() as datasaver:
        # Update plot titles to include the ID
        win.win_title += "{} ".format(datasaver.run_id)
        for plot_item in plots:
            plot_item._parent.plot_title += " (id: %d)" % datasaver.run_id

        _run_functions(ateach, param_vals=tuple())
        # Read out each parameter
        plot_number = 0
        for p, parameter in enumerate(param_meas):
            output[p][1] = parameter.get()
            shape = getattr(parameter, 'shape', None)
            if shape is not None and shape != tuple():
                data[p][:] = output[p][1] # Update 2D data
                if plot:
                    plots[plot_number].update(data[p])
                    plot_number += 1
            else:
                table_items[parameter.full_name] = (output[p][1],)

        # If stacked, make traces different
        if stack:
            plotitem.makeTracesDifferent()

        # Save data
        datasaver.add_result(*output)

        # Update table
        if table is not None:
            table.setData(table_items)

    _run_functions(atend) # Run functions at the end

    if plot and save:
        try:
            plot_tools.save_figure(win, datasaver.run_id)
        except:
            print(f"Failed to save figure {datasaver.run_id}")

    # Return the dataid, and a handle to the created window
    return (datasaver.run_id, win)  # can use plot_by_id(dataid)

def linear1d(param_set, start, stop, num_points, delay, *param_meas,
             plot=True, append=None, save=True,
             atstart=None, ateach=None, atend=None,
             wallcontrol=None, wallcontrol_slope=None,
             setback=False,
             write_period=120):
    """
    Run a sweep of a single parameter, between start and stop, with a delay after settings
    the point given by delay.

    Args:
        param_set (Parameter): The parameter to be swept

        start (Union[int, float]): Starting point of the parameter

        stop (Union[int, float]): End point of the parameter

        num_points (int): Number of points to take between start and stop (inclusive)

        delay (Union[int, float]): The delay after setting the parameter

        *param_meas (Iterable[Parameter]): A list of the parameters to be measured at each of the
        set points. If any of the parameters given are ArrayParameters then a 2D sweep will be
        taken on that parameter, using the setpoints given in that ArrayParamter.
        Note: At the current time, there is an assumption that the setpoints do NOT change during
        a measurement, and that points are uniformly distributed for the purposes of plotting.
        If the points are not uniformly distributed, data is correctly saved, however the live
        plot will be distorted.

        plot (Optional[bool]): If this value is set to False, the trace will not be live plotted.

        append (Optional[Union[bool, PlotWindow]]): If this parameter is not false, the trace
        will be appended to an existing window. Either the plot window should be given, or the
        last plot window created will be used.

        save (Optional[bool]): If this value is True, the plot window at the end will be saved
        as a png to the figures folder, with the trace id as the filename.

        atstart (Optional[Union[Callable,Iterable[Callable]]]): A function or list of functions
        to be run before the measurement is started. The functions will be run BEFORE the parameters
        are inserted into the measurement, hence if some parameters require setup before they are run,
        they can be inserted here.

        ateach (Optional[Union[Callable,Iterable[Callable]]]): A function or list of functions
        to be run after each time the sweep parameter is set. These functions will be run AFTER
        the delay, and so is suitable if an instrument requires a call to capture a trace before
        the parameter can be read.

        atend (Optional[Union[Callable,Iterable[Callable]]]): A function or list of functions
        to be run at the end of a trace. This is run AFTER the data is saved into the database,
        and after parameters are set back to their starting points (if setback is True), and
        can therefore be used to read the data that was taken and potentially do some post analysis.

        wallcontrol (Optional[Parameter]): An optional parameter that should be compensated as the measurement
        is performed. For example, this is useful for applying a compensating voltage on a sensor while other
        gates are swept.

        wallcontrol_slope (Optional[Union[int, float]]): The value of the compensation that should be
        applied to the wallcontrol parameter. Note: This must be given if wallcontrol is set.

        setback (Optional[bool]): If this is True, the setpoint parameter is returned to its starting
        value at the end of the sweep.

        write_period (Optional[int]): The time inbetween which data is written to the database.
        Irrespective of what this is set to, data will be saved when the week finishes, and will attempt
        to save in the case the sweep is interrupted.

    Returns:
        (id, win): ID is the trace id of the saved wave, win is a handle to the plot window that was created
        for the purposes of liveplotting.

    """

    _flush_buffers(*param_meas)
    if plot:
        win = _get_window(append)
    else:
        win = None

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

        if plot:
            # Create plot window
            if append is not None and append:
                plotitem = win.items[0]
            else:
                plotitem = win.addPlot(name=parameter.full_name,
                                       title="%s (%s) v.<br>%s (%s)" %
                                       (param_set.full_name, param_set.label,
                                        parameter.full_name, parameter.label))

            # Figure out if we have 1d or 2d data
            shape = getattr(parameter, 'shape', None)
            if shape is not None and shape != tuple():
                set_points_y = parameter.setpoints[0]
                data.append(np.ndarray((num_points, parameter.shape[0])))
            else:
                set_points_y = None
                data.append(np.full(num_points, np.nan))

            # Add data into the plot window
            plotdata = plotitem.plot(setpoint_x=set_points,
                                     setpoint_y=set_points_y,
                                     pen=(255, 0, 0),
                                     name=parameter.name)
            plots.append(plotdata)

            # Update axes
            if set_points_y is not None:
                plotitem.update_axes(param_set, parameter, param_y_setpoint=True)
                plotdata.update_histogram_axis(parameter)
            else:
                plotitem.update_axes(param_set, parameter)

    # Save wall control parameters if wall control is requested
    if wallcontrol is not None:
        wallcontrol_start = wallcontrol.get()
        step = (stop-start)/num_points

    # Run the sweep
    meas.write_period = write_period
    with meas.run() as datasaver:
        # Update plot titles to include the ID
        win.win_title += "{} ".format(datasaver.run_id)
        for plot_item in plots:
            plot_item._parent.plot_title += " (id: %d)" % datasaver.run_id

        # Then, run the actual sweep
        for i, set_point in enumerate(set_points):
            param_set.set(set_point)
            if wallcontrol is not None:
                wallcontrol.set(wallcontrol_start + i*step*wallcontrol_slope)
            _run_functions(ateach, param_vals=((param_set, set_point)))
            # Read out each parameter
            for p, parameter in enumerate(param_meas):
                output[p][1] = parameter.get()
                shape = getattr(parameter, 'shape', None)
                if shape is not None and shape != tuple():
                    data[p][i, :] = output[p][1] # Update 2D data
                    # For a 2D trace, figure out the value for data not yet set if this is the
                    # first column
                    if i == 0:
                        data[p][1:] = (np.min(output[p][1]) +
                                       np.max(output[p][1]))/2
                else:
                    data[p][i] = output[p][1] # Update 1D data

                if plot:
                    # Update live plots
                    plots[p].update(data[p])
            # Save data
            datasaver.add_result((param_set, set_point),
                                 *output)

    # Set back to start at the end of the measurement
    if setback:
        # Reset wall control
        if wallcontrol is not None:
            wallcontrol.set(wallcontrol_start)
        param_set.set(start)

    _run_functions(atend) # Run functions at the end

    if plot and save:
        try:
            plot_tools.save_figure(win, datasaver.run_id)
        except:
            print(f"Failed to save figure {datasaver.run_id}")

    # Return the dataid, and a handle to the created window
    return (datasaver.run_id, win)  # can use plot_by_id(dataid)

def linear2d(param_set1, start1, stop1, num_points1, delay1,
             param_set2, start2, stop2, num_points2, delay2,
             *param_meas,
             plot=True, append=False, save=True,
             atstart=None, ateachcol=None, ateach=None, atend=None,
             wallcontrol=None, wallcontrol_slope=None,
             setback=False, write_period=120):
    """
    Run a sweep of a single parameter, between start and stop, with a delay after settings
    the point given by delay.

    Args:
        param_set1 (Parameter): The parameter to be stepped on the x-axis

        start1 (Union[int, float]): Starting point of the x-axis parameter

        stop1 (Union[int, float]): End point of the x-axis parameter

        num_points1 (int): Number of points to take between start and stop (inclusive) on the x-axis

        delay1 (Union[int, float]): The delay after setting the parameter on the x-axis

        param_set2 (Parameter): The parameter to be swept on the y-axis

        start2 (Union[int, float]): Starting point of the y-axis parameter

        stop2 (Union[int, float]): End point of the y-axis parameter

        num_points2 (int): Number of points to take between start and stop (inclusive) on the y-axis

        delay2 (Union[int, float]): The delay after setting the parameter on the y-axis

        *param_meas (Iterable[Parameter]): A list of the parameters to be measured at each of the
        set points. These must be single valued for live plotting to work

        plot (Optional[bool]): If this value is set to False, the trace will not be live plotted.

        append (Optional[Union[bool, PlotWindow]]): If this parameter is not false, the trace
        will be appended to an existing window. Either the plot window should be given, or the
        last plot window created will be used.

        save (Optional[bool]): If this value is True, the plot window at the end will be saved
        as a png to the figures folder, with the trace id as the filename.

        atstart (Optional[Union[Callable,Iterable[Callable]]]): A function or list of functions
        to be run before the measurement is started. The functions will be run BEFORE the parameters
        are inserted into the measurement, hence if some parameters require setup before they are run,
        they can be inserted here.

        ateachcol (Optional[Union[Callable,Iterable[Callable]]]): A function or list of functions
        to be run after each column of data is complete, useful for example for doing more advanced
        wall control. These functions are run AFTER the delay.

        ateach (Optional[Union[Callable,Iterable[Callable]]]): A function or list of functions
        to be run after each time the sweep parameter is set. These functions will be run AFTER
        the delay, and so is suitable if an instrument requires a call to capture a trace before
        the parameter can be read.

        atend (Optional[Union[Callable,Iterable[Callable]]]): A function or list of functions
        to be run at the end of a trace. This is run AFTER the data is saved into the database,
        and after parameters are set back to their starting points (if setback is True), and
        can therefore be used to read the data that was taken and potentially do some post analysis.

        wallcontrol (Optional[Parameter]): An optional parameter that should be compensated as the measurement
        is performed. For example, this is useful for applying a compensating voltage on a sensor while other
        gates are swept.

        wallcontrol_slope (Optional[Union[int, float]]): The value of the compensation that should be
        applied to the wallcontrol parameter. Note: This must be given if wallcontrol is set.

        setback (Optional[bool]): If this is True, the setpoint parameter is returned to its starting
        value at the end of the sweep.

        write_period (Optional[int]): The time inbetween which data is written to the database.
        Irrespective of what this is set to, data will be saved when the week finishes, and will attempt
        to save in the case the sweep is interrupted.

    Returns:
        (id, win): ID is the trace id of the saved wave, win is a handle to the plot window that was created
        for the purposes of liveplotting.

    """
    _flush_buffers(*param_meas)
    if plot:
        win = _get_window(append, size=(800, 800))
    else:
        win = None

    # Register setpoints
    meas = Measurement()
    # Step Axis
    meas.register_parameter(param_set1)
    param_set1.post_delay = delay1
    set_points1 = np.linspace(start1, stop1, num_points1)
    # Sweep Axis
    meas.register_parameter(param_set2)
    param_set2.post_delay = delay2
    set_points2 = np.linspace(start2, stop2, num_points2)

    # Keep track of data and plots
    output = []
    data = np.ndarray((len(param_meas), num_points1, num_points2))
    plots = []

    # Run @start functions
    _run_functions(atstart)

    # Register each parameter
    for p, parameter in enumerate(param_meas):
        meas.register_parameter(parameter, setpoints=(param_set1, param_set2))
        output.append([parameter, None])

        # Add Plot item
        if plot:
            if append is not None and append:
                plotitem = win.items[0]
                plotdata = plotitem.plot(setpoint_x=set_points1, setpoint_y=set_points2)
            else:
                plotitem = win.addPlot(name=parameter.full_name,
                                       title="%s (%s) v.<br>%s (%s)" %
                                       (param_set1.full_name, param_set1.label,
                                        param_set2.full_name, param_set2.label))
                plotdata = plotitem.plot(setpoint_x=set_points1, setpoint_y=set_points2)
                plotitem.update_axes(param_set1, param_set2)
                plotdata.update_histogram_axis(parameter)
            plots.append(plotdata)


    # Set wall control parameters if necessary
    if wallcontrol is not None:
        wallcontrol_start = wallcontrol.get()
        step = (stop1-start1)/num_points1

    meas.write_period = write_period
    with meas.run() as datasaver:
        # Update plot titles
        win.win_title += "{} ".format(datasaver.run_id)
        for plot in plots:
            plot._parent.plot_title += " (id: %d)" % datasaver.run_id

        for i, set_point1 in enumerate(set_points1):
            param_set2.set(start2)
            param_set1.set(set_point1)
            if wallcontrol is not None:
                wallcontrol.set(wallcontrol_start + i*step*wallcontrol_slope)
            _run_functions(ateachcol, param_vals=((param_set1, set_point1)))
            for j, set_point2 in enumerate(set_points2):
                param_set2.set(set_point2)
                _run_functions(ateach, param_vals=((param_set1, set_point1),
                                                   (param_set2, set_point2)))
                for p, parameter in enumerate(param_meas):
                    output[p][1] = parameter.get()
                    fdata = data[p]
                    fdata[i, j] = output[p][1]

                    if plot:
                        if i == 0:
                            # Calculate z-range of data, and remove NaN's from first column
                            # This sets zero point for rest of data
                            z_range = (np.nanmin(fdata[i, :j+1]), np.nanmax(fdata[i, :j+1]))
                            fdata[0, j+1:] = (z_range[0] + z_range[1])/2
                            fdata[1:, :] = (z_range[0] + z_range[1])/2

                        # Update plot items, and update range every 10 points
                        if (num_points1*num_points2) < 1000 or (j%20) == 0:
                            plots[p].update(fdata, True)

                # Save data
                datasaver.add_result((param_set1, set_point1),
                                     (param_set2, set_point2),
                                     *output)

        # At the end, do one last update to make sure that all data is displayed.
        if plot:
            for i in range(len(param_meas)):
                fdata = data[i]
                plots[i].update(fdata, True)

    # Set paramters back to start
    if setback:
        if wallcontrol is not None:
            wallcontrol.set(wallcontrol_start)
        param_set1.set(start1)
        param_set2.set(start2)

    _run_functions(atend)

    if plot and save:
        try:
            plot_tools.save_figure(win, datasaver.run_id)
        except:
            print(f"Failed to save figure {datasaver.run_id}")


    return (datasaver.run_id, win)
