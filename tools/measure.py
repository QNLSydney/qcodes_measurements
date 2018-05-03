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
    global pyplot
    try:
        param_meas = list(param_meas)
        _flush_buffers(*param_meas)
        
        # Set up a plotting window

        win = pyplot.rpg.GraphicsLayoutWidget()
        win.show()
        win.resize(1000,600)
        win.setWindowTitle('Sweeping %s' % param_set.full_name)
        pyplot.windows.append(win)

        meas = Measurement()
        # register the first independent parameter
        meas.register_parameter(param_set)
        output = []
        param_set.post_delay = delay
        
        # Calculate setpoints, and keep track of data (data_set has an inconvenient format)
        set_points = np.linspace(start, stop, num_points)
        rset_points = pyplot.proc.transfer(set_points)
        data = np.full((len(param_meas), num_points), np.nan)
        plots = []

        for p, parameter in enumerate(param_meas):
            meas.register_parameter(parameter, setpoints=(param_set,))
            output.append([parameter, None])
            
            # Create plot window
            plot = win.addPlot(title="%s (%s) v.<br>%s (%s)" % 
                               (param_set.full_name, param_set.label, 
                                parameter.full_name, parameter.label))
            plotdata = plot.plot(x=set_points, y=data[p,:], pen=(255,0,0), name=parameter.name)
            
            # Label Axes
            botAxis = plot.getAxis('bottom')
            leftAxis = plot.getAxis('left')
            botAxis.setLabel(param_set.label, param_set.unit)
            leftAxis.setLabel(parameter.label, parameter.unit)
            
            plots.append({'plotdata':plotdata,
                          'plot': plot})

        with meas.run() as datasaver:
            for i, set_point in enumerate(set_points):
                param_set.set(set_point)
                for p, parameter in enumerate(param_meas):
                    output[p][1] = parameter.get()
                    data[p,i] = output[p][1]
                    plots[p]['plotdata'].setData(x=rset_points, y=data[p,:])
                    if i == 1:
                        plot = plots[p]['plot']
                        plot.setTitle(plot.titleLabel.text + 
                                      " (id: %d)" % datasaver.run_id)
                datasaver.add_result((param_set, set_point),
                                    *output)
        dataid = datasaver.run_id
    except:
        log.exception("Exception in linear1d.")
        raise

    return dataid  # can use plot_by_id(dataid)


def linear2d(param_set1, start1, stop1, num_points1, delay1,
             param_set2, start2, stop2, num_points2, delay2,
             *param_meas):
    
    # Set up a plotting window
    win = pyplot.rpg.GraphicsLayoutWidget()
    win.show()
    win.resize(800,800)
    win.setWindowTitle('Sweeping %s, %s' % 
                       (param_set1.full_name, param_set2.full_name))
    pyplot.windows.append(win)
    
    meas = Measurement()
    meas.register_parameter(param_set1)
    param_set1.post_delay = delay1
    set_points1 = np.linspace(start1, stop1, num_points1)
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
        implot = pyplot.rpg.ImageItem()
        plot.addItem(implot)
        
        # Set scaling
        step1 = (stop1-start1)/num_points1
        step2 = (stop2-start2)/num_points2
        implot.translate(start1, start2)
        implot.scale(step1, step2)
        
        # Add histogram
        hist = pyplot.rpg.HistogramLUTItem()
        hist.setImageItem(implot)
        hist.axis.setLabel(parameter.label, parameter.unit)
        gradient = hist.gradient
        gradient.setColorMap(pyplot.rcmap)
        win.addItem(hist)
        
        # Label Axes
        botAxis = plot.getAxis('bottom')
        leftAxis = plot.getAxis('left')
        botAxis.setLabel(param_set1.label, param_set1.unit)
        leftAxis.setLabel(param_set2.label, param_set2.unit)
        
        plots.append({
                'plot': plot,
                'img': implot,
                'hist': hist})

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
                    implot = plots[p]['img']
                    hist = plots[p]['hist']
                    
                    # Update plot
                    implot.setImage(fdata)
                    hist.setLevels(*z_range)
                    
                    # Update title if first point
                    if i == 0 and j == 0:
                        plot = plots[p]['plot']
                        plot.setTitle(plot.titleLabel.text + 
                                      " (id: %d)" % datasaver.run_id)

                datasaver.add_result((param_set1, set_point1),
                                     (param_set2, set_point2),
                                     *output)
    dataid = datasaver.run_id
    return dataid
