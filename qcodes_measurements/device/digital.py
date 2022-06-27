"""
Support functions for digital gates
"""
from functools import partial

from qcodes import ChannelList
from qcodes.utils.validators import Numbers, Bool, MultiType, Enum

try:
    import MDAC
except ModuleNotFoundError:
    class _Blank():
        MDACChannel = type(None)
        MDAC = type(None)
    MDAC = _Blank()
from .bb import BBChan

from .channel_wrapper import ChannelWrapper
from .gate import Gate, MDACGateWrapper, BBGateWrapper
from .device import Device
from .states import DigitalMode, GateMode, ConnState

class DigitalGate(Gate):
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
    def __init__(self, name, source, v_high, v_low, v_hist=0.2, label=None,
                 io_mode=None, **kwargs):
        # Initialize the parameter
        super().__init__(name=name,
                         source=source,
                         label=label,
                         rate=None,
                         max_step=None,
                         default_mode=GateMode.FREE,
                         **kwargs)

        self.vals = MultiType(Bool(), Numbers())
        self._v_high = v_high
        self._v_low = v_low
        self.v_hist = v_hist
        if io_mode is None:
            io_mode = DigitalMode.UNDEF
        self.io_mode = io_mode
        # If a gate is locked, it's value won't be changed
        self.lock = False

    @property
    def v_high(self):
        """
        Get/Set high voltage level
        """
        return self._v_high
    @v_high.setter
    def v_high(self, val):
        self._v_high = val
        lock = self.lock
        self.lock = False
        if self.io_mode in DigitalMode.OUTPUT_MODES:
            self(self())
        self.lock = lock

    @property
    def v_low(self):
        """
        Get/Set low voltage level
        """
        return self._v_low
    @v_low.setter
    def v_low(self, val):
        self._v_low = val
        lock = self.lock
        self.lock = False
        if self.io_mode in DigitalMode.OUTPUT_MODES:
            self(self())
        self.lock = lock

    def get_raw(self): #pylint: disable=E0202
        """
        Return the state of the gate if within the defined setpoints, otherwise return 0
        """
        voltage = self.source.voltage()
        if abs(voltage - self.v_high) < self.v_hist:
            return 1
        if abs(voltage - self.v_low) < self.v_hist:
            return 0
        return -1

    def set_raw(self, value): #pylint: disable=E0202
        """
        Set the output of this digital gate, unless the gate is locked, in which case don't do
        anything
        """
        if self.lock: # Don't change value if the gate is locked.
            return
        if value:
            self.source.voltage(self.v_high)
        else:
            self.source.voltage(self.v_low)

