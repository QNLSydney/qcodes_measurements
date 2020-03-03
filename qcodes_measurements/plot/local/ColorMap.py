from typing import Dict

from .RemoteProcessWrapper import RPGWrappedBase, get_remote

class ColorMap(RPGWrappedBase):
    _base = "ColorMap"
    _all_colors: Dict[str, "ColorMap"] = {}

    # Reserve names of local variables
    _name = None

    def __init__(self, name, pos, color, *args, **kwargs):
        self._name = None
        super().__init__(pos, color, *args, **kwargs)

        # Keep track of all color maps, and add them to the list of available colormaps
        ColorMap._all_colors[name] = self
        # And add each of our colors to the new list
        remote_list = self.get_remote_list()
        remote_list[name] = {
            'ticks': list(zip(pos, (tuple(int(y*255) for y in x) + (255,) for x in color))),
            'mode': 'rgb'
        }

    @classmethod
    def get_remote_list(cls):
        remote_list = get_remote().graphicsItems.GradientEditorItem.__getattr__('Gradients',
                                                                    _returnType="proxy")
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