import enum
from math import isclose
from time import sleep
from contextlib import contextmanager
from datetime import datetime

from qcodes import Instrument, InstrumentChannel, Parameter
from qcodes.utils import validators as vals

try:
    import MDAC
except ModuleNotFoundError:
    class _Blank():
        MDACChannel = type(None)
        MDAC = type(None)
    MDAC = _Blank()
from .bb import BBChan

__all__ = ["GateMode", "ConnState", "Gate", "GateWrapper", "MDACGateWrapper",
           "BBGateWrapper", "Ohmic", "OhmicWrapper", "MDACOhmicWrapper",
           "BBOhmicWrapper"]

class GateMode(str, enum.Enum):
    BIAS = enum.auto()
    COLD = enum.auto()
    FREE = enum.auto()


class ConnState(str, enum.Enum):
    BUS = enum.auto()
    GND = enum.auto()
    DAC = enum.auto()
    SMC = enum.auto()
    FLOAT = enum.auto()
    PROBE = enum.auto()
    UNDEF = enum.auto()


class Gate(Parameter):
    def __init__(self, name, source, label=None,
                 rate=0.05, max_step=5e-3, default_mode="COLD",
                 **kwargs):
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
                         vals=source.voltage.vals,
                         **kwargs)
        self.source = source
        self.rate = rate
        self.max_step = max_step
        self._gate_mode = default_mode

        # Check whether we have a hardware ramp
        if hasattr(source, "ramp"):
            self.has_ramp = True
        else:
            self.has_ramp = False

    # Create the concept of a gate mode that changes the validators
    # depending on the state of the gate
    @property
    def gate_mode(self):
        return self._gate_mode

    @gate_mode.setter
    def gate_mode(self, val):
        if isinstance(val, str):
            val = GateMode[val]
        if not isinstance(val, GateMode):
            raise TypeError(f"Invalid gate mode. Must be one of {tuple(GateMode)}.")

        self._gate_mode = val
        if val is GateMode.BIAS:
            self.vals = vals.Numbers(0, 0.5)
        elif val is GateMode.COLD:
            self.vals = vals.Numbers(-2.5, 0)
        else:
            self.vals = self.source.voltage.vals

    # Overwrite snapshot so that we pass the call onto the underlying voltage
    # parameter. Further, we'll force an update since other types of set often
    # cause this parameter to be overwritten
    def snapshot_base(self, update=True, params_to_skip_update=None):
        snap = self.source.voltage.snapshot_base(update, params_to_skip_update)
        snap['full_name'] = self.full_name
        snap['name'] = self.name
        snap['instrument'] = repr(self.instrument)
        snap['label'] = self.label
        return snap

    @property
    def _latest(self):
        return self.source.voltage._latest
    @_latest.setter
    def _latest(self, val):
        pass

    @contextmanager
    def soft_ramp(self, rate=None, step=None):
        """
        Set up a software ramp on the voltage parameter, saving the previous values
        of step/delay if available
        """
        # Save the old step/inter_delay
        old_step = self.source.voltage.step
        old_delay = self.source.voltage.inter_delay

        # Calculate a new step/inter_delay
        if step is None:
            step = self.max_step/10
        if rate is None:
            rate = self.rate
        delay = step/rate
        try:
            self.source.voltage.step = step
            self.source.voltage.inter_delay = delay
            yield self.source.voltage
        finally:
            self.source.voltage.step = old_step
            self.source.voltage.inter_delay = old_delay

    # Make getters and setters
    def get_raw(self): #pylint: disable=E0202
        """
        Get refers to the voltage of the underlying source
        """
        return self.source.voltage()

    def set_raw(self, value): #pylint: disable=E0202
        """
        Set the voltage to the selected value, ramping if the step is
        larger than max_step.

        Validation handled by the set wrapper.
        """
        # Set the value if we are close
        if isclose(value, self.source.voltage(), abs_tol=self.max_step):
            self.source.voltage(value)
            return

        # Otherwise ramp, using the hardware ramp if available
        # Two different ways to do it, with a ramp function or ramp parameter
        if self.has_ramp:
            if isinstance(self.source.ramp, Parameter):
                with self.source.rate.set_to(self.rate):
                    self.source.ramp(value)
                    while not isclose(value, self.source.voltage(), abs_tol=1e-4):
                        sleep(0.005)
            else:
                self.source.ramp(value, self.rate)
                while not isclose(value, self.source.voltage(), abs_tol=1e-4):
                    sleep(0.005)
        else:
            # set up a soft ramp and ramp with that instead
            with self.soft_ramp() as ramp:
                ramp(value)


