from qcodes import ParamSpec

from .RemoteProcessWrapper import RPGWrappedBase

class TableWidget(RPGWrappedBase):
    """
    Table
    """
    _base = "TableWidget"

    def getData(self):
        """
        Get the data from the table.
        """
        nRows = self.rowCount()
        nCols = self.columnCount()
        rowTitles = []
        rowData = []
        for i in range(nRows):
            rowTitles.append(self.verticalHeaderItem(i).text())
            data = []
            for j in range(nCols):
                data.append(self.item(i, j).text())
            rowData.append(data)
        return {title: data for title, data in zip(rowTitles, rowData)}

    def getHorizontalHeaders(self):
        """
        Get the headers from the table.
        """
        nCols = self.columnCount()
        colTitles = []
        for i in range(nCols):
            colTitles.append(self.horizontalHeaderItem(i).text())
        return colTitles

class LegendItem(RPGWrappedBase):
    """
    Legend handling code
    """
    _base = "LegendItem"

class TextItem(RPGWrappedBase):
    _base = "DraggableTextItem"
    _ANCHORS = {'tl': (0,0),
                'tr': (1,0),
                'bl': (0,1),
                'br': (1,1)}

    def setParentItem(self, p):
        self._base_inst.setParentItem(p)

    def anchor(self, anchor):
        """
        Put this text box in a position relative to
        (tl, tr, bl, br)
        """
        anchor_point = TextItem._ANCHORS[anchor]
        self._base_inst.anchor(itemPos=anchor_point,
                               parentPos=anchor_point,
                               offset=(0,0))

    @property
    def offset(self):
        return self.getOffset()
    @offset.setter
    def offset(self, offs):
        if not isinstance(offs, tuple) or len(offs) != 2:
            raise ValueError("Must be a tuple (x, y)")
        self.setOffset(offs)

    @property
    def text(self):
        text = "".join(self.getText()).replace("<br>", "\n")
        return text
    @text.setter
    def text(self, text):
        # Replace new lines with HTML line breaks
        text = text.replace("\r\n", "\n")
        text = text.replace("\r", "\n")
        text = text.replace("\n", "<br>")
        self.setText(str(text))

class PlotAxis(RPGWrappedBase):
    _base = "AxisItem"

    @property
    def label(self):
        return self.labelText
    @label.setter
    def label(self, text):
        self.setLabel(text=text, units=self.labelUnits)

    @property
    def units(self):
        return self.labelUnits
    @units.setter
    def units(self, units):
        self.setLabel(text=self.labelText, units=units)

    @property
    def unit(self):
        return self.labelUnits
    @unit.setter
    def unit(self, units):
        self.setLabel(text=self.labelText, units=units)

    def checkParamspec(self, paramspec: ParamSpec):
        if self.label != paramspec.label:
            return False
        if self.unit != paramspec.unit:
            return False
        return True
    def setParamspec(self, paramspec: ParamSpec):
        self.label = paramspec.label
        self.unit = paramspec.unit
    paramspec = property(None, setParamspec)
