from qcodes.instrument.parameter import _BaseParameter

from .RemoteProcessWrapper import RPGWrappedBase
from .PlotDataItem import ExtendedPlotDataItem
from .ImageItem import ImageItemWithHistogram
from .UIItems import TextItem

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
        return textbox_item

    def update_axes(self, param_x, param_y,
                    param_x_setpoint=False, param_y_setpoint=False):
        """
        Update axis labels, using param_x, param_y to pull labels
        If left or bottom axis is from an ArrayParameter, set param_x_setpoint
        or param_y_setpoint to true
        """
        if not isinstance(param_x, _BaseParameter):
            raise TypeError(f"param_x must be a qcodes parameter. Got {type(param_x)}.")
        if not isinstance(param_y, _BaseParameter):
            raise TypeError(f"param_y must be a qcodes parameter. Got {type(param_y)}")

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
    def traces(self):
        return self.listDataItems(_returnType="proxy")

    @property
    def items(self):
        return self.__getattr__("items", _returnType="proxy", _location="remote")