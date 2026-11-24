from collections.abc import Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from math import isclose
from time import sleep
from typing import Any

from qcodes.instrument import InstrumentBase
from qcodes.parameters import Parameter
from qcodes.parameters.cache import _Cache
from qcodes.validators import Numbers, Validator

from ..drivers._mdac import require_mdac
from .bb import BBChan
from .channel_wrapper import ChannelWrapper
from .mdac_wrappers import MDACWrapper
from .states import ConnState

__all__ = [
    "ConnState",
    "Gate",
    "GateWrapper",
    "MDACGateWrapper",
    "BBGateWrapper",
    "Ohmic",
    "OhmicWrapper",
    "MDACOhmicWrapper",
    "BBOhmicWrapper",
]


def limit_validator(limits: Sequence[float] | None) -> Numbers | None:
    """Limits are expressed in the contact's units, before scale and offset."""
    if limits is None:
        return None
    if len(limits) != 2:
        raise ValueError("limits must contain exactly [lower, upper]")
    return Numbers(*limits)


class _VoltageParameter(Parameter):
    """Voltage contact with a channel source and a cache that follows that source."""

    def __init__(
        self,
        name: str,
        source: InstrumentBase,
        *,
        label: str,
        unit: str = "V",
        limits: Sequence[float] | None,
        vals: Validator | None = None,
        rate: float = 0.05,
        max_step: float = 5e-3,
        default_value=None,
        **kwargs: Any,
    ):
        if not isinstance(source, InstrumentBase) or not isinstance(
            getattr(source, "voltage", None), Parameter
        ):
            raise TypeError("The source must be an instrument/channel with a voltage parameter")
        if not source.voltage.settable or not source.voltage.gettable:
            raise TypeError("The source voltage must support both get and set")
        if rate <= 0 or max_step <= 0:
            raise ValueError("rate and max_step must be positive")
        if "default_mode" in kwargs or "gate_mode" in kwargs:
            raise ValueError("GateMode has been removed; use limits or vals")
        initial_value = kwargs.pop("initial_value", None)
        if initial_value is not None and default_value is not None:
            raise ValueError("Specify only one of default_value and initial_value")
        requested_value = default_value if default_value is not None else initial_value
        initial_cache_value = kwargs.pop("initial_cache_value", None)
        if requested_value is not None and initial_cache_value is not None:
            raise ValueError("An initial cache value cannot be combined with an initial value")
        self.source = source
        self.source_name = source.full_name
        self.rate = rate
        self.max_step = max_step
        self.has_ramp = hasattr(source, "ramp")
        super().__init__(
            name=name,
            label=label,
            unit=unit,
            vals=vals if vals is not None else limit_validator(limits),
            **kwargs,
        )
        self._meta_attrs.append("source_name")
        self.cache = _SourceCache(self)
        if initial_cache_value is not None:
            self.cache.set(initial_cache_value)
        if requested_value is not None:
            self.set(requested_value)

    def _voltage_to_raw(self, value):
        return value

    def _raw_to_voltage(self, value):
        return value

    def get_raw(self):  # pylint: disable=method-hidden
        return self._voltage_to_raw(self.source.voltage())

    def validate(self, value) -> None:
        super().validate(value)
        self.source.voltage.validate(self._raw_to_voltage(self._from_value_to_raw_value(value)))

    @contextmanager
    def soft_ramp(self, rate=None, step=None):
        old_step = self.source.voltage.step
        old_delay = self.source.voltage.inter_delay
        step = self.max_step / 10 if step is None else step
        rate = self.rate if rate is None else rate
        if step <= 0 or rate <= 0:
            raise ValueError("Ramp rate and step must be positive")
        try:
            self.source.voltage.step = step
            self.source.voltage.inter_delay = step / rate
            yield self.source.voltage
        finally:
            self.source.voltage.step = old_step
            self.source.voltage.inter_delay = old_delay

    def set_raw(self, value) -> None:  # ty: ignore[incompatible-method-override]  # QCoDeS dynamic Command assignment.
        self._set_voltage(value)

    def _set_voltage(self, value) -> None:
        # Read the actual voltage so a stale cache cannot cause a large step.
        current = self.source.voltage()
        if value == current:
            return
        if abs(value - current) <= self.max_step:
            self.source.voltage(value)
        elif self.has_ramp:
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
            with self.soft_ramp() as ramp:
                ramp(value)


