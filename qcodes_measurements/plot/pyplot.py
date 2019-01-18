# -*- coding: utf-8 -*-

import sys
from math import ceil
import PyQt5
import re

import pyqtgraph.multiprocess as mp

import numpy as np
from numpy import linspace, ndarray

from qcodes.instrument.parameter import _BaseParameter

from ..plot import ChildProcessImportError, colors

# Get access to module level variables
this = sys.modules[__name__]

#Define some convenient functions
def _set_defaults(rpg):
    """
    Set up the default state of the plot windows. Add any other global config options here.
    """
    rpg.setConfigOption('background', 'w')
    rpg.setConfigOption('foreground', 'k')
    rpg.setConfigOption('leftButtonPan', False)
    rpg.setConfigOption('antialias', True)
    rpg._setProxyOptions(deferGetattr=False)

def _ensure_ndarray(array):
    """
    Ensure the given array is a numpy array. Necessary for some parts of pyqtgraph.
    """
    if array is None:
        return None
    if not isinstance(array, ndarray):
        return np.array(array)
    return array

def _ensure_val(f):
    """
    Decorator to ensure that a result is returned by value rather than as a proxy
    """
    def wrap(*args, **kwargs):
        val = f(*args, **kwargs)
        if isinstance(val, mp.remoteproxy.ObjectProxy):
            return val._getValue()
        return val
    return wrap

def _auto_wrap(f):
    """
    Decorator to ensure values are wrapped by RPGWrappedBase
    """
    def wrap(*args, **kwargs):
        val = f(*args, **kwargs)
        if isinstance(val, (tuple, list)):
            return tuple(RPGWrappedBase.autowrap(item) for item in val)
        else:
            return RPGWrappedBase.autowrap(val)
    return wrap

def _start_remote():
    if len(mp.QtProcess.handlers) == 0:
        proc = mp.QtProcess()
        this.rpg = proc._import('qcodes_measurements.plot.rpyplot')
        _set_defaults(this.rpg)
    else:
        raise ChildProcessImportError(f"Importing pyplot from child process")

def _restart_remote():
    if len(mp.QtProcess.handlers) == 0:
        _start_remote()
    else:
        for pid in mp.QtProcess.handlers:
            try:
                proc = mp.QtProcess.handlers[pid]
                if isinstance(proc, mp.QtProcess):
                    if not proc.exited:
                        mp.QtProcess.handlers[pid].join()
                else:
                    raise ChildProcessImportError(f"Importing pyplot from child process")
            except mp.ClosedError:
                continue
        mp.QtProcess.handlers.clear()
        _start_remote()

# --- ON STARTUP - Create a remote Qt process used for plotting in the background
# Check if a QApplication exists. It will not if we are not running from spyder...
if PyQt5.QtGui.QApplication.instance() is None:
    app = PyQt5.QtGui.QApplication([])
else:
    app = PyQt5.QtGui.QApplication.instance()

_start_remote()

