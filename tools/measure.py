import qcodes as qc
import logging as log
import numpy as np

from qcodes.instrument.visa import VisaInstrument
from qcodes.dataset.measurements import Measurement

import matplotlib
import matplotlib.pyplot as plt

import pyqtgraph as pg
import pyqtgraph.multiprocess as mp

from ..plot import pyplot

def _flush_buffers(*params):
    """
    If possible, flush the VISA buffer of the instrument of the
    provided parameters. The params can be instruments as well.

    Supposed to be called inside doNd like so:
    _flush_buffers(inst_set, *inst_meas)
    """

    for param in params:
        if hasattr(param, '_instrument'):
            inst = param._instrument
            if hasattr(inst, 'visa_handle'):
                status_code = inst.visa_handle.clear()
                if status_code is not None:
                    log.warning("Cleared visa buffer on "
                                "{} with status code {}".format(inst.name,
                                                                status_code))
        elif isinstance(param, VisaInstrument):
            inst = param
            status_code = inst.visa_handle.clear()
            if status_code is not None:
                log.warning("Cleared visa buffer on "
                            "{} with status code {}".format(inst.name,
                                                            status_code))


def linear1d(param_set, start, stop, num_points, delay, *param_meas):
    """
    """
    param_meas = list(param_meas)
    _flush_buffers(*param_meas)
    
    # Set up a plotting window
    win = pyplot.PlotWindow()
    win.win_title = 'Sweeping %s' % param_set.full_name
    win.resize(1000,600)

    meas = Measurement()
    # register the first independent parameter
    meas.register_parameter(param_set)
    param_set.post_delay = delay
    
    # Calculate setpoints, and keep track of data (data_set has an inconvenient format)
    output = []
    set_points = np.linspace(start, stop, num_points)
    data = []
    #data = np.full((len(param_meas), num_points), np.nan)
    plots = []

    for p, parameter in enumerate(param_meas):
        meas.register_parameter(parameter, setpoints=(param_set,))
        output.append([parameter, None])
        
        # Create plot window
        plot = win.addPlot(title="%s (%s) v.<br>%s (%s)" % 
                           (param_set.full_name, param_set.label, 
                            parameter.full_name, parameter.label))
        
        # Figure out if we have 1d or 2d data
        if getattr(parameter, 'shape', None):
            # If we have 2d data, figure out it's form
            # If a device returns multiple waves, we'll just plot the first
            # for now (for example pna mag/phase)
            shape = parameter.shape[0]
            set_points_y = parameter.setpoints[0]
            names = parameter.setpoint_names[0]
            units = parameter.setpoint_units[0]
            # We should now have the length of a 2D trace
            assert(isinstance(shape, int))
            
            # Create data array
            data.append(np.full((num_points, shape), 0))
            
            # And label our axes
            plot.left_axis.label = names
            plot.left_axis.units = units
            plot.bot_axis.label = param_set.label
            plot.bot_axis.units = param_set.unit
        else:
            # Create data arrays
            data.append(np.full(num_points, np.nan))
            set_points_y = None
            # Label axes
            plot.bot_axis.label = param_set.label
            plot.bot_axis.units = param_set.unit
            plot.left_axis.label = parameter.label
            plot.left_axis.units = parameter.unit            

        plotdata = pyplot.PlotData.getPlot(setpoint_x=set_points,
                                           setpoint_y=set_points_y,
                                           pen=(255,0,0), 
                                           name=parameter.name)
        plot.addItem(plotdata)
        plots.append(plot)

    with meas.run() as datasaver:
        for i, set_point in enumerate(set_points):
            param_set.set(set_point)
            for p, parameter in enumerate(param_meas):
                output[p][1] = parameter.get()
                if getattr(parameter, 'shape', None) is not None:
                    data[p][i,:] = output[p][1] # Update 2D data
                else:
                    data[p][i] = output[p][1] # Update 1D data
                
                if i == 0:
                    plots[p].plot_title += " (id: %d)" % datasaver.run_id
                    if getattr(parameter, 'shape', None) is not None:
                        # Set midpoint of 2D array
                        z_range = (np.min(data[p][i,:]), np.max(data[p][i,:]))
                        data[p][i+1:] = (z_range[0] + z_range[1])/2
                        # Set colorbar label
                        plots[p].traces[0].histogram.axis.label = parameter.label
                        plots[p].traces[0].histogram.axis.units = parameter.unit
                        
                plots[p].traces[0].update(data[p])
            datasaver.add_result((param_set, set_point),
                                *output)
    dataid = datasaver.run_id

    return (dataid, win)  # can use plot_by_id(dataid)


