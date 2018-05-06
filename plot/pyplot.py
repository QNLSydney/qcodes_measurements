# -*- coding: utf-8 -*-

from math import ceil
import warnings

import pyqtgraph as pg
import pyqtgraph.multiprocess as mp

from numpy import linspace, min, max, ndarray

from qcodes.instrument.parameter import _BaseParameter

from ..plot import colors

proc = mp.QtProcess()
rpg = proc._import('pyqtgraph')
rpg.setConfigOptions(antialias=True)
windows = []

class RPGWrappedBase(mp.remoteproxy.ObjectProxy):
    def __init__(self, *args, **kwargs):
        self.__dict__['_items'] = []
        self.__dict__['_parent'] = None
        if '_base' in self.__class__.__dict__:
            base = self.__class__.__dict__['_base'](*args, **kwargs)
            self.__dict__['_base_inst'] = base
            self.__dict__ = {**base.__dict__, **self.__dict__}
        self.append_no_proxy_types(ndarray)
            
    def __wrap__(self):
        # We still want to keep track of new items in wrapped objects
        self.__dict__['_items'] = []
        self.__dict__['_parent'] = None

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
    def autowrap(inst):
        # Figure out the types that we know how to autowrap
        subclass_types = {}
        for t in RPGWrappedBase.__subclasses__():
            base = getattr(t, '_base', None)
            if base is None:
                continue
            typestr = base._typeStr.split()[1].strip('\'>').split('.')[-1]
            subclass_types[typestr] = t

        # Then, if we have an object proxy, wrap it if it is in the list of wrappable types
        if isinstance(inst, mp.remoteproxy.ObjectProxy):
            if isinstance(inst, RPGWrappedBase):
                return inst
            typestr = inst._typeStr.split()[0].strip('< ').split('.')[-1]
            if typestr in subclass_types:
                return subclass_types[typestr].wrap(inst)
        # Otherwise, just return the bare instance
        return inst

    def wrap_adders(self, f):
        def save(*args, **kwargs):
            res = f(*args, **kwargs)
            # If the adder doesn't return the newly added item, we can assume the added item
            # was passed as the first non-keyword argument
            if res is None:
                if len(args) > 0:
                    res = args[0]
                else:
                    return
            # Try to translate to one of the friendly names
            res = RPGWrappedBase.autowrap(res)
            # Add the result to the items list if returned
            if isinstance(res, mp.remoteproxy.ObjectProxy):
                # Keep track of all objects that are added to a window, since
                # we can't get them back from the remote later
                self._items.append(res)
            if isinstance(res, RPGWrappedBase):
                # If we are a managed object, notify that we were added so that items can keep track
                # of which windows they are in.
                res._notify_added(self)
            return res
        return save

    def wrap_getters(self, f):
        def wrapper(*args, **kwargs):
            res = f(*args, **kwargs)
            return RPGWrappedBase.autowrap(res)
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
            return self.wrap_adders(attr)
        elif name.startswith("get") and callable(attr):
            return self.wrap_getters(attr)

        return attr

    def __repr__(self):
        return "<%s for %s >" % (self.__class__.__name__, super().__repr__())

    def _notify_added(self, parent):
        # Allow objects to keep track of which window they are in if they need to
        self._parent = parent


    def append_no_proxy_types(self, new_type):
        if not isinstance(new_type, type):
            raise TypeError('New no proxy type must be a type')
        noProxyTypes = self._getProxyOption('noProxyTypes')
        noProxyTypes.append(new_type)
        self._setProxyOptions(noProxyTypes=noProxyTypes[:])

    @property
    def items(self):
        return self._items[:]

class PlotWindow(RPGWrappedBase):
    _base = rpg.GraphicsLayoutWidget
    _windows = []

    def __init__(self, *args, **kwargs):
        """
        Create a new remote plot window, with title and size given
        """
        super().__init__(*args, **kwargs)
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
    @units.setter
    def units(self, units):
        self.setLabel(units=units)

