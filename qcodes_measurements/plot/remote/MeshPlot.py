from itertools import islice

import numpy as np
from pyqtgraph import GraphicsObject, HistogramLUTItem, mkPen
from PySide6 import QtCore, QtGui

from ...logging import get_logger
from .colors import COLORMAPS, DEFAULT_CMAP
from .PlotWindow import ExtendedPlotWindow
from .ViewBox import CustomViewBox

logger = get_logger("MeshPlot")


class MeshPlot(GraphicsObject):
    def __init__(
        self,
        *args,
        positions: np.ndarray | None = None,
        data: np.ndarray | None = None,
        colormap: str | None = None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)

        # Initialize data structures
        self.positions = positions
        self.mesh_data: np.ndarray | None = data
        self.rgb_data: None | np.ndarray = None
        self.polygons: list[tuple[int, QtGui.QPolygonF]] = []
        self.xmin, self.xmax = 0, 0
        self.ymin, self.ymax = 0, 0
        if positions is not None and data is not None:
            self.calc_lims()
        elif not (positions is None and data is None):
            raise ValueError("Either positions and data must both be given, or neither.")

        # Initialize menus
        self.menu = None
        self.gradientSelectorMenu = None

        # Create LUT item
        self._LUTitem = HistogramLUTItem()
        self._LUTitem.sigLookupTableChanged.connect(self.changedColorScale)
        self._LUTitem.sigLevelChangeFinished.connect(self.updateRGBData)
        if colormap is not None:
            self.changeColorScale(name=colormap)
        else:
            self.changeColorScale(name=DEFAULT_CMAP)

        # And update color and polygon data
        if self.mesh_data is not None:
            self.updateRGBData()
            self.calculate_polygons()

        # Attach a signal handler on parent changed
        self._parent = None

    ###
    # Function related to plot data
    def setData(self, positions, data):
        self.positions = positions
        self.mesh_data = data

        # Calculate data size
        self.calc_lims()

        # Update plot
        self.updateRGBData()
        self.calculate_polygons()

        # Update histogram and autorange
        hist, bins = np.histogram(self.mesh_data, "auto")
        newBins = np.ndarray(bins.size + 1)
        newHist = np.ndarray(hist.size + 2)
        newBins[0] = bins[0]
        newBins[-1] = bins[-1]
        newBins[1:-1] = (bins[:-1] + bins[1:]) / 2
        newHist[[0, -1]] = 0
        newHist[1:-1] = hist
        self._LUTitem.plot.setData(newBins, newHist)
        self._LUTitem.setLevels(newBins[0], newBins[-1])
        self._LUTitem.plot.getViewBox().itemBoundsChanged(self._LUTitem.plot)

        # Force viewport update
        view_box = self.getViewBox()
        if view_box is not None:
            view_box.itemBoundsChanged(self)
        self.update()

    ###
    # Functions relating to the size of the image
    def calc_lims(self):
        if self.positions is None or len(self.positions) == 0:
            self.xmin, self.xmax = 0, 0
            self.ymin, self.ymax = 0, 0
            return
        self.xmin, self.ymin = self.positions[0]
        self.xmax, self.ymax = self.positions[0]
        for x, y in islice(self.positions, 1, None):
            self.xmin, self.xmax = min(self.xmin, x), max(self.xmax, x)
            self.ymin, self.ymax = min(self.ymin, y), max(self.ymax, y)
        logger.debug(
            "Calculated limits (%f, %f) - (%f, %f)",
            self.xmin,
            self.ymin,
            self.xmax,
            self.ymax,
        )

    def width(self):
        return float(self.xmax - self.xmin)

    def height(self):
        return float(self.ymax - self.ymin)

    def boundingRect(self):
        tl = QtCore.QPointF(self.xmin, self.ymin)
        br = QtCore.QPointF(self.xmax, self.ymax)
        return QtCore.QRectF(tl, br)

    ###
    # Functions relating to the colorscale

    def setLevels(self, levels, update=True):
        """
        Hook setLevels to update histogram when the levels are changed in
        the image
        """
        self._LUTitem.setLevels(*levels)
        if update:
            self.updateRGBData()

    def changeColorScale(self, name=None):
        if name is None:
            raise ValueError("Name of color map must be given")
        logger.debug("Changed color scale to %s.", name)
        self._LUTitem.gradient.setColorMap(COLORMAPS[name])

    def getHistogramLUTItem(self):
        return self._LUTitem

    @property
    def histogram(self):
        return self.getHistogramLUTItem()

    def changedColorScale(self):
        logger.debug("Changed color scale")
        self.updateRGBData()

    def updateRGBData(self):
        minr, maxr = self._LUTitem.getLevels()
        logger.debug("Recoloring to changed levels: (%f, %f)", minr, maxr)
        if self.mesh_data is not None:
            scaled = (self.mesh_data - minr) / (maxr - minr)

            logger.debug("Calculating new colors")
            self.rgb_data = self._LUTitem.gradient.colorMap().map(scaled, mode="qcolor")
            logger.debug("Done")
            self.update()

    ###
    # Functions relating to drawing

    def calculate_polygons(self):
        """
        Calculate the polygons to be drawn by the mesh plot
        """
        raise NotImplementedError()

    def paint(self, painter, option, widget=None):
        logger.debug("Starting paint")
        visible = self.parentItem().boundingRect()
        if self.polygons is not None and self.polygons and self.rgb_data is not None:
            painter.setPen(mkPen(None))
            for poly in self.polygons:  # pylint: disable=not-an-iterable
                if not poly[1].boundingRect().intersects(visible):
                    continue
                painter.setBrush(self.rgb_data[poly[0]])
                painter.drawPolygon(poly[1])
            logger.debug("Done painting")
        else:
            logger.debug("No polygons to draw")

    def changeParent(self):
        super().changeParent()
        # Add the histogram to the parent
        view_box = self.getViewBox()
        if isinstance(view_box, ExtendedPlotWindow):
            logger.debug("Adding _LUTitem to parent %r.", view_box)
            view_box.addItem(self._LUTitem)
            self._parent = view_box
        elif view_box is None:
            if self._parent is not None:
                self._parent.removeItem(self._LUTitem)
                self._parent = None
        elif isinstance(view_box, CustomViewBox):
            # This second call always seems to occur... Ignore it, since we've added
            # ourselves to the plot window.
            pass
        else:
            raise NotImplementedError(
                "parentChanged is not implemented for anything "
                "other than ExtendedPlotWindows at this time. "
                f"Got {type(view_box)}."
            )
