from .RemoteProcessWrapper import RPGWrappedBase, get_remote
from .UIItems import TableWidget
from .PlotItem import PlotItem

class BasePlotWindow(RPGWrappedBase):
    _base = "GraphicsLayoutWidget"

    def __init__(self, *args, title=None, **kwargs):
        """
        Create a new remote plot window, with title and size given
        """
        super().__init__(*args, **kwargs)
        self.show()

        # Change plot title if given
        if title is not None:
            self.win_title = title

    @property
    def win_title(self):
        return self.windowTitle()
    @win_title.setter
    def win_title(self, title):
        self.setWindowTitle(str(title))

    @property
    def size(self):
        rsize = self._base_inst.size()
        return (rsize.width(), rsize.height())

    def addPlot(self, row=None, col=None, rowspan=1, colspan=1, **kargs):
        """
        Create a PlotItem and place it in the next available cell (or in the cell specified)
        All extra keyword arguments are passed to :func:`PlotItem.__init__ <pyqtgraph.PlotItem.__init__>`
        Returns the created item.
        """
        plot = PlotItem(**kargs)
        self.addItem(plot, row, col, rowspan, colspan)
        return plot

    @property
    def windows(self):
        raise NotImplementedError("Can't do this on a base plot window")

    @property
    def items(self):
        return self.getLayoutItems(_returnType="proxy")

class PlotWindow(BasePlotWindow):
    _base = "ExtendedPlotWindow"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def export(self, fname, export_type="image"):
        return self._base_inst.export(fname, export_type)

    @property
    def windows(self):
        return self.getWindows()

    @property
    def items(self):
        return self.getLayoutItems(_returnType="proxy")

    @property
    def table(self):
        for item in self.items:
            if isinstance(item, TableWidget):
                return item
        return None

    @classmethod
    def getWindows(cls):
        windows = get_remote().ExtendedPlotWindow.getWindows(_returnType="proxy")
        num_windows = len(windows)
        local_windows = []
        for i in range(num_windows):
            local_windows.append(RPGWrappedBase.autowrap(windows[i]))
        return local_windows

    @classmethod
    def find_by_id(cls, wid):
        windows = cls.getWindows()
        for window in windows:
            items = window.items
            for item in items:
                if isinstance(item, PlotItem):
                    if item.plot_title.endswith("(id: {})".format(wid)):
                        return window
        return None