class GateWrapper(InstrumentChannel):
    """
    Channel wrapper around a gate object, allowing access of some of the underlying
    states as parameters.

    Note: The accesses for various attributes can be confusing here:
        - self.gate - The underlying Gate object
        - self.parent - The underlying DAC/BB channel
    """
    def __init__(self, parent, name, GateType=Gate):
        super().__init__(parent.source, name)
        self.gate = parent
        if not isinstance(parent, GateType):
            raise TypeError("GateWrapper can only wrap gates")
        self._state = ConnState.UNDEF
        self.add_parameter('state',
                           get_cmd=self.get_state,
                           set_cmd=self.set_state)

    def get_state(self):
        return self._state

    def set_state(self, val):
        if val == ConnState.GND:
            self.ground()
        elif val == ConnState.BUS:
            self.bus()
        elif val == ConnState.SMC:
            self.open()
        elif val == ConnState.DAC:
            self.dac()
        elif val == ConnState.PROBE:
            self.probe()

    def ground(self):
        print(f"Manually Ground {self.name}")
        self._state = ConnState.GND

    def bus(self):
        print(f"Manually Bus {self.name}")
        self._state = ConnState.BUS

    def open(self):
        print(f'Manually Open {self.name}')
        self._state = ConnState.SMC

    def dac(self):
        print(f"Manually Connect DAC to {self.name}")
        self._state = ConnState.DAC

    def probe(self):
        raise ValueError("Probe doesn't make sense except on a DAC")


class MDACGateWrapper(GateWrapper):
    def __init__(self, parent, name, GateType=Gate):
        super().__init__(parent, name, GateType=GateType)
        if not isinstance(parent.source, MDAC.MDACChannel):
            raise TypeError("MDACGateWrapper must wrap a gate on an MDAC Channel")

        # Allow access to gate voltage
        self.parameters['voltage'] = self.gate

    def get_state(self):
        gnd = self.parent.gnd() == 'close'
        smc = self.parent.smc() == 'close'
        bus = self.parent.bus() == 'close'
        dac_output = self.parent.dac_output() == 'close'
        state = None
        if gnd:
            if not smc and not bus:
                state = ConnState.GND
            else:
                state = ConnState.UNDEF
        else:
            if bus:
                state = ConnState.BUS
            elif dac_output and not smc:
                state = ConnState.DAC
            elif dac_output and smc:
                state = ConnState.PROBE
            elif smc:
                state = ConnState.SMC
            else:
                state = ConnState.UNDEF
        return state

    def ground(self):
        self.parent.gnd('close')
        self.parent.smc('open')
        self.parent.bus('open')
        self.parent.dac_output('open')
        self.state._save_val(ConnState.GND)

    def bus(self):
        self.parent.bus('close')
        self.parent.smc('open')
        self.parent.gnd('open')
        self.parent.dac_output('open')
        self.state._save_val(ConnState.BUS)

    def dac(self):
        self.parent.dac_output('close')
        self.parent.bus('open')
        self.parent.smc('open')
        self.parent.gnd('open')
        self.state._save_val(ConnState.DAC)

    def open(self):
        self.parent.smc('close')
        self.parent.dac_output('open')
        self.parent.bus('open')
        self.parent.gnd('open')
        self.state._save_val(ConnState.SMC)

    def probe(self):
        self.parent.dac_output('close')
        self.parent.bus('open')
        self.parent.smc('close')
        self.parent.gnd('open')
        self.state._save_val(ConnState.PROBE)

class BBGateWrapper(GateWrapper):
    def __init__(self, parent, name, GateType=Gate):
        super().__init__(parent, name, GateType=GateType)
        if not isinstance(parent.source, BBChan):
            raise TypeError("BBGateWrapper must wrap a gate on an breakout box")

        # Allow access to gate voltage
        self.parameters['voltage'] = parent


