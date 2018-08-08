import enum
from math import isclose
from time import sleep
from contextlib import contextmanager
from datetime import datetime

from qcodes import Instrument, InstrumentChannel, Parameter
from qcodes.utils import validators as vals

try:
    import qcodes.instrument_drivers.qnl.MDAC as MDAC
except ModuleNotFoundError:
    MDAC = object()
    MDAC.MDACChannel = type(None)
    MDAC.MDAC = type(None)


class GateMode(enum.Enum):
    BIAS = enum.auto()
    COLD = enum.auto()
    FREE = enum.auto()


class ConnState(enum.Enum):
    BUS = enum.auto()
    GND = enum.auto()
    DAC = enum.auto()
    SMC = enum.auto()
    FLOAT = enum.auto()
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
        self.gate_mode = default_mode

        # Check whether we have a hardware ramp
        if hasattr(source, "ramp") and hasattr(source, "rate"):
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
    def get_raw(self):
        """
        Get refers to the voltage of the underlying source
        """
        return self.voltage()

    def set_raw(self, val):
        """
        Set the voltage to the selected value, ramping if the step is
        larger than max_step.

        Validation handled by the set wrapper.
        """
        # Set the value if we are close
        if isclose(val, self.source.voltage(), abs_tol=self.max_step):
            self.source.voltage(val)
            return

        # Otherwise ramp, using the hardware ramp if available
        if self.has_ramp:
            with self.source.rate.set_to(self.rate):
                self.source.ramp(val)
                while not isclose(val, self.source.voltage(), abs_tol=1e-4):
                    sleep(0.005)
        else:
            # set up a soft ramp and ramp with that instead
            with self.soft_ramp() as ramp:
                ramp(val)


class GateWrapper(InstrumentChannel):
    def __init__(self, parent, name):
        super().__init__(parent.source, name)
        if not isinstance(parent, Gate):
            raise TypeError("GateWrapper can only wrap gates")
        self._state = ConnState.UNDEF

    @property
    def state(self):
        return self._state

    def ground(self):
        print(f"Manually Ground {self.name}")
        self._state = ConnState.GND

    def bus(self):
        print(f"Manually Bus {self.name}")
        self._state = ConnState.BUS

    def dac(self):
        print(f"Manually Connect DAC to {self.name}")
        self._state = ConnState.DAC


class MDACGateWrapper(GateWrapper):
    def __init__(self, parent, name):
        super().__init__(parent, name)
        if not isinstance(parent.source, MDAC.MDACChannel):
            raise TypeError("MDACGateWrapper must wrap a gate on an MDAC Channel")

        # Allow access to gate voltage
        self.parameters['voltage'] = parent

    @property
    def state(self):
        gnd = self.instrument.gnd() == 'close'
        smc = self.instrument.smc() == 'close'
        bus = self.instrument.bus() == 'close'
        dac_output = self.instrument.dac_output() == 'close'
        if gnd:
            if not smc and not bus:
                return ConnState.GND
            else:
                return ConnState.UNDEF
        else:
            if bus:
                return ConnState.BUS
            elif dac_output:
                return ConnState.DAC
            elif smc:
                return ConnState.SMC

    def ground(self):
        self.instrument.ground('close')
        self.instrument.smc('open')
        self.instrument.bus('open')
        self.instrument.dac_output('open')

    def bus(self):
        self.instrument.bus('close')
        self.instrument.smc('open')
        self.instrument.ground('open')
        self.instrument.dac_output('open')

    def dac(self):
        self.instrument.dac_output('close')
        self.instrument.bus('open')
        self.instrument.smc('open')
        self.instrument.ground('open')


class Ohmic(Parameter):
    def __init__(self, name, source, label=None, **kwargs):
        # Check that the source is a valid voltage source
        if not isinstance(source, (Instrument, InstrumentChannel)):
            raise TypeError("The source must be an instrument or instrument channel.")
        if not hasattr(source, "voltage") or not hasattr(source.voltage, "set"):
            self.set_raw = None
            self.get_raw = None

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
        else:
            return super().snapshot_base(update)

    @property
    def _latest(self):
        if hasattr(self.source, "voltage"):
            return self.source.voltage._latest
        else:
            return {'value': None,
                    'ts': datetime.now(),
                    'raw_value': None}
    @_latest.setter
    def _latest(self, val):
        pass

    def get_raw(self):
        return self.source.voltage()

    def set_raw(self, val):
        return self.source.voltage(val)


class OhmicWrapper(InstrumentChannel):
    def __init__(self, parent, name):
        super().__init__(parent.source, name)
        if not isinstance(parent, Ohmic):
            raise TypeError("OhmicWrapper can only wrap ohmics")
        self._state = ConnState.UNDEF

    @property
    def state(self):
        return self._state

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

    @property
    def state(self):
        gnd = self.instrument.gnd() == 'close'
        smc = self.instrument.smc() == 'close'
        bus = self.instrument.bus() == 'close'
        dac_output = self.instrument.dac_output() == 'close'
        if gnd:
            if not smc and not bus:
                return ConnState.GND
            else:
                return ConnState.UNDEF
        else:
            if bus:
                return ConnState.BUS
            elif dac_output:
                return ConnState.DAC
            elif smc:
                return ConnState.SMC
            else:
                return ConnState.FLOAT

    def ground(self):
        self.instrument.ground('close')
        self.instrument.smc('open')
        self.instrument.bus('open')
        self.instrument.dac_output('open')

    def bus(self):
        self.instrument.bus('close')
        self.instrument.smc('open')
        self.instrument.ground('open')
        self.instrument.dac_output('open')

    def float(self):
        self.instrument.dac_output('open')
        self.instrument.bus('open')
        self.instrument.smc('open')
        self.instrument.ground('open')

    def smc(self):
        self.instrument.smc('close')
        self.instrument.dac_output('open')
        self.instrument.bus('open')
        self.instrument.ground('open')
