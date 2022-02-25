import logging as log
from inspect import signature
from collections import namedtuple

from wrapt import decorator
from tqdm import tqdm
import numpy as np

from qcodes.utils.deprecate import deprecate
from qcodes.instrument.visa import VisaInstrument
from qcodes.dataset.measurements import Measurement

from ..plot import pyplot, plot_tools

Setpoint = namedtuple("Setpoint", ("param", "index", "value"))
LivePlotDataItem = namedtuple("LivePlotDataItem", ("plot", "plotdata", "data"))

def _flush_buffers(*params):
    """
    If possible, flush the VISA buffer of the instrument of the
    provided parameters. The params can be instruments as well.
    This ensures there is no stale data read off...

    Supposed to be called inside linearNd like so:
    _flush_buffers(*params)
    """

    for param in params:
        if hasattr(param, 'root_instrument'):
            inst = param.root_instrument
        elif isinstance(param, VisaInstrument):
            inst = param
        else:
            inst = None

        if inst is not None and hasattr(inst, 'visa_handle'):
            status_code = inst.visa_handle.clear()
            if status_code is not None:
                log.warning("Cleared visa buffer on "
                            "%s with status code %d", inst.name, status_code)

def _run_function(function, param_vals=None):
    """
    Run a function, passing param_vals as an optional tuple of (*(Setpoint(param, index, param_val)))
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
    if append is None or append is False:
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

@decorator
def _plot_sweep(sweep_func, _, args, kwargs):
    """
    Create plot window and save figure at end irrespective of whether the sweep is cancelled.

    Args:
        save (Optional[bool]): Whether or not a figure should be saved. If this is true,
        the result of the sweep is saved into the "figures" folder.

        plot (Optional[bool]): If this value is set to False, the trace will not be live plotted.

        append (Optional[Union[bool, PlotWindow]]): If this parameter is not false, the trace
        will be appended to an existing window. Either the plot window should be given, or the
        last plot window created will be used.

        *args, **kwargs: Parameters passed to the sweep function
    """
    plot = kwargs.get("plot", True)
    if "plot" in kwargs:
        del kwargs["plot"]
    append = kwargs.get("append", None)
    save = kwargs.get("save", True)

    # Create a plot window
    if plot:
        win = _get_window(append)
    else:
        win = None

    # Try run the sweep, passing the plot window to the sweep function for update
    run_id = None
    try:
        if isinstance(append, pyplot.PlotWindow) or append is True:
            append = True
        kwargs["win"] = win
        kwargs["append"] = append
        run_id = sweep_func(*args, **kwargs)
    finally:
        # Save the plot if a save was requested
        if win is not None and save:
            # Check if the sweep completed successfully. If not, try and pull the run_id from the window.
            # If it is still none, no data was taken, let's just bail out without saving
            if run_id is None:
                run_id = getattr(win, "run_id", None)
            if run_id is not None:
                try:
                    plot_tools.save_figure(win, run_id)
                except Exception:
                    print(f"Failed to save figure {run_id}")
    return run_id, win

@deprecate(alternative="qcm.tools.doNd.do0d")
@_plot_sweep
def do0d(*param_meas,
         win=None, append=False, stack=False, legend=False,
         atstart=None, ateach=None, atend=None):
    """
    Run a sweep of a single parameter, between start and stop, with a delay after settings
    the point given by delay.

    Args:
        *param_meas (Iterable[Parameter]): A list of the parameters to be measured at each of the
        set points. If any of the parameters given are ArrayParameters then a 1D sweep will be
        taken on that parameter, using the setpoints given in that ArrayParamter.

        win (Optional[PlotWindow]): The plot window to add plots to. If this value is None, the sweep
        will not be live plotted.

        append (bool): If this parameter is true, the trace will be appended to an existing window.

        stack (Optional[bool]): If this parameter is given, all parameters are stacked over
        each other on a single plot, otherwise separate plots are created for each measured parameter.

        legend (Optional[bool]): If true, a legend is added to each plot item.

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

    # Register setpoints
    meas = Measurement()

    # Keep track of data and plots
    output = []
    plots = []
    table = None
    table_items = {}

    # Run @start functions
    _run_functions(atstart)

    # Register each of the sweep parameters and set up a plot window for them
    for p, parameter in enumerate(param_meas):
        meas.register_parameter(parameter)
        output.append([parameter, None])

        if win is not None:
            # Figure out if we have 1d or 2d data
            shape = getattr(parameter, 'shape', None)
            if shape is not None and shape != tuple():
                set_points = parameter.setpoints[0]
                data = np.ndarray((parameter.shape[0],))
            else:
                set_points = None

            # Create plot window
            if set_points is not None:
                if append:
                    plotitem = win.items[0]
                elif stack and win.items:
                    plotitem = win.items[0]
                    plotitem.plot_title += f" {parameter.full_name}"
                else:
                    plotitem = win.addPlot(name=parameter.full_name,
                                           title="%s (%s)" %
                                           (parameter.full_name, parameter.label))
                    if legend:
                        plotitem.addLegend()

                # Add data into the plot window
                plotdata = plotitem.plot(setpoint_x=set_points,
                                         pen=(255, 0, 0),
                                         name=parameter.full_name)
                plotitem.update_axes(parameter, parameter, param_x_setpoint=True)
                plots.append(LivePlotDataItem(plotitem, plotdata, data))
            else:
                if table is None:
                    table = pyplot.TableWidget(sortable=False)
                    t_widget = win.scene().addWidget(table)
                    t_widget.setMinimumSize(300, 0)
                    win.addItem(t_widget)
                table_items[parameter.full_name] = (0,)

    try:
        with meas.run() as datasaver:
            if win is not None:
                # Update plot titles to include the ID
                win.run_id = datasaver.run_id
                win.win_title += "{} ".format(datasaver.run_id)
                for plotitem in plots:
                    plotitem.plot.plot_title += " (id: %d)" % datasaver.run_id

            _run_functions(ateach, param_vals=tuple())
            # Read out each parameter
            plot_number = 0
            for p, parameter in enumerate(param_meas):
                output[p][1] = parameter.get()
                shape = getattr(parameter, 'shape', None)
                if win is not None:
                    if shape is not None and shape != tuple():
                        plots[plot_number].data[:] = output[p][1] # Update 2D data
                        plots[plot_number].plotdata.update(plots[plot_number].data)
                        plot_number += 1
                    else:
                        table_items[parameter.full_name] = (output[p][1],)

            # If stacked, make traces different
            if stack:
                plots[0].plot.makeTracesDifferent()

            # Save data
            datasaver.add_result(*output)

            # Update table
            if table is not None:
                table.setData(table_items)
    finally:
        _run_functions(atend) # Run functions at the end

    # Return the dataid
    return datasaver.run_id  # can use plot_by_id(dataid)

