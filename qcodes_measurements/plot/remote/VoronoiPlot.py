import struct

import scipy.spatial as spatial

from PyQt5 import QtCore, QtGui

from .MeshPlot import MeshPlot
from .DataItem import ExtendedDataItem
from ...logging import get_logger
logger = get_logger("VoronoiPlot")

class VoronoiPlot(ExtendedDataItem, MeshPlot):
    def __init__(self, *args, positions=None, data=None, colormap=None, **kwargs):
        super().__init__(*args, positions=positions, data=data, colormap=colormap, **kwargs)

        # Reserve spot for voronoi
        self.voronoi = None

    ###
    # Functions relating to drawing

    def calculate_polygons(self):
        """
        Convert the raw data into a voronoi plot
        """
        logger.debug("Generating voronoi graph")
        if len(self.positions) > 2:
            self.voronoi = spatial.Voronoi(self.positions)
        else:
            return

        # Then generate a list of polygons for finite regions
        logger.debug("Generating Polygons")
        self.polygons.clear()
        for ind, p in enumerate(self.voronoi.point_region):
            p_vertices = self.voronoi.regions[p]
            n_vertices = len(p_vertices)
            buf = bytearray(4 + n_vertices*16)
            struct.pack_into('>i', buf, 0, n_vertices)
            for i, point in enumerate(p_vertices):
                if point == -1:
                    break
                point = self.voronoi.vertices[point]
                struct.pack_into('>2d', buf, 4+i*16, point[0], point[1])
            else:
                ds = QtCore.QDataStream(QtCore.QByteArray.fromRawData(buf))
                poly = QtGui.QPolygonF()
                ds >> poly # pylint: disable=pointless-statement
                self.polygons.append((ind, poly))

        logger.debug("Clearing Voronoi")
        # Clear the voronoi
        del self.voronoi
        self.voronoi = None

        logger.info("Done")
