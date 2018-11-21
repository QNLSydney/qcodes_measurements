"""
Support functions for digital gates
"""
from functools import partial

from qcodes import Instrument, InstrumentChannel, ChannelList, Parameter
from qcodes.utils.validators import Numbers, Bool, MultiType

try:
    import MDAC
except ModuleNotFoundError:
    class _Blank():
        MDACChannel = type(None)
        MDAC = type(None)
    MDAC = _Blank()
from .bb import BBChan

from .gate import GateWrapper, MDACGateWrapper, BBGateWrapper
from .device import Device

class DigitalGate(Parameter):
    """
    Represents a digital gate, i.e. one that has two possible values, v_high and v_low.
    This will usually be part of a DigitalDevice which will control the values
    of v_high/v_low as parameters.
    Parameters:
        source: The voltage source
        name: Gate name
        v_high: high voltage level
        v_low: low voltage level
        v_hist: range around v_high/v_low around which a high/low value will be read
    """
    def __init__(self, source, name, v_high, v_low, v_hist=0.2, label=None):
        # Check that the source is a valid voltage source
        if not isinstance(source, (Instrument, InstrumentChannel)):
            raise TypeError("The source must be an instrument or instrument channel.")
        if not hasattr(source, "voltage") or not hasattr(source.voltage, "set"):
            raise TypeError("The source for a gate must be able to set a voltage")

        if label is None:
            label = name

        # Initialize the parameter
        super().__init__(name=name,
                         label=label,
                         unit="V",
                         vals=MultiType(Bool(), Numbers()))
        self.source = source
        self.v_high = v_high
        self.v_low = v_low
        self.v_hist = v_hist

    def get_raw(self):
        if abs(self.source.voltage() - self.v_high) < self.v_hist:
            return 1
        return 0

    def set_raw(self, val):
        if val:
            self.source.voltage(self.v_high)
        else:
            self.source.voltage(self.v_low)

class DigitalDevice(Device):
    """
    Device which expects digital control as well as potential analog
    voltages
    """
    def __init__(self, name):
        super().__init__(name)

        # Add digital gates to the device
        digital_gates = ChannelList(self, "digital_gates", GateWrapper)
        self.add_submodule("digital_gates", digital_gates)

        # Add digital parameters
        self._v_high = 1.8
        self._v_low = 0
        self.add_parameter("v_high",
                           initial_value=1.8,
                           get_cmd=partial(getattr, self, "_v_high"),
                           set_cmd=self._update_vhigh,
                           vals=Numbers())
        self.add_parameter("v_low",
                           initial_value=0,
                           get_cmd=partial(getattr, self, "_v_low"),
                           set_cmd=self._update_vlow,
                           vals=Numbers())

    def add_digital_gate(self, name, source, **kwargs):
        self.add_parameter(name, parameter_class=DigitalGate, source=source,
                           v_high=self.v_high(), v_low=self.v_low(), **kwargs)

    def add_parameter(self, name, parameter_class=Parameter, **kwargs):
        super().add_parameter(name, parameter_class, **kwargs)
        new_param = self.parameters[name]

        if isinstance(new_param, DigitalGate):
            if isinstance(new_param.source, MDAC.MDACChannel):
                self.digital_gates.append(MDACGateWrapper(new_param, name))
            elif isinstance(new_param.source, BBChan):
                self.digital_gates.append(BBGateWrapper(new_param, name))
            else:
                self.digital_gates.append(GateWrapper(new_param, name))

    def _update_vhigh(self, new_val):
        for gate in self.digital_gates:
            gate.v_high = new_val
        self._v_high = new_val
    def _update_vlow(self, new_val):
        for gate in self.digital_gates:
            gate.v_low = new_val
        self._v_low = new_val
