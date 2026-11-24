"""
Support functions for digital gates
"""

from collections.abc import Sequence
from functools import partial
from typing import Unpack

from qcodes.instrument import ChannelList
from qcodes.instrument.instrument_base import InstrumentBaseKWArgs, TParameter
from qcodes.parameters import ParameterBase
from qcodes.validators import Bool, Enum, MultiType, Numbers

from ..drivers._mdac import is_mdac_channel
from .bb import BBChan
from .channel_wrapper import ChannelWrapper
from .device import ContactDefinitions, Device
from .gate import BBGateWrapper, Gate, MDACGateWrapper
from .states import ConnState, DigitalMode


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

    def __init__(
        self,
        name,
        source,
        v_high,
        v_low,
        v_hist=0.2,
        label=None,
        io_mode=None,
        **kwargs,
    ):
        self._v_high = v_high
        self._v_low = v_low
        self.v_hist = v_hist
        self.io_mode = DigitalMode.UNDEF if io_mode is None else io_mode
        self.lock = False
        kwargs.setdefault("limits", None)
        kwargs.setdefault("vals", MultiType(Bool(), Numbers()))
        kwargs.setdefault("unit", "")

        # Initialize the parameter
        super().__init__(
            name=name,
            source=source,
            label=label,
            **kwargs,
        )

    @property
    def v_high(self):
        """
        Get/Set high voltage level
        """
        return self._v_high

    @v_high.setter
    def v_high(self, val):
        value = self() if self.io_mode in DigitalMode.OUTPUT_MODES else None
        self._v_high = val
        lock = self.lock
        self.lock = False
        try:
            if value is not None and value != -1:
                self(value)
        finally:
            self.lock = lock

    @property
    def v_low(self):
        """
        Get/Set low voltage level
        """
        return self._v_low

    @v_low.setter
    def v_low(self, val):
        value = self() if self.io_mode in DigitalMode.OUTPUT_MODES else None
        self._v_low = val
        lock = self.lock
        self.lock = False
        try:
            if value is not None and value != -1:
                self(value)
        finally:
            self.lock = lock

    def _voltage_to_raw(self, value):
        """
        Return the logical state near a defined level, or -1 for an unknown level.
        """
        if abs(value - self.v_high) < self.v_hist:
            return 1
        if abs(value - self.v_low) < self.v_hist:
            return 0
        return -1

    def _raw_to_voltage(self, value):
        return self.v_high if value else self.v_low

    def set_raw(self, value):  # pylint: disable=E0202
        """
        Set the output of this digital gate, unless the gate is locked, in which case don't do
        anything
        """
        if self.lock:  # Don't change value if the gate is locked.
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

        self.add_parameter("out", get_cmd=parent, set_cmd=parent, vals=self.gate.vals)

        self.add_parameter(
            "io_mode",
            get_cmd=lambda: self.gate.io_mode,
            set_cmd=self._set_io_mode,
            vals=Enum(*DigitalMode),
        )

        self.add_parameter(
            "lock",
            get_cmd=partial(getattr, self.gate, "lock"),
            set_cmd=partial(setattr, self.gate, "lock"),
            vals=Bool(),
        )

        self.add_parameter(
            "v_high",
            get_cmd=partial(getattr, self.gate, "v_high"),
            set_cmd=partial(setattr, self.gate, "v_high"),
            vals=self.parent.voltage.vals,
        )

        self.add_parameter(
            "v_low",
            get_cmd=partial(getattr, self.gate, "v_low"),
            set_cmd=partial(setattr, self.gate, "v_low"),
            vals=self.parent.voltage.vals,
        )

        # Note: we override the voltage parameter here, since by default the GateWrapper
        # pulls the voltage from self.gate, which for a digital gate returns 0/1
        self.parameters["voltage"] = self.parent.voltage

        # Once this object has been created, we can check the state of the underlying
        # gate and fill it in for the gate, it if is currently unknown
        if self.gate.io_mode == DigitalMode.UNDEF:
            state = self.state()
            self.gate.io_mode = DigitalMode.map_conn_state_to_digital_mode(state)

    def _set_io_mode(self, val):
        self.lock(False)
        if val in DigitalMode.INPUT_MODES and self.voltage() != 0:
            self.gate._set_voltage(0)
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


class BBDigitalGateWrapper(DigitalGateWrapper, BBGateWrapper):
    """
    Digital gate wrapper of an BB, which allows set/get of state
    """


