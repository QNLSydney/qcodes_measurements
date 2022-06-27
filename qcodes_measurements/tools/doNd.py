"""
Wrappers around qcodes.utils.dataset.doNd functions that live-plot
data during the sweep.
"""

import re
import sys
import inspect
import functools
import itertools
import numpy as np
from typing import Any, Optional, Union, Tuple, List, Mapping
from dataclasses import dataclass, field

from qcodes import config
from qcodes.dataset.data_set import DataSet
from qcodes.dataset.descriptions.param_spec import ParamSpecBase
from qcodes.instrument.parameter import _BaseParameter
import qcodes.utils.dataset.doNd as doNd

from ..plot import PlotWindow, PlotItem, PlotDataItem, ImageItem, TableWidget
from ..plot.plot_tools import save_figure
from ..logging import get_logger

# Get access to module level variables
this = sys.modules[__name__]
this.current = None
logger = get_logger("tools.doNd")


# Utility functions for parsing parameter names from plot titles
_param = r"(\w+)\s+\([^)]+\)"
_single_id = r"(\d+)(?:-(\d+))?(?:, (?=\d))?"
_id = r"(\(id:\s+(?:\d+(?:-\d+)?(?:, (?=\d))?)+\))"
_id_re = re.compile(_single_id, re.IGNORECASE)
_plot_title_re = re.compile(r"("+_param+r"\s+v\.(?:<br>|\s)+"+_param+r")\s+"+_id, re.MULTILINE|re.IGNORECASE)
_single_param_title_re = re.compile(r"("+_param+r")\s*"+_id, re.MULTILINE)


def _get_window(append, size=(1000, 600)):
    """
    Return a handle to a plot window to use for this plot.
    If append is False, create a new plot window, otherwise return
    a handle to the given window, or the last created window.

    Args:
        append (Union[bool, PlotWindow]): If true, return the last
        created plot window, if PlotWindow, return that window, otherwise
        a new window will be created.

        size (Tuple[int, int]): The size in px of the new plot window. If append
        is not false, this parameter has no effect.
    """
    # Set up a plotting window
    if append is None or append is False:
        win = PlotWindow()
        win.win_title = 'ID: '
        win.resize(*size)
    elif isinstance(append, PlotWindow):
        # Append to the given window
        win = append
    elif isinstance(append, bool):
        # Append to the last trace if true
        win = PlotWindow.getWindows()[-1]
    else:
        raise ValueError("Unknown argument to append. Either give a plot window"
                         " or true to append to the last plot")
    return win

def _explode_ids(ids_str: str) -> List[int]:
    """
    Explode a list of ids from a plot title into a list of all
    ids.
    """
    ids = []
    for match in _id_re.finditer(ids_str):
        start, stop = match.groups()
        if stop is None:
            ids.append(int(start))
        else:
            ids.extend(range(int(start), int(stop)+1))
    return tuple(ids)


def _reduce_ids(ids: List[int]):
    strings = []
    i = 1
    r = 0
    while i < len(ids):
        if ids[i] == ids[i-1]+1:
            i += 1
        else:
            if i-1 == r:
                strings.append(f"{ids[r]}")
            else:
                strings.append(f"{ids[r]}-{ids[i-1]}")
            r = i
            i += 1
    if i-1 == r:
        strings.append(f"{ids[r]}")
    else:
        strings.append(f"{ids[r]}-{ids[i-1]}")
    return strings


def _parse_title(title) -> Tuple[str, Tuple[str], Tuple[int]]:
    match = _plot_title_re.fullmatch(title)
    if not match:
        # Might be a single title re
        match = _single_param_title_re.fullmatch(title)
        if not match:
            return None
        paramstr, param_name, ids = match.groups()
        ids = _explode_ids(ids)
        return(paramstr, (param_name,), ids)
    paramstr, param1_name, param2_name, ids = match.groups()
    ids = _explode_ids(ids)
    return (paramstr, (param1_name, param2_name), ids)


def _compatible_plot_item(win: PlotWindow,
                          p_bot: ParamSpecBase,
                          p_left: Optional[ParamSpecBase] = None) -> Optional[PlotItem]:
    """
    Returns a compatible plot item if found
    """
    if p_left is not None:
        axes = (p_bot.name, p_left.name)
    else:
        axes = (p_bot.name, )
    for item in win.items:
        if isinstance(item, PlotItem):
            _, params, _ = _parse_title(item.plot_title)
            if params == axes:
                return item
    return None


