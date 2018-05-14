import time
import os
import json
import numpy as np
import logging as log

from qcodes.instrument.parameter import Parameter
from qcodes.dataset.measurements import Measurement
from .measure import _flush_buffers, linear1d, linear2d
from qcodes.dataset.plotting import plot_by_id
from qcodes.dataset.experiment_container import load_by_id
from qcodes.dataset.data_export import get_data_by_id, get_shaped_data_by_runid
from qcodes.instrument_drivers.qnl.MDAC import MDACChannel

from ..plot import pyplot

def setup(ohmics, gates, shorts):
    """
    Set all gates to correct states in MDAC. Note, assume that we are connected
    to the device through Micro-D's
    
    Ohmics, Gates, Shorts are all ChannelLists
    """
    
    # Check that all gates are in a safe state
    assert(all(gate.voltage() == 0 for gate in gates))
    assert(all(ohmic.voltage() == 0 for ohmic in ohmics))
    assert(all(short.voltage() == 0 for short in shorts))
    
    # Connect all pins to MicroD
    gates.microd('close')
    ohmics.microd('close')
    shorts.microd('close')
    
    # Set gates to dac_output
    gates.dac_output('close')
    gates.smc('open')
    gates.gnd('open')
    
    # Set ohmics/shorts to grounded and SMC
    ohmics.gnd('close')
    ohmics.dac_output('open')
    ohmics.smc('close')
    shorts.gnd('close')
    shorts.dac_output('open')
    shorts.smc('close')

def ensure_channel(mdac_channel):
    """
    Ensure that the parameter refers to the base mdac channel,
    rather than a parameter
    """
    if isinstance(mdac_channel, Parameter) and mdac_channel.name in ('voltage', 'ramp'):
        channel = mdac_channel._instrument
    elif isinstance(mdac_channel, MDACChannel):
        channel = mdac_channel
    else:
        log.exception("Can't do an MDAC 1d sweep on something that isnt an"
                      " MDAC channel")
        raise TypeError("Trying to ramp a not MDAC channel")
    return channel
    
def ramp(mdac_channel, to):
    if (to > 0 or to < -1.5):
        raise ValueError("{} is pretty big. Are you sure?".format(to))
    mdac_channel = ensure_channel(mdac_channel)
    mdac_channel.ramp(to)
    while not np.isclose(to, mdac_channel.voltage(), 1e-3):
        time.sleep(0.01)

def linear1d_ramp(mdac_channel, start, stop, num_points, delay, *param_meas, 
                  rampback=False, **kwargs):
    """
    Pull out the ramp parameter from the mdac and do a 1d sweep
    """
    mdac_channel = ensure_channel(mdac_channel)
    # Set labels correctly
    old_label = mdac_channel.ramp.label
    mdac_channel.ramp.label = mdac_channel.voltage.label

    try:
        ramp(mdac_channel, start)
        trace_id = linear1d(mdac_channel.voltage, start, stop, num_points, delay, *param_meas, **kwargs)
    finally:
        # Restore label
        mdac_channel.ramp.label = old_label

    # Add gate labels
    run_id, win = trace_id
    add_gate_label(win, run_id)
    save_figure(win, run_id)
    
    # Rampback if requested
    if rampback:
        ramp(mdac_channel, start)
    
    return trace_id

def linear2d_ramp(mdac_channel1, start1, stop1, num_points1, delay1,
             mdac_channel2, start2, stop2, num_points2, delay2,
             *param_meas, rampback=False, **kwargs):
    
    # Pull out MDAC chanels
    mdac_channel1 = ensure_channel(mdac_channel1)
    mdac_channel2 = ensure_channel(mdac_channel2)

    # Set labels correctly
    old_label = (mdac_channel1.ramp.label, mdac_channel2.ramp.label)
    mdac_channel1.ramp.label = mdac_channel1.voltage.label
    mdac_channel2.ramp.label = mdac_channel2.voltage.label

    try:
        ramp(mdac_channel1, start1)
        ramp(mdac_channel2, start2)
        range2 = abs(start2 - stop2)
        delay1 += range2/mdac_channel2.rate()
        trace_id = linear2d(mdac_channel1.voltage, start1, stop1, num_points1, delay1,
                            mdac_channel2.ramp, start2, stop2, num_points2, delay2,
                            *param_meas, **kwargs)
    finally:
        # Restore labels
        mdac_channel1.ramp.label = old_label[0]
        mdac_channel2.ramp.label = old_label[1]

    # Add gate labels
    run_id, win = trace_id
    add_gate_label(win, run_id)
    save_figure(win, run_id)
    
    # Rampback if requested
    if rampback:
        ramp(mdac_channel1, start1)
        ramp(mdac_channel2, start2)
    
    return trace_id

