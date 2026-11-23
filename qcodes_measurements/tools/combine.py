from collections import defaultdict
from math import isclose
from time import perf_counter
from typing import cast

from qcodes.parameters import Parameter

from ..device.gate import Gate
from ..drivers.MDAC import MDAC, MDACBasicChannel

_MDAC_RAMP_TIMEOUT_GRACE = 1.0  # seconds


def _mdac_channel(parameter: Parameter) -> MDACBasicChannel | None:
    if isinstance(parameter, Gate):
        # A digital gate or another custom setter can have different output rules.
        if type(parameter).set_raw is not Gate.set_raw or (
            type(parameter)._set_voltage is not Gate._set_voltage
        ):
            return None
        parameter = parameter.source.voltage
    channel = parameter.instrument
    if isinstance(channel, MDACBasicChannel) and parameter is channel.voltage:
        return channel
    return None


def _can_defer_set(parameter: Parameter) -> bool:
    return (
        parameter.settable
        and not parameter.abstract
        and parameter.step is None
        and not parameter.inter_delay
        and not parameter.post_delay
        and parameter.on_set_callback is None
        and type(parameter).global_on_set_callback is None
    )


class CombinedParameter(Parameter):
    def __init__(self, *parameters, name=None, label=None, unit=None):

        if name is None:
            raise ValueError("name is required. Got None.")
        if label is None:
            label = parameters[0].label
        if unit is None:
            unit = parameters[0].unit
        if not all(isinstance(x, Parameter) for x in parameters):
            raise TypeError("All parameters to combine must be qcodes parameters.")
        self._parameters = parameters
        channels = tuple(channel for p in parameters if (channel := _mdac_channel(p)) is not None)
        # Two setters for the same output may use different rates or voltage conversions.
        self._mdac_channels = channels if len(set(channels)) == len(parameters) else ()

        super().__init__(name=name, label=label, unit=unit)

    def get_raw(self):
        """
        Return the value of the first parameter. Since they are combined, we assume that they all have
        the same value.
        """
        return self._parameters[0]()

    def set_raw(self, value) -> None:  # ty: ignore[incompatible-method-override]  # QCoDeS dynamic Command assignment.
        """Set all parameters, overlapping MDAC ramps where possible."""
        if self._mdac_channels and all(
            _can_defer_set(channel.voltage)
            and (parameter is channel.voltage or _can_defer_set(parameter))
            and (not isinstance(parameter, Gate) or parameter.source.voltage is channel.voltage)
            for parameter, channel in zip(self._parameters, self._mdac_channels, strict=True)
        ):
            self._set_mdac(value)
            return
        for parameter in self._parameters:
            parameter(value)

    def _set_mdac(self, value) -> None:
        # Validate all targets before any channel starts moving.
        for parameter in self._parameters:
            parameter.validate(value)
        self.cache.invalidate()

        waits: dict[tuple[MDAC, bool], list[MDACBasicChannel]] = defaultdict(list)
        timed_waits: list[tuple[float, MDACBasicChannel]] = []
        gate_targets: dict[MDACBasicChannel, float] = {}
        for parameter, channel in zip(self._parameters, self._mdac_channels, strict=True):
            # The sense channel (0) is outside the driver's bulk ramp status query.
            wait_group = cast(MDAC, channel.parent), channel.channel_number() == 0
            if isinstance(parameter, Gate):
                target = parameter._from_value_to_raw_value(value)
                current = channel.voltage()
                if target == current:
                    continue
                if abs(target - current) > parameter.max_step:
                    channel.voltage.cache.invalidate()
                    channel.ramp(target, parameter.rate)
                    gate_targets[channel] = target
                    waits[wait_group].append(channel)
                    continue
            else:
                target = value

            # Direct voltage sets use the channel's rate and blocking settings.
            if channel.voltage_set_ramp_rate() is not None and channel.voltage_set_blocking():
                timeout = channel.voltage_set_block_timeout()
                try:
                    channel._nonblocking_set_voltage(target)
                finally:
                    channel.voltage.cache.invalidate()
                if timeout is None:
                    waits[wait_group].append(channel)
                else:
                    grace = _MDAC_RAMP_TIMEOUT_GRACE if timeout > 0 else 0.0
                    timed_waits.append((perf_counter() + timeout + grace, channel))
            else:
                channel.voltage(target)

        for deadline, channel in sorted(timed_waits, key=lambda wait: wait[0]):
            if not channel.block(timeout=max(0.0, deadline - perf_counter())):
                raise TimeoutError(f"Ramp did not complete on {channel.full_name}")
        for (mdac, _), channels in waits.items():
            if not mdac.wait_ramps(channels):
                raise TimeoutError(f"Ramps did not complete on {mdac.name}")

        for channel, target in gate_targets.items():
            if not isclose(channel.voltage.cache.get(), target, abs_tol=1e-4):
                raise RuntimeError(f"{channel.full_name} stopped before reaching {target} V")
        for parameter in self._parameters:
            if isinstance(parameter, Gate):
                parameter._t_last_set = perf_counter()