class RPGWrappedBase(mp.remoteproxy.ObjectProxy):
    # Keep track of children so they aren't recomputed each time
    _subclass_types = None

    # Reserve names for local variables, so they aren't proxied.
    _items = None
    _parent = None
    _base_inst = None

    # Cache remote functions, allowing proxy options for each to be set
    _remote_functions = None
    _remote_function_options = None

    def __init__(self, *args, **kwargs):
        self._items = []
        self._parent = None
        self._remote_functions = {}
        self._remote_function_options = {}
        if '_base' in self.__class__.__dict__:
            try:
                base = getattr(this.rpg, self.__class__._base)
                base = base(*args, **kwargs)
            except mp.ClosedError:
                _restart_remote()
                base = getattr(this.rpg, self.__class__._base)
                base = base(*args, **kwargs)
            self._base_inst = base
        else:
            raise TypeError("Base instance not defined. Don't know how to create remote object.")

    def __wrap__(self):
        # We still want to keep track of new items in wrapped objects
        self._items = []
        self._parent = None
        self._remote_functions = {}
        self._remote_function_options = {}
        # And make sure that ndarrays are still proxied

    @classmethod
    def wrap(cls, instance, *args, **kwargs):
        if not isinstance(instance, mp.remoteproxy.ObjectProxy):
            raise TypeError("We can only wrap ObjectProxies")

        # Create an empty instance of RPGWrappedBase,
        # and copy over instance variables
        base_inst = cls.__new__(cls)
        base_inst.__dict__ = {**base_inst.__dict__,
                              **instance.__dict__}
        base_inst._base_inst = instance

        # If we do want to initialize some instance variables, we can do it in
        # the special __wrap__ method
        __wrap__ = getattr(base_inst, '__wrap__', None)
        if __wrap__ is not None:
            __wrap__(*args, **kwargs)

        return base_inst

    @staticmethod
    def autowrap(inst):
        # Figure out the types that we know how to autowrap
        if RPGWrappedBase._subclass_types is None:
            RPGWrappedBase._subclass_types = {}
            def append_subclasses(sc_dict, cls):
                for typ in cls.__subclasses__():
                    append_subclasses(sc_dict, typ)
                    base = getattr(typ, '_base', None)
                    if base is None:
                        continue
                    typestr = base
                    sc_dict[typestr] = typ
            append_subclasses(RPGWrappedBase._subclass_types, RPGWrappedBase)

        # Then, if we have an object proxy, wrap it if it is in the list of wrappable types
        if isinstance(inst, mp.remoteproxy.ObjectProxy):
            if isinstance(inst, RPGWrappedBase):
                return inst
            typestr = inst._typeStr.split()[0].strip('< ').split('.')[-1]
            if typestr in RPGWrappedBase._subclass_types:
                return RPGWrappedBase._subclass_types[typestr].wrap(inst)
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
                if res not in self._items:
                    self._items.append(res)
            if isinstance(res, RPGWrappedBase):
                # If we are a managed object, notify that we were added so that items can keep track
                # of which windows they are in.
                res._notify_added(self)
            return res
        return save

    def wrap_getters(self, f):
        return _auto_wrap(f)

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
        # Check for ipython special methods
        if re.match("_repr_.*_", name):
            raise AttributeError("Ignoring iPython special methods")
        if re.match("_ipython_.*_", name):
            raise AttributeError("Ignoring iPython special methods")

        # Check whether this function has been cached
        if name in self._remote_functions:
            return self._remote_functions[name]

        # Get attribute from object proxy
        attr = getattr(self._base_inst, name)

        # Check if item is a wrappable type
        attr = self.autowrap(attr)

        # Wrap adders and getters
        if name.startswith("add") and callable(attr):
            return self.wrap_adders(attr)
        elif name.startswith("get") and callable(attr):
            return self.wrap_getters(attr)

        # Save a cached copy, if we have a function with specific options
        if callable(attr) and name in self._remote_function_options:
            attr._setProxyOptions(**self._remote_function_options[name])
            self._remote_functions[name] = attr

        return attr

    def __repr__(self):
        return "<%s for %s>" % (self.__class__.__name__, super().__repr__())

    def _notify_added(self, parent):
        # Allow objects to keep track of which window they are in if they need to
        self._parent = parent

    @property
    def items(self):
        return tuple(self._items)

class BasePlotWindow(RPGWrappedBase):
    _base = "GraphicsLayoutWidget"

    def __init__(self, *args, title=None, **kwargs):
        """
        Create a new remote plot window, with title and size given
        """
        super().__init__(*args, **kwargs)
        self.show()

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
        plot = PlotItem(**kargs)
        self.addItem(plot, row, col, rowspan, colspan)
        return plot

    @property
    def windows(self):
        raise NotImplementedError("Can't do this on a base plot window")

    @property
    @_auto_wrap
    def items(self):
        return self.getLayoutItems()

class PlotWindow(BasePlotWindow):
    _base = "ExtendedPlotWindow"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def export(self, fname, export_type="image"):
        return self._base_inst.export(fname, export_type)

    @property
    @_auto_wrap
    def windows(self):
        return self.getWindows()

    @classmethod
    @_auto_wrap
    def getWindows(cls):
        return this.rpg.ExtendedPlotWindow.getWindows()

    @classmethod
    def find_by_id(cls, wid):
        windows = cls.getWindows()
        for window in windows:
            items = window.items
            for item in items:
                if isinstance(item, PlotItem):
                    if item.plot_title.endswith("(id: {})".format(wid)):
                        return window
        return None

class PlotAxis(RPGWrappedBase):
    _base = "AxisItem"

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

