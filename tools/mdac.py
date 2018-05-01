import time
import os
import json
import numpy as np
import logging as log
from qcodes.dataset.measurements import Measurement
from . measure import _flush_buffers
from qcodes.dataset.plotting import plot_by_id
from qcodes.dataset.experiment_container import load_by_id

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

def linear1d_ramp(mdac_channel, start, stop, num_points, delay, *param_meas):
    """
    """
    if abs(mdac_channel.voltage() - start) > 1e-6:
        mdac_channel.ramp(start)

    try:
        elapsed_time = time.time()
        param_meas = list(param_meas)
        _flush_buffers(*param_meas)

        meas = Measurement()
        # register the first independent parameter
        meas.register_parameter(mdac_channel.voltage)
        output = []

        voltages = []

        mdac_channel.voltage.post_delay = delay

        for parameter in param_meas:
            meas.register_parameter(parameter, setpoints=(mdac_channel.voltage,))
            output.append([parameter, None])

        with meas.run() as datasaver:
            for set_point in np.linspace(start, stop, num_points):
                mdac_channel.ramp(set_point)
                for i, parameter in enumerate(param_meas):
                    output[i][1] = parameter.get()
                datasaver.add_result((mdac_channel.voltage, set_point),
                                    *output)
        dataid = datasaver.run_id
        elapsed_time = time.time() - elapsed_time
        print("Elapsed time in s: ", elapsed_time)
    except:
        log.exception("Exception in linear1d_ramp")
        raise

    return dataid # can use plot_by_id(dataid)

def linear2d_ramp(mdac_channel1, start1, stop1, num_points1, delay1,
             mdac_channel2, start2, stop2, num_points2, delay2,
             *param_meas):

    if abs(mdac_channel1.voltage() - start1) > 1e-6:
        mdac_channel1.ramp(start1)

    if abs(mdac_channel2.voltage() - start2) > 1e-6:
        mdac_channel2.ramp(start2)
    try:
        elapsed_time = time.time()
        param_meas = list(param_meas)
        _flush_buffers(*param_meas)

        meas = Measurement()
        meas.register_parameter(mdac_channel1.voltage)
        mdac_channel1.voltage.post_delay = delay1
        meas.register_parameter(mdac_channel2.voltage)
        mdac_channel2.voltage.post_delay = delay2
        output = []
        for parameter in param_meas:
            meas.register_parameter(parameter, setpoints=(mdac_channel1.voltage, mdac_channel2.voltage))
            output.append([parameter, None])

        with meas.run() as datasaver:
            for set_point1 in np.linspace(start1, stop1, num_points1):
                mdac_channel1.ramp(set_point1)
                for set_point2 in np.linspace(start2, stop2, num_points2):
                    mdac_channel2.ramp(set_point1)
                    for i, parameter in enumerate(param_meas):
                        output[i][1] = parameter.get()
                    datasaver.add_result((mdac_channel1.voltage, set_point1),
                                        (mdac_channel2.voltage, set_point2),
                                        *output)
        dataid = datasaver.run_id
        elapsed_time = time.time() - elapsed_time
        print("Elapsed time in s: ", elapsed_time)
    except:
        log.exception("Exception in linear2d_ramp")

    return dataid

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