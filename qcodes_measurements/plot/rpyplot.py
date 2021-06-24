# -*- coding: utf-8 -*-

import pyqtgraph
from pyqtgraph import GraphicsLayoutWidget, \
                      PlotItem, PlotDataItem, ImageItem, \
                      HistogramLUTItem, ColorMap, TableWidget, \
                      LabelItem, LegendItem, AxisItem, \
                      setConfigOption, setConfigOptions, getConfigOption, \
                      mkPen, mkBrush

from . import remote
from .remote.colors import COLORMAPS, DEFAULT_CMAP
from .remote.PlotWindow import ExtendedPlotWindow
from .remote.DraggableTextItem import DraggableTextItem
from .remote.PlotDataItem import ExtendedPlotDataItem
from .remote.PlotItem import ExtendedPlotItem
from .remote.ImageItem import ExtendedImageItem, ImageItemWithHistogram
from .remote.VoronoiPlot import VoronoiPlot
from .remote.ColorMesh import ColorMesh
from ..logging import get_logger, set_log_level

__all__ = ['remote', 'ExtendedPlotWindow', 'DraggableTextItem', 'ExtendedPlotDataItem',
           'ExtendedPlotItem', 'ExtendedImageItem', 'ImageItemWithHistogram',
           'GraphicsLayoutWidget', 'AxisItem', 'PlotItem', 'HistogramLUTItem',
           'ColorMap', 'LegendItem', 'PlotDataItem', 'ImageItem', 'VoronoiPlot',
           'TableWidget', 'LabelItem', 'setConfigOption', 'setConfigOptions',
           'ColorMesh', 'getConfigOption', 'pyqtgraph', 'COLORMAPS', 'DEFAULT_CMAP',
           'get_logger', 'set_log_level', 'mkPen', 'mkBrush']