class DigitalGateWrapper(ChannelWrapper):
    """
    Digital gate wrapper, which allows set/get of state

    Note: The accesses for various attributes can be confusing here:
        - self.gate - The underlying DigitalGate
        - self.parent - The underlying DAC/BB channel
    """
    def __init__(self, parent, name):
        if not isinstance(parent, DigitalGate):
            raise TypeError("DigitalGateWrapper can only wrap DigitalGates")
        super().__init__(parent, name)

        self.add_parameter("out",
                           get_cmd=parent,
                           set_cmd=parent,
                           vals=self.gate.vals)

        self.add_parameter("io_mode",
                           get_cmd=lambda: str(self.gate.io_mode),
                           set_cmd=self._set_io_mode,
                           vals=Enum(*DigitalMode))

        self.add_parameter("lock",
                           get_cmd=partial(getattr, self.gate, "lock"),
                           set_cmd=partial(setattr, self.gate, "lock"),
                           vals=Bool())

        self.add_parameter("v_high",
                           get_cmd=partial(getattr, self.gate, "v_high"),
                           set_cmd=partial(setattr, self.gate, "v_high"),
                           vals=self.parent.voltage.vals)

        self.add_parameter("v_low",
                           get_cmd=partial(getattr, self.gate, "v_low"),
                           set_cmd=partial(setattr, self.gate, "v_low"),
                           vals=self.parent.voltage.vals)

        # Note: we override the voltage parameter here, since by default the GateWrapper
        # pulls the voltage from self.gate, which for a digital gate returns 0/1
        self.parameters['voltage'] = self.parent.voltage

        # Once this object has been created, we can check the state of the underlying
        # gate and fill it in for the gate, it if is currently unknown
        if self.gate.io_mode == DigitalMode.UNDEF:
            state = self.state()
            self.gate.io_mode = DigitalMode.map_conn_state_to_digital_mode(state)


    def _set_io_mode(self, val):
        self.lock(False)
        if val in DigitalMode.INPUT_MODES:
            self.voltage(0)
        if val == DigitalMode.IN:
            self.smc()
        elif val == DigitalMode.OUT:
            self.dac()
        elif val == DigitalMode.PROBE_OUT:
            self.probe()
        elif val == DigitalMode.BUS_OUT:
            self.state(ConnState.DAC_BUS)
        elif val == DigitalMode.HIGH:
            self.dac()
            self.out(1)
            self.lock(True)
        elif val == DigitalMode.LOW:
            self.dac()
            self.out(0)
            self.lock(True)
        elif val == DigitalMode.GND:
            self.ground()
        elif val == DigitalMode.FLOAT:
            self.open()
        else:
            raise ValueError("Invalid IO Mode")
        self.gate.io_mode = val

class MDACDigitalGateWrapper(DigitalGateWrapper, MDACGateWrapper):
    """
    Digital gate wrapper of an MDAC, which allows set/get of state
    """
    def __init__(self, parent, name):
        super().__init__(parent, name)
        self.parent.filter.vals = Enum(0, 1, 2)
        self.parent.filter(0)

class BBDigitalGateWrapper(DigitalGateWrapper, BBGateWrapper):
    """
    Digital gate wrapper of an BB, which allows set/get of state
    """

class DigitalDevice(Device):
    """
    Device which expects digital control as well as potential analog
    voltages
    """
    def __init__(self, name):
        super().__init__(name)

        # Add digital gates to the device
        digital_gates = ChannelList(self, "digital_gates", DigitalGateWrapper)
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

    def add_digital_gate(self, name, source, io_mode=None, **kwargs):
        if "initial_value" in kwargs and io_mode is not None:
            initial_value = kwargs["initial_value"]
            del kwargs["initial_value"]
        else:
            initial_value = None
        self.add_parameter(name, parameter_class=DigitalGate, source=source,
                           v_high=self.v_high(), v_low=self.v_low(), io_mode=io_mode,
                           **kwargs)
        if io_mode is not None:
            gate = self.get_channel_controller(self.parameters[name])
            if gate.io_mode() != io_mode:
                gate.io_mode(io_mode)
            if initial_value is not None:
                self.parameters[name](initial_value)

    def store_new_param(self, new_param):
        if isinstance(new_param, DigitalGate):
            if isinstance(new_param.source, MDAC.MDACChannel):
                self.digital_gates.append(MDACDigitalGateWrapper(new_param, new_param.name))
            elif isinstance(new_param.source, BBChan):
                self.digital_gates.append(BBDigitalGateWrapper(new_param, new_param.name))
            else:
                self.digital_gates.append(DigitalGateWrapper(new_param, new_param.name))
        else:
            super().store_new_param(new_param)

    def _update_vhigh(self, new_val):
        for gate in self.digital_gates:
            gate.v_high(new_val)
        self._v_high = new_val
    def _update_vlow(self, new_val):
        for gate in self.digital_gates:
            gate.v_low(new_val)
        self._v_low = new_val

    def get_channel_controller(self, param):
        """
        Return the channel controller for a given parameter
        """
        if isinstance(param, DigitalGate):
            return getattr(self.digital_gates, param.name)
        return super().get_channel_controller(param)
