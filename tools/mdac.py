import time
import os
import json
import numpy as np
import logging as log

from qcodes.instrument.parameter import Parameter
from qcodes.dataset.measurements import Measurement
from .measure import _flush_buffers, linear1d, linear2d
from qcodes.dataset.experiment_container import load_by_id
from qcodes.dataset.data_export import get_data_by_id, get_shaped_data_by_runid
from qcodes.instrument_drivers.qnl.MDAC import MDACChannel

from ..plot import pyplot, plot_tools

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

def setup_bus(mdac, channels, bus_channel):
    # Set up bus channel for output
    assert(np.isclose(bus_channel.voltage(), 0))
    bus_channel.bus('close')
    bus_channel.dac_output('close')
    bus_channel.microd('open')
    bus_channel.smc('open')
    bus_channel.gnd('open')
    bus_channel.rate(0.05)
    bus_channel.filter(2)
    
    # Connect bus to front panel
    mdac.bus('close')
    
    # Get all gates ready
    assert(all(np.isclose(x, 0) for x in channels.voltage()))
    channels.dac_output('close')
    channels.gnd('open')
    channels.rate(0.05)
    channels.filter(2)

def apply_bus(mdac, channels, bus_channel, voltage):
    assert(voltage > 0 and voltage <= 0.3) # Voltage is valid
    assert(all(x == 'open' for x in channels.gnd())) 
    assert(all(x == 'close' for x in channels.dac_output())) # Channels are ready to output
    assert(bus_channel.bus() == 'close') # bus channel is bussed
    assert(bus_channel.microd() == 'open') # bus channel is not a device channel
    assert(bus_channel.dac_output() == 'close') # bus channel is set up for dac output
    
    # Once all checks pass...
    channels.ramp(voltage)
    ramp(bus_channel, voltage, True)

def end_bus(mdac, channels, bus_channel):
    channels.ramp(0)
    ramp(bus_channel, 0)
    while not all(np.isclose(x, 0) for x in channels.voltage()):
        time.sleep(0.1)
    
    # Disconnect all channels
    channels.gnd('close')
    
    # Clear bus channel
    bus_channel.gnd('close')
    bus_channel.bus('open')
    
def ramp(mdac_channel, to, sure=False):
    if (to > 0 or to < -1.5) and not sure:
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
        trace_id = linear1d(mdac_channel.voltage, start, stop, num_points, delay, *param_meas, **kwargs, save=False)
    finally:
        # Restore label
        mdac_channel.ramp.label = old_label

    # Add gate labels
    run_id, win = trace_id
    add_gate_label(win, run_id)
    plot_tools.save_figure(win, run_id)
    
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
                            *param_meas, **kwargs, save=False)
    finally:
        # Restore labels
        mdac_channel1.ramp.label = old_label[0]
        mdac_channel2.ramp.label = old_label[1]

    # Add gate labels
    run_id, win = trace_id
    add_gate_label(win, run_id)
    plot_tools.save_figure(win, run_id)
    
    # Rampback if requested
    if rampback:
        ramp(mdac_channel1, start1)
        ramp(mdac_channel2, start2)
    
    return trace_id

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