class Ohmic(Parameter):
    def __init__(self, name, source, label=None, **kwargs):
        # Check that the source is a valid voltage source
        if not isinstance(source, (Instrument, InstrumentChannel)):
            raise TypeError("The source must be an instrument or instrument channel.")
        if not hasattr(source, "voltage") or not hasattr(source.voltage, "set"):
            del self.set_raw
            del self.get_raw

        if label is None:
            label = name

        # Initialize the parameter
        super().__init__(name=f"{name}_bias",
                         label=f"{name} Bias",
                         unit="V",
                         vals=vals.Numbers(-2e-3, 2e-3),
                         **kwargs)
        self.source = source

    # Overwrite snapshot so that we pass the call onto the underlying voltage
    # parameter. Further, we'll force an update since other types of set often
    # cause this parameter to be overwritten
    def snapshot_base(self, update=True, params_to_skip_update=None):
        if hasattr(self.source, "voltage"):
            snap = self.source.voltage.snapshot_base(update, params_to_skip_update)
            snap['full_name'] = self.full_name
            snap['name'] = self.name
            snap['instrument'] = repr(self.instrument)
            snap['label'] = self.label
            return snap
        return super().snapshot_base(update)

    @property
    def _latest(self):
        if hasattr(self.source, "voltage"):
            return self.source.voltage._latest
        return {'value': None,
                'ts': datetime.now(),
                'raw_value': None}
    @_latest.setter
    def _latest(self, val):
        pass

    def get_raw(self): #pylint: disable=E0202
        return self.source.voltage()

    def set_raw(self, value): #pylint: disable=E0202
        return self.source.voltage(value)


class OhmicWrapper(InstrumentChannel):
    def __init__(self, parent, name):
        super().__init__(parent.source, name)
        if not isinstance(parent, Ohmic):
            raise TypeError("OhmicWrapper can only wrap ohmics")
        self._state = ConnState.UNDEF
        self.add_parameter('state',
                           get_cmd=self.get_state,
                           set_cmd=self.set_state)

    def get_state(self):
        return self._state

    def set_state(self, val):
        if val == ConnState.GND:
            self.ground()
        elif val == ConnState.BUS:
            self.bus()
        elif val == ConnState.SMC:
            self.open()
        elif val == ConnState.DAC:
            self.dac()
        else:
            raise ValueError("Invalid state for ohmic")

    def ground(self):
        print(f"Manually Ground {self.name}")
        self._state = ConnState.GND

    def bus(self):
        print(f"Manually Bus {self.name}")
        self._state = ConnState.BUS

    def float(self):
        print(f"Manually Float {self.name}")
        self._state = ConnState.FLOAT

    def smc(self):
        self.float()


class MDACOhmicWrapper(OhmicWrapper):
    def __init__(self, parent, name):
        super().__init__(parent, name)
        if not isinstance(parent.source, MDAC.MDACChannel):
            raise TypeError("MDACGateWrapper must wrap a gate on an MDAC Channel")

        # Allow access to gate voltage
        self.parameters['voltage'] = parent

    def get_state(self):
        gnd = self.parent.gnd() == 'close'
        smc = self.parent.smc() == 'close'
        bus = self.parent.bus() == 'close'
        dac_output = self.parent.dac_output() == 'close'
        state = None
        if gnd:
            if not smc and not bus:
                state = ConnState.GND
            else:
                state = ConnState.UNDEF
        else:
            if bus:
                state = ConnState.BUS
            elif dac_output:
                state = ConnState.DAC
            elif smc:
                state = ConnState.SMC
            else:
                state = ConnState.FLOAT
        return state

    def ground(self):
        self.parent.gnd('close')
        self.parent.smc('open')
        self.parent.bus('open')
        self.parent.dac_output('open')
        self.state._save_val(ConnState.GND)

    def bus(self):
        self.parent.bus('close')
        self.parent.smc('open')
        self.parent.gnd('open')
        self.parent.dac_output('open')
        self.state._save_val(ConnState.BUS)

    def float(self):
        self.parent.dac_output('open')
        self.parent.bus('open')
        self.parent.smc('open')
        self.parent.gnd('open')
        self.state._save_val(ConnState.FLOAT)

    def smc(self):
        self.parent.smc('close')
        self.parent.dac_output('open')
        self.parent.bus('open')
        self.parent.gnd('open')
        self.state._save_val(ConnState.SMC)


class BBOhmicWrapper(OhmicWrapper):
    def __init__(self, parent, name):
        super().__init__(parent, name)
        if not isinstance(parent.source, BBChan):
            raise TypeError("BBGateWrapperWithMDAC must wrap a gate on an breakout box")

        # Allow access to gate voltage
        self.parameters['voltage'] = parent
