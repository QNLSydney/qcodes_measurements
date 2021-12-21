from PyQt5 import QtCore, QtGui, QtWidgets

from pyqtgraph import GraphicsWidget, GraphicsWidgetAnchor, LabelItem, Point, mkPen, mkBrush

from ...logging import get_logger
logger = get_logger("DraggableTextItem")

class DraggableTextItem(GraphicsWidget, GraphicsWidgetAnchor):
    def __init__(self, *args, text="", offset=None):
        GraphicsWidget.__init__(self)
        GraphicsWidgetAnchor.__init__(self)
        self.setFlag(self.ItemIgnoresTransformations)
        self.layout = QtWidgets.QGraphicsGridLayout()
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

        self.pen = mkPen(255, 255, 255, 100)
        self.brush = mkBrush(100, 100, 100, 50)

        self.updateSize()

    def setParentItem(self, parent):
        ret = GraphicsWidget.setParentItem(self, parent)
        if self.offset is not None:
            offset = Point(self.offset)
            anchorx = 1 if offset[0] <= 0 else 0
            anchory = 1 if offset[1] <= 0 else 0
            anchor = (anchorx, anchory)
            self.anchor(itemPos=anchor, parentPos=anchor, offset=offset)
        parent.items.append(self)
        return ret

    def paint(self, p, _options, _widget):
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
