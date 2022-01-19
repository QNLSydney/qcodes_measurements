from functools import partial
from PyQt5 import QtCore, QtGui, QtWidgets
import numpy as np
import scipy.linalg

from pyqtgraph import ImageItem, ColorMap, graphicsItems, HistogramLUTItem

from .DataItem import ExtendedDataItem
from .PlotWindow import ExtendedPlotWindow
from .ViewBox import CustomViewBox
from .colors import COLORMAPS, DEFAULT_CMAP
from ...logging import get_logger
logger = get_logger("ImageItem")

class ExtendedImageItem(ExtendedDataItem, ImageItem):
    def __init__(self, setpoint_x, setpoint_y, *args, colormap=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.setpoint_x = setpoint_x
        self.setpoint_y = setpoint_y
        self.menu = None
        self.gradientSelectorMenu = None
        self.cmap = None
        if colormap is not None:
            self.changeColorScale(name=colormap)
        else:
            self.changeColorScale(name=DEFAULT_CMAP)

        self.rescale()

    def mouseClickEvent(self, ev):
        return False

    def getContextMenus(self, *, rect=None, event=None):
        if self.menu is None:
            self.menu = QtWidgets.QMenu()
        self.menu.clear()

        # Add color selector
        if self.gradientSelectorMenu is None:
            l = 80
            self.gradientSelectorMenu = QtWidgets.QMenu()
            self.gradientSelectorMenu.setTitle("Color Scale")
            gradients = graphicsItems.GradientEditorItem.Gradients
            for g in gradients:
                if g in COLORMAPS:
                    cmap = COLORMAPS[g]
                else:
                    pos = [x[0] for x in gradients[g]['ticks']]
                    colors = [x[1] for x in gradients[g]['ticks']]
                    mode = ColorMap.RGB if gradients[g]['mode'] == 'rgb' else ColorMap.HSV_POS
                    cmap = ColorMap(pos, colors, mode=mode)
                    COLORMAPS[g] = cmap

                px = QtGui.QPixmap(l, 15)
                p = QtGui.QPainter(px)
                grad = cmap.getGradient(QtCore.QPointF(0, 0), QtCore.QPointF(l, 0))
                brush = QtGui.QBrush(grad)
                p.fillRect(QtCore.QRect(0, 0, l, 15), brush)
                p.end()
                label = QtWidgets.QLabel()
                label.setPixmap(px)
                label.setContentsMargins(1, 1, 1, 1)
                act = QtWidgets.QWidgetAction(self)
                act.setDefaultWidget(label)
                act.triggered.connect(partial(self.changeColorScale, name=g))
                act.name = g
                self.gradientSelectorMenu.addAction(act)
        self.menu.addMenu(self.gradientSelectorMenu)

        # Actions that use the scale box
        if rect is not None:
            xrange = rect.left(), rect.right()
            yrange = rect.top(), rect.bottom()

            qaction = QtWidgets.QAction("Colour By Marquee", self.menu)
            qaction.triggered.connect(partial(self.colorByMarquee, xrange=xrange, yrange=yrange))
            self.menu.addAction(qaction)

            qaction = QtWidgets.QAction("Plane Fit", self.menu)
            qaction.triggered.connect(partial(self.planeFit, xrange=xrange, yrange=yrange))
            self.menu.addAction(qaction)

            qaction = QtWidgets.QAction("Level Columns", self.menu)
            qaction.triggered.connect(partial(self.levelColumns, xrange=xrange, yrange=yrange))
            self.menu.addAction(qaction)

        self.menu.setTitle("Image Item")

        return self.menu

    def changeColorScale(self, name=None):
        if name is None:
            raise ValueError("Name of color map must be given")
        self.cmap = name
        self.setLookupTable(COLORMAPS[self.cmap].getLookupTable(0.0, 1.0, alpha=False))

    def getLimits(self, data, limits):
        """
        Get the indicies from the given data array that correspond
        to the given limits.
        """
        flipped = False
        if data[0] > data[-1]:
            flipped = True
            data = np.flipud(data)
        limits = np.searchsorted(data, limits)
        if flipped:
            length = len(data)
            limits = tuple(sorted(length-x for x in limits))
        return limits

    def colorByMarquee(self, xrange, yrange):
        # Extract indices of limits
        xmin, xmax = xrange
        ymin, ymax = yrange
        xmin_p, xmax_p = self.getLimits(self.setpoint_x, (xmin, xmax))
        ymin_p, ymax_p = self.getLimits(self.setpoint_y, (ymin, ymax))

        logger.info("Doing a colorByMarquee between x: %r, y: %r", xrange, yrange)
        logger.debug("Calculated limits: x: (%d, %d), y: (%d, %d)", xmin_p, xmax_p, ymin_p, ymax_p)

        # Then calculate the min/max range of the array
        data = self.image[xmin_p:xmax_p, ymin_p:ymax_p]
        min_v, max_v = np.min(data), np.max(data)

        # Then set the range
        self.setLevels((min_v, max_v))

    def planeFit(self, xrange, yrange):
        # Extract indices of limits
        xmin, xmax = xrange
        ymin, ymax = yrange
        xmin_p, xmax_p = self.getLimits(self.setpoint_x, (xmin, xmax))
        ymin_p, ymax_p = self.getLimits(self.setpoint_y, (ymin, ymax))

        logger.info("Doing a planeFit between x: %r, y: %r", xrange, yrange)
        logger.debug("Calculated limits: x: (%d, %d), y: (%d, %d)", xmin_p, xmax_p, ymin_p, ymax_p)

        # Get the coordinate grid
        X, Y = np.meshgrid(self.setpoint_x[xmin_p:xmax_p], self.setpoint_y[ymin_p:ymax_p])
        X = X.flatten()
        Y = Y.flatten()
        CG = np.c_[X, Y, np.ones(X.shape)]

        # Get the data in the correct format
        data = self.image[xmin_p:xmax_p, ymin_p:ymax_p]
        data = data.T.flatten()
        assert(data[1] == self.image[xmin_p+1, ymin_p])

        # Perform the fit
        C, _, _, _ = scipy.linalg.lstsq(CG, data, overwrite_a=True, overwrite_b=True)

        # Then, do the plane fit on the image
        X, Y = np.meshgrid(self.setpoint_x, self.setpoint_y)
        Z = C[0]*X + C[1]*Y + C[2]
        image = self.image - Z.T
        self.setImage(image)

    def levelColumns(self, xrange, yrange):
        # Extract indices of limits
        ymin, ymax = yrange
        ymin_p, ymax_p = self.getLimits(self.setpoint_y, (ymin, ymax))

        logger.info("Doing a levelColumns between y: %r", yrange)
        logger.debug("Calculated limits: y: {(%d, %d)}", ymin_p, ymax_p)

        # Get a list of means for that column
        col_mean = self.image[:, ymin_p:ymax_p]
        col_mean = np.mean(col_mean, axis=1)
        col_mean.shape = col_mean.shape + (1,)

        # Subtract from that column
        image = self.image - col_mean
        self.setImage(image)

    def rescale(self):
        step_x = (self.setpoint_x[-1] - self.setpoint_x[0])/len(self.setpoint_x)
        step_y = (self.setpoint_y[-1] - self.setpoint_y[0])/len(self.setpoint_y)

        self.resetTransform()
        self.translate(self.setpoint_x[0], self.setpoint_y[0])
        self.scale(step_x, step_y)

class ImageItemWithHistogram(ExtendedImageItem):
    def __init__(self, setpoint_x, setpoint_y, *args, colormap=None, **kwargs):
        # Create the attached histogram
        self._LUTitem = HistogramLUTItem()

        # Initialize self
        super().__init__(setpoint_x, setpoint_y, *args, colormap=colormap, **kwargs)

        # Update _LUTitem
        self._LUTitem.setImageItem(self)
        self._LUTitem.autoHistogramRange() # enable autoscaling

        # Attach a signal handler on parent changed
        self._parent = None

    def setLevels(self, levels, update=True):
        """
        Hook setLevels to update histogram when the levels are changed in
        the image
        """
        super().setLevels(levels, update)
        self._LUTitem.setLevels(*self.levels)

    def changeColorScale(self, name=None):
        if name is None:
            raise ValueError("Name of color map must be given")
        self.cmap = name
        self._LUTitem.gradient.setColorMap(COLORMAPS[name])

    def getHistogramLUTItem(self):
        return self._LUTitem

    def parentChanged(self):
        super().parentChanged()
        # Add the histogram to the parent
        view_box = self.getViewBox()
        if isinstance(view_box, ExtendedPlotWindow):
            logger.debug("Adding _LUTitem to parent %r.", view_box)
            view_box.addItem(self._LUTitem)
            self._parent = view_box
        elif view_box is None:
            if getattr(self, "_parent", None) is not None:
                self._parent.removeItem(self._LUTitem)
                self._parent = None
        elif isinstance(view_box, CustomViewBox):
            # This second call always seems to occur... Ignore it, since we've added
            # ourselves to the plot window.
            pass
        else:
            raise NotImplementedError("parentChanged is not implemented for anything "
                                      "other than ExtendedPlotWindows at this time. "
                                      f"Got {type(view_box)}.")
