from functools import partial
from sys import exc_info

from PyQt5 import QtCore, QtGui, QtWidgets
from pyqtgraph import ViewBox, PlotDataItem, PlotCurveItem, \
                      ImageItem, GraphicsObject, mkPen, mkBrush

from .PlotMenu import PlotMenuMixin
from .DataItem import ExtendedDataItem
from ...logging import get_logger
logger = get_logger("ViewBox")

class CustomViewBox(PlotMenuMixin, ViewBox):
    def __init__(self, *args, **kwargs):
        # Initialize the superclass
        ViewBox.__init__(self, *args, **kwargs)

        # Create a scale box
        self.scaleBox = DraggableScaleBox()
        self.scaleBox.hide()
        self.addItem(self.scaleBox, ignoreBounds=True)
        # And disable the one from PyQtGraph
        self.removeItem(self.rbScaleBox)

        # The mouse mode is not used, since we override what left clicking does anyway
        removeMenuItems = ('Mouse Mode', )
        for menuItem in removeMenuItems:
            actions = self.menu.actions()
            for action in actions:
                if action.text() == menuItem:
                    self.menu.removeAction(action)

        # Extra menu actions
        self.makeTracesDifferentAction = QtWidgets.QAction("Make All Traces Different", self.menu)
        self.makeTracesDifferentAction.triggered.connect(self.makeTracesDifferent)

    def mouseClickEvent(self, ev):
        if ev.button() & QtCore.Qt.LeftButton:
            if self.scaleBox.isVisible():
                rect = self.scaleBox.mapRectToScene(self.scaleBox.getRect())
                pos = ev.scenePos()
                logger.debug(f"Rect has position %r. Event has position %r.", rect, pos)
                if not rect.contains(pos):
                    ev.accept()
                    self.scaleBox.hide()
                    return True
        if ev.button() & QtCore.Qt.RightButton:
            ev.accept()
            logger.debug(f"Trying to raise context menu")
            try:
                self.raiseContextMenu(ev)
            except Exception as e:
                logger.exception("Exception trying to raise context menu!", exc_info=exc_info())
                raise
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
            itemNumbers = [x for x in self.addedItems if isinstance(x, (PlotDataItem, ImageItem))]
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
        self.scaleBox.setRect(r)

    def removePlotItem(self, item):
        """
        Remove a plot item from the screen
        """
        self.parentObject().removeItem(item)
        self.scaleBox.hide()

    def makeTracesDifferent(self, _checked=False, items=None):
        self.parentObject().makeTracesDifferent(items=items)


class DraggableScaleBox(PlotMenuMixin, GraphicsObject):
    def __init__(self):
        GraphicsObject.__init__(self)
        # Set menu, create when necessary
        self.menu = None

        # Set size
        self._rect = QtCore.QRectF(0, 0, 0, 0)
        self._boundingRect = QtCore.QRectF(0, 0, 0, 0)

        # Set Formatting
        self.pen = mkPen((255, 255, 100), width=1)
        self.brush = mkBrush(255, 255, 0, 100)
        self.setZValue(1e9)

    # All graphics items must have paint() and boundingRect() defined.
    def setRect(self, r):
        self._rect = r
        self._boundingRect = self._boundingRect.united(r)
        self.update()

    def getRect(self):
        return self._rect

    def boundingRect(self):
        return self._boundingRect

    def show(self):
        """
        Reset the maximum bounding rect and show
        """
        self._boundingRect = QtCore.QRectF(0, 0, 0, 0)
        super().show()

    def hide(self):
        """
        Reset the rect and hide
        """
        self._rect = QtCore.QRectF(0, 0, 0, 0)
        super().hide()

    def paint(self, p, _options, _widget):
        p.setPen(self.pen)
        p.setBrush(self.brush)
        p.drawRect(self._rect)

    # On right-click, raise the context menu
    def mouseClickEvent(self, ev):
        if ev.button() == QtCore.Qt.RightButton:
            if self.raiseContextMenu(ev):
                ev.accept()

    # This method will be called when this item's _children_ want to raise
    # a context menu that includes their parents' menus.
    def getContextMenus(self, event=None):
        if self.menu is None:
            self.menu = QtWidgets.QMenu()
            self.menu.triggered.connect(self.hide)
            self.menu.setTitle("Scale Box")

            # Add scale items
            rbActions = (
                ('Expand', partial(self.expand, axis='XY')),
                ('Expand X', partial(self.expand, axis='X')),
                ('Expand Y', partial(self.expand, axis='Y')),
            )
            for action in rbActions:
                qaction = QtWidgets.QAction(action[0], self.menu)
                qaction.triggered.connect(action[1])
                self.menu.addAction(qaction)

        # Add plot items to the menu. First get a list of items in the view
        items = self.getViewBox().childGroup.childItems()
        # Figure out an item numbering for labelling purposes
        itemNumbers = [x for x in self.parentObject().childItems() if isinstance(x, (ExtendedDataItem, PlotDataItem, ImageItem))]
        itemNumbers = dict((x[1], x[0]) for x in enumerate(itemNumbers))

        # Then, filter items under the box
        logger.debug("Bounding rect for scale box is: %r", self.boundingRect())
        items = [i for i in items if i.collidesWithItem(self)]
        logger.debug("Items in the selection are: %r", items)

        # And finally add context menus
        self.addPlotContextMenus(items, itemNumbers, self.menu, self.boundingRect())

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
