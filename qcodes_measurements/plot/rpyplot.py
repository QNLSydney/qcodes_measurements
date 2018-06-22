# -*- coding: utf-8 -*-

from math import ceil
import warnings
from functools import partial
import weakref
from collections import namedtuple
import colorsys

from pyqtgraph import *
from pyqtgraph.exporters import *
import pyqtgraph.multiprocess as mp
from pyqtgraph.GraphicsScene.mouseEvents import MouseClickEvent

from PyQt5.QtWidgets import QApplication, QMessageBox, QMainWindow, QAction, QGraphicsSceneMouseEvent
from PyQt5 import QtCore

import numpy as np
from numpy import linspace, min, max, ndarray, searchsorted
import scipy.linalg

import logging
import sys, traceback

logger = logging.getLogger("rpyplot")
log_handler = logging.FileHandler("rpyplot.log")
log_handler.setLevel(logging.INFO)
log_format = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
log_handler.setFormatter(log_format)
logger.addHandler(log_handler)
def exc(type, value, tb):
    logger.exception("Uncaught Exception: {}\n{}".format(str(value), str(traceback.format_tb(tb))))
sys.excepthook = exc

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
        items = [mp.proxy(item) for item in items]
        return items

    @classmethod
    def getWindows(cls):
        windows = [mp.proxy(item) for item in cls._windows]
        return windows

class DraggableTextItem(GraphicsWidget, GraphicsWidgetAnchor):
    def __init__(self, text="", offset=None, *args, **kwargs):
        GraphicsWidget.__init__(self)
        GraphicsWidgetAnchor.__init__(self)
        self.setFlag(self.ItemIgnoresTransformations)
        self.layout = QtGui.QGraphicsGridLayout()
        self.setLayout(self.layout)
        self.item_anchor = (0, 0)
        self.object_anchor = (0, 0)
        if offset is None:
            self.offset = (0, 0)
        else:
            self.offset = offset

        self.label_item = LabelItem()
        self.label_item.setText(text)
        self.layout.addItem(self.label_item, 0, 0)

        self.pen = fn.mkPen(255,255,255,100)
        self.brush = fn.mkBrush(100,100,100,50)

        self.updateSize()

    def setParentItem(self, p):
        ret = GraphicsWidget.setParentItem(self, p)
        if self.offset is not None:
            offset = Point(self.offset)
            anchorx = 1 if offset[0] <= 0 else 0
            anchory = 1 if offset[1] <= 0 else 0
            anchor = (anchorx, anchory)
            self.anchor(itemPos=anchor, parentPos=anchor, offset=offset)
        return ret

    def paint(self, p, *args):
        p.setPen(self.pen)
        p.setBrush(self.brush)
        p.drawRect(self.boundingRect())

    def anchor(self, itemPos, parentPos, offset=(0, 0)):
        super().anchor(itemPos, parentPos, offset)
        self.item_anchor = itemPos
        self.object_anchor = parentPos
        self.offset = offset

    def getOffset(self):
        return self.offset
    def setOffset(self, offs):
        if not isinstance(offs, tuple) or len(offs) != 2:
            raise ValueError("Must be a tuple (x, y)")
        self.anchor(self.item_anchor, self.object_anchor, offs)

    def updateSize(self):
        self.setGeometry(0, 0, self.label_item.width()+10, self.label_item.height())
    def boundingRect(self):
        return QtCore.QRectF(0, 0, self.width(), self.height())

    def hoverEvent(self, ev):
        ev.acceptDrags(QtCore.Qt.LeftButton)
    def mouseDragEvent(self, ev):
        if ev.button() == QtCore.Qt.LeftButton:
            dpos = ev.pos() - ev.lastPos()
            self.autoAnchor(self.pos() + dpos)

    def setText(self, text):
        self.label_item.setText(str(text))
        self.updateSize()
    def getText(self):
        return self.label_item.text