def add_gate_label(plots, id):
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
    elif isinstance(plots, pyplot.PlotData):
        plots = (plots,)

    for item in plots:
        if isinstance(item, pyplot.PlotItem):
            txt = item.textbox('<br>'.join(label_txt))
            txt.anchor('br')
            txt.offset = (-10, -50)
        else:
            print("Item is a {}".format(type(item)))

def save_figure(plot, id, fig_folder=None):
    if fig_folder is None:
        fig_folder = os.path.join(os.getcwd(), 'figures')

    if not os.path.exists(fig_folder):
        os.makedirs(fig_folder)

    path = os.path.join(fig_folder, '{}.png'.format(id))
    print("Saving to: {}".format(path))
    plot.export(path)
    time.sleep(1)

def plot_Wtext_by_run(exp, kt, save_fig=False, fig_folder=None):
    ds = exp.data_set(kt)
    return plot_Wtext(ds.run_id, save_fig, fig_folder)

def plot_Wtext(id, save_fig=False, fig_folder=None):
    """
    """
    win = pyplot.PlotWindow(title='ID: {}'.format(id))
    data = get_shaped_data_by_runid(id)

    for data_num, plot_data in enumerate(data):
        plot = win.addPlot()

        if len(plot_data) == 2:
            plot.plot_title = "{} (id: {})".format(plot_data[0]['label'], id)
            setpoint_x = plot_data[0]['data']
            data = plot_data[1]['data']
            if np.all(np.isnan(data)):
                continue
            plot.plot(setpoint_x=setpoint_x, data=data, pen='r')
            plot.left_axis.label=plot_data[1]['label']
            plot.left_axis.units=plot_data[1]['unit']
            plot.bot_axis.label=plot_data[0]['label']
            plot.bot_axis.units=plot_data[0]['unit']
        elif len(plot_data) == 3:
            plot.plot_title = "{} v {} (id: {})".format(plot_data[0]['label'],
                                                        plot_data[1]['label'],
                                                        id)
            setpoint_x = plot_data[0]['data']
            setpoint_y = plot_data[1]['data']
            data = plot_data[2]['data']
            if np.all(np.isnan(data)):
                continue
            data = np.nan_to_num(data).T

            implot = plot.plot(setpoint_x=setpoint_x, setpoint_y=setpoint_y, data=data)

            plot.left_axis.label=plot_data[1]['label']
            plot.left_axis.units=plot_data[1]['unit']
            plot.bot_axis.label=plot_data[0]['label']
            plot.bot_axis.units=plot_data[0]['unit']
            implot.histogram.label=plot_data[2]['label']
            implot.histogram.units=plot_data[2]['unit'] 
        else:
            raise ValueError("Invalid number of datas")

    add_gate_label(win, id)

    if save_fig:
        save_figure(win, id, fig_folder)
        
    return win

def append_by_id(win, id):
    data = get_shaped_data_by_runid(id)
    
    if len(data) != 1:
        raise ValueError("Can only append a single parameter")
    if len(data[0]) != 2:
        raise ValueError("Currently only works on scatter plots")
    setpoint_x = data[0][0]['data']
    data = data[0][1]['data']
    
    plot = win.items[0]
    plot.plot(setpoint_x=setpoint_x, data=data, pen='r')

    win.win_title += ", {}".format(id)
    plot.plot_title += " (id: {})".format(id)

def change_filter_all(mdac, filter):
    """
    filter is 1 for a 1kHz filter, 2 for a 10Hz filter
    """
    mdac.channels.filter(filter)

def all_to_smc(mdac, command='close'):
    """
    """
    print("Setting all voltages to zero.")
    mdac.channels.ramp(0)
    mdac.channels.smc(command)

def all_to_microd(mdac, command='close'):
    """
    """
    print("Setting all voltages to zero.")
    mdac.channels.ramp(0)
    mdac.channels.microd(command)

def all_to_zero(mdac):
    mdac.channels.ramp(0)

def ground_all(mdac):
    mdac.channels.ramp(0)
    mdac.channels.gnd('close')
    mdac.channels.dac_output('open')