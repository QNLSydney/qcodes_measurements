import wrapt
from functools import partial, reduce
from scipy import signal
import numpy as np
import qcodes
import qcodes.utils.validators as vals

"""
Define some modules for doing filtering on parameters as they come in. This is mainly
useful when we actually want to store some derived quantity (i.e. the differentiated signal)
or we need to apply some transformation before the data becomes useful.

The parameters of the filter

Example usage would be:
```py
import qcodes_measurements as qcm

# This is the parameter from the instrument. Let's say we want to smooth it:
parameter_to_measure = scope.ch1.trace
smoothed_parameter = qcm.SmoothFilter(parameter_to_measure)

# Then we can use this parameter as normal
smoothed_paramter.prepare_curvedata()
qcm.linear1d(mdac.LW1, -0.7, -0.8, 200, 0.1, smoothed_parameter)
```
"""

class BaseWrappedParameter(wrapt.CallableObjectProxy):
    """
    Allow filters to be wrapped around parameters, for example to differentiate
    data as it comes in.
    """

    wrappers = None
    def __init__(self, parameter):
        """
        Initialize the wrapped class
        """
        super().__init__(parameter)

    def __call__(self):
        """
        Need to wrap call to reference our overridden get, rather
        than the one in the parent
        """
        return self.get()

    @property
    def name(self):
        """
        Annotate the name of the parameter with wrap to indicate that a filter
        has been applied to the parameter
        """
        param_name = self.__wrapped__.name
        if not param_name.startswith("wrap_"):
            param_name = f"wrap_{param_name}"
        return param_name

    def snapshot(self, *args, **kwargs):
        """
        Overwrite snapshot too, as the wrapped parameter snapshot will not have
        access to our snapshot_base unless it is called from here
        """
        # Take a snapshot of the base
        snap = self.__wrapped__.snapshot(*args, **kwargs)

        # Add a list of wrappers to the snapshot
        snap['wrappers'] = snap.get('wrappers', []) + [self.wrappers]

        # Return the snapshot with wrappers included
        return snap

    def get_raw(self):
        raise RuntimeError("I think this shouldn't be called?")

    def snapshot_base(self, *args, **kwargs):
        raise RuntimeError("I think this shouldn't be called?")

class FilterWrapper(BaseWrappedParameter):
    """
    Wrap a filter function around a parameter. The filter used will be stored
    in metadata along with any parameters used to run it.
    """

    # The following variables are reserved, otherwise they would be passed along
    # to the base parameter
    filter_func = None
    args = None
    kwargs = None
    _label = None
    _unit = None

    def __init__(self, parameter, *, filter_func,
                 label=None, unit=None, args=None, kwargs=None):
        """
        Args:
            parameter - the parameter to wrap
            filter_func - the filter to apply to the result of the parameter
            label - (optional) relabel the data
            unit - (optional) change the units of the data
            *args, **kwargs - arguments passed to the filter function
        """
        super().__init__(parameter)
        self.filter_func = filter_func
        self.args = args if args is not None else []
        self.kwargs = kwargs if kwargs is not None else {}
        self._label = label
        self._unit = unit

        snap = {'type': type(self).__name__,
                'filter_func': filter_func.__name__,
                'doc': filter_func.__doc__,
                'args': args,
                'kwargs': kwargs}
        self.wrappers = snap

    @property
    def label(self):
        if self._label is None:
            return self.__wrapped__.label
        return self._label

    @property
    def unit(self):
        if self._unit is None:
            return self.__wrapped__.unit
        return self._unit

    def get(self):
        d = self.__wrapped__.get()
        d = self.filter_func(d, *self.args, **self.kwargs)
        return d

