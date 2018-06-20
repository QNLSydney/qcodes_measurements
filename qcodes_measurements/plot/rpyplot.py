# -*- coding: utf-8 -*-

from math import ceil
import warnings
from functools import partial
import weakref
from collections import namedtuple

from pyqtgraph import *
from pyqtgraph.exporters import *
import pyqtgraph.multiprocess as mp
from pyqtgraph.GraphicsScene.mouseEvents import MouseClickEvent

from PyQt5.QtWidgets import QApplication, QMessageBox, QMainWindow, QAction, QGraphicsSceneMouseEvent
from PyQt5 import QtCore

from numpy import linspace, min, max, ndarray

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

    def addPlotContextMenus(self, items, itemNumbers, menu):
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
            dataitem = item.parentObject()
            if not hasattr(dataitem, "getContextMenus"):
                continue                

            # Figure out the name and references of this item
            name = dataitem.name()
            ind = itemNumbers[dataitem]
            if name is None:
                name = f"(Trace: {ind+1})"
            else:
                name = f"{name} (Trace: {ind+1})"

            # Create menus for each of the items
            menu = dataitem.getContextMenus()
            menu.setTitle(name)
            itemsToAdd.append((ind, menu))

        # Sort the items by the index
        itemsToAdd.sort(key=lambda x: x[0])

        # Add each of the items in to the menu
        if itemsToAdd:
            self.addedMenuItems.append(self.menu.addSeparator())
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

    def raiseContextMenu(self, ev):
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

        pos = ev.screenPos()
        menu.popup(QtCore.QPoint(pos.x(), pos.y()))
        return True

    # This method will be called when this item's _children_ want to raise
    # a context menu that includes their parents' menus.
    def getContextMenus(self, event=None):
        if self.menu is None:
            self.menu = QtGui.QMenu()
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

        # Add plot items to the menu
        items = self.scene().items(self.mapRectToScene(self.boundingRect()))
        # Let's figure out as well the number of the plot item for labelling purposes
        itemNumbers = [x for x in self.parentObject().childItems() if isinstance(x, PlotDataItem) or isinstance(x, ImageItem)]
        itemNumbers = dict((x[1], x[0]) for x in enumerate(itemNumbers))
        self.addPlotContextMenus(items, itemNumbers, self.menu)

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
        # Hide the scale box
        self.hide()

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
        if event is not None:
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

    def listDataItems(self):
        """
        Create a picklable list of data items.
        """
        data_items = super().listDataItems()
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

class ExtendedPlotDataItem(PlotDataItem):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.menu = None

    def getContextMenus(self):
        if self.menu is None:
            self.menu = QtGui.QMenu()

            qaction = QtGui.QAction("Remove Item", self.menu)
            qaction.triggered.connect(partial(self.getViewBox().removePlotItem, self))
            self.menu.addAction(qaction)
        self.menu.setTitle(self.name())
        return self.menu

class ExtendedImageItem(ImageItem):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.menu = None

    def getContextMenus(self):
        if self.menu is None:
            self.menu = QtGui.QMenu()

            qaction = QtGui.QAction("Colour By Marquee", self.menu)
            self.menu.addAction(qaction)

            qaction = QtGui.QAction("Plane Fit", self.menu)
            self.menu.addAction(qaction)
        self.menu.setTitle(name)
        return self.menu

class ImageItemWithHistogram(ExtendedImageItem):
    def __init__(self, *args, colormap, **kwargs):
        super().__init__(*args, **kwargs)
        # Create the attached histogram
        self._LUTitem = HistogramLUTItem()
        self._LUTitem.setImageItem(self)
        self._LUTitem.gradient.setColorMap(colormap)
        self._LUTitem.autoHistogramRange() # enable autoscaling

        # Attach a signal handler on parent changed
        #self.sigParentChanged.connect(self.parentChanged)

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