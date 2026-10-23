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
        self.setpoint_x = ensure_ndarray(val)
        if self.yData is not None:
            self.setData(self.setpoint_x, self.yData)

    @property
    def yData(self):
        return self.__getattr__("yData", _returnType="value", _location="remote")

    @property
    def data(self):
        """
        Return the data underlying this trace
        """
        return (self.xData, self.yData)


class ExtendedPlotDataItem(PlotDataItem):
    _base = "ExtendedPlotDataItem"

    def __init__(self, setpoint_x, *args, **kwargs):
        super().__init__(setpoint_x, *args, **kwargs)
        self.setpoint_x = ensure_ndarray(setpoint_x)
        self._remote_function_options["update"] = {"callSync": "off"}
        self._remote_function_options["setData"] = {"callSync": "off"}

    def __wrap__(self, *args, **kwargs):
        setpoint_x = kwargs.pop("setpoint_x", None)

        super().__wrap__(*args, **kwargs)
        self._remote_function_options["update"] = {"callSync": "off"}
        self._remote_function_options["setData"] = {"callSync": "off"}

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
        self.setpoint_x = ensure_ndarray(x)
        self.__getattr__("setData", _location="remote")(x, y, *args, **kwargs)
