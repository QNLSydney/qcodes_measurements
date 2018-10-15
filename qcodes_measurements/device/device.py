from qcodes import Instrument, ChannelList

from .gate import *

try:
    import qcodes.instrument_drivers.qnl.MDAC as MDAC
except ModuleNotFoundError:
    class _Blank(object):
        pass
    MDAC = _Blank()
    MDAC.MDACChannel = type(None)
    MDAC.MDAC = type(None)


class Device(Instrument):
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
        super().add_parameter(name, parameter_class, **kwargs)
        new_param = self.parameters[name]

        if isinstance(new_param, Gate):
            if isinstance(new_param.source, MDAC.MDACChannel):
                self.gates.append(MDACGateWrapper(new_param, name))
            else:
                self.gates.append(GateWrapper(new_param, name))
        elif isinstance(new_param, Ohmic):
            if isinstance(new_param.source, MDAC.MDACChannel):
                self.ohmics.append(MDACOhmicWrapper(new_param, name))
            else:
                self.ohmics.append(OhmicWrapper(new_param, name))
