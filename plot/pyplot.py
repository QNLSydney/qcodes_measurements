# -*- coding: utf-8 -*-

from math import ceil

import pyqtgraph as pg
import pyqtgraph.multiprocess as mp

from numpy import linspace

from ..plot import colors

proc = mp.QtProcess()
rpg = proc._import('pyqtgraph')
rpg.setConfigOptions(antialias=True)
windows = []

# Transfer color scales to remote process, truncating steps to 16 if necessary
rcmaps = {}
for color in colors.__all__:
    data = getattr(colors, "_%s_data" % color)
    step = ceil(len(data) / 16)
    rcmap = rpg.ColorMap(pos=linspace(0.0, 1.0, 
                         len(data[::step])), color=data[::step])
    rcmaps[color] = rcmap

# Set the default color scale to viridis
rcmap = rcmaps["viridis"]

class RPGWrappedBase(mp.remoteproxy.ObjectProxy):
    def __init__(self, *args, **kwargs):
        self.__dict__['_items'] = []
        if '_base' in self.__class__.__dict__:
            base = self.__class__.__dict__['_base']()
            self.__dict__['_base_inst'] = base
            self.__dict__ = {**base.__dict__, **self.__dict__}
            
    def __wrap__(self):
        # We still want to keep track of new items in wrapped objects
        self.__dict__['_items'] = []

    @classmethod
    def wrap(cls, instance, *args, **kwargs):
        if not isinstance(instance, mp.remoteproxy.ObjectProxy):
            raise TypeError("We can only wrap ObjectProxies")

        # Create an empty instance of the class we want wrapped,
        # and copy over instance variables
        base_inst = cls.__new__(cls)
        base_inst.__dict__ = {**base_inst.__dict__, 
                              **instance.__dict__}
        base_inst.__dict__['_base_inst'] = instance

        # If we do want to initialize some instance variables, we can do it in
        # the special __wrap__ method
        __wrap__ = getattr(base_inst, '__wrap__', None)
        if __wrap__ is not None:
            __wrap__(*args, **kwargs)

        return base_inst

    @staticmethod
    def autoWrap(inst):
        subclass_types = {}
        for t in RPGWrappedBase.__subclasses__():
            typestr = t._base._typeStr.split()[1].strip('\'>').split('.')[-1]
            subclass_types[typestr] = t

        if isinstance(inst, mp.remoteproxy.ObjectProxy):
            if isinstance(inst, RPGWrappedBase):
                return inst
            typestr = inst._typeStr.split()[0].strip('< ').split('.')[-1]
            if typestr in subclass_types:
                return subclass_types[typestr].wrap(inst)

        return inst

    def wrapAdders(self, f):
        def save(*args, **kwargs):
            res = f(*args, **kwargs)
            res = RPGWrappedBase.autoWrap(res)
            if res is not None and isinstance(res, mp.remoteproxy.ObjectProxy):
                self._items.append(res)
            return res
        return save

    def wrapGetters(self, f):
        def wrapper(*args, **kwargs):
            res = f(*args, **kwargs)
            return RPGWrappedBase.autoWrap(res)
        return wrapper

    def __setattr__(self, name, value):
        if name in self.__class__.__dict__ or name in self.__dict__:
            object.__setattr__(self, name, value)
        elif name == "__dict__":
            object.__setattr__(self, name, value)
        else:
            super().__setattr__(name, value)

    def __getattr__(self, name):
        attr = super().__getattr__(name)
        if name.startswith("add") and callable(attr):
            return self.wrapAdders(attr)
        elif name.startswith("get") and callable(attr):
            return self.wrapGetters(attr)

        return attr

    def __repr__(self):
        return "<%s for %s >" % (self.__class__.__name__, super().__repr__())

class PlotWindow(RPGWrappedBase):
    _base = rpg.GraphicsLayoutWidget
    _windows = []

    def __init__(self, *args, **kwargs):
        """
        Create a new remote plot window, with title and size given
        """
        super().__init__(self, *args, **kwargs)
        self.show()

        # Keep track of all windows globally
        PlotWindow._windows.append(self)

    def __del__(self):
        PlotWindow._windows.remove(self)

    @property
    def win_title(self):
        return self.windowTitle()

    @win_title.setter
    def win_title(self, title):
        self.setWindowTitle(str(title))

    @property
    def size(self):
        rsize = self._base_inst.size()
        return (rsize.width(), rsize.height())

class PlotAxis(RPGWrappedBase):
    _base = rpg.AxisItem

    @property
    def label(self):
        return self.labelText
    @label.setter
    def label(self, text):
        self.setLabel(text=text)

    @property
    def units(self):
        return self.labelUnits
    @label.setter
    def units(self, units):
        self.setLabel(units=units)