def _register_subscriber():
    """
    Register live plotting in the qcodes config object.
    """
    if "qcm" not in config.subscription.subscribers:
        logger.info("Registering qcm as a default subscriber")
        config.subscription.subscribers["qcm"] = {
            'factory': 'qcodes_measurements.tools.doNd.subscriber',
            'factory_kwargs': {},
            'subscription_kwargs': {
                'min_wait': 10,
                'min_count': 0,
                'callback_kwargs': {}
            }
        }
        config.subscription.default_subscribers.append("qcm")

# Tuple for live plotting
@dataclass(frozen=False)
class LivePlotWindow:
    plot_window: Optional[PlotWindow]
    stack: bool = False
    append: bool = False
    dataset: DataSet = None
    datacount: Mapping[str, int] = field(default_factory=dict)
    table_items: Mapping[str, Union[int, float]] = None
    plot_items: Mapping[str, Union[PlotDataItem, ImageItem]] = field(default_factory=dict)
    plot_params: List[_BaseParameter] = None


def do_nothing(new_data, data_len, state):
    """
    Function that does nothing
    """
    return


def update_plots(new_data, data_len, state):
    """
    Function that updates plots when live plotting
    """
    write_count = this.current.dataset.cache._write_status
    # Don't update if we haven't started measuring yet
    if not write_count or any(wc == 0 for wc in write_count.values()): return
    run_desc = this.current.dataset.description
    data_cache = this.current.dataset.cache.data()
    params = run_desc.interdeps
    shapes = run_desc.shapes
    plot_items = this.current.plot_items.items()
    table_items = this.current.table_items.items() if this.current.table_items is not None else ()
    for param, plotitem in itertools.chain(plot_items, table_items):
        # Keep track of how much of the plot we've written, and only update
        # parameters that are being measured.
        if param not in write_count:
            continue
        if param not in this.current.datacount:
            this.current.datacount[param] = write_count[param]
        elif write_count[param] == this.current.datacount[param]:
            continue
        else:
            this.current.datacount[param] = write_count[param]

        # Update plots
        if shapes[param] == (1,):
            val = data_cache[param][param][0]
            if isinstance(val, (float, np.float16, np.float32, np.float64)):
                val = np.format_float_scientific(val)
            else:
                val = str(val)
            this.current.table_items[param].append(val)
        elif len(shapes[param]) == 1:
            paramspec = params[param]
            setpoint_param = params.dependencies[paramspec][0]
            plotitem.setData(data_cache[param][setpoint_param.name][:write_count[param]],
                             data_cache[param][param][:write_count[param]])
        else:
            paramspec = params[param]
            bot_axis = params.dependencies[paramspec][0]
            left_axis = params.dependencies[paramspec][1]
            data = data_cache[param][param]

            # Check if we are in the first column or if we need to clear nans
            if np.isnan(data[-1,-1]) or write_count[param] < shapes[param][1]:
                meanval = data.flat[:write_count[param]].mean()
                data.flat[write_count[param]:] = meanval

            # Update axis scales as data comes in
            if plotitem.no_xscale:
                # Set Y-scale until we have the entire first column
                if plotitem.no_yscale and write_count[param] >= shapes[param][1]:
                    ldata = data_cache[param][left_axis.name]
                    ymin, ymax = ldata[0, 0], ldata[0, -1]
                    plotitem.setpoint_y = np.linspace(ymin, ymax, shapes[param][1])
                    plotitem.no_yscale = False
                    plotitem.rescale()
                elif plotitem.no_yscale and write_count[param] >= 2:
                    ldata = data_cache[param][left_axis.name]
                    ymin, step = ldata[0, 0], ldata[0, 1]-ldata[0, 0]
                    plotitem.setpoint_y = np.linspace(ymin, ymin + step*shapes[param][1], shapes[param][1], endpoint=False)
                    plotitem.rescale()

                # Set X-scale
                if write_count[param]/shapes[param][1] > 1:
                    bdata = data_cache[param][bot_axis.name]
                    xmin, step = bdata[0, 0], bdata[1, 0]-bdata[0, 0]
                    plotitem.setpoint_x = np.linspace(xmin, xmin + step*shapes[param][0], shapes[param][0], endpoint=False)
                    plotitem.no_xscale = False
                    plotitem.rescale()
            # Rescale x-axis when we have all values in case of F.P. error
            if write_count[param] == shapes[param][0]*shapes[param][1]:
                bdata = data_cache[param][bot_axis.name]
                xmin, xmax = bdata[0, 0], bdata[-1, 0]
                plotitem.setpoint_x = np.linspace(xmin, xmax, shapes[param][0])
                plotitem.no_xscale = False
                plotitem.rescale()

            # Update the plot
            plotitem.update(data)

    # Update table items if requested, expanding parameters that weren't measured
    # if necessary.
    if this.current.table_items:
        nItems = max(len(x) for x in this.current.table_items.values())
        for item in this.current.table_items:
            if len(this.current.table_items[item]) < nItems:
                this.current.table_items[item].append("")
        col_titles = this.current.plot_window.table.getHorizontalHeaders()
        if len(col_titles) < nItems:
            col_titles.append(str(this.current.dataset.run_id))
        this.current.plot_window.table.setData(this.current.table_items)
        this.current.plot_window.table.setHorizontalHeaderLabels(col_titles)

    # Done update
    return