class PlotItem(RPGWrappedBase):
    _base = rpg.PlotItem

    def __init__(self, plot_title=None):
        """
        Create a new plot. This has to be embedded inside 
        a plot window to actually be visible
        """
        super().__init__()
        # Keep track of traces that are plotted
        self.__dict__['_traces'] = []
    
    def __wrap__(self):
        super().__wrap__()
        # Keep track of traces that are plotted
        self.__dict__['_traces'] = []

    def plot(self, setpoint_x, setpoint_y=None, data=None, **kwargs):
        """
        Add some plotdata to this plot
        """
        # Create a plot, 1d if we have a single setpoint, 2d if we have 2 setpoints
        if setpoint_y is None:
            plotdata = PlotDataItem(setpoint_x, **kwargs)
        else:
            plotdata = ImageItemWithHistogram(setpoint_x, setpoint_y, **kwargs)
        self.addItem(plotdata)

        # if there is data, plot it
        if data is not None:
            plotdata.update(data)

        return plotdata

    def wrap_adders(self, f):
        # Wrap to save into items first
        f = super().wrap_adders(f)
        def save(*args, **kwargs):
            res = f(*args, **kwargs)
            # If we add a PlotData object, add it to traces too
            if isinstance(res, PlotData):
                self._traces.append(res)
            return res
        return save

    def update_axes(self, param_x, param_y,
                    param_x_setpoint=False, param_y_setpoint=False):
        """
        Update axis labels, using param_x, param_y to pull labels
        If left or bottom axis is from an ArrayParameter, set param_x_setpoint
        or param_y_setpoint to true
        """
        print(param_x, param_y)
        if not isinstance(param_x, _BaseParameter):
            raise TypeError("param_x must be a qcodes parameter")
        if not isinstance(param_y, _BaseParameter):
            raise TypeError("param_y must be a qcodes parameter")

        if param_x_setpoint:
            self.bot_axis.label = param_x.setpoint_labels[0]
            self.bot_axis.units = param_x.setpoint_units[0]
        else:
            self.bot_axis.label = param_x.label
            self.bot_axis.units = param_x.unit
        if param_y_setpoint:
            self.left_axis.label = param_y.setpoint_labels[0]
            self.left_axis.units = param_y.setpoint_units[0]
        else:
            self.left_axis.label = param_y.label
            self.left_axis.units = param_y.unit

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

