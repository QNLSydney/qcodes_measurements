# -*- coding: utf-8 -*-

from math import ceil
import warnings
import re
import PyQt5

import pyqtgraph as pg
import pyqtgraph.multiprocess as mp

import numpy as np
from numpy import linspace, min, max, ndarray

from qcodes.instrument.parameter import _BaseParameter

from ..plot import ChildProcessImportError, colors

#Define some convenient functions
def _set_defaults(rpg):
    """
    Set up the default state of the plot windows. Add any other global config options here.
    """
    rpg.setConfigOption('background', 'w')
    rpg.setConfigOption('foreground', 'k')
    rpg.setConfigOption('leftButtonPan', False)
    rpg._setProxyOptions(deferGetattr=True)

def _ensure_ndarray(array):
    """
    Ensure the given array is a numpy array. Necessary for some parts of pyqtgraph.
    """
    if array is None:
        return None
    if not isinstance(array, ndarray):
        return np.array(array)
    return array

# --- ON STARTUP - Create a remote Qt process used for plotting in the background
# Check if a QApplication exists. It will not if we are not running from spyder...
if PyQt5.QtGui.QApplication.instance() is None:
    app = PyQt5.QtGui.QApplication([])
else:
    app = PyQt5.QtGui.QApplication.instance()

if len(mp.QtProcess.handlers) == 0:
    proc = mp.QtProcess()
    rpg = proc._import('qcodes_measurements.plot.rpyplot')
    _set_defaults(rpg)
else:
    proc = next(iter(mp.QtProcess.handlers.values()))
    # Check whether it is closed
    if isinstance(proc, mp.QtProcess):
        try:
            rpg = proc._import('qcodes_measurements.plot.rpyplot')
        except mp.ClosedError:
            mp.QtProcess.handlers.clear()
            proc = mp.QtProcess()
            rpg = proc._import('qcodes_measurements.plot.rpyplot')
            _set_defaults(rpg)
    else:
        raise ChildProcessImportError("Importing pyplot from child process")
    
class RPGWrappedBase(mp.remoteproxy.ObjectProxy):
    # Reserve names for local variables, so they aren't proxied.
    _items = None
    _parent = None
    _base_inst = None

    def __init__(self, *args, **kwargs):
        self._items = []
        self._parent = None
        if '_base' in self.__class__.__dict__:
            base = self.__class__.__dict__['_base'](*args, **kwargs)
            self._base_inst = base
        else:
            raise TypeError("Base instance not defined. Don't know how to create remote object.")
        self.append_no_proxy_types(ndarray)
            
    def __wrap__(self):
        # We still want to keep track of new items in wrapped objects
        self._items = []
        self._parent = None
        # And make sure that ndarrays are still proxied
        self.append_no_proxy_types(ndarray)

    @classmethod
    def wrap(cls, instance, *args, **kwargs):
        if not isinstance(instance, mp.remoteproxy.ObjectProxy):
            raise TypeError("We can only wrap ObjectProxies")

        # Create an empty instance of RPGWrappedBase,
        # and copy over instance variables
        base_inst = cls.__new__(cls)
        base_inst.__dict__ = {**base_inst.__dict__, 
                              **instance.__dict__}
        self._base_inst = instance

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
        def append_subclasses(d, cls):
            for t in cls.__subclasses__():
                append_subclasses(d, t)
                base = getattr(t, '_base', None)
                if base is None:
                    continue
                typestr = base._typeStr.split()[1].strip('\'>').split('.')[-1]
                d[typestr] = t
        append_subclasses(subclass_types, RPGWrappedBase)

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
        for cls in self.__class__.__mro__:
            if name in cls.__dict__:
                return object.__setattr__(self, name, value)
        if name in self.__dict__:
            object.__setattr__(self, name, value)
        elif name == "__dict__":
            object.__setattr__(self, name, value)
        else:
            super().__setattr__(name, value)

    def __getattr__(self, name):
        attr = getattr(self._base_inst, name)
            
        if name.startswith("add") and callable(attr):
            print("Attribute adder")
            return self.wrap_adders(attr)
        elif name.startswith("get") and callable(attr):
            return self.wrap_getters(attr)

        return attr

    def __repr__(self):
        return "<%s for %s>" % (self.__class__.__name__, super().__repr__())

    def _notify_added(self, parent):
        # Allow objects to keep track of which window they are in if they need to
        self._parent = parent

    def append_no_proxy_types(self, new_type):
        if not isinstance(new_type, type):
            raise TypeError('New no proxy type must be a type')
        noProxyTypes = self._getProxyOption('noProxyTypes')
        if new_type not in noProxyTypes:
            noProxyTypes.append(new_type)
        self._setProxyOptions(noProxyTypes=noProxyTypes[:])

    @property
    def items(self):
        return self._items[:]

