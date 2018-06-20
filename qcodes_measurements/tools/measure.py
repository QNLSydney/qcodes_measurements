import logging as log
import numpy as np

from collections import Iterable

from qcodes.instrument.visa import VisaInstrument
from qcodes.dataset.measurements import Measurement

from .. import pyplot, plot_tools

def _flush_buffers(*params):
    """
    If possible, flush the VISA buffer of the instrument of the
    provided parameters. The params can be instruments as well.
    This ensures there is no stale data read off...

    Supposed to be called inside doNd like so:
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

def _run_functions(functions, err_name="functions"):
    # Run @start functions
    if functions is not None:
        if callable(functions):
            functions()
        elif isinstance(functions, Iterable) and all(callable(x) for x in functions):
            for func in functions:
                func()
        else:
            raise TypeError("{} must be a function or a list of functions".format(err_name))


def linear1d(param_set, start, stop, num_points, delay, *param_meas,
             append=None, save=True, 
             atstart=None, ateach=None, atend=None,
             wallcontrol=None, wallcontrol_slope=None,
             setback=False,
             write_period=120):
    """
    """

    _flush_buffers(*param_meas)
    # Set up a plotting window
    if append is None or not append:
        win = pyplot.PlotWindow()
        win.win_title = 'ID: '
        win.resize(1000,600)
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
            plot = win.addPlot(title="%s (%s) v.<br>%s (%s)" % 
                               (param_set.full_name, param_set.label, 
                                parameter.full_name, parameter.label))
        
        # Figure out if we have 1d or 2d data
        if getattr(parameter, 'shape', None):
            # If we have 2d data, we need to know its length
            shape = parameter.shape[0]
            set_points_y = parameter.setpoints[0]
            
            # Create data array
            data.append(np.ndarray((num_points, shape)))
        else:
            # Create data arrays
            data.append(np.full(num_points, np.nan))
            set_points_y = None        

        plotdata = plot.plot(setpoint_x=set_points,
                             setpoint_y=set_points_y,
                             pen=(255,0,0), 
                             name=parameter.name)
        
        # Update axes
        if set_points_y is not None:
            plot.update_axes(param_set, parameter, param_y_setpoint=True)
            plotdata.update_histogram_axis(parameter)
        else:
            plot.update_axes(param_set, parameter)
        plots.append(plotdata)

    if wallcontrol is not None:
        wallcontrol_start = wallcontrol.get()
        step = (stop-start)/num_points

    meas.write_period = write_period

    with meas.run() as datasaver:
        # Update plot titles
        win.win_title += "{} ".format(datasaver.run_id)
        for i in range(len(param_meas)):
            plots[i]._parent.plot_title += " (id: %d)" % datasaver.run_id

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
            # Save data
            datasaver.add_result((param_set, set_point),
                                *output)

    if wallcontrol is not None:
        wallcontrol.set(wallcontrol_start)

    if setback:
        param_set.set(start)

    _run_functions(atend)

    if save:
        plot_tools.save_figure(win, datasaver.run_id)
    return (datasaver.run_id, win)  # can use plot_by_id(dataid)

def linear2d(param_set1, start1, stop1, num_points1, delay1,
             param_set2, start2, stop2, num_points2, delay2,
             *param_meas, 
             atstart=None, ateach=None, atend=None,
             wallcontrol=None, wallcontrol_slope=None,
             setback=False, save=True):
    
    _flush_buffers(*param_meas)
    # Set up a plotting window
    win = pyplot.PlotWindow()
    win.win_title = 'ID: '
    win.resize(800,800)
    
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
    
    for p, parameter in enumerate(param_meas):
        meas.register_parameter(parameter, setpoints=(param_set1, param_set2))
        output.append([parameter, None])
        
        # Add Plot item
        plot = win.addPlot(title="%s (%s) v.<br>%s (%s)" % 
                           (param_set1.full_name, param_set1.label,
                            param_set2.full_name, param_set2.label))
        plotdata = plot.plot(setpoint_x=set_points1, setpoint_y=set_points2)
        plot.update_axes(param_set1, param_set2)
        plotdata.update_histogram_axis(parameter)
        plots.append(plotdata)

    if wallcontrol is not None:
        wallcontrol_start = wallcontrol.get()
        step = (stop1-start1)/num_points1

    with meas.run() as datasaver:
        # Set write period to much longer...
        datasaver.write_period = 120
        # Update plot titles
        win.win_title += "{} ".format(datasaver.run_id)
        for i in range(len(param_meas)):
            plots[i]._parent.plot_title += " (id: %d)" % datasaver.run_id
            plots[i].pause_update()
        
        for i, set_point1 in enumerate(set_points1):
            param_set2.set(start2)
            param_set1.set(set_point1)
            if wallcontrol is not None:
                wallcontrol.set(wallcontrol_start + i*step*wallcontrol_slope)
            for j, set_point2 in enumerate(set_points2):
                param_set2.set(set_point2)
                _run_functions(ateach)
                for p, parameter in enumerate(param_meas):
                    output[p][1] = parameter.get()
                    fdata = data[p]
                    fdata[i, j] = output[p][1]
                    
                    if i == 0:
                        # Calculate z-range of data, and remove NaN's from first column
                        # This sets zero point for rest of data
                        z_range = (np.nanmin(fdata[i,:j+1]), np.nanmax(fdata[i,:j+1]))
                        fdata[0,j+1:] = (z_range[0] + z_range[1])/2
                        fdata[1:,:] = (z_range[0] + z_range[1])/2
                    
                    # Update plot items, and update range every 10 points
                    if (num_points1*num_points2) < 1000 or (j%20) == 0:
                        plots[p].update(fdata, update_range=((j%100) == 0))

                # Save data
                datasaver.add_result((param_set1, set_point1),
                                     (param_set2, set_point2),
                                     *output)
        
        for i in range(len(param_meas)):
            fdata = data[i]
            plots[i].update(fdata, True)
            plots[i].resume_update()

    if wallcontrol is not None:
        wallcontrol.set(wallcontrol_start)

    if setback:
        param_set1.set(start1)
        param_set2.set(start2)

    _run_functions(atend)

    if save:
        plot_tools.save_figure(win, datasaver.run_id)

    return (datasaver.run_id, win)
    