# -*- coding: utf-8 -*-

from math import ceil
import warnings

from pyqtgraph import *
from pyqtgraph.exporters import *
import pyqtgraph.multiprocess as mp

from PyQt5.QtWidgets import QApplication, QMessageBox, QMainWindow, QAction

from numpy import linspace, min, max, ndarray

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
        p.setPen(fn.mkPen(255,255,255,100))
        p.setBrush(fn.mkBrush(100,100,100,50))
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

class ExtendedPlotItem(PlotItem):
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
            """
            addItem does not keep track of images, let's add it ourselves
            """
            self.dataItems.append(item)

    def listDataItems(self):
        """
        Create a picklable list of data items.
        """
        data_items = super().listDataItems()
        data_items = [mp.proxy(item) for item in data_items]
        return data_items


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

    @classmethod
    def getWindows(cls):
        windows = [mp.proxy(item) for item in cls._windows]
        return windows

class ImageItemWithHistogram(ImageItem):
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