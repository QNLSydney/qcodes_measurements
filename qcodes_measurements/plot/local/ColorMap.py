from .RemoteProcessWrapper import RPGWrappedBase, get_remote


class ColorMap(RPGWrappedBase):
    _base = "ColorMap"
    _all_colors: dict[str, "ColorMap"] = {}

    # Reserve names of local variables
    _name: str = ""

    def __init__(self, name, pos, color, *args, **kwargs):
        self._name = name
        super().__init__(pos, color, *args, **kwargs)

        # Keep track of all color maps, and add them to the list of available colormaps
        ColorMap._all_colors[name] = self
        # And add each of our colors to the new list
        remote_list = self.get_remote_list()
        remote_list[name] = {
            "ticks": [
                (float(position), tuple(int(component * 255) for component in rgb) + (255,))
                for position, rgb in zip(pos, color, strict=True)
            ],
            "mode": "rgb",
        }

    @classmethod
    def get_remote_list(cls):
        remote_list = get_remote().pyqtgraph.graphicsItems.GradientEditorItem.__getattr__(
            "Gradients", _returnType="proxy"
        )
        return remote_list

    @classmethod
    def get_color_map(cls, name):
        return cls._all_colors[name]

    @classmethod
    def color_maps(cls):
        return cls._all_colors

    @property
    def name(self):
        return self._name
