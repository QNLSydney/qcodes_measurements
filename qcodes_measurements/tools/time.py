import time
import numpy as np

from qcodes.dataset.measurements import Measurement

from .measure import Setpoint, _flush_buffers, _run_functions, _plot_sweep
from ..logging import get_logger
logger = get_logger("tools.time")

def _interruptible_sleep(sleep_time):
    while sleep_time > 1:
        time.sleep(1)
        sleep_time -= 1
    time.sleep(sleep_time)
    return

@_plot_sweep
def sweep_time(*param_meas, delay=10, until=None,
               win=None, append=False, plot_params=None, annotation=None,
               atstart=(), ateach=(), atend=()):
    """
    Run a time sweep, with a delay between each point. This sweep will run for `until` seconds,
    or indefinitely if until is None

    Args:
        *param_meas (Iterable[Parameter]): A list of the parameters to be measured at each of the
        set points. For now, these MUST be simple parameters. Arrays cannot be measured.

        win (Optional[PlotWindow]): The plot window to add plots to. If this value is None, the sweep
        will not be live plotted.

        append (bool): If this parameter is true, the trace will be appended to an existing window.

        plot_params (Optional[Iterable[Parameter]]): A list of parameters to plot. If not passed or None,
        all measured parameters will be automatically plotted.

        atstart (Optional[Union[Callable,Iterable[Callable]]]): A function or list of functions
        to be run before the measurement is started. The functions will be run BEFORE the parameters
        are inserted into the measurement, hence if some parameters require setup before they are run,
        they can be inserted here.

        ateach (Optional[Union[Callable,Iterable[Callable]]]): A function or list of functions
        to be run after each time the sweep parameter is set. These functions will be run AFTER
        the delay, and so is suitable if an instrument requires a call to capture a trace before
        the parameter can be read.

        atend (Optional[Union[Callable,Iterable[Callable]]]): A function or list of functions
        to be run at the end of a trace. This is run AFTER the data is saved into the database,
        and after parameters are set back to their starting points (if setback is True), and
        can therefore be used to read the data that was taken and potentially do some post analysis.

    Returns:
        (iw, win): ID is the trace id of the saved wave, win is a handle to the plot window that was created
        for the purposes of liveplotting.
    """
    _flush_buffers(*param_meas)

    # Register setpoints
    m = Measurement()
    m.register_custom_parameter("time", label="Time", unit="s")

    _run_functions(atstart)

    # Keep track of data and plots
    plt_data = {}
    time_data = np.full((1,), np.nan)
    array_size = 1
    curr_point = 0

    # If plot_params is not given, plot all measured parameters
    if plot_params is None:
        plot_params = param_meas

    # Set up parameters
    for param in param_meas:
        m.register_parameter(param, setpoints=("time", ))

        # Create plot window
        if win is not None and param in plot_params:
            plot = win.addPlot(name=param.full_name,
                            title=f"{param.full_name} ({param.label})")
            plot.left_axis.label = param.label
            plot.left_axis.unit = param.unit
            plot.bot_axis.label = "Time"
            plot.bot_axis.unit = "s"
            plotdata = plot.plot(setpoint_x=time_data, name=param.name, pen=(255,0,0))
            plt_data[param] = (plot, plotdata, np.full((1,), np.nan))

    if win is not None and annotation is not None:
        win.items[0].textbox(annotation)

    try:
        with m.run() as datasaver:
            start_time = time.monotonic()
            win.win_title += f"{datasaver.run_id}"
            for pd in plt_data.values():
                pd[0].plot_title += f" (id: {datasaver.run_id})"
            while True:
                # Update each parameter
                data = [("time", time.monotonic()-start_time)]
                time_data[curr_point] = data[-1][1]

                _run_functions(ateach, param_vals=(Setpoint("time", curr_point, data[-1][1])))

                if until is not None and time_data[curr_point] > until:
                    break

                for param in param_meas:
                    val = param()
                    if val is None:
                        val = np.nan
                    data.append((param, val))
                    if param in plot_params:
                        plt_data[param][2][curr_point] = data[-1][1]
                        plt_data[param][1].xData = time_data
                        plt_data[param][1].update(plt_data[param][2])

                curr_point += 1

                # Resize plot arrays if necessary
                if array_size == curr_point:
                    array_size *= 2
                    logger.debug("New plot array size: %d", array_size)
                    time_data.resize(array_size)
                    time_data[array_size//2:] = np.nan
                    for pld in plt_data.values():
                        pld[2].resize(array_size)
                        pld[2][array_size//2:] = np.nan

                datasaver.add_result(*data)

                # Wait until the next point time. Try to keep track of how long it
                # took for equipment to respond
                next_time = start_time + delay*curr_point
                while time.monotonic() < next_time:
                    sleep_time = max(0, min(0.01, time.monotonic() - next_time))
                    _interruptible_sleep(sleep_time)
    except KeyboardInterrupt:
        print(f"Trace cancelled with Ctrl-C")
        print(f"Ending plot at time {time.monotonic() - start_time}.")
    finally:
        _run_functions(atend)

    return datasaver.run_id