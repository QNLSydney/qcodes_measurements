import struct
import itertools
from typing import Union

import numpy as np
import pandas as pd

from PyQt5 import QtCore, QtGui

from .MeshPlot import MeshPlot
from .DataItem import ExtendedDataItem
from ...logging import get_logger
logger = get_logger("ColorMesh")

axis_input = Union[None, str, np.ndarray]
data_type = Union[None, np.ndarray, pd.Series]

class ColorMesh(ExtendedDataItem, MeshPlot):
    def __init__(self, *args,
                 data_frame: pd.DataFrame=None, data:axis_input = None, x_axis:axis_input = None, y_axis: axis_input = None,
                 colormap=None, **kwargs):
        super().__init__(*args, positions=None, data=None, colormap=colormap, **kwargs)

        self.x_axis: data_type = None
        self.y_axis: data_type = None
        self.data: data_type = None
        # We don't use positions so it can be deleted
        del self.positions

        if data_frame is not None:
            self.setPandasData(data_frame, data, x_axis, y_axis)
        elif data is not None and isinstance(data, np.ndarray):
            self.setNumpyData(data, x_axis, y_axis)
        elif data is not None or x_axis is not None or y_axis is not None:
            raise ValueError("Could not interpret combination of axis/data values passed.")


    ###
    # Functions relating to the size of the image
    def calc_lims(self):
        if self.data is None:
            self.xmin, self.xmax = 0, 0
            self.ymin, self.ymax = 0, 0
            return
        self.xmin, self.xmax = np.min(self.x_axis), np.max(self.x_axis)
        self.ymin, self.ymax = np.min(self.y_axis), np.max(self.y_axis)
        logger.debug("Calculated limits (%f, %f) - (%f, %f)", self.xmin, self.ymin,
                     self.xmax, self.ymax)

    def width(self):
        return self.xmax - self.xmin

    def height(self):
        return self.ymax - self.ymin

    def boundingRect(self):
        tl = QtCore.QPointF(self.xmin, self.ymin)
        br = QtCore.QPointF(self.xmax, self.ymax)
        return QtCore.QRectF(tl, br)

    ###
    # Functions relating to setting data
    def setData(self, positions, data):
        raise ValueError("Data must be set using setNumpyData or setPandasData for a ColorMesh")

    def setNumpyData(self, data: np.ndarray, x_axis: np.ndarray, y_axis:np.ndarray):
        # Validate data
        if not isinstance(data, np.ndarray):
            raise TypeError(f"data must be a numpy array. Got {type(data)}.")
        if data.ndim != 2:
            raise ValueError(f"data must be a 2D grid of points. Got {data.ndim} dimensions instead.")

        # Validate axes
        if x_axis is None or y_axis is None:
            raise ValueError("If data is given as an array, x_axis and y_axis must also be given as 2D arrays.")
        if not isinstance(x_axis, np.ndarray) or x_axis.ndim != 2:
            raise ValueError("x_axis must be a 2-dimensional ndarray corresponding to the x-coordinate of the "
                             "points in the data array.")
        if not isinstance(y_axis, np.ndarray) or y_axis.ndim != 2:
            raise ValueError("y_axis must be a 2-dimensional ndarray corresponding to the y-coordinate of the "
                             "points in the data array.")
        self.x_axis = x_axis
        self.y_axis = y_axis
        self.data = data

        # Finally, generate polygons
        self._reshape_data()

    def setPandasData(self, data_frame, data:Union[None, str] = None,
                      x_axis:Union[None, str] = None, y_axis:Union[None, str] = None):
        """
        Set the data in the mesh from a pandas data frame.

        Args:
            data_frame: The pandas data frame containing the data and a grid of (potentially non-linear) ax.
                        The dataframe must contain a 2D index, and may contain multiple columns, one of which should
                        be the data, and optionally the new x-axis and y-axis.
            data:       The name of the data column
            x_axis:     The name of the x-axis column
            y_axis:     The name of the y-axis column
        """
        # Validate inputs
        if data is not None and not isinstance(data, str):
            raise TypeError("If a dataframe is passed, data must be a string "
                            "containing the name of the column in the dataframe. "
                            f"Got {type(data)}.")
        if x_axis is not None and not isinstance(x_axis, str):
            raise TypeError("If a dataframe is passed, x_axis must be a string "
                            "containing the name of the column in the dataframe or None. "
                            f"Got {type(x_axis)}.")
        if y_axis is not None and not isinstance(y_axis, str):
            raise TypeError("If a dataframe is passed, x_axis must be a string "
                            "containing the name of the column in the dataframe or None. "
                            f"Got {type(y_axis)}.")

        # Figure out which columns is the data
        if data is None:
            if data_frame.columns.size > 1:
                raise ValueError(f"Can't figure out data column. "
                                 f"Available columns are: {data_frame.columns}")
            self.data = data_frame[data_frame.columns[0]]
        else:
            self.data = data_frame[data]
        # And double check that we have a 2D grid of data
        if self.data.ndim != 2:
            raise ValueError("Data must be arranged as a 2D grid.")

        # Figure out which column is the x and y-axis
        if x_axis is None:
            self.x_axis = data_frame.index.values
            self.x_axis = np.tile(self.x_axis, self.data.shape[1]).T
        else:
            self.x_axis = data_frame[x_axis]

        if y_axis is None:
            self.y_axis = data_frame.columns.levels[-1]
            self.y_axis = np.tile(self.y_axis, self.data.shape[0])
        else:
            self.y_axis = data_frame[y_axis]

        # Finally, generate polygons
        self._reshape_data()

    def _reshape_data(self):
        # Validate data shape, and reshape data if necessary
        if self.x_axis.shape != self.y_axis.shape:
            raise ValueError(f"X-axis and y-axis shapes are different. Got {self.x_axis.shape} and {self.y_axis.shape} respectively.")
        if self.x_axis.shape == self.data.shape:
            # We need to calculate the average of neighbouring points in the data, since the point locations
            # are the corners of the polygons that make up the mesh.
            self.data = (self.data[:-1,:-1] + self.data[1:,:-1] + self.data[:-1,1:] + self.data[1:,1:])/4
        elif self.x_axis.shape[0] != self.data.shape[0]+1 or self.x_axis.shape[1] != self.data.shape[1]+1:
            raise ValueError(f"Data shape ({self.data.shape}) is incompatible with axis shapes ({self.x_axis.shape})")

        logger.debug("Loaded data of size %r", self.data.shape)
        self.data = self.data.flatten()

        logger.debug("Generating histogram")
        # Update histogram and autorange
        hist, bins = np.histogram(self.data, "auto")
        newBins = np.ndarray(bins.size+1)
        newHist = np.ndarray(hist.size+2)
        newBins[0] = bins[0]
        newBins[-1] = bins[-1]
        newBins[1:-1] = (bins[:-1] + bins[1:])/2
        newHist[[0,-1]] = 0
        newHist[1:-1] = hist
        logger.debug("Generated histogram of size %d", newBins.size)
        self._LUTitem.plot.setData(newBins, newHist)
        self._LUTitem.setLevels(newBins[0], newBins[-1])
        self._LUTitem.plot.getViewBox().itemBoundsChanged(self._LUTitem.plot)

        # Generate polygons
        self.calculate_polygons()
        # And recalculate bounds
        self.calc_lims()
        self.updateRGBData()
        # Force viewport update
        self.getViewBox().itemBoundsChanged(self)
        self.update()

    ###
    # Functions relating to drawing

    def calculate_polygons(self):
        """
        Convert the raw data into a mesh plot
        """
        # Then generate a list of polygons for finite regions
        logger.debug("Generating Polygons")
        self.polygons.clear()
        xsize, ysize = self.x_axis.shape[0]-1, self.y_axis.shape[1]-1
        for (x, y) in itertools.product(range(xsize), range(ysize)):
            buf = bytearray(4 + 5*16)
            buf[3] = 5
            struct.pack_into('>2d', buf, 4,  self.x_axis[x, y], self.y_axis[x, y])
            struct.pack_into('>2d', buf, 20, self.x_axis[x+1, y], self.y_axis[x+1, y])
            struct.pack_into('>2d', buf, 36, self.x_axis[x+1, y+1], self.y_axis[x+1, y+1])
            struct.pack_into('>2d', buf, 52, self.x_axis[x, y+1], self.y_axis[x, y+1])
            struct.pack_into('>2d', buf, 68, self.x_axis[x, y], self.y_axis[x, y])
            ds = QtCore.QDataStream(QtCore.QByteArray.fromRawData(buf))
            poly = QtGui.QPolygonF()
            ds >> poly # pylint: disable=pointless-statement
            self.polygons.append((x*ysize + y, poly))

        logger.info("Done")
