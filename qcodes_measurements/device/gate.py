from math import isclose
from time import sleep
from contextlib import contextmanager
from datetime import datetime

from typing import Optional, Sequence, TYPE_CHECKING, Union, Callable, List, \
    Dict, Any, Sized, Iterable, cast, Type, Tuple, Iterator #pylint: disable=unused-import


from qcodes import InstrumentChannel, Parameter
from qcodes.instrument.base import InstrumentBase
from qcodes.instrument.parameter import _Cache
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

# for now the type the parameter may contain is not restricted at all
ParamDataType = Any
ParamRawDataType = Any

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
        self.gate_mode = default_mode

        # Check whether we have a hardware ramp
        if hasattr(source, "ramp"):
            self.has_ramp = True
        else:
            self.has_ramp = False

        # Initialize the parameter
        super().__init__(name=name,
                         label=label,
                         unit="V",
                         vals=self.vals,
                         **kwargs)

        # Set the cache to a wrapped Gate cache
        self.cache = _GateCache(self)

    @property
    def gate_mode(self):
        """
        Create the concept of a gate mode that changes the validators
        depending on the state of the gate
        """
        return self._gate_mode

    @gate_mode.setter
    def gate_mode(self, val):
        if isinstance(val, GateMode):
            pass
        elif isinstance(val, str):
            val = GateMode[val]
        else:
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
        snap['value'] = self._from_raw_value_to_value(snap['value'])
        snap['raw_value'] = snap['value']
        snap['scale'] = getattr(self, "scale", None)
        return snap

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
    def get_raw(self): #pylint: disable=method-hidden
        """
        Get refers to the voltage of the underlying source
        """
        return self.source.voltage()

    def set_raw(self, value): #pylint: disable=method-hidden
        """
        Set the voltage to the selected value, ramping if the step is
        larger than max_step.

        Validation handled by the set wrapper.
        """
        # Set the value if we are close
        if abs(value - self.cache.raw_value) <= self.max_step:
            self.source.voltage(value)
            return

        # Otherwise ramp, using the hardware ramp if available
        # Two different ways to do it, with a ramp function or ramp parameter
        if self.has_ramp:
            if isinstance(self.source.ramp, Parameter):
                with self.source.rate.set_to(self.rate):
                    self.source.ramp(value)
                    while not isclose(value, self._from_value_to_raw_value(self.get()), abs_tol=1e-4):
                        sleep(0.005)
            else:
                self.source.ramp(value, self.rate)
                while not isclose(value, self._from_value_to_raw_value(self.get()), abs_tol=1e-4):
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


        # Allow access to gate voltage
        self.parameters['voltage'] = parent

class MDACGateWrapper(MDACWrapper, GateWrapper):
    def __init__(self, parent, name):
        super().__init__(parent, name)
        if not isinstance(parent.source, MDAC.MDACChannel):
            raise TypeError("MDACGateWrapper must wrap a gate on an MDAC Channel")


class BBGateWrapper(GateWrapper):
    def __init__(self, parent, name):
        super().__init__(parent, name)
        if not isinstance(parent.source, BBChan):
            raise TypeError("BBGateWrapper must wrap a gate on an breakout box")


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
        super().__init__(name=f"{name}",
                         label=f"{name} Bias",
                         unit="V",
                         vals=vals.Numbers(-2e-3, 2e-3),
                         **kwargs)
        self.source = source

        # Create a wrapped cache
        self.cache = _GateCache(self)

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
            snap['value'] = self._from_raw_value_to_value(snap['value'])
            snap['raw_value'] = snap['value']
            snap['scale'] = getattr(self, "scale", None)
            return snap
        return super().snapshot_base(update)

    def get_raw(self): #pylint: disable=method-hidden
        return self.source.voltage()

    def set_raw(self, value): #pylint: disable=method-hidden
        return self.source.voltage(value)


class OhmicWrapper(ChannelWrapper):
    def __init__(self, parent, name):
        if not isinstance(parent, Ohmic):
            raise TypeError("OhmicWrapper can only wrap ohmics")
        super().__init__(parent, name)

        if hasattr(parent.source, "voltage"):
            self.parameters["voltage"] = parent


class MDACOhmicWrapper(MDACWrapper, OhmicWrapper):
    def __init__(self, parent, name):
        super().__init__(parent, name)
        if not isinstance(parent.source, MDAC.MDACChannel):
            raise TypeError("MDACGateWrapper must wrap a gate on an MDAC Channel")


class BBOhmicWrapper(OhmicWrapper):
    def __init__(self, parent, name):
        super().__init__(parent, name)
        if not isinstance(parent.source, BBChan):
            raise TypeError("BBGateWrapperWithMDAC must wrap a gate on an breakout box")


class _GateCache(_Cache):
    """
    Cache object for a wrapped parameter to hold its value and raw value

    It also implements ``set`` method for setting parameter's value without
    invoking its ``set_cmd``, and ``get`` method that allows to retrieve the
    cached value of the parameter without calling ``get_cmd`` might be called
    unless the cache is invalid. This parameter is wrapped to correctly use
    the cached value from the wrapped parameter while applying the correct
    scaling factor.

    Args:
         parameter: instance of the parameter that this cache belongs to.
         max_val_age: Max time (in seconds) to trust a value stored in cache.
            If the parameter has not been set or measured more recently than
            this, an additional measurement will be performed in order to
            update the cached value. If it is ``None``, this behavior is
            disabled. ``max_val_age`` should not be used for a parameter
            that does not have a get function.
    """
    def __init__(self,
                 parameter: Union[Gate, Ohmic],
                 max_val_age: Optional[float] = None):
        if not isinstance(parameter, (Gate, Ohmic)):
            raise TypeError(f"GateCache can only wrap Gates or Ohmics, got {type(parameter)}")
        self._source = parameter.source.voltage
        super().__init__(parameter, max_val_age)

    @property
    def raw_value(self) -> ParamRawDataType:
        """Raw value of the parameter"""
        return self._source.cache.get()

    @property
    def timestamp(self) -> Optional[datetime]:
        """
        Timestamp of the moment when cache was last updated

        If ``None``, the cache hasn't been updated yet and shall be seen as
        "invalid".
        """
        return self._source.cache.timestamp

    def set(self, value: ParamDataType) -> None:
        """
        Set the cached value of the parameter without invoking the
        ``set_cmd`` of the parameter (if it has one). This is forwarded
        onto the underlying parameter

        Args:
            value: new value for the parameter
        """
        self._parameter.validate(value)
        raw_value = self._parameter._from_value_to_raw_value(value)
        self._source.cache.set(raw_value)

    def get(self, get_if_invalid: bool = True) -> ParamDataType:
        """
        Return cached value of the underlying parameter, with correct scaling
        applied.

        Args:
            get_if_invalid: if set to ``True``, ``get()`` on a parameter
                will be performed in case the cached value is invalid (for
                example, due to ``max_val_age``, or because the parameter has
                never been captured)
        """
        value = self._parameter._from_raw_value_to_value( #pylint: disable=protected-access
            self._source.cache.get(get_if_invalid))
        return value
