from functools import partial

import numpy as np
from PyQt5 import QtGui, QtWidgets

from pyqtgraph import PlotDataItem, mkColor

from .DataItem import ExtendedDataItem
from ...logging import get_logger
logger = get_logger("PlotDataItem")

class ExtendedPlotDataItem(ExtendedDataItem, PlotDataItem):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.menu = None

        # Plot color selection dialog
        self.colorDialog = QtWidgets.QColorDialog()
        self.colorDialog.setOption(QtWidgets.QColorDialog.ShowAlphaChannel, True)
        self.colorDialog.setOption(QtWidgets.QColorDialog.DontUseNativeDialog, True)
        self.colorDialog.colorSelected.connect(self.colorSelected)

        # Store x-setpoint, since we may add nan values back in
        self.setpoint_x = tuple()

    def getContextMenus(self, *, rect=None, event=None):
        if self.menu is None:
            self.menu = QtWidgets.QMenu()

            qaction = QtWidgets.QAction("Select Color", self.menu)
            qaction.triggered.connect(self.selectColor)
            self.menu.addAction(qaction)

            qaction = QtWidgets.QAction("Remove Item", self.menu)
            qaction.triggered.connect(partial(self.getViewBox().removePlotItem, self))
            self.menu.addAction(qaction)

        if self.name() is not None:
            self.menu.setTitle(self.name())
        else:
            self.menu.setTitle("Trace")
        return self.menu

    def selectColor(self):
        color = self.opts['pen']
        if isinstance(color, QtGui.QPen):
            color = color.color() # pylint: disable=no-member
        elif not isinstance(color, QtGui.QColor):
            color = mkColor(color)
        self.colorDialog.setCurrentColor(color)
        self.colorDialog.open()

    def colorSelected(self, color):
        self.setPen(color)

    def update(self, yData, *args, **kwargs):
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
            connect[dontconnect] = 0

        # Update data
        self.setData(x=xData, y=yData, connect=connect, *args, **kwargs)

    def setName(self, name):
        self.opts['name'] = str(name)