class Gate(_VoltageParameter):
    def __init__(
        self,
        name,
        source,
        label=None,
        rate=0.05,
        max_step=5e-3,
        *,
        unit="V",
        limits=(-2.5, 0),
        **kwargs,
    ):
        super().__init__(
            name,
            source,
            label=name if label is None else label,
            unit=unit,
            limits=limits,
            rate=rate,
            max_step=max_step,
            **kwargs,
        )


class Ohmic(_VoltageParameter):
    def __init__(
        self,
        name,
        source,
        label=None,
        unit="V",
        limits=(-2e-3, 2e-3),
        **kwargs,
    ):
        super().__init__(
            name,
            source,
            label=f"{name} Bias" if label is None else label,
            unit=unit,
            limits=limits,
            **kwargs,
        )


class GateWrapper(ChannelWrapper):
    def __init__(self, parent, name):
        if not isinstance(parent, Gate):
            raise TypeError("GateWrapper can only wrap gates")
        super().__init__(parent, name)
        self.parameters["voltage"] = parent


class MDACGateWrapper(MDACWrapper, GateWrapper):
    def __init__(self, parent, name):
        if not isinstance(parent.source, require_mdac().MDACChannel):
            raise TypeError("MDACGateWrapper must wrap a gate on an MDAC Channel")
        super().__init__(parent, name)


class BBGateWrapper(GateWrapper):
    def __init__(self, parent, name):
        if not isinstance(parent.source, BBChan):
            raise TypeError("BBGateWrapper must wrap a gate on a breakout box")
        super().__init__(parent, name)


class OhmicWrapper(ChannelWrapper):
    def __init__(self, parent, name):
        if not isinstance(parent, Ohmic):
            raise TypeError("OhmicWrapper can only wrap ohmics")
        super().__init__(parent, name)
        self.parameters["voltage"] = parent


class MDACOhmicWrapper(MDACWrapper, OhmicWrapper):
    def __init__(self, parent, name):
        if not isinstance(parent.source, require_mdac().MDACChannel):
            raise TypeError("MDACOhmicWrapper must wrap an ohmic on an MDAC Channel")
        super().__init__(parent, name)


class BBOhmicWrapper(OhmicWrapper):
    def __init__(self, parent, name):
        if not isinstance(parent.source, BBChan):
            raise TypeError("BBOhmicWrapper must wrap an ohmic on a breakout box")
        super().__init__(parent, name)


class _SourceCache(_Cache):
    """Mirror the source cache without changing the public channel-valued source.

    A normal Parameter cache becomes stale when the DAC is set directly. Digital
    gates additionally translate physical voltage into their logical raw value.
    """

    def __init__(self, parameter: _VoltageParameter):
        super().__init__(parameter, parameter.cache.max_val_age)
        self._contact = parameter
        self._source = parameter.source.voltage.cache

    @property
    def raw_value(self):
        value = self._source.get(get_if_invalid=False)
        return None if value is None else self._contact._voltage_to_raw(value)

    @property
    def timestamp(self) -> datetime | None:
        return self._source.timestamp

    @property
    def valid(self) -> bool:
        if not self._source.valid:
            return False
        if self._max_val_age is None:
            return True
        return (
            self.timestamp is not None
            and (datetime.now(UTC) - self.timestamp).total_seconds() < self._max_val_age
        )

    def invalidate(self) -> None:
        self._source.invalidate()

    def get(self, get_if_invalid: bool = True):
        if get_if_invalid and not self.valid:
            return self._contact.get()
        value = self._source.get(get_if_invalid=get_if_invalid)
        if value is None:
            return None
        return self._contact._from_raw_value_to_value(self._contact._voltage_to_raw(value))

    def set(self, value) -> None:
        self._contact.validate(value)
        self.set_from_raw_value(self._contact._from_value_to_raw_value(value))

    def set_from_raw_value(self, raw_value) -> None:
        self._source.set(self._contact._raw_to_voltage(raw_value))

    def _update_with(self, *, value, raw_value, timestamp=None) -> None:
        # The source's get/set already updated its cache.
        pass