class ReduceFilterWrapper(BaseWrappedParameter):
    """
    Wrap a filter function around a parameter that will reduce the number of points to one. The filter used will be stored
    in metadata along with any parameters used to run it.
    The end result converts an ArrayParameter like object to a Parameter like object.
    """

    # The following variables are reserved, otherwise they would be passed along
    # to the base parameter
    filter_func = None
    args = None
    kwargs = None

    def __init__(self, parameter, *, filter_func, args=None, kwargs=None):
        """
        Args:
            parameter - the parameter to wrap
            filter_func - the filter to apply to the result of the parameter
            *args, **kwargs - arguments passed to the filter function
        """
        super().__init__(parameter)
        self.filter_func = filter_func
        self.args = args if args is not None else []
        self.kwargs = kwargs if kwargs is not None else {}

        snap = {'type': type(self).__name__,
                'filter_func': filter_func.__name__,
                'doc': filter_func.__doc__,
                'args': args,
                'kwargs': kwargs}
        self.wrappers = snap

    @property
    def setpoints(self):
        return None
    @property
    def shape(self):
        return None
    @property
    def setpoint_labels(self):
        return None
    @property
    def setpoint_units(self):
        return None
    @property
    def setpoint_names(self):
        return None
    @property
    def __class__(self):
        return qcodes.instrument.parameter.Parameter

    def get(self):
        d = self.__wrapped__.get()
        d = self.filter_func(d, *self.args, **self.kwargs)
        return d


class CutWrapper(BaseWrappedParameter):
    """
    Cut a certain number of records from the front or back of a parameter.
    """

    # The following variables are reserved, otherwise they would be passed along
    # to the base parameter
    fromstart = None
    fromend = None

    def __init__(self, parameter, fromstart=0, fromend=0):
        # Must be an array paramter
        assert(hasattr(parameter, "shape") or isinstance(parameter.vals, vals.Arrays))

        super().__init__(parameter)

        # Save variables
        if fromstart < 0:
            raise ValueError(f"Number of points to trim from start must be"
                             f" greater than 0. Given {fromstart}")
        if fromend < 0:
            raise ValueError(f"Number of points to trim from end must be"
                             f" greater than 0. Given {fromend}")
        self.fromstart = fromstart
        self.fromend = fromend

        snap = {'type': type(self).__name__,
                'doc': type(self).__doc__,
                'fromstart': fromstart,
                'fromend': fromend}
        self.wrappers = snap

    @property
    def shape(self):
        """
        Trim the returned shape appropriately
        """
        old_shape = self.__wrapped__.shape
        assert(len(old_shape) == 1) # Only support 1D arrays for now
        old_shape = old_shape[0]
        return (max(old_shape-self.fromstart-self.fromend, 0),)

    @property
    def setpoints(self):
        """
        Trim the setpoints of the data. Setpoints may be a parameter, in which case,
        we need to return a new parameter array.
        """
        if isinstance(self.__wrapped__, qcodes.ParameterWithSetpoints):
            cut_setpoints = []
            for setpoint in self.__wrapped__.setpoints:
                cut_setpoints.append(CutWrapper(setpoint, self.fromstart, self.fromend))
            return tuple(cut_setpoints)
        else:
            old_setpoints = self.__wrapped__.setpoints
            assert(len(old_setpoints) == 1) # Only support 1D arrays for now
            if self.fromend == 0:
                old_setpoints = old_setpoints[0][self.fromstart:]
            else:
                old_setpoints = old_setpoints[0][self.fromstart:-self.fromend]
            return (old_setpoints,)

    @property
    def vals(self):
        """
        Overwrite the Arrays validator if we are a ParameterWithSetpoints.
        """
        if not isinstance(self.__wrapped__, qcodes.ParameterWithSetpoints):
            return self.__wrapped__.vals

        def cut_shape(old_shape, subshape):
            return max(0, old_shape-subshape)
        new_shape = []
        for param in self.__wrapped__.vals.shape_unevaluated:
            new_shape.append(FilterWrapper(param, filter_func=cut_shape, args=(self.fromstart+self.fromend,)))
        return vals.Arrays(shape=tuple(new_shape))

    def get(self):
        d = self.__wrapped__.get()
        if self.fromend == 0:
            d = d[self.fromstart:]
        else:
            d = d[self.fromstart:-self.fromend]
        return d

# Define a function to stack filters easily
def _compose(*filters):
    def compose(a, b):
        return lambda x: a(b(x))
    return reduce(compose, filters, lambda x: x)

# Define a couple of commonly used filters
SmoothFilter = partial(FilterWrapper, filter_func=signal.savgol_filter, args=(15, 3))
GradientFilter = partial(FilterWrapper, filter_func=np.gradient)
# Differentiate with smoothing on two sides.
DiffFilter = _compose(SmoothFilter, GradientFilter, SmoothFilter)
# Take the mean of an array
MeanFilter = partial(ReduceFilterWrapper, filter_func=np.mean)
