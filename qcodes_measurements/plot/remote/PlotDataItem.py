import numpy as np
from pyqtgraph import PlotDataItem, mkColor
from PySide6 import QtGui, QtWidgets

from ...logging import get_logger
from .DataItem import ExtendedDataItem

logger = get_logger("PlotDataItem")


class ExtendedPlotDataItem(ExtendedDataItem, PlotDataItem):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.menu = None

        # Plot color selection dialog
        self.colorDialog = QtWidgets.QColorDialog()
        self.colorDialog.setOption(QtWidgets.QColorDialog.ColorDialogOption.ShowAlphaChannel, True)
        self.colorDialog.setOption(
            QtWidgets.QColorDialog.ColorDialogOption.DontUseNativeDialog, True
        )
        self.colorDialog.colorSelected.connect(self.colorSelected)

        # Store x-setpoint, since we may add nan values back in
        self.setpoint_x = np.asarray(self.xData) if self.xData is not None else np.empty(0)

    def getContextMenus(self, event=None, *, rect=None):
        if self.menu is None:
            self.menu = QtWidgets.QMenu()

            qaction = QtGui.QAction("Select Color", self.menu)
            qaction.triggered.connect(self.selectColor)
            self.menu.addAction(qaction)

            qaction = QtGui.QAction("Remove Item", self.menu)
            qaction.triggered.connect(self._remove_from_plot)
            self.menu.addAction(qaction)

        if self.name() is not None:
            self.menu.setTitle(str(self.name()))
        else:
            self.menu.setTitle("Trace")
        return self.menu

    def _remove_from_plot(self):
        view = self.getViewBox()
        if view is not None:
            view.removePlotItem(self)

    def selectColor(self):
        color = self.opts["pen"]
        if isinstance(color, QtGui.QPen):
            color = color.color()  # pylint: disable=no-member
        elif not isinstance(color, QtGui.QColor):
            color = mkColor(color)
        self.colorDialog.setCurrentColor(color)
        self.colorDialog.open()

    def colorSelected(self, color):
        self.setPen(color)

    def setTraceData(self, x, y, *args, **kwargs):
        """Keep the original setpoints and replace the trace in a single request."""
        if not self._updates_enabled():
            return
        self.setpoint_x = x
        self.setData(x, y, *args, **kwargs)

    def update(self, yData, *args, **kwargs):
        if not self._updates_enabled():
            return
        # Filter out nan values, due to https://github.com/pyqtgraph/pyqtgraph/issues/1057
        if not isinstance(yData, np.ndarray):
            yData = np.array(yData)
        notnan = ~(np.isnan(self.setpoint_x) | np.isnan(yData))
        xData = self.setpoint_x[notnan]
        yData = yData[notnan]

        # If connect is explicitly specified, use that, otherwise calculate
        # the necessary connections
        connect = kwargs.pop("connect", None)
        if connect is None:
            # Figure out which points are separated by nan values
            nanind = np.where(~notnan)[0]
            dontconnect = nanind + np.cumsum(np.full_like(nanind, -1))
            connect = np.ones_like(xData, dtype=np.int32)
            dontconnect = dontconnect[(dontconnect >= 0) & (dontconnect < connect.size)]
            connect[dontconnect] = 0

        # Update data
        self.setData(*args, x=xData, y=yData, connect=connect, **kwargs)

    def setName(self, name):
        self.opts["name"] = str(name)