class PlotMenu(object):
    def __init__(self):
        """
        Keep track of objects added to the menu, since they must be removed
        when the plot items that should be visible are changed
        """
        self.addedMenuItems = []

    def raiseContextMenu(self, ev):
        """
        Raise the context menu, removing extra separators as they are added pretty recklessly
        """
        menu = self.getContextMenus(ev)
        # Let the scene add on to the end of our context menu
        # (this is optional)
        menu = self.scene().addParentContextMenus(self, menu, ev)
        # Collapse sequential separators
        i = 1
        actions = menu.actions()
        while i < len(actions):
            if actions[i].isSeparator() and actions[i-1].isSeparator():
                menu.removeAction(actions[i])
                actions.remove(actions[i])
                continue
            i += 1

        # Display the separator
        pos = ev.screenPos()
        menu.popup(QtCore.QPoint(pos.x(), pos.y()))
        return True

    def addPlotContextMenus(self, items, itemNumbers, menu, rect=None):
        """
        Add plot items to the menu

        Args:
            items: List of plot items to add to the menu
            itemNumbers: Dictionary mapping items to the index in the plot
            menu: The menu to add items to
        """
        # First, remove all items added previously
        for item in self.addedMenuItems:
            menu.removeAction(item)
        self.addedMenuItems.clear()

        # And create a sorted list of items under the rectangle
        itemsToAdd = []
        for item in items:
            if not isinstance(item, (PlotCurveItem, PlotDataItem, ImageItem)):
                continue
            if isinstance(item, PlotCurveItem):
                dataitem = item.parentObject()
            else:
                dataitem = item

            if not hasattr(dataitem, "getContextMenus"):
                continue                

            # Figure out the name and references of this item
            if hasattr(dataitem, "name"):
                name = dataitem.name()
            else:
                name = None
            ind = itemNumbers[dataitem]
            if name is None:
                name = f"(Trace: {ind+1})"
            else:
                name = f"{name} (Trace: {ind+1})"

            # Create menus for each of the items
            if isinstance(dataitem, (ExtendedPlotDataItem, ExtendedImageItem)):
                menu = dataitem.getContextMenus(rect=rect, event=None)
            else:
                menu = dataitem.getContextMenus(event=None)
            menu.setTitle(name)
            itemsToAdd.append((ind, menu))

        # Sort the items by the index
        itemsToAdd.sort(key=lambda x: x[0])

        # Add each of the items in to the menu
        if itemsToAdd:
            self.addedMenuItems.append(self.menu.addSeparator())
            if len(itemsToAdd) == 1:
                for item in itemsToAdd[0][1].actions():
                    self.addedMenuItems.append(item)
                    self.menu.addAction(item)
            else:
                for item in itemsToAdd:
                    self.addedMenuItems.append(self.menu.addMenu(item[1]))

        return itemsToAdd

class DraggableScaleBox(PlotMenu, GraphicsObject):
    def __init__(self):
        GraphicsObject.__init__(self)
        PlotMenu.__init__(self)
        # Set menu, create when necessary
        self.menu = None

        # Set Formatting
        self.pen = mkPen((255,255,100), width=1)
        self.brush = mkBrush(255,255,0,100)
        self.setZValue(1e9)

    # All graphics items must have paint() and boundingRect() defined.
    def boundingRect(self):
        return QtCore.QRectF(0, 0, 1, 1)
    
    def paint(self, p, *args):
        p.setPen(self.pen)
        p.setBrush(self.brush)
        p.drawRect(self.boundingRect())

    # On right-click, raise the context menu
    def mouseClickEvent(self, ev):
        if ev.button() == QtCore.Qt.RightButton:
            if self.raiseContextMenu(ev):
                ev.accept()

    # This method will be called when this item's _children_ want to raise
    # a context menu that includes their parents' menus.
    def getContextMenus(self, event=None):
        if self.menu is None:
            self.menu = QtGui.QMenu()
            self.menu.triggered.connect(self.hide)
            self.menu.setTitle("Scale Box")

            # Add scale items
            rbActions = (
                ('Expand', partial(self.expand, axis='XY')),
                ('Expand X', partial(self.expand, axis='X')),
                ('Expand Y', partial(self.expand, axis='Y')),
            )
            for action in rbActions:
                qaction = QtGui.QAction(action[0], self.menu)
                qaction.triggered.connect(action[1])
                self.menu.addAction(qaction)

        # Get the size of the scale box
        vb = self.getViewBox()
        rect = self.mapRectToItem(vb.childGroup, self.boundingRect())

        # Add plot items to the menu
        items = self.scene().items(self.mapRectToScene(self.boundingRect()))
        # Let's figure out as well the number of the plot item for labelling purposes
        itemNumbers = [x for x in self.parentObject().childItems() if isinstance(x, PlotDataItem) or isinstance(x, ImageItem)]
        itemNumbers = dict((x[1], x[0]) for x in enumerate(itemNumbers))
        self.addPlotContextMenus(items, itemNumbers, self.menu, rect)

        return self.menu

    def expand(self, axis='XY'):
        # Get the viewbox to scale
        vb = self.getViewBox()
        # Get the size of the scale box
        p = self.mapRectToItem(vb.childGroup, self.boundingRect())
        
        # Set axes to existing if we don't want to set them
        if 'X' not in axis:
            existingRect = vb.viewRect()
            p.setLeft(existingRect.left())
            p.setRight(existingRect.right())
        if 'Y' not in axis:
            existingRect = vb.viewRect()
            p.setBottom(existingRect.bottom())
            p.setTop(existingRect.top())

        # Do scale
        vb.setRange(rect=p, padding=0)
        vb.axHistoryPointer += 1
        vb.axHistory = vb.axHistory[:vb.axHistoryPointer] + [p]