class BasePlotItem(RPGWrappedBase):
    _base = "PlotItem"

    def __init__(self, title=None, **kwargs):
        """
        Create a new plot. This has to be embedded inside
        a plot window to actually be visible
        """
        super().__init__(**kwargs)

        # Update title if requested
        if title is not None:
            self.plot_title = title

    def plot(self, *, setpoint_x, setpoint_y=None, data=None, **kwargs):
        """
        Add some plotdata to this plot
        """
        # Create a plot, 1d if we have a single setpoint, 2d if we have 2 setpoints
        if setpoint_y is None:
            plotdata = ExtendedPlotDataItem(setpoint_x, **kwargs)
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
        self._items.append(textbox_item)
        return textbox_item

    def update_axes(self, param_x, param_y,
                    param_x_setpoint=False, param_y_setpoint=False):
        """
        Update axis labels, using param_x, param_y to pull labels
        If left or bottom axis is from an ArrayParameter, set param_x_setpoint
        or param_y_setpoint to true
        """
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


class PlotItem(BasePlotItem):
    _base = "ExtendedPlotItem"

    def export(self, fname, export_type="image"):
        return self._base_inst.export(fname, export_type)

    @property
    @_auto_wrap
    def traces(self):
        return self.listDataItems(proxy=True)

class TextItem(RPGWrappedBase):
    _base = "DraggableTextItem"
    _ANCHORS = {'tl': (0,0),
                'tr': (1,0),
                'bl': (0,1),
                'br': (1,1)}

    def setParentItem(self, p):
        self._base_inst.setParentItem(p)
        if isinstance(p, RPGWrappedBase):
            p._items.append(self)

    def anchor(self, anchor):
        """
        Put this text box in a position relative to
        (tl, tr, bl, br)
        """
        anchor_point = TextItem._ANCHORS[anchor]
        self._base_inst.anchor(itemPos=anchor_point,
                               parentPos=anchor_point,
                               offset=(0,0))

    @property
    def offset(self):
        return self.getOffset()
    @offset.setter
    def offset(self, offs):
        if not isinstance(offs, tuple) or len(offs) != 2:
            raise ValueError("Must be a tuple (x, y)")
        self.setOffset(offs)

    @property
    def text(self):
        text = "".join(self.getText()).replace("<br>", "\n")
        return text
    @text.setter
    def text(self, text):
        # Replace new lines with HTML line breaks
        text = text.replace("\r\n", "\n")
        text = text.replace("\r", "\n")
        text = text.replace("\n", "<br>")
        self.setText(str(text))

class HistogramLUTItem(RPGWrappedBase):
    _base = "HistogramLUTItem"

    # Reserve names of local variables
    _cmap = None

    def __init__(self, *args, allowAdd=False, **kwargs):
        super().__init__(*args, **kwargs)
        self._cmap = None
        self.allowAdd = allowAdd
        self._remote_function_options['setLevels'] = {'callSync': 'off'}
        self._remote_function_options['imageChanged'] = {'callSync': 'off'}
    def __wrap__(self, *args, **kwargs):
        super().__wrap__(*args, **kwargs)
        self._remote_function_options['setLevels'] = {'callSync': 'off'}
        self._remote_function_options['imageChanged'] = {'callSync': 'off'}

    @property
    @_auto_wrap
    def axis(self):
        return self._base_inst.axis

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
    _base = "ColorMap"
    _all_colors = {}

    # Reserve names of local variables
    _name = None

    def __init__(self, name, pos, color, *args, **kwargs):
        self._name = None
        super().__init__(pos, color, *args, **kwargs)

        # Keep track of all color maps, and add them to the list of available colormaps
        ColorMap._all_colors[name] = self
        # And add each of our colors to the new list
        remote_list = self.get_remote_list()
        remote_list[name] = {
            'ticks': list(zip(pos, (tuple(int(y*255) for y in x) + (255,) for x in color))),
            'mode': 'rgb'
        }

    @classmethod
    def get_remote_list(cls):
        remote_list = this.rpg.graphicsItems.GradientEditorItem.__getattr__('Gradients',
                                                                    _returnType="proxy")
        return remote_list

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
# maps = ColorMap.get_remote_list()._getValue()
ColorMap.get_remote_list().clear()
for color, cdata in colors.__data__.items():
    step = ceil(len(cdata) / 16)
    rcmap = ColorMap(name=color,
                     pos=linspace(0.0, 1.0, len(cdata[::step])),
                     color=cdata[::step])
    if color == 'viridis':
        rcmap = ColorMap(name=color+"_nlin",
                         pos=[0] + list(1/(x**1.5) for x in range(15, 0, -1)),
                         color=cdata[::step])
    del cdata, step, rcmap, color
