import time
import os
import numpy as np
import logging as log
from qcodes.dataset.measurements import Measurement
from . measure import _flush_buffers
from qcodes.dataset.plotting import plot_by_id

import matplotlib
import matplotlib.pyplot as plt

def ramp_mdac(channel_voltage, voltage, rate=0.05, max_jump=0.002):
    curr_v = channel_voltage()
    diff_v = abs(curr_v - voltage)
    
    if diff_v > max_jump:
        n_steps, remainder = divmod(diff_v, max_jump)
        v_steps = np.linspace(curr_v, voltage, num=int(n_steps))
        sleep_time = (diff_v / rate)/n_steps

        for vv in v_steps:
            time.sleep(sleep_time) # in seconds
#             print(vv)
            channel_voltage(vv)

        if remainder != 0 and abs(channel_voltage() - voltage)< max_jump:
            channel_voltage(voltage)
        else:
            channel_voltage(voltage)
            # print("MDAC channel is not at desired voltage.")
            
    else:
        pass

def linear1d_ramp(mdac_channel_voltage, start, stop, num_points, delay, *param_meas):
    """
    """
    if abs(mdac_channel_voltage() - start) > 1e-6:
        ramp_mdac(mdac_channel_voltage, start)

    try:
        param_meas = list(param_meas)
        _flush_buffers(*param_meas)

        meas = Measurement()
        # register the first independent parameter
        meas.register_parameter(mdac_channel_voltage)
        output = []
        mdac_channel_voltage.post_delay = delay

        for parameter in param_meas:
            meas.register_parameter(parameter, setpoints=(mdac_channel_voltage,))
            output.append([parameter, None])

        with meas.run() as datasaver:
            for set_point in np.linspace(start, stop, num_points):
                ramp_mdac(mdac_channel_voltage, set_point)
                for i, parameter in enumerate(param_meas):
                    output[i][1] = parameter.get()
                datasaver.add_result((mdac_channel_voltage, set_point),
                                    *output)
        dataid = datasaver.run_id
    except:
        log.exception("Exception in linear1d_ramp")
        raise

    return dataid  # can use plot_by_id(dataid)

def linear2d_ramp(mdac_channel_v1, start1, stop1, num_points1, delay1,
             mdac_channel_v2, start2, stop2, num_points2, delay2,
             *param_meas):

    if abs(mdac_channel_v1() - start1) > 1e-6:
        ramp_mdac(mdac_channel_v1, start1)

    if abs(mdac_channel_v2() - start2) > 1e-6:
        ramp_mdac(mdac_channel_v2, start2)
    try:
        param_meas = list(param_meas)
        _flush_buffers(*param_meas)

        meas = Measurement()
        meas.register_parameter(mdac_channel_v1)
        mdac_channel_v1.post_delay = delay1
        meas.register_parameter(mdac_channel_v2)
        mdac_channel_v2.post_delay = delay2
        output = []
        for parameter in param_meas:
            meas.register_parameter(parameter, setpoints=(mdac_channel_v1, mdac_channel_v2))
            output.append([parameter, None])

        with meas.run() as datasaver:
            for set_point1 in np.linspace(start1, stop1, num_points1):
                # param_set1.set(set_point1)
                ramp_mdac(mdac_channel_v1, set_point1)
                for set_point2 in np.linspace(start2, stop2, num_points2):
                    # param_set2.set(set_point2)
                    ramp_mdac(mdac_channel_v2, set_point2)
                    for i, parameter in enumerate(param_meas):
                        output[i][1] = parameter.get()
                    datasaver.add_result((mdac_channel_v1, set_point1),
                                        (mdac_channel_v2, set_point2),
                                        *output)
        dataid = datasaver.run_id
    except:
        log.exception("Exception in linear2d_ramp")

    return dataid

def plot_Wtext(dataid, mdac, fontsize=10, textcolor='black', textweight='normal'):
    """
    """
    fig = plt.figure()
    ax = fig.add_subplot(111)
    ax, cax = plot_by_id(dataid, ax)

    fig.suptitle('ID: {}'.format(dataid))
    y_coord = 0.4
    for channel in mdac.channels:
        if abs(channel.voltage()) > 1e-5:
            fig.text(0.4,y_coord,'{}: {}'.format(channel.voltage.label, channel.voltage() ),
                     fontsize=fontsize, color=textcolor, weight=textweight)
            y_coord -= 0.05
    fig.savefig(os.path.join(os.getcwd(),'figures', '{}.png'.format(dataid)))

def change_filter_all(mdac):
    """
    """
    print("Setting all voltages to zero.")
    for channel in mdac.channels:
        ramp_mdac(channel.voltage, 0)
        channel.filter(2)

def all_to_smc(mdac, command='close'):
    """
    """
    print("Setting all voltages to zero.")
    for channel in mdac.channels:
        ramp_mdac(channel.voltage, 0)
        channel.smc(command)

def all_to_microd(mdac, command='close'):
    """
    """
    print("Setting all voltages to zero.")
    for channel in mdac.channels:
        channel.voltage(0)
        channel.microd(command)

def all_to_zero(mdac):
    for channel in mdac.channels:
        ramp_mdac(channel.voltage, 0)