def subscriber(dataset, **kwargs):
    """
    Attach a plot window to the dataset and supply an update
    method that will update the live plots.
    """
    # First, check if we actually want to do anything. If not, we return
    # a blank function
    if this.current is None or this.current.plot_window is None:
        logger.info(f"Live plotting disabled for {dataset.run_id}.")
        if this.current is not None:
            this.current.dataset = dataset
        return do_nothing

    # Update the plot title
    window_run_ids = _explode_ids(f"({this.current.plot_window.win_title})")
    if not window_run_ids or window_run_ids is None:
        window_run_ids = (dataset.run_id,)
    else:
        window_run_ids = window_run_ids + (dataset.run_id,)
    run_id_str = ', '.join(_reduce_ids(window_run_ids))
    this.current.plot_window.win_title = f"ID: {run_id_str}"

    # Otherwise, register parameters into the window
    this.current.dataset = dataset
    win = this.current.plot_window
    win.run_id = dataset.run_id
    run_desc = dataset.description
    params = run_desc.interdeps
    shapes = run_desc.shapes
    if this.current.plot_params is None:
        this.current.plot_params = set(params.names)
    else:
        this.current.plot_params = set(p.fullname for p in this.current.plot_params)

    for param in itertools.chain(params.dependencies, params.standalones):
        name = param.name
        if name not in this.current.plot_params:
            logger.info("Parameter %s not in list of plot parameters %r", name, this.current.plot_params)
            continue

        # Figure out the shape of the parameter
        if shapes[name] == (1,):
            logger.info("Adding 0D parameter %s", name)
            if win.table is None:
                table = TableWidget(sortable=False)
                t_widget = win.scene().addWidget(table)
                t_widget.setMinimumSize(300, 0)
                win.addItem(t_widget)
                this.current.table_items = {}
            elif this.current.table_items is None:
                this.current.table_items = win.table.getData()
            if name not in this.current.table_items:
                if this.current.table_items:
                    nVals = len(next(iter(this.current.table_items.values())))
                else:
                    nVals = 0
                this.current.table_items[name] = [""]*nVals
            win.table.setHorizontalHeaderLabels(list(str(s) for s in window_run_ids))
        elif len(shapes[name]) == 1:
            logger.info("Adding 1D parameter %s with shape %r", name, shapes[name])
            bot_axis = params.dependencies[param][0]

            # If we need to stack or append, find the right plot
            plotitem = None
            if this.current.stack:
                try:
                    plotitem = next(iter(i for i in win.items if isinstance(i, PlotItem)))
                except StopIteration:
                    pass
            elif this.current.append:
                plotitem = _compatible_plot_item(win, bot_axis, param)
                if plotitem is None:
                    logger.warning("Append requested but appropriate plotitem not found."
                                   " Making a new one.")

            # Couldn't find an appropriate plotitem - make a new one
            if plotitem is None:
                plotitem = win.addPlot(name=name,
                                       title=(f"{bot_axis.name} ({bot_axis.label}) v.<br>"
                                              f"{param.name} ({param.label}) "
                                              f"(id: {run_id_str})"))
                plotitem.bot_axis.paramspec = bot_axis
                plotitem.left_axis.paramspec = param
            else:
                # Update ID string
                paramstr, _, _ = _parse_title(plotitem.plot_title)
                plotitem.plot_title = f"{paramstr} (id: {run_id_str})"
            # Add new trace to the plot
            plotdata = plotitem.plot(setpoint_x=[],
                                     pen=(255, 0, 0),
                                     name=param.name)
            this.current.plot_items[param.name] = plotdata
        elif len(shapes[name]) == 2:
            logger.info("Adding 2D parameter %s with shape %r", name, shapes[name])
            bot_axis = params.dependencies[param][0]
            left_axis = params.dependencies[param][1]

            plotitem = None
            if this.current.stack:
                logger.warning("Can't stack 2D param %r. Will create a new plot instead.", name)
            if this.current.append:
                plotitem = _compatible_plot_item(win, bot_axis, left_axis)
                if plotitem is None:
                    logger.warning("Append requested but appropriate plotitem not found."
                                   " Making a new one.")

            # Couldn't find an appropriate plotitem - make a new one
            if plotitem is None:
                plotitem = win.addPlot(name=name,
                                       title=(f"{bot_axis.name} ({bot_axis.label}) v.<br>"
                                              f"{left_axis.name} ({left_axis.label}) "
                                              f"(id: {run_id_str})"))
                plotitem.bot_axis.paramspec = bot_axis
                plotitem.left_axis.paramspec = left_axis
            else:
                # Update ID string
                paramstr, _, _ = _parse_title(plotitem.plot_title)
                plotitem.plot_title = f"{paramstr} (id: {run_id_str})"

            # Add new trace to the plot
            # Initially the axes are set to some random range, this will be filled
            # in once the first column is taken.
            plotdata = plotitem.plot(setpoint_x=np.linspace(0, 1, shapes[name][0]),
                                     setpoint_y=np.linspace(0, 1, shapes[name][1]),
                                     name=name)
            plotdata.no_xscale = True
            plotdata.no_yscale = True
            this.current.plot_items[name] = plotdata
        else:
            logger.warning("Trying to plot a dataset with more than 2 dimensions. "
                           "Will not create plot for this item")

    return update_plots


