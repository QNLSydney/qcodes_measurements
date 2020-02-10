import logging
import colorsys

from pyqtgraph import PlotItem, ImageItem, PlotDataItem, LegendItem
from pyqtgraph.multiprocess import proxy

from .ViewBox import CustomViewBox
from .PlotDataItem import ExtendedPlotDataItem

logger = logging.getLogger("rpyplot.PlotItem")
logger.setLevel(logging.DEBUG)

class ExtendedPlotItem(PlotItem):
    def __init__(self, *args, **kwargs):
        """
        Create a new PlotItem, same as a base plotitem, but with a few
        extra pieces of functionality.
          - Keep track of images and allow proxying
          - Use a custom view box, such that we can do common tasks
        """
        if 'viewBox' not in kwargs:
            vb = CustomViewBox()
            kwargs['viewBox'] = vb
        super().__init__(*args, **kwargs)

        # Keep track of context menus for items in this plot
        self.itemMenus = {}

    def addItem(self, item, *args, **kwargs):
        super().addItem(item, *args, **kwargs)
        if isinstance(item, ImageItem):
            # addItem does not keep track of images, let's add it ourselves
            self.dataItems.append(item)

    def listItems(self, proxy_items=False):
        """
        Create a pickleable list of items in the plot
        """
        if proxy_items:
            items = [proxy(item) for item in self.items]
        else:
            items = self.items
        return items

    def listDataItems(self, proxy_items=False):
        """
        Create a picklable list of data items.
        """
        data_items = super().listDataItems()
        if proxy_items:
            data_items = [proxy(item) for item in data_items]
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

    def addLegend(self, size=None, offset=(30, 30)):
        """
        Reimplement addLegend to check if legend already exists.
        The default one should do this, but doesn't
        seem to work on our extended version for some reason?
        """
        if self.legend is None:
            self.legend = LegendItem(size, offset)
            self.legend.setParentItem(self.vb)
        return self.legend
