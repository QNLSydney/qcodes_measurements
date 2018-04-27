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
    """
    print("Setting all voltages to zero.")
    for channel in mdac.channels:
        channel.ramp(0)
        channel.filter(2)

def all_to_smc(mdac, command='close'):
    """
    """
    print("Setting all voltages to zero.")
    for channel in mdac.channels:
        channel.ramp(0)
        channel.smc(command)

def all_to_microd(mdac, command='close'):
    """
    """
    print("Setting all voltages to zero.")
    for channel in mdac.channels:
        channel.ramp(0)
        channel.microd(command)

def all_to_zero(mdac):
    for channel in mdac.channels:
        channel.ramp(0)

def ground_all(mdac):
    for ch in mdac.channels:
        channel.ramp(0)
        ch.dac_output('open')
        ch.gnd('close')