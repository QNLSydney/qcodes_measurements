from typing import List
from PyQt5.QtWidgets import QGraphicsProxyWidget

from pyqtgraph import GraphicsLayoutWidget
from pyqtgraph.exporters import ImageExporter, SVGExporter

from ...logging import get_logger
logger = get_logger("PlotWindow")

class ExtendedPlotWindow(GraphicsLayoutWidget):
    _windows: List["ExtendedPlotWindow"] = []
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._windows.append(self)

    def export(self, fname, export_type="image"):
        """
        Save the item as an image
        """
        if export_type == "image":
            exporter = ImageExporter(self.scene())
        elif export_type == "svg":
            exporter = SVGExporter(self.scene())
        exporter.export(fname)
        del exporter

    def closeEvent(self, event):
        self._windows.remove(self)
        event.accept()

    def getLayoutItems(self):
        layout = self.ci
        items = list(layout.items.keys())
        for i, item in enumerate(items):
            if isinstance(item, QGraphicsProxyWidget):
                items[i] = item.widget()
        return items

    @classmethod
    def getWindows(cls):
        return cls._windows