class PlotItem(RPGWrappedBase):
    _base = rpg.PlotItem

    def __init__(self, plot_title=None):
        """
        Create a new plot. This has to be embedded inside 
        a plot window to actually be visible
        """
        # Keep track of traces that are plotted
        self.__dict__['_traces'] = []
    
    def __wrap__(self):
        super().__wrap__()
        # Keep track of traces that are plotted
        self.__dict__['_traces'] = []

    def wrapAdders(self, f):
        def save(*args, **kwargs):
            res = f(*args, **kwargs)
            res = RPGWrappedBase.autoWrap(res)
            if res is not None and isinstance(res, mp.remoteproxy.ObjectProxy):
                self._items.append(res)
                if isinstance(res, PlotDataItem):
                    self._traces.append(res)
            return res
        return save

    def plot(self, *args, **kwargs):
        plotitem = PlotDataItem.wrap(super().__getattr__('plot')(*args, **kwargs))
        self._items.append(plotitem)
        self._traces.append(plotitem)
        return plotitem

    @property
    def plot_title(self):
        return self.titleLabel.text

    @plot_title.setter
    def plot_title(self, title):
        self.setTitle(title)

    @property
    def left_axis(self):
        return self.getAxis('left')

    @property
    def bot_axis(self):
        return self.getAxis('bottom')

    @property
    def traces(self):
        return self._traces[:]

class PlotData(RPGWrappedBase):
    pass

    @staticmethod
    def getPlot(setpoint_x, setpoint_y=None, *args, **kwargs):
        if setpoint_y is None:
            return PlotDataItem(setpoint_x, *args, **kwargs)
        return ImageItem(setpoint_x, setpoint_y, *args, **kwargs)

    def update(self, data, *args, **kwargs):
        """
        Define a common way of updating plots for 1D and 2D plots
        """
        raise NotImplementedError("Can't update this")

class HistogramLUTItem(RPGWrappedBase):
    _base = rpg.HistogramLUTItem

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    @property
    def axis(self):
        return RPGWrappedBase.autoWrap(super().__getattr__('axis'))

class PlotDataItem(PlotData):
    _base = rpg.PlotDataItem

    def __init__(self, setpoint_x=None, *args, **kwargs):
        self.__dict__['setpoint_x'] = setpoint_x
        super().__init__(*args, **kwargs)

    def __wrap__(self, *args, **kwargs):
        if 'setpoint_x' in kwargs:
            # If we know what our setpoints are, use them
            self.__dict__['setpoint_x'] = kwargs['setpoint_x']
        else:
            try:
                # Otherwise try and extract them from the existing data
                xData = self.xData
                if isinstance(xData, mp.remoteproxy.ObjectProxy):
                    self.__dict__['setpoint_x'] = xData._getValue()
                else:
                    self.__dict__['setpoint_x'] = xData
            except AttributeError:
                self.__dict__['setpoint_x'] = None

    def update(data, *args, **kwargs):
        self.setData(x=data, y=self.setpoint_x, *args, **kwargs)

class ImageItem(PlotData):
    _base = rpg.ImageItem

    def __init__(self, setpoint_x, setpoint_y, *args, **kwargs):
        self.__dict__['setpoint_x'] = setpoint_x
        self.__dict__['setpoint_y'] = setpoint_y
        super().__init__(*args, **kwargs)

        # Set axis scales correctly
        self._force_rescale()

    def __wrap__(self, *args, **kwargs):
        if 'setpoint_x' in kwargs and 'setpoint_y' in kwargs:
            # If we are given the scalings, use them
            self.__dict__['setpoint_x'] = kwargs['setpoint_x']
            self.__dict__['setpoint_y'] = kwargs['setpoint_y']
        elif 'setpoint_x' in kwargs or 'setpoint_y' in kwargs:
            # If we are only given one, that must be an error
            raise TypeError('setpoint_x or _y given without the other. Both or neither are necessary')
        else:
            # Otherwise try to figure it out
            image = self.image
            if image is None:
                # There is no data here, let's just set some random scales
                self.__dict__['setpoint_x'] = (0, 1)
                self.__dict__['setpoint_y'] = (0, 1)
            else:
                image = image._getValue()
                x_len, y_len = image.shape

                # And then query the translation
                offs = self.scenePos()._getValue()
                x_offs, y_offs = offs.x(), offs.y()

                # And then the scaling
                x_scale, y_scale = im.sceneTransform().map(1.0, 1.0)
                x_scale, y_scale = x_scale - x_offs, y_scale - y_offs

                # And then calculate the points
                self.__dict__['setpoint_x'] = linspace(x_offs, x_offs+x_scale*x_len, x_len)
                self.__dict__['setpoint_y'] = linspace(y_offs, y_offs+y_scale*y_len, y_len)
        # Finally reset the scale to make sure we are consistent
        self._force_rescale()

    def _force_rescale(self):
        step_x = (self.setpoint_x[1] - self.setpoint_x[0])/len(setpoint_x)
        step_y = (self.setpoint_y[1] - self.setpoint_y[0])/len(setpoint_y)

        self.resetTransform()
        self.translate(self.setpoint_x[0], self.setpoint_y[0])
        self.scale(step_x, step_y)

    def update(data, *args, **kwargs):
        self.setImage(data, autoLevels=True)

class ImageItemWithHistogram(ImageItem):
    def __init__(self, setpoint_x, setpoint_y, colormap, *args, **kwargs):
        super().__init__(setpoint_x, setpoint_y, *args, **kwargs)

        # Add the histogram
        hist = HistogramLUTItem()
        hist.setImageItem(self._base_inst)
        self.__dict__['hist'] = hist

    @property
    def histogram(self):
        return self._hist

    @property
    def colormap(self):
        #TODO: This is where I LEFT OFF
        pass