class DigitalDevice(Device):
    """
    Device which expects digital control as well as potential analog
    voltages
    """

    def __init__(
        self,
        name: str,
        *,
        gates: ContactDefinitions | None = None,
        ohmics: ContactDefinitions | None = None,
        default_gate_limit: Sequence[float] | None = (-2.5, 0),
        default_ohmic_limit: Sequence[float] | None = (-2e-3, 2e-3),
        v_high: float = 1.8,
        v_low: float = 0,
        **kwargs: Unpack[InstrumentBaseKWArgs],
    ) -> None:
        super().__init__(
            name,
            default_gate_limit=default_gate_limit,
            default_ohmic_limit=default_ohmic_limit,
            **kwargs,
        )

        # Add digital gates to the device
        digital_gates = ChannelList(self, "digital_gates", DigitalGateWrapper)
        self.digital_gates = self.add_submodule("digital_gates", digital_gates)

        # Add digital parameters
        self._v_high = v_high
        self._v_low = v_low
        self.v_high = self.add_parameter(
            "v_high",
            initial_value=v_high,
            get_cmd=partial(getattr, self, "_v_high"),
            set_cmd=self._update_vhigh,
            vals=Numbers(),
        )
        self.v_low = self.add_parameter(
            "v_low",
            initial_value=v_low,
            get_cmd=partial(getattr, self, "_v_low"),
            set_cmd=self._update_vlow,
            vals=Numbers(),
        )
        self._configure_contacts(gates, ohmics)

    def add_digital_gate(self, name, source, io_mode=None, **kwargs):
        return self._add_contact(name, DigitalGate, source, io_mode=io_mode, **kwargs)

    def add_parameter(
        self,
        name: str,
        parameter_class: type[TParameter] | None = None,
        **kwargs,
    ) -> TParameter:
        if parameter_class is not None and issubclass(parameter_class, DigitalGate):
            kwargs.setdefault("v_high", self._v_high)
            kwargs.setdefault("v_low", self._v_low)
            kwargs.setdefault("limits", None)
        return super().add_parameter(name, parameter_class, **kwargs)

    def _configured_class(self, kind, parameter_type):
        if kind == "gates" and parameter_type in (
            "DigitalGate",
            "qcodes_measurements.device.DigitalGate",
        ):
            return DigitalGate
        return super()._configured_class(kind, parameter_type)

    def _configure_controller(self, parameter, controller, state, io_mode, value):
        if io_mode is None:
            super()._configure_controller(parameter, controller, state, None, value)
            if isinstance(parameter, DigitalGate) and state is not None:
                parameter.io_mode = DigitalMode.map_conn_state_to_digital_mode(controller.state())
            return
        if not isinstance(parameter, DigitalGate):
            raise ValueError("io_mode is only supported for digital gates")
        mode = (
            DigitalMode[io_mode]
            if isinstance(io_mode, str) and not isinstance(io_mode, DigitalMode)
            else io_mode
        )
        mode_states = {
            DigitalMode.IN: ConnState.SMC,
            DigitalMode.OUT: ConnState.DAC,
            DigitalMode.PROBE_OUT: ConnState.PROBE,
            DigitalMode.BUS_OUT: ConnState.DAC_BUS,
            DigitalMode.HIGH: ConnState.DAC,
            DigitalMode.LOW: ConnState.DAC,
            DigitalMode.GND: ConnState.GND,
            DigitalMode.FLOAT: ConnState.FLOAT,
        }
        if mode not in mode_states:
            raise ValueError(f"Invalid IO mode {mode!r}")
        desired_state = mode_states[mode]
        if state is not None and state != desired_state:
            raise ValueError("state and io_mode request different connection states")
        voltage = parameter.source.voltage()
        if mode in (DigitalMode.HIGH, DigitalMode.LOW):
            target = parameter.v_high if mode is DigitalMode.HIGH else parameter.v_low
            if value is None and voltage != target:
                raise ValueError(
                    f"{parameter.name}: {mode.name} requires an explicit default_value"
                )
            if (
                value is not None
                and parameter._raw_to_voltage(parameter._from_value_to_raw_value(value)) != target
            ):
                raise ValueError("default_value conflicts with io_mode")
        super()._configure_controller(parameter, controller, desired_state, None, value)
        parameter.io_mode = mode
        parameter.lock = False

    def _finalize_contact(self, parameter, io_mode):
        if isinstance(parameter, DigitalGate) and io_mode is not None:
            parameter.lock = parameter.io_mode in (DigitalMode.HIGH, DigitalMode.LOW)

    def lookup_source(self, source):
        source = self.resolve_source(source)
        for controller in self.digital_gates:
            if controller.gate.source is source:
                return controller
        return super().lookup_source(source)

    def store_new_param(self, new_param: ParameterBase) -> None:
        if isinstance(new_param, DigitalGate):
            if is_mdac_channel(new_param.source):
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
