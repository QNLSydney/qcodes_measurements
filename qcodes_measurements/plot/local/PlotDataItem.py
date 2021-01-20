from .RemoteProcessWrapper import RPGWrappedBase, ensure_ndarray
from .ExtendedDataItem import ExtendedDataItem

class PlotDataItem(ExtendedDataItem, RPGWrappedBase):
    _base = "PlotDataItem"

    def __init__(self, setpoint_x, *args, **kwargs):
        super().__init__(*args, **kwargs)
        #self.xData = ensure_ndarray(setpoint_x)
        self._remote_function_options['setData'] = {'callSync': 'off'}

    def __wrap__(self, *args, **kwargs):
        setpoint_x = kwargs.pop("setpoint_x", None)

        super().__wrap__(*args, **kwargs)
        self._remote_function_options['setData'] = {'callSync': 'off'}

        if setpoint_x is not None:
            # If we know what our setpoints are, use them
            self.xData = setpoint_x

    def update(self, data, *args, **kwargs):
        """
        Update the data in the plot.

        Note: The data must not contain NaN values, or the plot will not show any data.
        """
        self.setData(x=self.setpoint_x, y=ensure_ndarray(data), *args, **kwargs)

    @property
    def setpoint_x(self):
        return self.xData
    @property
    def xData(self):
        return self._base_inst.__getattr__("xData", _returnType="value", _location="remote")
    @xData.setter
    def xData(self, val):
        self._base_inst.xData = ensure_ndarray(val)

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
        self._remote_function_options['update'] = {'callSync': 'off'}
        self._remote_function_options['setData'] = {'callSync': 'off'}

    def __wrap__(self, *args, **kwargs):
        setpoint_x = kwargs.pop("setpoint_x", None)

        super().__wrap__(*args, **kwargs)
        self._remote_function_options['update'] = {'callSync': 'off'}
        self._remote_function_options['setData'] = {'callSync': 'off'}

        if setpoint_x is not None:
            # If we know what our setpoints are, use them
            self.setpoint_x = ensure_ndarray(setpoint_x)

    @property
    def setpoint_x(self):
        return self._base_inst.setpoint_x
    @setpoint_x.setter
    def setpoint_x(self, val):
        self._base_inst.setpoint_x = ensure_ndarray(val)

    @property
    def xData(self):
        return self.__getattr__("xData", _returnType="value", _location="remote")
    @xData.setter
    def xData(self, val):
        val = ensure_ndarray(val)
        self._base_inst.xData = val
        self._base_inst.setpoint_x = val

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
        self.__getattr__("setData", _location="remote")(x, y, *args, **kwargs)
