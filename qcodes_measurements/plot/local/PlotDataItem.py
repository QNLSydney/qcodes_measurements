from .ExtendedDataItem import ExtendedDataItem
from .RemoteProcessWrapper import RPGWrappedBase, ensure_ndarray


class PlotDataItem(ExtendedDataItem, RPGWrappedBase):
    _base = "PlotDataItem"
    _setpoint_x = None

    def __init__(self, setpoint_x, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setpoint_x = ensure_ndarray(setpoint_x)
        self._remote_function_options["setData"] = {"callSync": "off"}

    def __wrap__(self, *args, **kwargs):
        setpoint_x = kwargs.pop("setpoint_x", None)

        super().__wrap__(*args, **kwargs)
        self._remote_function_options["setData"] = {"callSync": "off"}

        if setpoint_x is not None:
            # If we know what our setpoints are, use them
            self.xData = setpoint_x

    def update(self, data, *args, **kwargs):
        """
        Update the data in the plot.

        Note: The data must not contain NaN values, or the plot will not show any data.
        """
        self.setData(*args, x=self.setpoint_x, y=ensure_ndarray(data), **kwargs)

    @property
    def setpoint_x(self):
        return self._setpoint_x

    @setpoint_x.setter
    def setpoint_x(self, val):
        self._setpoint_x = ensure_ndarray(val)

    @property
    def xData(self):
        return self.__getattr__("xData", _returnType="value", _location="remote")

    @xData.setter
    def xData(self, val):
        val = ensure_ndarray(val)
        if self._base == "PlotDataItem":
            self._setpoint_x = val
        self._call_helper(
            "set_trace_x",
            self._base_inst,
            val,
            _handler=self._base_inst._handler,
            _callSync="off",
        )

    @property
    def yData(self):
        return self.__getattr__("yData", _returnType="value", _location="remote")

    @property
    def data(self):
        """
        Return the data underlying this trace
        """
        return self._call_helper(
            "trace_data",
            self._base_inst,
            _handler=self._base_inst._handler,
            _returnType="value",
        )


class ExtendedPlotDataItem(PlotDataItem):
    _base = "ExtendedPlotDataItem"

    def __init__(self, setpoint_x, *args, **kwargs):
        super().__init__(setpoint_x, *args, **kwargs)
        self._remote_function_options["update"] = {"callSync": "off"}
        self._remote_function_options["setData"] = {"callSync": "off"}
        self._remote_function_options["setTraceData"] = {"callSync": "off"}

    def __wrap__(self, *args, **kwargs):
        setpoint_x = kwargs.pop("setpoint_x", None)

        super().__wrap__(*args, **kwargs)
        self._remote_function_options["update"] = {"callSync": "off"}
        self._remote_function_options["setData"] = {"callSync": "off"}
        self._remote_function_options["setTraceData"] = {"callSync": "off"}

        if setpoint_x is not None:
            # If we know what our setpoints are, use them
            self.setpoint_x = ensure_ndarray(setpoint_x)

    @property
    def setpoint_x(self):
        return self.__getattr__("setpoint_x", _returnType="value", _location="remote")

    @setpoint_x.setter
    def setpoint_x(self, val):
        self._base_inst.setpoint_x = ensure_ndarray(val)

    def update(self, data, *args, **kwargs):
        """
        Update the data in a plot, sending only the new y-data. The data will be filtered
        for NaN values correctly in the remote plot window.
        """
        self.__getattr__("update", _location="remote")(data, *args, **kwargs)

    def setData(self, x, y, *args, **kwargs):
        """
        Set the x, y in the plot, without filtering for NaN values.
        """
        # Store setpoints and plot the data in one command; sending x separately
        # would serialize and transfer the full array twice on every live update.
        self.__getattr__("setTraceData", _location="remote")(
            ensure_ndarray(x), ensure_ndarray(y), *args, **kwargs
        )