class CustomViewBox(PlotMenu, ViewBox):
    def __init__(self, *args, **kwargs):
        # Initialize the superclass
        ViewBox.__init__(self, *args, **kwargs)
        PlotMenu.__init__(self)

        # Create a scale box
        self.scaleBox = DraggableScaleBox()
        self.scaleBox.hide()
        self.addItem(self.scaleBox, ignoreBounds=True)

        # The mouse mode is not used, since we override what left clicking does anyway
        removeMenuItems = ('Mouse Mode', )
        for menuItem in removeMenuItems:
            actions = self.menu.actions()
            for action in actions:
                if action.text() == menuItem:
                    self.menu.removeAction(action)

        # Extra menu actions
        self.makeTracesDifferentAction = QAction("Make All Traces Different", self.menu)
        self.makeTracesDifferentAction.triggered.connect(self.makeTracesDifferent)

    def mouseClickEvent(self, ev):
        if (ev.button() & QtCore.Qt.LeftButton):
            if (self.scaleBox.isVisible()):
                rect = self.scaleBox.mapRectToScene(self.scaleBox.boundingRect())
                pos = ev.scenePos()
                if not rect.contains(pos):
                    ev.accept()
                    self.scaleBox.hide()
                    return True
        if (ev.button() & QtCore.Qt.RightButton):
            ev.accept()
            self.raiseContextMenu(ev)
            return True

        return super().mouseClickEvent(ev)

    def mouseDragEvent(self, ev, axis=None):
        # Draw the box
        if ev.button() & QtCore.Qt.LeftButton:
            ev.accept()
            if not self.scaleBox.isVisible():
                self.scaleBox.show()
            self.updateScaleBox(ev.buttonDownPos(), ev.pos())
            return True

        return super().mouseDragEvent(ev)

    def getContextMenus(self, event=None):
        """
        Add extra plot items to the viewbox menu when we right click on a plot item.
        We do this in the view box instead of in the plot item otherwise the axis menus
        get collapsed down.
        """

        # If we clicked on a trace, add it to the context menu
        if event is not None and event.acceptedItem == self:
            # Add plot context meny items
            traces = self.parentObject().listDataItems()
            if any(isinstance(trace, PlotDataItem) for trace in traces):
                self.menu.addAction(self.makeTracesDifferentAction)
            else:
                self.menu.removeAction(self.makeTracesDifferentAction)

            # for plot curve items, we need to do an additional check that we 
            # are actually on the curve, as itemsNearEvent uses the boundingBox
            def filterNear(item):
                if isinstance(item, PlotCurveItem):
                    mouseShape = item.mapToScene(item.mouseShape())
                    return mouseShape.contains(event.scenePos())
                return True

            # Get a list of the items near this event
            itemNumbers = [x for x in self.addedItems if isinstance(x, PlotDataItem) or isinstance(x, ImageItem)]
            itemNumbers = dict((x[1], x[0]) for x in enumerate(itemNumbers))
            items = filter(filterNear, self.scene().itemsNearEvent(event))
            self.addPlotContextMenus(items, itemNumbers, self.menu)

        return self.menu

    def updateScaleBox(self, p1, p2):
        """
        Draw the rectangular scale box on screen
        """
        r = QtCore.QRectF(p1, p2)
        r = self.childGroup.mapRectFromParent(r)

        self.scaleBox.setPos(r.topLeft())
        self.scaleBox.resetTransform()
        self.scaleBox.scale(r.width(), r.height())

    def removePlotItem(self, item):
        """
        Remove a plot item from the screen
        """
        self.parentObject().removeItem(item)
        self.scaleBox.hide()

    def makeTracesDifferent(self, checked=False, items=None):
        self.parentObject().makeTracesDifferent(items=items)

