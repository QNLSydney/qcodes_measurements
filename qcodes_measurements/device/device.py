from qcodes.instrument import ChannelList, InstrumentBase
from qcodes.instrument.instrument_base import TParameter
from qcodes.parameters import Parameter

from .gate import (
    BBGateWrapper,
    BBOhmicWrapper,
    Gate,
    GateWrapper,
    MDACGateWrapper,
    MDACOhmicWrapper,
    Ohmic,
    OhmicWrapper,
)

try:
    import MDAC  # type: ignore
except ModuleNotFoundError:

    class _Blank:
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

    def add_gate(self, name, source, state=None, **kwargs):
        if "initial_value" in kwargs and state is not None:
            initial_value = kwargs["initial_value"]
            del kwargs["initial_value"]
        else:
            initial_value = None
        self.add_parameter(name, parameter_class=Gate, source=source, **kwargs)
        if state is not None:
            gate = self.get_channel_controller(self.parameters[name])
            assert isinstance(gate, GateWrapper)
            if gate.state() != state:
                gate.state(state)
        if initial_value is not None:
            self.parameters[name](initial_value)

    def add_ohmic(self, name, source, state=None, **kwargs):
        self.add_parameter(name, parameter_class=Ohmic, source=source, **kwargs)
        if state is not None:
            gate = self.get_channel_controller(self.parameters[name])
            assert isinstance(gate, OhmicWrapper)
            gate.state(state)

    def add_parameter(  # type: ignore
        self, name, parameter_class: type[Parameter] | None = Parameter, **kwargs
    ) -> Parameter:
        """
        Add a new parameter to the instrument and store it in an appropriate list, if
        we are keeping track of gates/ohmics.
        """
        new_param = super().add_parameter(
            name, parameter_class=parameter_class, **kwargs
        )
        self.store_new_param(new_param)
        return new_param

    def store_new_param(self, new_param):
        """
        Store the new parameter in an appropriate list of gates or ohmics
        """
        if isinstance(new_param, Gate):
            if isinstance(new_param.source, MDAC.MDACChannel):
                self.gates.append(MDACGateWrapper(new_param, new_param.name))
            elif isinstance(new_param.source, BBChan):
                self.gates.append(BBGateWrapper(new_param, new_param.name))
            else:
                self.gates.append(GateWrapper(new_param, new_param.name))
        elif isinstance(new_param, Ohmic):
            if isinstance(new_param.source, MDAC.MDACChannel):
                self.ohmics.append(MDACOhmicWrapper(new_param, new_param.name))
            elif isinstance(new_param.source, BBChan):
                self.ohmics.append(BBOhmicWrapper(new_param, new_param.name))
            else:
                self.ohmics.append(OhmicWrapper(new_param, new_param.name))

    def get_channel_controller(self, param):
        """
        Return the channel controller for a given parameter
        """
        if isinstance(param, Gate):
            return getattr(self.gates, param.name)
        elif isinstance(param, Ohmic):
            return getattr(self.ohmics, param.name)
        return None

    def lookup_source(self, source):
        """
        Check if a given parameter is controlled through the device.
        Example: sample.lookup_source(mdac.ch01) -> sample.gates.LW1
        """
        for gate in self.gates:
            if gate.gate.source is source:
                return gate
        for ohmic in self.ohmics:
            if ohmic.gate.source is source:
                return ohmic
        return None
