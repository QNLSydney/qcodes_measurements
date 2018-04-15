import qcodes as qc
import logging as log
import numpy as np

from qcodes.instrument.visa import VisaInstrument
from qcodes.dataset.measurements import Measurement


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
    try:
        param_meas = list(param_meas)
        _flush_buffers(*param_meas)

        meas = Measurement()
        # register the first independent parameter
        meas.register_parameter(param_set)
        output = []
        param_set.post_delay = delay

        for parameter in param_meas:
            meas.register_parameter(parameter, setpoints=(param_set,))
            output.append([parameter, None])

        with meas.run() as datasaver:
            for set_point in np.linspace(start, stop, num_points):
                param_set.set(set_point)
                for i, parameter in enumerate(param_meas):
                    output[i][1] = parameter.get()
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

    meas = Measurement()
    meas.register_parameter(param_set1)
    param_set1.post_delay = delay1
    meas.register_parameter(param_set2)
    param_set1.post_delay = delay2
    output = []
    for parameter in param_meas:
        meas.register_parameter(parameter, setpoints=(param_set1, param_set2))
        output.append([parameter, None])

    with meas.run() as datasaver:
        for set_point1 in np.linspace(start1, stop1, num_points1):
            param_set1.set(set_point1)
            for set_point2 in np.linspace(start2, stop2, num_points2):
                param_set2.set(set_point2)
                for i, parameter in enumerate(param_meas):
                    output[i][1] = parameter.get()
                datasaver.add_result((param_set1, set_point1),
                                     (param_set2, set_point2),
                                     *output)
    dataid = datasaver.run_id
    return dataid