class ExtendedPlotItem(PlotItem):
    def __init__(self, *args, **kwargs):
        """
        Create a new PlotItem, same as a base plotitem, but with a few
        extra pieces of functionality.
          - Export Image
          - Keep track of images and allow proxying
          - Use a custom view box, such that we can do common tasks
        """
        if 'viewBox' not in kwargs:
            vb = CustomViewBox()
            kwargs['viewBox'] = vb
        super().__init__(*args, **kwargs)

        # Keep track of context menus for items in this plot
        self.itemMenus = {}

    def export(self, fname, export_type="image"):
        """
        Save the item as an image
        """
        if export_type == "image":
            exporter = ImageExporter(self)
        elif export_type == "svg":
            exporter = SVGExporter(self)
        exporter.export(fname)
        del exporter

    def addItem(self, item, *args, **kwargs):
        super().addItem(item, *args, **kwargs)
        if isinstance(item, ImageItem):
            # addItem does not keep track of images, let's add it ourselves
            self.dataItems.append(item)

    def listDataItems(self, proxy=False):
        """
        Create a picklable list of data items.
        """
        data_items = super().listDataItems()
        if proxy:
            data_items = [mp.proxy(item) for item in data_items]
        return data_items

    def plot(self, *args, **kargs):
        """
        Reimplements PlotItem.plot to use ExtendedPlotDataItems.
        Add and return a new plot.
        See :func:`PlotDataItem.__init__ <pyqtgraph.PlotDataItem.__init__>` for data arguments
        
        Extra allowed arguments are:
            clear    - clear all plots before displaying new data
            params   - meta-parameters to associate with this data
        """        
        clear = kargs.get('clear', False)
        params = kargs.get('params', None)
          
        if clear:
            self.clear()
            
        item = ExtendedPlotDataItem(*args, **kargs)
            
        if params is None:
            params = {}
        self.addItem(item, params=params)
        
        return item

    def makeTracesDifferent(self, saturation=0.8, value=0.9, items=None):
        """
        Color each of the traces in a plot a different color
        """
        if items is None:
            items = self.listDataItems()
        items = [x for x in items if isinstance(x, PlotDataItem)]
        ntraces = len(items)

        for i, trace in enumerate(items):
            color = colorsys.hsv_to_rgb(i/ntraces, saturation, value)
            color = tuple(int(c*255) for c in color)
            trace.setPen(*color)

class ExtendedPlotDataItem(PlotDataItem):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.menu = None

        # Plot color selection dialog
        self.colorDialog = QtGui.QColorDialog()
        self.colorDialog.setOption(QtGui.QColorDialog.ShowAlphaChannel, True)
        self.colorDialog.setOption(QtGui.QColorDialog.DontUseNativeDialog, True)
        self.colorDialog.colorSelected.connect(self.colorSelected)

    def getContextMenus(self, *, rect=None, event=None):
        if self.menu is None:
            self.menu = QtGui.QMenu()

            qaction = QtGui.QAction("Select Color", self.menu)
            qaction.triggered.connect(self.selectColor)
            self.menu.addAction(qaction)

            qaction = QtGui.QAction("Remove Item", self.menu)
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
            color = color.color()
        elif not isinstance(color, QtGui.QColor):
            color = mkColor(color)
        self.colorDialog.setCurrentColor(color)
        self.colorDialog.open()

    def colorSelected(self, color):
        self.setPen(color)

    def update(self, yData):
        self.setData(x=self.xData, y=yData)

