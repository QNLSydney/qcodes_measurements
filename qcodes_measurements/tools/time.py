import time
import numpy as np
from inspect import signature
from collections import namedtuple

from qcodes.validators import Arrays
from qcodes.parameters import ParameterWithSetpoints
from qcodes.dataset.measurements import Measurement

from .doNd import _live_plot
from ..logging import get_logger

logger = get_logger("tools.time")

Setpoint = namedtuple("Setpoint", ("param", "index", "value"))


def _interruptible_sleep(sleep_time):
    while sleep_time > 1:
        time.sleep(1)
        sleep_time -= 1
    time.sleep(sleep_time)
    return


def _run_function(function, param_vals=None):
    """
    Run a function, passing param_vals as an optional tuple of (*(Setpoint(param, index, param_val)))
    Note: This function assumes we've already unwrapped lists using _run_functions.
    """
    if callable(function):
        sig = signature(function)
        if len(sig.parameters) == 1:
            if param_vals is not None:
                function(param_vals)
            else:
                raise RuntimeError(
                    "Function expects parameter values but none were provided"
                )
        else:
            function()
    else:
        raise TypeError("_run_function expects a function")


def _run_functions(functions, param_vals=None, err_name="functions"):
    """
    Run a function or list of functions
    """
    if functions is not None:
        if callable(functions):
            _run_function(functions, param_vals)
        else:
            try:
                if all(callable(x) for x in functions):
                    for func in functions:
                        _run_function(func, param_vals)
                else:
                    raise TypeError()
            except TypeError:
                raise TypeError(
                    "{} must be a function or a list of functions".format(err_name)
                )


@_live_plot
def sweep_time(
    *param_meas,
    delay=10,
    until=None,
    atstart=(),
    ateach=(),
    atend=(),
    do_plot=None,
):
    """
    Run a time sweep, with a delay between each point. This sweep will run for `until` seconds,
    or indefinitely if until is None

    Args:
        *param_meas (Iterable[Parameter]): A list of the parameters to be measured at each of the
        set points. For now, these MUST be simple parameters. Arrays cannot be measured.

        delay (float): Time in seconds between points

        until (float): Total time to run

        annotation

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
    # Register setpoints
    m = Measurement()
    m.register_custom_parameter("time", label="Time", unit="s")

    _run_functions(atstart)

    # Set up parameters
    shapes = {}
    if until is not None:
        estimated_points: int = int(until // delay)
    else:
        estimated_points = 10
    for param in param_meas:
        m.register_parameter(param, setpoints=("time",))
        if isinstance(param, ParameterWithSetpoints):
            assert isinstance(param.vals, Arrays) and param.vals.shape is not None
            shapes[param.full_name] = (estimated_points,) + param.vals.shape
        elif isinstance(param.vals, Arrays) and param.vals.shape is not None:
            shapes[param.full_name] = (estimated_points,) + param.vals.shape
        else:
            shapes[param.full_name] = (estimated_points,)
        m.set_shapes(shapes)

    start_time = 0
    curr_point = 0
    datasaver = None
    try:
        with m.run() as datasaver:
            start_time = time.monotonic()
            while True:
                # Update each parameter
                curr_time = time.monotonic() - start_time
                data = [("time", curr_time)]

                _run_functions(
                    ateach, param_vals=(Setpoint("time", curr_point, curr_time))
                )

                if until is not None and curr_time > until:
                    break

                for param in param_meas:
                    val = param()
                    if val is None:
                        val = np.nan
                    data.append((param, val))
                curr_point += 1

                datasaver.add_result(*data)

                # Wait until the next point time. Try to keep track of how long it
                # took for equipment to respond
                next_time = start_time + delay * curr_point
                while time.monotonic() < next_time:
                    sleep_time = max(0, min(0.01, time.monotonic() - next_time))
                    _interruptible_sleep(sleep_time)
    except KeyboardInterrupt:
        print(f"Trace cancelled with Ctrl-C")
        print(f"Ending plot at time {time.monotonic() - start_time}.")
    finally:
        _run_functions(atend)

    if datasaver:
        return datasaver.dataset, None, None
    return None
