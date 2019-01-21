from qcodes import ChannelList, Parameter
from qcodes.instrument.base import InstrumentBase

from .gate import Gate, Ohmic, GateWrapper, OhmicWrapper, \
                  MDACGateWrapper, MDACOhmicWrapper, \
                  BBGateWrapper, BBOhmicWrapper

try:
    import MDAC
except ModuleNotFoundError:
    class _Blank():
        MDACChannel = type(None)
        MDAC = type(None)
    MDAC = _Blank()
from .bb import BBChan


class Device(InstrumentBase):
    def __init__(self, name):
        super().__init__(name)

        # Add gates to the device
        gates = ChannelList(self, "gates", GateWrapper)
        self.add_submodule("gates", gates)

        # Add ohmics to the device
        ohmics = ChannelList(self, "ohmics", OhmicWrapper)
        self.add_submodule("ohmics", ohmics)

    def add_gate(self, name, source, **kwargs):
        self.add_parameter(name, parameter_class=Gate, source=source, **kwargs)

    def add_ohmic(self, name, source, **kwargs):
        self.add_parameter(name, parameter_class=Ohmic, source=source, **kwargs)

    def add_parameter(self, name, parameter_class=Parameter, **kwargs):
        super().add_parameter(name, parameter_class=parameter_class, **kwargs)
        new_param = self.parameters[name]

        if isinstance(new_param, Gate):
            if isinstance(new_param.source, MDAC.MDACChannel):
                self.gates.append(MDACGateWrapper(new_param, name))
            elif isinstance(new_param.source, BBChan):
                self.gates.append(BBGateWrapper(new_param, name))
            else:
                self.gates.append(GateWrapper(new_param, name))
        elif isinstance(new_param, Ohmic):
            if isinstance(new_param.source, MDAC.MDACChannel):
                self.ohmics.append(MDACOhmicWrapper(new_param, name))
            elif isinstance(new_param.source, BBChan):
                self.ohmics.append(BBOhmicWrapper(new_param, name))
            else:
                self.ohmics.append(OhmicWrapper(new_param, name))

    def get_channel_controller(self, param):
        """
        Return the channel controller for a given parameter
        """
        if isinstance(param, Gate):
            return getattr(self.gates, param.name)
        elif isinstance(param, Ohmic):
            return getattr(self.ohmics, param.name)
        return None
