import numpy as np

from ..tools import do1d
from ..siPrefix import si_format
from ..plot import PlotWindow

def leakage_sweep(voltage_out, current_in, *extra_params, to=1e-3, nump=101, wt=0.1, ithaco_impedance=20, print_res=True, **mkwargs):
    data, _, _= do1d(voltage_out, 0, to, nump, wt, current_in, *extra_params, **mkwargs)
    voltage_out(0)
    id = data.run_id
    plot = PlotWindow.find_by_id(id)
    if plot is not None:
        plot.close()

    volt_param = voltage_out.full_name
    curr_param = current_in.full_name

    darr = data.to_xarray_dataset()
    fit, res, _, _, _ = np.polyfit(darr[volt_param].values, darr[curr_param].values, 1, full=True)
    try:
        fit[0] = 1/abs(fit[0])
        print(fit)
        if np.isinf(fit[0]) or np.isnan(fit[0]):
            raise ValueError()
        if print_res:
            resistance = fit[0] - ithaco_impedance
            print(f"Resistance was: {si_format(resistance, 3)}Ω")
            print(f"Residuals were: {res}")
    except (ValueError, ZeroDivisionError):
        if print_res:
            print("Channel open")
        return [1e9, 0], 0

    return fit, res