class HistogramLUTItem(RPGWrappedBase):
    _base = rpg.HistogramLUTItem

    def __init__(self, *args, allowAdd=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.__dict__['_cmap'] = None
        self.allowAdd = allowAdd

    @property
    def axis(self):
        return RPGWrappedBase.autowrap(super().__getattr__('axis'))

    @property
    def allowAdd(self):
        return self.gradient.allowAdd
    @allowAdd.setter
    def allowAdd(self, val):
        self.gradient.allowAdd = bool(val)

    @property
    def colormap(self):
        if self._cmap is None:
            return None
        return self._cmap._getValue()
    @colormap.setter
    def colormap(self, cmap):
        if not isinstance(cmap, ColorMap):
            raise TypeError("cmap must be a color map")
        self.gradient.setColorMap(cmap._base_inst)
        self._cmap = cmap

class ColorMap(RPGWrappedBase):
    _base = rpg.ColorMap
    _all_colors = {}
    _remote_list = rpg.graphicsItems.GradientEditorItem.__getattr__('Gradients', 
                                                                    _returnType="proxy")

    def __init__(self, name, pos, color, *args, **kwargs):
        self.__dict__['_name'] = name
        super().__init__(pos, color, *args, **kwargs)

        # Keep track of all color maps, and add them to the list of available colormaps
        ColorMap._all_colors[name] = self
        # And add each of our colors to the new list
        ColorMap._remote_list[name] = {
            'ticks': list(zip(pos, (tuple(int(y*255) for y in x) + (255,) for x in color))),
            'mode': 'rgb'
        }


    @classmethod
    def get_color_map(cls, name):
        return cls._all_colors[name]
    @classmethod
    def color_maps(cls):
        return cls._all_colors

    @property
    def name(self):
        return self._name
    
## Transfer color scales to remote process, truncating steps to 16 if necessary
maps = ColorMap._remote_list._getValue()
ColorMap._remote_list.clear()
# for name, vals in maps.items():
#     data = vals['ticks']
#     mode = vals['mode']
#     rcmap = ColorMap(name=name,
#                      pos=tuple(x[0] for x in data),
#                      color=tuple(x[1] for x in data))
for color in colors.__data__.keys():
   data = colors.__data__[color]
   step = ceil(len(data) / 16)
   rcmap = ColorMap(name=color, 
                    pos=linspace(0.0, 1.0, len(data[::step])), 
                    color=data[::step])
rcmap = ColorMap.get_color_map('viridis')

class PlotData(RPGWrappedBase):
    """
    Base class for trace-like objects (1d or 2d plots)
    """
    def update(self, data, *args, **kwargs):
        """
        Define a common way of updating plots for 1D and 2D plots
        """
        raise NotImplementedError("Can't update this")

    @property
    def data(self):
        """
        Return the data underlying this plot
        """
        raise NotImplementedError("This should be implemented by the actual plot item")

class PlotDataItem(PlotData):
    _base = rpg.PlotDataItem

    def __init__(self, setpoint_x=None, *args, **kwargs):
        self.__dict__['setpoint_x'] = setpoint_x
        self.__dict__['set_data'] = None
        super().__init__(*args, **kwargs)

    def __wrap__(self, *args, **kwargs):
        super().__wrap__(*args, **kwargs)
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

    def update(self, data, *args, **kwargs):
        if self.set_data is None:
            # Cache update function so we don't have to request it each time we update
            self.set_data = self.setData
            self.set_data._setProxyOptions(callSync='off')
        self.set_data(x=self.setpoint_x, y=data, *args, **kwargs)

    @property
    def data(self):
        """
        Return the data underlying this trace
        """
        return (self.xData, self.yData)

class ImageItem(PlotData):
    _base = rpg.ImageItem

    def __init__(self, setpoint_x, setpoint_y, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.__dict__['setpoint_x'] = setpoint_x
        self.__dict__['setpoint_y'] = setpoint_y
        self.__dict__['set_image'] = None
        # Set axis scales correctly
        self._force_rescale()

    def __wrap__(self, *args, **kwargs):
        super().__wrap__(*args, **kwargs)
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
                # There is no data here, let's just set some scaling to the identity
                self.__dict__['setpoint_x'] = (0, 1)
                self.__dict__['setpoint_y'] = (0, 1)
            else:
                image = image._getValue()
                x_len, y_len = image.shape

                # And then query the translation
                offs = self.scenePos()._getValue()
                x_offs, y_offs = offs.x(), offs.y()

                # And then the scaling
                x_scale, y_scale = self.sceneTransform().map(1.0, 1.0)
                x_scale, y_scale = x_scale - x_offs, y_scale - y_offs

                # And then calculate the points
                self.__dict__['setpoint_x'] = linspace(x_offs, x_offs+x_scale*x_len, x_len)
                self.__dict__['setpoint_y'] = linspace(y_offs, y_offs+y_scale*y_len, y_len)
        # Finally reset the scale to make sure we are consistent
        self._force_rescale()

    def _force_rescale(self):
        step_x = (self.setpoint_x[-1] - self.setpoint_x[0])/len(self.setpoint_x)
        step_y = (self.setpoint_y[-1] - self.setpoint_y[0])/len(self.setpoint_y)

        self.resetTransform()
        self.translate(self.setpoint_x[0], self.setpoint_y[0])
        self.scale(step_x, step_y)

    def update(self, data, *args, **kwargs):
        if self.set_image is None:
            # Cache update function so we don't have to request it each time we update
            self.set_image = self.setImage
            self.set_image._setProxyOptions(callSync='off')
        self.set_image(data, autoLevels=True)

    @property
    def data(self):
        """
        Return the data underlying this trace
        """
        return self.image

class ImageItemWithHistogram(ImageItem):
    _base = rpg.ImageItem

    def __init__(self, setpoint_x, setpoint_y, colormap=rcmap, *args, **kwargs):
        super().__init__(setpoint_x, setpoint_y, *args, **kwargs)
        # Add instance variables
        self.__dict__['_hist'] = None
        self.__dict__['set_levels'] = None

        # Add the histogram
        self._hist = HistogramLUTItem()
        self._hist.setImageItem(self._base_inst)
        self._hist.colormap = colormap
        self._hist.autoHistogramRange() # enable autoscaling

    def _notify_added(self, parent):
        """
        We need to keep track of out parent window, since histograms exist
        outside of the ImageItem view
        Each time we are added to a window, add the histogram item as well

        TODO: Should this be wrapped up into a new gridlayout? That would allow us
        to not keep track of parent items?
        """
        super()._notify_added(parent)
        if self._hist is not None:
            parent._parent.addItem(self._hist)

    def update(self, data, *args, **kwargs):
        super().update(data, *args, **kwargs)
        z_range = (min(data), max(data))
        if self.set_levels is None:
            # Cache update function so we don't have to request it each time we update
            self.set_levels = self.histogram.setLevels
            self.set_levels._setProxyOptions(callSync='off')
        self.set_levels(*z_range)

    def update_histogram_axis(self, param_z):
        """
        Update histogram axis labels
        """
        if not isinstance(param_z, _BaseParameter):
            raise TypeError("param_z must be a qcodes parameter")
        self.histogram.axis.label = param_z.label
        self.histogram.axis.units = param_z.unit

    @property
    def histogram(self):
        return self._hist

    @property
    def colormap(self):
        return self._hist.colormap
    @colormap.setter
    def colormap(self, cmap):
        self._hist.colormap = cmap