import numpy as np
from qcodes import Parameter, ArrayParameter

from .RemoteProcessWrapper import RPGWrappedBase, ensure_ndarray, get_remote
from .ExtendedDataItem import ExtendedDataItem
from .ColorMap import ColorMap

class HistogramLUTItem(RPGWrappedBase):
    _base = "HistogramLUTItem"

    def __init__(self, *args, allowAdd=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.allowAdd = allowAdd
        self._remote_function_options['setLevels'] = {'callSync': 'off'}
        self._remote_function_options['imageChanged'] = {'callSync': 'off'}
    def __wrap__(self, *args, **kwargs):
        super().__wrap__()
        self._remote_function_options['setLevels'] = {'callSync': 'off'}
        self._remote_function_options['imageChanged'] = {'callSync': 'off'}

    @property
    def axis(self):
        return self.__getattr__("axis", _location="remote")

    @property
    def allowAdd(self):
        return self.gradient.allowAdd
    @allowAdd.setter
    def allowAdd(self, val):
        self.gradient.allowAdd = bool(val)


class ImageItem(ExtendedDataItem, RPGWrappedBase):
    _base = "ImageItem"

    def __init__(self, setpoint_x, setpoint_y, *args, colormap=None, **kwargs):
        super().__init__(setpoint_x, setpoint_y, *args, colormap=colormap, **kwargs)
        setpoint_x = ensure_ndarray(setpoint_x)
        setpoint_y = ensure_ndarray(setpoint_y)
        self._remote_function_options['setImage'] = {'callSync': 'off'}
        # Set axis scales correctly
        self._force_rescale(setpoint_x, setpoint_y)

        if colormap is not None:
            if not isinstance(colormap, ColorMap):
                try:
                    colormap = get_remote().COLORMAPS['colormap']
                except KeyError:
                    raise ValueError(f"Can't find colormap {colormap}.")
            lut = colormap.getLookupTable(0, 1, alpha=False)
            self.setLookupTable(lut)

    def __wrap__(self, *args, **kwargs):
        super().__wrap__(*args, **kwargs)
        self._remote_function_options['setImage'] = {'callSync': 'off'}

    def _force_rescale(self, setpoint_x, setpoint_y):
        step_x = (setpoint_x[-1] - setpoint_x[0])/len(setpoint_x)
        step_y = (setpoint_y[-1] - setpoint_y[0])/len(setpoint_y)

        self.resetTransform()
        self.translate(setpoint_x[0], setpoint_y[0])
        self.scale(step_x, step_y)

    def update(self, data, *args, **kwargs):
        self.setImage(ensure_ndarray(data), autoDownsample=True)

    @property
    def image(self):
        """
        Return the data underlying this trace
        """
        return self.__getattr__("image", _returnType="value", _location="remote")
    @property
    def data(self):
        """
        Return the data underlying this trace
        """
        return self.image

class ExtendedImageItem(ImageItem):
    """
    Extended image item keeps track of x and y setpoints remotely, as this is necessary
    to do more enhanced image processing that makes use of the axis scale, like color-by-marquee.
    """
    _base = "ExtendedImageItem"

    def __init__(self, setpoint_x, setpoint_y, *args, colormap=None, **kwargs):
        super().__init__(setpoint_x, setpoint_y, *args, colormap=colormap, **kwargs)
        self.setpoint_x = setpoint_x
        self.setpoint_y = setpoint_y

    def _force_rescale(self, setpoint_x, setpoint_y):
        """
        This is handled on the server side...
        """

    @property
    def setpoint_x(self):
        return self.__getattr__("setpoint_x", _returnType="value", _location="remote")
    @setpoint_x.setter
    def setpoint_x(self, val):
        self._base_inst.setpoint_x = val

    @property
    def setpoint_y(self):
        return self.__getattr__("setpoint_y", _returnType="value", _location="remote")
    @setpoint_y.setter
    def setpoint_y(self, val):
        self._base_inst.setpoint_y = val

class ImageItemWithHistogram(ExtendedImageItem):
    _base = "ImageItemWithHistogram"

    # Local Variables
    _histogram = None

    def __init__(self, setpoint_x, setpoint_y, *args, colormap=None, **kwargs):
        super().__init__(setpoint_x, setpoint_y, *args, colormap=colormap, **kwargs)
        self._histogram = None

    def __wrap__(self, *args, **kwargs):
        super().__wrap__(*args, **kwargs)
        self._histogram = None

    def pause_update(self):
        """
        Pause histogram autoupdates while a sweep is running.
        """
        try:
            self._base_inst.sigImageChanged.disconnect()
        except AttributeError:
            pass

    def resume_update(self):
        """
        Resume histogram autoupdate
        """
        self._base_inst.sigImageChanged.connect(self.histogram.imageChanged)

    def update(self, data, *args, **kwargs):
        super().update(data, *args, **kwargs)
        # Only update the range if requested
        if kwargs.get('update_range', True):
            z_range = (np.min(data), np.max(data))
            self.histogram.imageChanged()
            self.histogram.setLevels(*z_range)

    def update_histogram_axis(self, param_z):
        """
        Update histogram axis labels
        """
        if not isinstance(param_z, (Parameter, ArrayParameter)):
            raise TypeError("param_z must be a qcodes parameter")
        self.histogram.axis.label = param_z.label
        self.histogram.axis.units = param_z.unit

    @property
    def histogram(self):
        if self._histogram is None:
            self._histogram = self.getHistogramLUTItem()
        return self._histogram

    @property
    def colormap(self):
        return self.cmap
    @colormap.setter
    def colormap(self, cmap):
        self.changeColorScale(name=cmap)