class BasePlotWindow(RPGWrappedBase):
    _base = rpg.GraphicsLayoutWidget

    def __init__(self, title=None, *args, **kwargs):
        """
        Create a new remote plot window, with title and size given
        """
        super().__init__(*args, **kwargs)
        self.show()

        # Set plot defaults
        # Background white
        self.setBackground((255,255,255))

        # Change plot title if given
        if title is not None:
            self.win_title = title

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

    def addPlot(self, row=None, col=None, rowspan=1, colspan=1, **kargs):
        """
        Create a PlotItem and place it in the next available cell (or in the cell specified)
        All extra keyword arguments are passed to :func:`PlotItem.__init__ <pyqtgraph.PlotItem.__init__>`
        Returns the created item.
        """
        plot = ExtendedPlotItem(**kargs)
        self.addItem(plot, row, col, rowspan, colspan)
        return plot

    @property
    def windows(self):
        raise NotImplementedError("Can't do this on a base plot window")

    @property
    def items(self):
        items = self.getLayoutItems()
        items = [RPGWrappedBase.autowrap(item) for item in items]
        return items

class PlotWindow(BasePlotWindow):
    _base = rpg.ExtendedPlotWindow

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def export(self, fname, export_type="image"):
        return super().__getattr__("export")(fname, export_type)

    @property
    def windows(self):
        open_windows = super().__getattr__("getWindows")()
        open_windows = [RPGWrappedBase.autowrap(item) for item in open_windows]
        return open_windows

    @classmethod
    def getWindows(cls):
        open_windows = rpg.ExtendedPlotWindow.getWindows()
        open_windows = [RPGWrappedBase.autowrap(item) for item in open_windows]
        return open_windows

    @classmethod
    def find_by_id(cls, id):
        windows = cls.getWindows()
        for window in windows:
            items = window.items
            for item in items:
                if isinstance(item, PlotItem):
                    if item.plot_title.endswith("(id: {})".format(id)):
                        return window
        return None

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

    def __init__(self, title=None):
        """
        Create a new plot. This has to be embedded inside 
        a plot window to actually be visible
        """
        super().__init__()
        
        # Update title if requested
        if title is not None:
            self.plot_title = title

    def plot(self, *, setpoint_x, setpoint_y=None, data=None, **kwargs):
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

    def textbox(self, text):
        """
        Add a text box to this plot
        """
        textbox_item = TextItem(text=str(text))
        textbox_item.setParentItem(self)
        return textbox_item

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
        raise NotImplementedError("Can't get a list of traces from a non-extended plot_item")
    

class ExtendedPlotItem(PlotItem):
    _base = rpg.ExtendedPlotItem

    def export(self, fname, export_type="image"):
        return super().__getattr__("export")(fname, export_type)

    @property
    def traces(self):
        data_items = self.listDataItems()
        data_items = [RPGWrappedBase.autowrap(item) for item in data_items]
        return data_items

class TextItem(RPGWrappedBase):
    _base = rpg.DraggableTextItem
    _ANCHORS = {'tl': (0,0),
                'tr': (1,0),
                'bl': (0,1),
                'br': (1,1)}

    def setParentItem(self, p):
        super().__getattr__("setParentItem")(p)
        if isinstance(p, RPGWrappedBase):
            p._items.append(self)

    def anchor(self, anchor):
        """
        Put this text box in a position relative to
        (tl, tr, bl, br)
        """
        anchor_point = TextItem._ANCHORS[anchor]
        super().__getattr__("anchor")(itemPos=anchor_point,
                                      parentPos=anchor_point,
                                      offset=(0,0))

    @property
    def offset(self):
        pos = self.getOffset()
        return pos
    @offset.setter
    def offset(self, offs):
        if not isinstance(offs, tuple) or len(offs) != 2:
            raise ValueError("Must be a tuple (x, y)")
        self.setOffset(offs)

    @property
    def text(self):
        text = self.getText().replace("<br>", "\n")
        return text
    @text.setter
    def text(self, text):
        # Replace new lines with HTML line breaks
        text = text.replace("\r\n", "\n")
        text = text.replace("\r", "\n")
        text = text.replace("\n", "<br>")
        self.setText(str(text))

