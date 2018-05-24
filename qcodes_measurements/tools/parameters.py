from qcodes import Parameter

import wrapt

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

    def snapshot_base(*args, **kwargs):
        # Take a snapshot of the base
        snap = super().snapshot_base(*args, **kwargs)

        # Add a list of wrappers to the snapshot
        snap['wrappers'] = snap.get('wrappers', []) + [self.wrappers]

        # Return the snapshot with wrappers included
        return snap

def FilterWrapper(BaseWrappedParameter):
    """
    Wrap a filter function around a parameter. The filter used will be stored
    in metadata along with any parameters used to run it.
    """

    # The following variables are reserved, otherwise they would be passed along
    # to the base parameter
    filter_func = None
    args = None
    kwargs = None

    def __init__(self, parameter, filter_func, *args, **kwargs):
        """
        Args:
            parameter - the parameter to wrap
            filter_func - the filter to apply to the result of the parameter
            *args, **kwargs - arguments passed to the filter function
        """
        super().__init__(parameter)
        self.filter_func = filter_func
        self.args = args
        self.kwargs = kwargs

        snap = {'type': type(self).__name__,
                'filter_func': filter_func.__name__,
                'doc': filter_func.__doc__,
                'args': args,
                'kwargs': kwargs}
        self.wrappers = wrappers

    def get_raw(self):
        d = super().get_raw()
        d = self.filter_func(d, *args, **kwargs)
        return d

def CutResultWrapper(BaseWrappedParameter):
    """
    Cut a certain number of records from the front or back of a parameter.
    """

    # The following variables are reserved, otherwise they would be passed along
    # to the base parameter
    fromstart = None
    fromend = None

    def __init__(self, parameter, fromstart=0, fromend=0):
        super().__init__(parameter)

        # Save variables
        if fromstart < 0:
            raise ValueError(f"Number of points to trim from start must be"
                             f" greater than 0. Given {fromstart}")
        if fromend < 0:
            raise ValueError(f"Number of points to trim from start must be"
                             f" greater than 0. Given {fromend}")
        self.fromstart = fromstart
        self.fromend = fromend

        snap = {'type': type(self).__name__,
                'doc': type(self).__doc__,
                'fromstart': fromstart,
                'fromend': fromend}
        self.wrappers = wrappers

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
        Trim the setpoints of the data
        """
        old_setpoints = self.__wrapped__.setpoints
        assert(len(old_setpoints) == 1) # Only support 1D arrays for now
        old_setpoints = old_setpoints[0][self.fromstart:-self.fromend]
        return (old_setpoints,)
    

    def get_raw(self):
        d = super().get_raw()
        d = d[self.fromstart:-self.fromend]
        return d