# -*- coding: utf-8 -*-
import pyqtgraph
from pyqtgraph import GraphicsLayoutWidget, \
                      PlotItem, PlotDataItem, ImageItem, \
                      HistogramLUTItem, ColorMap, TableWidget, \
                      LabelItem, LegendItem, AxisItem, \
                      setConfigOption, setConfigOptions, getConfigOption

from .remote.colors import COLORMAPS, DEFAULT_CMAP
from .remote.PlotWindow import ExtendedPlotWindow
from .remote.DraggableTextItem import DraggableTextItem
from .remote.PlotDataItem import ExtendedPlotDataItem
from .remote.PlotItem import ExtendedPlotItem
from .remote.ImageItem import ExtendedImageItem, ImageItemWithHistogram

__all__ = ['ExtendedPlotWindow', 'DraggableTextItem', 'ExtendedPlotDataItem',
           'ExtendedPlotItem', 'ExtendedImageItem', 'ImageItemWithHistogram',
           'GraphicsLayoutWidget', 'AxisItem', 'PlotItem', 'HistogramLUTItem',
           'ColorMap', 'LegendItem', 'PlotDataItem', 'ImageItem', 'TableWidget',
           'LabelItem', 'setConfigOption', 'setConfigOptions', 'getConfigOption',
           'pyqtgraph', 'COLORMAPS', 'DEFAULT_CMAP']
