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
from qcodes.instrument_drivers.qnl.MDAC import MDACChannel

import matplotlib
import matplotlib.pyplot as plt

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
    
def ramp(mdac_channel, to):
    if isinstance(mdac_channel, Parameter) and mdac_channel.name == "voltage":
        voltage = mdac_channel
        ramp = mdac_channel._instrument.ramp
    elif isinstance(mdac_channel, Parameter) and mdac_channel.name == "ramp":
        voltage = mdac_channel._instrument.voltage
        ramp = mdac_channel
    elif isinstance(mdac_channel, MDACChannel):
        voltage = mdac_channel.voltage
        ramp = mdac_channel.ramp
    else:
        log.exception("Can't ramp on something that isnt an MDAC channel")
        raise TypeError("Trying to ramp a not MDAC channel")
    
    ramp(to)
    while not np.isclose(to, voltage(), 1e-3):
        time.sleep(0.01)

def linear1d_ramp(mdac_channel, start, stop, num_points, delay, *param_meas, 
                  rampback=False):
    """
    Pull out the ramp parameter from the mdac and do a 1d sweep
    """
    if isinstance(mdac_channel, Parameter) and mdac_channel.name == "voltage":
        voltage = mdac_channel
        ramp = mdac_channel._instrument.ramp
    elif isinstance(mdac_channel, Parameter) and mdac_channel.name == "ramp":
        voltage = mdac_channel._instrument.voltage
        ramp = mdac_channel
    elif isinstance(mdac_channel, MDACChannel):
        voltage = mdac_channel.voltage
        ramp = mdac_channel.ramp
    else:
        log.exception("Can't do an MDAC 1d sweep on something that isnt an"
                      " MDAC channel")
        raise TypeError("Trying to ramp a not MDAC channel")
    
    ramp(start)
    while not np.isclose(start, voltage(), 1e-3):
        time.sleep(0.01)
    trace_id = linear1d(voltage, start, stop, num_points, delay, *param_meas)
    
    if rampback:
        ramp(start)
        while not np.isclose(start, voltage(), 1e-3):
            time.sleep(0.01)
    
    return trace_id

def linear2d_ramp(mdac_channel1, start1, stop1, num_points1, delay1,
             mdac_channel2, start2, stop2, num_points2, delay2,
             *param_meas):
    
    if isinstance(mdac_channel1, Parameter) and mdac_channel1.name == "voltage":
        voltage1 = mdac_channel1
        ramp1 = mdac_channel1._instrument.ramp
    elif isinstance(mdac_channel1, Parameter) and mdac_channel1.name == "ramp":
        voltage1 = mdac_channel1._instrument.voltage
        ramp1 = mdac_channel1
    elif isinstance(mdac_channel1, MDACChannel):
        voltage1 = mdac_channel1.voltage
        ramp1 = mdac_channel1.ramp
    else:
        log.exception("Can't do an MDAC 2d sweep on something that isnt an"
                      " MDAC channel")
        raise TypeError("Trying to ramp a not MDAC channel")
        
    if isinstance(mdac_channel2, Parameter) and mdac_channel2.name == "voltage":
        voltage2 = mdac_channel2
        ramp2 = mdac_channel2._instrument.ramp
    elif isinstance(mdac_channel2, Parameter) and mdac_channel2.name == "ramp":
        voltage2 = mdac_channel2._instrument.voltage
        ramp2 = mdac_channel2
    elif isinstance(mdac_channel2, MDACChannel):
        voltage2 = mdac_channel2.voltage
        ramp2 = mdac_channel2.ramp
    else:
        log.exception("Can't do an MDAC 2d sweep on something that isnt an"
                      " MDAC channel")
        raise TypeError("Trying to ramp a not MDAC channel")

    ramp1(start1)
    ramp2(start2)
    while not np.isclose(start1, voltage1(), 1e-3):
        time.sleep(0.01)
    while not np.isclose(start2, voltage2(), 1e-3):
        time.sleep(0.01)
    return linear2d(voltage1, start1, stop1, num_points1, delay1,
                    voltage2, start2, stop2, num_points2, delay2,
                    *param_meas)


def plot_Wtext(dataid, mdac, fontsize=10, textcolor='black', textweight='normal', fig_folder=None):
    """
    """
    fig = plt.figure()
    ax = fig.add_subplot(111)
    ax, cax = plot_by_id(dataid, ax)
    fig.suptitle('ID: {}'.format(dataid))
    
    ds = load_by_id(dataid)
    json_meta = json.loads(ds.get_metadata('snapshot'))
    sub_dict = json_meta['station']['instruments']['mdac']['submodules']
    
    y_coord = 0.85
    
    for ch in range(1, len(mdac.channels)+1):
        ch_str = 'ch{num:02d}'.format(num=ch)
        v_value = sub_dict[ch_str]['parameters']['voltage']['value']
        if abs(v_value) > 1e-6:
            label = sub_dict[ch_str]['parameters']['voltage']['label']
            fig.text(0.77, y_coord,'{}: {:+.4f}'.format(label, v_value ),
                     fontsize=fontsize, color=textcolor, weight=textweight,
                     transform=plt.gcf().transFigure)
            y_coord -= 0.05
    fig.subplots_adjust(right=0.75)
    
    if fig_folder is None:
        fig_folder = os.path.join(os.getcwd(), 'figures')

    if not os.path.exists(fig_folder):
        os.makedirs(fig_folder)

    fig.savefig(os.path.join(fig_folder, '{}.png'.format(dataid)))

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