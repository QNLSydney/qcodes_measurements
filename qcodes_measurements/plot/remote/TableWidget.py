from pyqtgraph import TableWidget as PGTableWidget


class TableWidget(PGTableWidget):
    @staticmethod
    def _text(item):
        return item.text() if item is not None else ""

    def getData(self):
        return {
            self._text(self.verticalHeaderItem(row)): [
                self._text(self.item(row, col)) for col in range(self.columnCount())
            ]
            for row in range(self.rowCount())
        }

    def getHorizontalHeaders(self):
        return [self._text(self.horizontalHeaderItem(col)) for col in range(self.columnCount())]
