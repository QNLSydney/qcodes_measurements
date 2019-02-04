from math import isclose
from time import sleep
from contextlib import contextmanager
from datetime import datetime

from qcodes import InstrumentChannel, Parameter
from qcodes.instrument.base import InstrumentBase
from qcodes.utils import validators as vals

try:
    import MDAC
except ModuleNotFoundError:
    class _Blank():
        MDACChannel = type(None)
        MDAC = type(None)
    MDAC = _Blank()
from .bb import BBChan
from .states import GateMode, ConnState
from .channel_wrapper import ChannelWrapper
from .mdac_wrappers import MDACWrapper

__all__ = ["GateMode", "ConnState", "Gate", "GateWrapper", "MDACGateWrapper",
           "BBGateWrapper", "Ohmic", "OhmicWrapper", "MDACOhmicWrapper",
           "BBOhmicWrapper"]

class Gate(Parameter):
    def __init__(self, name, source, label=None,
                 rate=0.05, max_step=5e-3, default_mode="COLD",
                 **kwargs):
        # Check that the source is a valid voltage source
        if not isinstance(source, (InstrumentBase, InstrumentChannel)):
            raise TypeError("The source must be an instrument or instrument channel.")
        if not hasattr(source, "voltage") or not hasattr(source.voltage, "set"):
            raise TypeError("The source for a gate must be able to set a voltage")

        if label is None:
            label = name

        self.source = source
        self.rate = rate
        self.max_step = max_step
        self._gate_mode = default_mode

        # Check whether we have a hardware ramp
        if hasattr(source, "ramp"):
            self.has_ramp = True
        else:
            self.has_ramp = False

        # Initialize the parameter
        super().__init__(name=name,
                         label=label,
                         unit="V",
                         vals=source.voltage.vals,
                         **kwargs)

    @property
    def gate_mode(self):
        """
        Create the concept of a gate mode that changes the validators
        depending on the state of the gate
        """
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


class GateWrapper(ChannelWrapper):
    """
    Channel wrapper around a gate object, allowing access of some of the underlying
    states as parameters.

    Note: The accesses for various attributes can be confusing here:
        - self.gate - The underlying Gate object
        - self.parent - The underlying DAC/BB channel
    """
    def __init__(self, parent, name):
        if not isinstance(parent, Gate):
            raise TypeError("GateWrapper can only wrap gates")
        super().__init__(parent, name)

class MDACGateWrapper(MDACWrapper, GateWrapper):
    def __init__(self, parent, name):
        super().__init__(parent, name)
        if not isinstance(parent.source, MDAC.MDACChannel):
            raise TypeError("MDACGateWrapper must wrap a gate on an MDAC Channel")

        # Allow access to gate voltage
        self.parameters['voltage'] = self.gate


class BBGateWrapper(GateWrapper):
    def __init__(self, parent, name):
        super().__init__(parent, name)
        if not isinstance(parent.source, BBChan):
            raise TypeError("BBGateWrapper must wrap a gate on an breakout box")

        # Allow access to gate voltage
        self.parameters['voltage'] = parent


class Ohmic(Parameter):
    def __init__(self, name, source, label=None, **kwargs):
        # Check that the source is a valid voltage source
        if not isinstance(source, (InstrumentBase, InstrumentChannel)):
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


class OhmicWrapper(ChannelWrapper):
    def __init__(self, parent, name):
        if not isinstance(parent, Ohmic):
            raise TypeError("OhmicWrapper can only wrap ohmics")
        super().__init__(parent, name)


class MDACOhmicWrapper(MDACWrapper, OhmicWrapper):
    def __init__(self, parent, name):
        super().__init__(parent, name)
        if not isinstance(parent.source, MDAC.MDACChannel):
            raise TypeError("MDACGateWrapper must wrap a gate on an MDAC Channel")

        # Allow access to gate voltage
        self.parameters['voltage'] = parent


class BBOhmicWrapper(OhmicWrapper):
    def __init__(self, parent, name):
        super().__init__(parent, name)
        if not isinstance(parent.source, BBChan):
            raise TypeError("BBGateWrapperWithMDAC must wrap a gate on an breakout box")

        # Allow access to gate voltage
        self.parameters['voltage'] = parent