rcmap = ColorMap.get_color_map('viridis')

class LegendItem(RPGWrappedBase):
    """
    Legend handling code
    """
    _base = "LegendItem"

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
    _base = "PlotDataItem"

    def __init__(self, setpoint_x=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.xData = _ensure_ndarray(setpoint_x)
        self._remote_function_options['setData'] = {'callSync': 'off'}

    def __wrap__(self, *args, **kwargs):
        super().__wrap__(*args, **kwargs)
        self._remote_function_options['setData'] = {'callSync': 'off'}
        if 'setpoint_x' in kwargs:
            # If we know what our setpoints are, use them
            self.xData = kwargs['setpoint_x']

    def update(self, data, *args, **kwargs):
        self.setData(x=self.setpoint_x, y=_ensure_ndarray(data), *args, **kwargs)

    @property
    def setpoint_x(self):
        return self.xData
    @property
    @_ensure_val
    def xData(self):
        return self._base_inst.xData
    @xData.setter
    def xData(self, val):
        self._base_inst.xData = _ensure_ndarray(val)
    @property
    @_ensure_val
    def yData(self):
        return self._base_inst.yData
    @property
    def data(self):
        """
        Return the data underlying this trace
        """
        return (self.xData, self.yData)

class ExtendedPlotDataItem(PlotDataItem):
    _base = "ExtendedPlotDataItem"

    def update(self, data, *args, **kwargs):
        self._base_inst.update(data)

class ImageItem(PlotData):
    _base = "ImageItem"

    def __init__(self, setpoint_x, setpoint_y, *args, colormap=None, **kwargs):
        super().__init__(setpoint_x, setpoint_y, *args, colormap=colormap, **kwargs)
        setpoint_x = _ensure_ndarray(setpoint_x)
        setpoint_y = _ensure_ndarray(setpoint_y)
        self._remote_function_options['setImage'] = {'callSync': 'off'}
        # Set axis scales correctly
        self._force_rescale(setpoint_x, setpoint_y)

        if colormap is not None:
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
        self.setImage(_ensure_ndarray(data), autoDownsample=True)

    @property
    @_ensure_val
    def image(self):
        """
        Return the data underlying this trace
        """
        return self._base_inst.image
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
        super().__init__(setpoint_x, setpoint_y, *args, colormap, **kwargs)
        self.setpoint_x = setpoint_x
        self.setpoint_y = setpoint_y

    def _force_rescale(self, setpoint_x, setpoint_y):
        """
        This is handled on the server side...
        """
        pass

    @property
    @_ensure_val
    def setpoint_x(self):
        return self._base_inst.setpoint_x
    @setpoint_x.setter
    def setpoint_x(self, val):
        self._base_inst.setpoint_x = val

    @property
    @_ensure_val
    def setpoint_y(self):
        return self._base_inst.setpoint_y
    @setpoint_y.setter
    def setpoint_y(self, val):
        self._base_inst.setpoint_y = val

class ImageItemWithHistogram(ExtendedImageItem):
    _base = "ImageItemWithHistogram"

    # Local Variables
    _histogram = None

    def __init__(self, setpoint_x, setpoint_y, colormap=rcmap, *args, **kwargs):
        super().__init__(setpoint_x, setpoint_y, *args, **kwargs)
        self._histogram = None

        # Set colormap
        if colormap is not None:
            self.colormap = colormap

    def __wrap__(self, *args, **kwargs):
        super().__wrap__(*args, **kwargs)
        self._histogram = None

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
            z_range = (np.min(data), np.max(data))
            self.histogram.imageChanged()
            self.histogram.setLevels(*z_range)

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
        if self._histogram is None:
            self._histogram = self.getHistogramLUTItem()
        return self._histogram

    @property
    def colormap(self):
        return self.histogram.colormap
    @colormap.setter
    def colormap(self, cmap):
        self.histogram.colormap = cmap

class TableWidget(RPGWrappedBase):
    """
    Table
    """
    _base = "TableWidget"