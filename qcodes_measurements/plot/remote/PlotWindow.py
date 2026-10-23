from pyqtgraph import GraphicsLayoutWidget
from pyqtgraph.exporters import ImageExporter, SVGExporter
from pyqtgraph.Qt.QtWidgets import QGraphicsProxyWidget

from ...logging import get_logger

logger = get_logger("PlotWindow")


class ExtendedPlotWindow(GraphicsLayoutWidget):
    _windows: list["ExtendedPlotWindow"] = []

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
        else:
            raise ValueError("Unrecogized exporter. Must be image or svg.")
        exporter.export(fname)
        del exporter

    def closeEvent(self, event):
        # Cleanup can deliver another close event after a user has closed the window.
        if self in self._windows:
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
