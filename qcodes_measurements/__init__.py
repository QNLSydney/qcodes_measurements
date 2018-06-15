from .plot import ChildProcessImportError
import logging

__all__ = ['tools', 'plot']

logger = logging.getLogger("qcodes_measurements")

try:
    # Import shortcuts to plot tools
    from .plot import plot_tools, pyplot

    # Import shortcuts to measurements
    from .tools.midas import midasLinear1d
    from .tools.measure import linear1d, linear2d
    from .tools.parameters import FilterWrapper, CutWrapper, SmoothFilter, DiffFilter, MeanFilter, ReduceFilterWrapper

    # If we have an MDAC, import MDAC shortcuts
    try:
        import qcodes.instrument_drivers.qnl.MDAC as MDAC
        from .tools.mdac import *
    except ModuleNotFoundError:
        logger.info("MDAC drivers not present. Not loading drivers")
except ChildProcessImportError:
    # Don't do this if we are the child process
    pass
