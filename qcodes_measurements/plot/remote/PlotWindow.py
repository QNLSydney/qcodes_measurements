from functools import partial

from pyqtgraph import GraphicsLayoutWidget
from pyqtgraph.exporters import ImageExporter, SVGExporter
from pyqtgraph.Qt.QtWidgets import QGraphicsProxyWidget, QWidget

from ...logging import get_logger

logger = get_logger("PlotWindow")


class ExtendedPlotWindow(GraphicsLayoutWidget):
    _windows: list["ExtendedPlotWindow"] = []

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._plot_closed = False
        if self not in self._windows:
            self._windows.append(self)
        self.destroyed.connect(partial(type(self)._forget_window, id(self)))

    @classmethod
    def _forget_window(cls, identity, *args):
        cls._windows[:] = [window for window in cls._windows if id(window) != identity]

    def showEvent(self, event):
        self._plot_closed = False
        if self not in self._windows:
            self._windows.append(self)
        super().showEvent(event)

    def close(self):
        """Hide a reusable window, matching a native window-manager close."""
        return QWidget.close(self)

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
        # PySide's widget() transfers ownership from Python to Qt for widgets
        # embedded by scene().addWidget(). Do this before the scene can be freed.
        if (scene := self.scene()) is not None:
            for item in scene.items():
                if isinstance(item, QGraphicsProxyWidget):
                    item.widget()
        self._plot_closed = True
        self._forget_window(id(self))
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
        return list(cls._windows)

    @classmethod
    def find_by_id(cls, wid):
        from .PlotItem import ExtendedPlotItem

        for window in cls._windows:
            for item in window.getLayoutItems():
                if isinstance(item, ExtendedPlotItem) and item.titleLabel.text.endswith(
                    f"(id: {wid})"
                ):
                    return window
        return None
