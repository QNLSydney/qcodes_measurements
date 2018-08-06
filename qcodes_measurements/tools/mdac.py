import time
import numpy as np
import logging as log

from qcodes import ChannelList
from qcodes.instrument.parameter import Parameter
from qcodes.instrument_drivers.qnl.MDAC import MDACChannel

from qdev_wrappers.parameters import DelegateParameter

from .. import linear1d, linear2d
from ..plot import plot_tools

def setup(mdac, ohmics, gates, shorts, bias=None, trigger=None, microd_high=48):
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
    
    # Set gates to dac_output
    gates.dac_output('close')
    gates.smc('open')
    gates.gnd('open')
    gates.filter(2)
    gates.rate(0.05)
    
    # Set high gates (after microd) to SMC output
    mdac.channels[microd_high:].microd('open')
    mdac.channels[microd_high:].dac_output('close')
    mdac.channels[microd_high:].smc('close')
    mdac.channels[microd_high:].gnd('open')
    mdac.channels[microd_high:].filter(2)
    mdac.channels[microd_high:].rate(0.05)
    
    # Set ohmics/shorts to grounded and SMC
    ohmics.gnd('close')
    ohmics.dac_output('open')
    ohmics.smc('open')
    ohmics.filter(1)
    
    if shorts:
        shorts.microd('close')
        shorts.gnd('close')
        shorts.dac_output('open')
        shorts.smc('open')
        ohmics.filter(1)
    
    if bias is not None:
        bias.dac_output('close')
        bias.smc('close')
        bias.gnd('open')
        bias.voltage.scale = 100
        bias.ramp.scale = 100
        bias.rate.scale = 100
        bias.filter(2)
        bias.rate(0.0001)
    
    if trigger is not None:
        trigger.dac_output('close')
        trigger.smc('close')
        trigger.gnd('open')
        trigger.microd('open')
        trigger.filter(1)

def ensure_channel(mdac_channel):
    """
    Ensure that the parameter refers to the base mdac channel,
    rather than a parameter
    """
    if isinstance(mdac_channel, DelegateParameter):
        channel = mdac_channel.source._instrument
    elif isinstance(mdac_channel, Parameter) and mdac_channel.name in ('voltage', 'ramp'):
        channel = mdac_channel._instrument
    elif isinstance(mdac_channel, MDACChannel):
        channel = mdac_channel
    else:
        log.exception("Can't do an MDAC 1d sweep on something that isnt an"
                      " MDAC channel")
        raise TypeError("Can't extract an MDAC channel from: {}".format(type(mdac_channel)))
    return channel

def make_channel_list(mdac, name, channels):
    ch_list = ChannelList(mdac, name, mdac.ch01.__class__)
    for i in channels:
        ch_list.append(mdac.channels[i])
    ch_list.lock()
    return ch_list

def setup_bus(mdac, channels, bus_channel):
    # Set high gates to open
    mdac.channels[48:].gnd('close')
    mdac.channels[48:].smc('open')
    mdac.channels[48:].microd('open')
    mdac.channels[48:].bus('open')
    mdac.channels[48:].dac_output('open')
    
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
    """
    Set all channels to 0 and ground. Note that we will still need to disconnect
    the bus channel from the bus.
    """
    channels.ramp(0)
    ramp(bus_channel, 0)
    while not all(np.isclose(x, 0) for x in channels.voltage()):
        time.sleep(0.1)
    while not np.isclose(bus_channel.voltage(), 0):
        time.sleep(0.1)
    
    # Disconnect all channels
    channels.gnd('close')
    
    # Clear bus channel
    bus_channel.gnd('close')
    
def ramp(mdac_channel, to, sure=False):
    if (to > 0 or to < -1.75) and not sure:
        raise ValueError("{} is pretty big. Are you sure?".format(to))
    base = ensure_channel(mdac_channel)
    base.ramp(to)
    if isinstance(mdac_channel, MDACChannel):
        mdac_channel = mdac_channel.voltage
    while not np.isclose(to, mdac_channel(), 1e-3):
        time.sleep(0.01)

def linear1d_ramp(mdac_channel, start, stop, num_points, delay, *param_meas, 
                  rampback=False, wallcontrol=None, **kwargs):
    """
    Pull out the ramp parameter from the mdac and do a 1d sweep
    """

    # Save wallcontrol parameters
    if wallcontrol is not None:
        wallcontrol_start = wallcontrol()

    ramp(mdac_channel, start)
    trace_id = linear1d(mdac_channel, start, stop, num_points, delay, *param_meas, **kwargs, wallcontrol=wallcontrol, save=False)

    # Add gate labels
    run_id, win = trace_id
    if "append" not in kwargs or not kwargs['append']:
        plot_tools.add_gate_label(win, run_id)
    plot_tools.save_figure(win, run_id)
    
    # Rampback if requested
    if rampback:
        ramp(mdac_channel, start)
        if wallcontrol:
            ramp(wallcontrol, wallcontrol_start)
            wallcontrol()
        mdac_channel()
    
    return trace_id

def linear2d_ramp(mdac_channel1, start1, stop1, num_points1, delay1,
             mdac_channel2, start2, stop2, num_points2, delay2,
             *param_meas, rampback=False, wallcontrol=None, **kwargs):
    
    # Save wallcontrol parameters
    if wallcontrol is not None:
        wallcontrol_start = wallcontrol()

    # Pull out MDAC chanels
    ch1 = ensure_channel(mdac_channel1)
    ch2 = ensure_channel(mdac_channel2)

    # Do some validation
    if start1 == stop1:
        raise ValueError("Start and stop are the same for ch1")
    if start2 == stop2:
        raise ValueError("Start and stop are the same for ch2")
    if ch1 == ch2:
        raise ValueError("ch1 and ch2 are the same")

    # Set labels correctly
    old_label = (mdac_channel1.label, mdac_channel2.label)
    ch1.ramp.label = mdac_channel1.label
    ch2.ramp.label = mdac_channel2.label

    try:
        ramp(mdac_channel1, start1)
        ramp(mdac_channel2, start2)
        range2 = abs(start2 - stop2)
        delay1 += range2/ch2.rate()
        trace_id = linear2d(mdac_channel1, start1, stop1, num_points1, delay1,
                            ch2.ramp, start2, stop2, num_points2, delay2,
                            *param_meas, **kwargs, wallcontrol=wallcontrol, save=False)
    finally:
        # Restore labels
        ch1.ramp.label = old_label[0]
        ch2.ramp.label = old_label[1]

    # Add gate labels
    run_id, win = trace_id
    if "append" not in kwargs or not kwargs['append']:
        plot_tools.add_gate_label(win, run_id)
    plot_tools.save_figure(win, run_id)
    
    # Rampback if requested
    if rampback:
        ramp(mdac_channel1, start1)
        ramp(mdac_channel2, start2)
        if wallcontrol:
            ramp(wallcontrol, wallcontrol_start)
    
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