def _live_plot(wrapped):
    """
    Wrap do*d functions from qcodes and augment them with the capability for live
    plotting.

    Extend the docstring and signature to include the new parameters so that the help
    documentation still works.
    """
    # Make sure qcm is registered
    _register_subscriber()

    WRAPPER_ASSIGNMENTS = ('__module__', '__name__', '__qualname__', '__doc__')
    @functools.wraps(wrapped, assigned=WRAPPER_ASSIGNMENTS)
    def wrapped_function(*args: Any,
                         plot: bool = True,
                         plot_params: Optional[List[_BaseParameter]] = None,
                         append: Optional[Union[bool, PlotWindow]] = False,
                         save: bool = True,
                         stack: bool = False,
                         **kwargs: Any):
        kwargs["do_plot"] = False

        # Get the plot window if requested
        if plot:
            win = _get_window(append)
        elif append:
            raise RuntimeError("Append requested despite plot being False.")
        else:
            win = None

        this.current = LivePlotWindow(plot_window=win,
                                      append=(append is not False),
                                      stack=stack,
                                      plot_params=plot_params)

        ret_val = None
        try:
            ret_val = wrapped(*args, **kwargs)
        finally:
            # Try and save the plot if save was requested. If the run failed, we still try
            # to pull a run ID out of the window in order to save.
            if win is not None and save:
                if ret_val is not None:
                    run_id = ret_val[0].run_id
                else:
                    run_id = getattr(win, "run_id", None)
                if run_id is not None:
                    try:
                        save_figure(win, run_id)
                    except:
                        logger.error(f"Failed to save figure {run_id}.")
                else:
                    logger.warning("Couldn't find run id for sweep. Not saved!")

            # Add the window to the return
            if win is not None and ret_val is not None:
                if ret_val[1] == [None]:
                    ret_val[1].clear()
                    ret_val[1].append(win)

            # And reset this.current
            this.current = None

        # Return the sweep result
        return ret_val

    # Update the docstring to include the new parameters
    wrapped_function.__doc__ = f"""
    Wrapped instance of {wrapped.__name__} which enables live plotting during a measurement.

    Args:
        plot (Optional[bool]): Whether or not live plotting should be performed
        on this dataset.

        plot_params (Optional[List[_BaseParameter]]):  Which parameters to plot

        append (Optional[bool | PlotWindow]): If this parameter is not false, the
        trace will be appended to an existing window. Either the plot window should
        be given or the last plot window will be used.

        stack (Optional[bool]): Stack the plots on a single axis, rather than creating a
        new plot for each item.

        save (Optional[bool]): Whether or not the figure should be saved.

    Original Docstring
    ------------------
    {wrapped.__doc__}
    """

    old_signature = inspect.signature(wrapped)
    new_signature = inspect.signature(wrapped_function, follow_wrapped=False)
    #combined_parameters = dict(old_signature.parameters.items()) | dict(new_signature.parameters.items())
    combined_parameters = dict(list(old_signature.parameters.items()) + list(new_signature.parameters.items()))
    del combined_parameters["do_plot"]
    del combined_parameters["args"]
    del combined_parameters["kwargs"]
    combined_signature = inspect.Signature(combined_parameters.values(),
                                           return_annotation=old_signature.return_annotation)
    wrapped_function.__signature__ = combined_signature

    return wrapped_function


# Create wrapped doNd functions
do0d = _live_plot(doNd.do0d)
do1d = _live_plot(doNd.do1d)
do2d = _live_plot(doNd.do2d)