def linear2d(param_set1, start1, stop1, num_points1, delay1,
             param_set2, start2, stop2, num_points2, delay2,
             *param_meas):
    
    # Set up a plotting window
    win = pyplot.PlotWindow()
    win.win_title = 'Sweeping %s, %s' % (param_set1.full_name, 
                                         param_set2.full_name)
    win.resize(800,800)
    
    meas = Measurement()
    # Step Axis
    meas.register_parameter(param_set1)
    param_set1.post_delay = delay1
    set_points1 = np.linspace(start1, stop1, num_points1)
    # Sweep Axis
    meas.register_parameter(param_set2)
    param_set2.post_delay = delay2
    set_points2 = np.linspace(start2, stop2, num_points2)
    
    output = []
    data = np.full((len(param_meas), num_points1, num_points2), np.nan)
    plots = []
    
    for p, parameter in enumerate(param_meas):
        meas.register_parameter(parameter, setpoints=(param_set1, param_set2))
        output.append([parameter, None])
        
        # Add Plot item
        plot = win.addPlot(title="%s (%s) v.<br>%s (%s)" % 
                               (param_set1.full_name, param_set1.label,
                                param_set2.full_name, param_set2.label))
        plotdata = pyplot.PlotData.getPlot(set_points1, set_points2)
        plot.addItem(plotdata)
        
        # Label Axes
        plot.left_axis.label = param_set2.label
        plot.left_axis.units = param_set2.unit
        plot.bot_axis.label = param_set1.label
        plot.bot_axis.units = param_set1.unit
        plotdata.histogram.axis.label = parameter.label
        plotdata.histogram.axis.units = parameter.unit
        
        plots.append(plot)

    with meas.run() as datasaver:
        for i, set_point1 in enumerate(set_points1):
            param_set2.set(start2)
            param_set1.set(set_point1)
            for j, set_point2 in enumerate(set_points2):
                param_set2.set(set_point2)
                for p, parameter in enumerate(param_meas):
                    output[p][1] = parameter.get()
                    data[p, i, j] = output[p][1]
                    
                    # Calculate z-range of data, and remove NaN's from first column
                    # This sets zero point for rest of data
                    fdata = data[p,:,:]
                    # Range of completed columns
                    if i == 0:
                        z_range = (np.nanmin(fdata[i,:j+1]), np.nanmax(fdata[i,:j+1]))
                        fdata[0,j+1:] = (z_range[0] + z_range[1])/2
                        fdata[1:,:] = (z_range[0] + z_range[1])/2
                    else:
                        z_range = (np.min((np.nanmin(fdata[:i,:]), np.nanmin(fdata[i,:j+1]))),
                                   np.max((np.nanmax(fdata[:i,:]), np.nanmax(fdata[i,:j+1]))))
                    
                    # Retrieve plot items
                    plots[p].traces[0].update(data[p,:])
                    
                    # Update title to include sweep id if first point
                    if i == 0 and j == 0:
                        plots[p].plot_title += " (id: %d)" % datasaver.run_id

                datasaver.add_result((param_set1, set_point1),
                                     (param_set2, set_point2),
                                     *output)
    dataid = datasaver.run_id
    return (dataid, win)
