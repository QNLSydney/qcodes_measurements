from .RemoteProcessWrapper import RPGWrappedBase


class _MeshPlot(RPGWrappedBase):
    @property
    def data(self):
        return self.__getattr__("mesh_data", _returnType="value", _location="remote")

    @property
    def polygons(self):
        # Qt polygons cannot be pickled; keep their container in the plot process.
        return self._base_inst.__getattr__("polygons", _returnType="proxy")


class VoronoiPlot(_MeshPlot):
    """Voronoi plot."""

    _base = "VoronoiPlot"


class ColorMesh(_MeshPlot):
    """Colour mesh plot."""

    _base = "ColorMesh"