class HistogramLUTItem(RPGWrappedBase):
    _base = rpg.HistogramLUTItem

    # Reserve names of local variables
    _cmap = None

    def __init__(self, *args, allowAdd=False, **kwargs):
        super().__init__(*args, **kwargs)
        self._cmap = None
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

    # Reserve names of local variables
    _name = None

    def __init__(self, name, pos, color, *args, **kwargs):
        self._name = None
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
for color in colors.__data__.keys():
    data = colors.__data__[color]
    step = ceil(len(data) / 16)
    rcmap = ColorMap(name=color, 
                     pos=linspace(0.0, 1.0, len(data[::step])), 
                     color=data[::step])
    if color == 'viridis':
        rcmap = ColorMap(name=color+"_nlin",
                         pos=[0] + list(1/(x**1.5) for x in range(15,0,-1)),
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

    # Reserve names of local variables
    setpoint_x = None
    set_data = None

    def __init__(self, setpoint_x=None, *args, **kwargs):
        self.setpoint_x = _ensure_ndarray(setpoint_x)
        self.set_data = None
        super().__init__(*args, **kwargs)

    def __wrap__(self, *args, **kwargs):
        super().__wrap__(*args, **kwargs)
        if 'setpoint_x' in kwargs:
            # If we know what our setpoints are, use them
            self.setpoint_x = kwargs['setpoint_x']
        else:
            try:
                # Otherwise try and extract them from the existing data
                xData = self.xData
                if isinstance(xData, mp.remoteproxy.ObjectProxy):
                    self.setpoint_x = xData._getValue()
                else:
                    self.setpoint_x = xData
            except AttributeError:
                self.setpoint_x = None

    def update(self, data, *args, **kwargs):
        if self.set_data is None:
            # Cache update function so we don't have to request it each time we update
            self.set_data = self.setData
            self.set_data._setProxyOptions(callSync='off')
        self.set_data(x=self.setpoint_x, y=_ensure_ndarray(data), *args, **kwargs)

    @property
    def data(self):
        """
        Return the data underlying this trace
        """
        return (self.xData, self.yData)

class ImageItem(PlotData):
    _base = rpg.ImageItem

    # Reserve names of local variables
    setpoint_x = None
    setpoint_y = None
    set_image = None

    def __init__(self, setpoint_x, setpoint_y, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setpoint_x = _ensure_ndarray(setpoint_x)
        self.setpoint_y = _ensure_ndarray(setpoint_y)
        self.set_image = None
        # Set axis scales correctly
        self._force_rescale()

    def __wrap__(self, *args, **kwargs):
        super().__wrap__(*args, **kwargs)

        self.set_image = None

        if 'setpoint_x' in kwargs and 'setpoint_y' in kwargs:
            # If we are given the scalings, use them
            self.setpoint_x = kwargs['setpoint_x']
            self.setpoint_y = kwargs['setpoint_y']
        elif 'setpoint_x' in kwargs or 'setpoint_y' in kwargs:
            # If we are only given one, that must be an error
            raise TypeError('setpoint_x or _y given without the other. Both or neither are necessary')
        else:
            # Otherwise we just don't know....
            return
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
        #assert(data.shape == (self.setpoint_y.shape[0], self.setpoint_x.shape[0]))

        self.set_image(_ensure_ndarray(data), autoDownsample=True)

    @property
    def data(self):
        """
        Return the data underlying this trace
        """
        return self.image

class ImageItemWithHistogram(ImageItem):
    _base = rpg.ImageItemWithHistogram

    # Reserve names of local variables
    update_histogram = None
    set_levels = None

    def __init__(self, setpoint_x, setpoint_y, colormap=rcmap, *args, **kwargs):
        super().__init__(setpoint_x, setpoint_y, *args, colormap=colormap, **kwargs)
        # Add instance variables
        self.set_levels = None
        self.update_histogram = None

    def pause_update(self):
        """
        Pause histogram autoupdates while a sweep is running.
        """
        try:
            self._base_inst.sigImageChanged.disconnect()
        except:
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
            z_range = (min(data), max(data))
            if self.set_levels is None:
                # Cache update function so we don't have to request it each time we update
                self.set_levels = self.histogram.setLevels
                self.set_levels._setProxyOptions(callSync='off')
            if self.update_histogram is None:
                self.update_histogram = self.histogram.imageChanged
                self.update_histogram._setProxyOptions(callSync='off')
            self.update_histogram()
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
        return self.getHistogramLUTItem()

    @property
    def colormap(self):
        return self.histogram.colormap
    @colormap.setter
    def colormap(self, cmap):
        self.histogram.colormap = cmap