from pyqtgraph import GraphicsLayoutWidget
from pyqtgraph.exporters import ImageExporter, SVGExporter
from pyqtgraph.multiprocess import proxy

from ..multiprocess import get_logger
logger = get_logger("PlotWindow")

class ExtendedPlotWindow(GraphicsLayoutWidget):
    _windows = []
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
        items = [proxy(item) for item in items]
        return items

    @classmethod
    def getWindows(cls):
        windows = [proxy(item) for item in cls._windows]
        return windows