class ExtendedImageItem(ImageItem):
    colormaps = {}

    def __init__(self, setpoint_x, setpoint_y, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setpoint_x = setpoint_x
        self.setpoint_y = setpoint_y
        self.menu = None
        self.gradientSelectorMenu = None

        self.rescale()

    def mouseClickEvent(self, ev):
        return False

    def getContextMenus(self, *, rect=None, event=None):
        if self.menu is None:
            self.menu = QtGui.QMenu()
        self.menu.clear()

        # Add color selector
        if self.gradientSelectorMenu is None:
            l = 80
            self.gradientSelectorMenu = QtGui.QMenu()
            self.gradientSelectorMenu.setTitle("Color Scale")
            gradients = graphicsItems.GradientEditorItem.Gradients
            for g in gradients:
                if g in ExtendedImageItem.colormaps:
                    cmap = ExtendedImageItem.colormaps[g]
                else:
                    pos = [x[0] for x in gradients[g]['ticks']]
                    colors = [x[1] for x in gradients[g]['ticks']]
                    mode = ColorMap.RGB if gradients[g]['mode'] == 'rgb' else ColorMap.HSV_POS
                    cmap = ColorMap(pos, colors, mode=mode)
                    self.colormaps[g] = cmap

                px = QtGui.QPixmap(l, 15)
                p = QtGui.QPainter(px)
                grad = cmap.getGradient(QtCore.QPointF(0,0), QtCore.QPointF(l,0))
                brush = QtGui.QBrush(grad)
                p.fillRect(QtCore.QRect(0, 0, l, 15), brush)
                p.end()
                label = QtGui.QLabel()
                label.setPixmap(px)
                label.setContentsMargins(1, 1, 1, 1)
                act = QtGui.QWidgetAction(self)
                act.setDefaultWidget(label)
                act.triggered.connect(partial(self.changeColorScale, name=g))
                act.name = g
                self.gradientSelectorMenu.addAction(act)
        self.menu.addMenu(self.gradientSelectorMenu)

        # Actions that use the scale box
        if rect is not None:
            xrange = rect.left(), rect.right()
            yrange = rect.top(), rect.bottom()

            qaction = QtGui.QAction("Colour By Marquee", self.menu)
            qaction.triggered.connect(partial(self.colorByMarquee, xrange=xrange, yrange=yrange))
            self.menu.addAction(qaction)

            qaction = QtGui.QAction("Plane Fit", self.menu)
            qaction.triggered.connect(partial(self.planeFit, xrange=xrange, yrange=yrange))
            self.menu.addAction(qaction)

            qaction = QtGui.QAction("Level Columns", self.menu)
            qaction.triggered.connect(partial(self.levelColumns, xrange=xrange, yrange=yrange))
            self.menu.addAction(qaction)

        self.menu.setTitle("Image Item")

        return self.menu

    def changeColorScale(self, checked=False, name=None):
        if name is None:
            raise ValueError("Name of color map must be given")
        cmap = self.colormaps[name]
        self.setLookupTable(cmap.getLookupTable(0.0, 1.0, alpha=False))

    def colorByMarquee(self, xrange, yrange):
        # Extract indices of limits
        xmin, xmax = xrange
        ymin, ymax = yrange
        xmin_p, xmax_p = searchsorted(self.setpoint_x, (xmin, xmax))
        ymin_p, ymax_p = searchsorted(self.setpoint_y, (ymin, ymax))

        # Then calculate the min/max range of the array
        data = self.image[xmin_p:xmax_p,ymin_p:ymax_p]
        min_v, max_v = min(data), max(data)

        # Then set the range
        self.setLevels((min_v, max_v))

    def planeFit(self, xrange, yrange):
        # Extract indices of limits
        xmin, xmax = xrange
        ymin, ymax = yrange
        xmin_p, xmax_p = searchsorted(self.setpoint_x, (xmin, xmax))
        ymin_p, ymax_p = searchsorted(self.setpoint_y, (ymin, ymax))

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
        C,_,_,_ = scipy.linalg.lstsq(CG, data, overwrite_a=True, overwrite_b=True)

        # Then, do the plane fit on the image
        X, Y = np.meshgrid(self.setpoint_x, self.setpoint_y)
        Z = C[0]*X + C[1]*Y + C[2]
        image = self.image - Z.T
        self.setImage(image)

    def levelColumns(self, xrange, yrange):
        # Extract indices of limits
        ymin, ymax = yrange
        ymin_p, ymax_p = searchsorted(self.setpoint_y, (ymin, ymax))

        # Get a list of means for that column
        col_mean = self.image[:,ymin_p:ymax_p]
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
    def __init__(self, setpoint_x, setpoint_y, *args, colormap, **kwargs):
        super().__init__(setpoint_x, setpoint_y, *args, **kwargs)
        # Create the attached histogram
        self._LUTitem = HistogramLUTItem()
        self._LUTitem.setImageItem(self)
        if colormap is not None:
            self._LUTitem.gradient.setColorMap(colormap)
        self._LUTitem.autoHistogramRange() # enable autoscaling

        # Attach a signal handler on parent changed
        #self.sigParentChanged.connect(self.parentChanged)

    def setLevels(self, levels, update=True):
        """
        Hook setLevels to update histogram when the levels are changed in
        the image
        """
        super().setLevels(levels, update)
        self._LUTitem.setLevels(*self.levels)

    def changeColorScale(self, checked=False, name=None):
        if name is None:
            raise ValueError("Name of color map must be given")
        cmap = self.colormaps[name]
        self._LUTitem.gradient.setColorMap(cmap)

    def getHistogramLUTItem(self):
        return self._LUTitem
    
    def parentChanged(self):
        super().parentChanged()
        print("Called: Parent is: {}".format(repr(self.parentObject())))
        # Add the histogram to the parent
        view_box = self.getViewBox()
        if isinstance(view_box, ExtendedPlotWindow):
            view_box.addItem(self._LUTitem)
            self._parent = view_box
        elif self.getViewBox() is None:
            if self._parent is not None:
                self._parent.removeItem(self._LUTitem)
                self._parent = None