@deprecate(alternative="qcm.tools.doNd.do1d")
@_plot_sweep
def linear1d(param_set, start, stop, num_points, delay, *param_meas,
             win=None, append=False, plot_params=None,
             atstart=None, ateach=None, atend=None,
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

        win (Optional[PlotWindow]): The plot window to add plots to. If this value is None, the sweep
        will not be live plotted.

        append (bool): If this parameter is true, the trace will be appended to an existing window.

        plot_params (Optional[Iterable[Parameter]]): A list of measured parameters to live plot. If no
        value is given, then all parameters will be live-plotted

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

    # Register setpoints
    meas = Measurement()
    meas.register_parameter(param_set)
    param_set.post_delay = delay
    set_points = np.linspace(start, stop, num_points)

    # Keep track of data and plots
    if plot_params is None:
        plot_params = param_meas
    output = []
    plots = {}

    # Run @start functions
    _run_functions(atstart)

    # Register each of the sweep parameters and set up a plot window for them
    for p, parameter in enumerate(param_meas):
        meas.register_parameter(parameter, setpoints=(param_set,))
        output.append([parameter, None])

        if win is not None and parameter in plot_params:
            # Create plot window
            if append:
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
                data = np.ndarray((num_points, parameter.shape[0]))
            else:
                set_points_y = None
                data = np.full(num_points, np.nan)

            # Add data into the plot window
            plotdata = plotitem.plot(setpoint_x=set_points,
                                     setpoint_y=set_points_y,
                                     pen=(255, 0, 0),
                                     name=parameter.name)
            plots[parameter] = LivePlotDataItem(plotitem, plotdata, data)

            # Update axes
            if set_points_y is not None:
                plotitem.update_axes(param_set, parameter, param_y_setpoint=True)
                plotdata.update_histogram_axis(parameter)
            else:
                plotitem.update_axes(param_set, parameter)

    # Run the sweep
    meas.write_period = write_period
    pbar = None
    try:
        with meas.run() as datasaver:
            if win is not None:
                # Update plot titles to include the ID
                win.run_id = datasaver.run_id
                win.win_title += "{} ".format(datasaver.run_id)
                for plotitem in plots.values():
                    plotitem.plot.plot_title += " (id: %d)" % datasaver.run_id

            # Then, run the actual sweep
            pbar = tqdm(total=num_points, unit="pt", position=0, leave=True)
            for i, set_point in enumerate(set_points):
                param_set.set(set_point)
                _run_functions(ateach, param_vals=(Setpoint(param_set, i, set_point),))
                # Read out each parameter
                for p, parameter in enumerate(param_meas):
                    output[p][1] = parameter.get()
                    shape = getattr(parameter, 'shape', None)
                    if win is not None and parameter in plots:
                        if shape is not None and shape != tuple():
                            plots[parameter].data[i, :] = output[p][1] # Update 2D data
                            # For a 2D trace, figure out the value for data not yet set if this is the
                            # first column
                            if i == 0:
                                plots[parameter].data[1:] = (np.min(output[p][1]) +
                                                             np.max(output[p][1]))/2
                            # Update live plots
                            plots[parameter].plotdata.update(plots[parameter].data)
                        else:
                            plots[parameter].data[i] = output[p][1] # Update 1D data
                            plots[parameter].plotdata.setData(set_points[:i], plots[parameter].data[:i])


                # Save data
                datasaver.add_result((param_set, set_point),
                                     *output)
                pbar.update(1)
    finally:
        # Set back to start at the end of the measurement
        if setback:
            param_set.set(start)

        # Close the progress bar
        if pbar is not None:
            pbar.close()

        _run_functions(atend) # Run functions at the end

    # Return the dataid
    return datasaver.run_id  # can use plot_by_id(dataid)

@deprecate(alternative="qcm.tools.doNd.do2d")
@_plot_sweep
def linear2d(param_set1, start1, stop1, num_points1, delay1,
             param_set2, start2, stop2, num_points2, delay2,
             *param_meas,
             win=None, append=False, plot_params=None,
             atstart=None, ateachcol=None, ateach=None, atend=None,
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

        win (Optional[PlotWindow]): The plot window to add plots to. If this value is None, the sweep
        will not be live plotted.

        append (bool): If this parameter is true, the trace will be appended to an existing window.

        plot_params (Optional[Iterable[Parameter]]): A list of measured parameters to live plot. If no
        value is given, then all parameters will be live-plotted

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
    if plot_params is None:
        plot_params = param_meas
    output = []
    plots = {}

    # Run @start functions
    _run_functions(atstart)

    # Register each parameter
    for p, parameter in enumerate(param_meas):
        meas.register_parameter(parameter, setpoints=(param_set1, param_set2))
        output.append([parameter, None])

        # Add Plot item
        if win is not None and parameter in plot_params:
            if append:
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
            plots[parameter] = LivePlotDataItem(plotitem, plotdata, np.ndarray((num_points1, num_points2)))

    meas.write_period = write_period
    pbar = None
    try:
        with meas.run() as datasaver:
            # Update plot titles
            win.run_id = datasaver.run_id
            win.win_title += "{} ".format(datasaver.run_id)
            for plotitem in plots.values():
                    plotitem.plot.plot_title += " (id: %d)" % datasaver.run_id

            pbar = tqdm(total=num_points1, unit="col", position=0, leave=True)
            for i, set_point1 in enumerate(set_points1):
                param_set2.set(start2)
                param_set1.set(set_point1)
                _run_functions(ateachcol, param_vals=(Setpoint(param_set1, i, set_point1),))
                for j, set_point2 in enumerate(set_points2):
                    param_set2.set(set_point2)
                    _run_functions(ateach, param_vals=(Setpoint(param_set1, i, set_point1),
                                                       Setpoint(param_set2, j, set_point2)))
                    for p, parameter in enumerate(param_meas):
                        output[p][1] = parameter.get()

                        if win is not None and parameter in plots:
                            fdata = plots[parameter].data
                            fdata[i, j] = output[p][1]
                            if i == 0:
                                # Calculate z-range of data, and remove NaN's from first column
                                # This sets zero point for rest of data
                                z_range = (np.nanmin(fdata[i, :j+1]), np.nanmax(fdata[i, :j+1]))
                                fdata[0, j+1:] = (z_range[0] + z_range[1])/2
                                fdata[1:, :] = (z_range[0] + z_range[1])/2

                            # Update plot items, and update range every 10 points
                            if (num_points1*num_points2) < 1000 or (j%20) == 0:
                                plots[parameter].plotdata.update(fdata, True)

                    # Save data
                    datasaver.add_result((param_set1, set_point1),
                                         (param_set2, set_point2),
                                         *output)
                pbar.update(1)

            # At the end, do one last update to make sure that all data is displayed.
            if win is not None:
                for pd in plots.values():
                    pd.plotdata.update(pd.data, True)
    finally:
        # Set paramters back to start
        if setback:
            param_set1.set(start1)
            param_set2.set(start2)

        # Close progress bar
        if pbar is not None:
            pbar.close()

        _run_functions(atend)

    return datasaver.run_id
