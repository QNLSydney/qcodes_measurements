"""
DRIVER FOR MDAC V1.9
STEVEN WADDY & ANDREW KELLY, QNL, UNIVERSITY OF SYDNEY
MODIFIED FOR ZMQ MIRROR, SP 12/2025
Not to be copied, redistributed or reproduced without permission
"""

import datetime
import functools
import inspect
import json
import logging
import os
import sys
import time
import warnings
from collections.abc import Mapping, Sequence
from enum import Enum, IntEnum
from fractions import Fraction
from pathlib import Path
from typing import (
    Any,
    Literal,
    NoReturn,
    Protocol,
    cast,
    runtime_checkable,
)

import qcodes
import qcodes.logger
import serial
import serial.tools
import serial.tools.list_ports
import zmq
from dateutil import parser
from packaging.version import Version
from qcodes import validators as vals
from qcodes.instrument import ChannelList, Instrument, InstrumentChannel
from qcodes.logger.instrument_logger import get_instrument_logger
from qcodes.metadatable import SnapshotUpdate, normalize_snapshot_update
from qcodes.parameters import DelegateParameter, Parameter

try:
    # pywin32 requires you to run a post install script to
    # work properly. Since we are only using it for error
    # reporting we will write the drivers such that we only
    # optionally depend on it.
    import win32api  # ty: ignore[unresolved-import]  # Optional Windows error reporting.

    has_win32api = True
except ImportError:
    has_win32api = False

# Debug settings/variables
fake_configurable_trigger_levels = False

_driver_path = os.path.dirname(os.path.abspath(__file__))

qcodes_version = Version(qcodes.__version__)
# This is the minimal version of qcodes.
__v0_32_0__ = Version("0.32.0")

# This is the version of the MDAC driver.
# It should be revised each time the driver is updated
__MDAC_driver_version__ = Version("0.4.0")

# This is the minimum MDAC firmware version that is supported.
# If a firmware version LESS than this is detected, the driver will issue an ERROR ...
# and abort unless _errors_are_fatal has been set to False.
__MDAC_driver_min_supported_fw__ = Version("1.8")

# This is the maximum MDAC firmware version that is known to be supported.
# ie: the MDAC version that was current when this driver was released.
# It's not known whether later versions are compatible.  So, if a firmware
# version GREATER than this is detected, the driver will issue a WARNING ...
# but it will attempt to continue to operate.
__MDAC_driver_max_supported_fw__ = Version("1.9")

# Anticipate the need for these "interesting values".
__v1_8__ = Version("1.8")
__v1_9__ = Version("1.9")

# This is the maximum number of buses supported by the driver.
# To extend this number, define the extra _set/_get_bus_x functions (in the MDACChannel and MDAC classes).
__MDAC_buses_max_supported__: int = 4

__MDAC_default_autosync_state__: bool = True


@runtime_checkable
class DaisyChain(Protocol):
    """Interface supplied by an external MDAC daisy-chain controller."""

    _minions: Sequence[Any] | None
    channels_isense: Sequence[Any]
    channels: Sequence[Any]
    _last_mdac_status: str | None
    _last_mdac_reply: str | None

    def _set_arb_engine(self, on_off: str) -> None: ...
    def _get_arb_engine(self) -> str: ...
    def bus_owner(self, bus_num: int) -> Any: ...
    def bus_channels(self, bus_num: int) -> Any: ...
    def disconnect_bus(self, bus_num: int) -> None: ...
    def sync(self) -> None: ...
    def _write(self, mdac: Any, cmd: str) -> None: ...
    def _read(self, decode_response: bool = True) -> str: ...


class ProtocolChars:
    RS = "\x1e"
    ETB = "\x17"
    CR = "\r"
    LF = "\n"
    CRLF = "\r\n"
    CAN = "\x18"


PresetType = tuple[str, str, str, str, str, str, str, str, str, str, str, str | None]
DacParseType = tuple[int, str, str, str, str, list[str], list[str] | None]
DacPresetType = tuple[int, list[str]]


class FirmwareFeature(IntEnum):
    LimitRate = 0
    CheckLimited = 1
    PowerManipulation = 2
    MultiBus = 3
    VoltageTest = 4
    CmdSeqNum = 5
    MdacAddr = 6
    ComsChecksums = 7
    MapChannels = 8
    BusNumberRequired = 9
    DaisyChainSupported = 10
    ConfigurableTriggerOutLevels = 11
    GetPresets = 12
    SlavesStatus = 13
    LocateMDAC = 14
    MultiWaveforms = 15
    DacsStatus = 16
    DacsLimits = 17
    TriggersStatus = 18
    NumBuses = 19
    PeakTemperature = 20
    SnapshotSupported = 21
    DacAllOutputs = 22
    DacsConfig = 23
    DacLimitsIncludesChanNum = 24
    AutoNumberIncludesIpAddress = 25
    ArbInterp1d = 26


class MdacVariant(Enum):
    Mdac64 = "MDAC64"
    Mdac8 = "MDAC8"
    Mdac24 = "MDAC24"
    MdacDaisyChain = "MDAC Daisychain"
    Unknown = "UNKNOWN"


__MDAC_driver_supported_variants__: tuple[MdacVariant, MdacVariant, MdacVariant, MdacVariant] = (
    MdacVariant.Mdac64,
    MdacVariant.Mdac8,
    MdacVariant.Mdac24,
    MdacVariant.MdacDaisyChain,
)


class DaisyChainState(IntEnum):
    StandAlone = 0
    Supervisor = 1
    Minion = 2
    MinionPending = 3


class ChecksumError(Exception):
    pass


class SeqNumError(Exception):
    pass


class ReadTimeoutError(Exception):
    pass


class MsgRelayFailure(Exception):
    pass


_MDAC_COMMS_ID_AUTOFILL = -1


class ParameterParsers:
    @staticmethod
    def _lowercase_parser(s: str) -> str:
        s = s.lower()
        return s

    @staticmethod
    def _uppercase_parser(s: str) -> str:
        s = s.upper()
        return s

    _WAVEFORM_VALIDATION = vals.Enum("arb", "sin", "tri", "sqr", "saw", "was", "ramp", "off")
    _ARB_VALIDATION = vals.Enum("off", "OFF", "on", "ON", "armed", "ARMED")
    _TRIGGER_DIRECTION_VALIDATION = vals.Enum("up", "down")
    _TRIGGER_LEVEL_VALIDATION = vals.Enum("high", "low")
    _FILTERS = vals.Enum(1, 2)
    _FILTERS_INCLUDING_0 = vals.Enum(0, 1, 2)


def reverse_dict_lookup[K, V](d: Mapping[K, V], val: V) -> K | None:
    k = None
    for key, value in d.items():
        if value == val:
            k = key
    return k


def _ASSERT(test: bool) -> None:
    # Using this instead of builtin assert() ensures Users can't block it with python -O
    if not test:
        currentframe = inspect.currentframe()
        assert currentframe is not None
        calling_frame = currentframe.f_back
        raise AssertionError(calling_frame)


class MDACBasicChannel(InstrumentChannel):
    """Base Channel Class for MDAC DACs"""

    def __init__(self, parent: "MDAC", name: str, channum: int, cardnum: int) -> None:
        """
        Args:
            parent (Instrument): The instrument the channel is a part of.
            name (str): the name of the channel
            channum (int): MDAC channel number ([1-64] for populated MDAC)
            cardnum (int): the number of the card holding the DAC for this channel
        """
        super().__init__(parent, name)

        self._cardnum = cardnum
        self._channum = channum
        self._min_dac_sample = 0
        self._max_dac_sample = 2**20 - 1
        self._samplerate: float = self.parent._samplerate  # Sa/s
        self._LSB_undivided = 9.54e-6  # volts
        self._LSB_divided = 9.54e-8  # volts
        self._LSB = self._LSB_undivided
        self._gain: float = 1
        self._maximum_voltage: float = 5
        self._minimum_voltage: float = -5
        self._divider_multiplier = 0.01
        self._arb_data_max_samples = 32
        self._zero_volts_dac: int
        self._default_ramprate = 0.001  # V/s
        self._autosync = __MDAC_default_autosync_state__  # sync after waveform update
        self._max_ramp_rate_capability = (
            self._maximum_voltage - self._minimum_voltage
        ) * self._samplerate

        dac_config = self._get_dac_config()
        self._wave_max_samples = int(dac_config[1])
        self._ramp_max_samples = int(dac_config[3])
        self._wave_max_seconds = float(dac_config[2])
        self._ramp_max_seconds = float(dac_config[4])

        # channel parameters
        self.voltage = Parameter(
            "voltage",
            instrument=self,
            label=f"Channel {self._channum} voltage",
            unit="V",
            set_cmd=self._set_voltage,
            get_cmd=self._get_voltage,
            vals=vals.Numbers(-5, 5),
            docstring="current output voltage [volts], set according to voltage_ramp_rate",
        )

        self.voltage_raw = Parameter(
            "voltage_raw",
            instrument=self,
            label=f"Channel {self._channum} voltage_raw",
            unit="LSB",
            set_cmd=self._set_dac_raw,
            get_cmd=self._get_dac_raw,
            vals=vals.Ints(self._min_dac_sample, self._max_dac_sample),
            docstring="current output voltage [raw values], set according to voltage_ramp_rate",
        )

        self.voltage_set_ramp_rate = Parameter(
            "voltage_set_ramp_rate",
            instrument=self,
            label=f"Channel {self._channum} voltage set ramp rate",
            unit="V/Sec",
            set_cmd=None,
            get_cmd=None,
            vals=vals.MultiType(vals.Numbers(min_value=0), vals.Enum(None)),
            set_parser=self._process_voltage_set_ramp_rate,
            docstring="ramp rate used when setting voltages, None == set immediately",
        )

        self.voltage_set_blocking = Parameter(
            "voltage_set_blocking",
            instrument=self,
            label=f"Channel {self._channum} voltage set blocking behaviour",
            set_cmd=None,
            get_cmd=None,
            vals=vals.Bool(),
            initial_value=False,
            docstring="if True, voltage set operations will block until the voltage is reached",
        )

        self.voltage_set_block_timeout = Parameter(
            "voltage_set_block_timeout",
            instrument=self,
            label=f"Channel {self._channum} voltage set block timeout",
            set_cmd=None,
            get_cmd=None,
            initial_value=None,
            vals=vals.MultiType(vals.Numbers(min_value=0), vals.Enum(None)),
            docstring="Timeout on waiting for blocking voltage set \n"
            "operations to complete. None means wait indefinitely.",
        )

        self.amplitude = Parameter(
            "amplitude",
            instrument=self,
            label=f"Channel {self._channum} amplitude",
            unit="Vpp",
            get_cmd=self._get_amplitude,
            set_cmd=self._set_otf_amplitude,
            vals=vals.Numbers(0, 10),
            docstring="current waveform's peak-to-peak amplitude (zero for DC)",
        )

        self.offset = Parameter(
            "offset",
            instrument=self,
            label=f"Channel {self._channum} offset",
            unit="V",
            set_cmd=f"DAC:{self._channum}:PRESET:OFFSET {{}}",
            get_cmd=self._get_offset,
            vals=vals.Numbers(-5, 5),
            docstring="current waveform's offset",
        )

        self.frequency = Parameter(
            "frequency",
            instrument=self,
            label=f"Channel {self._channum} frequency",
            unit="Hz",
            get_cmd=self._get_frequency,
            docstring="current waveform's frequency",
        )

        self.period = Parameter(
            "period",
            instrument=self,
            label=f"Channel {self._channum} period",
            unit="Sec",
            get_cmd=self._get_period,
            docstring="current waveform's period",
        )

        self.phase = Parameter(
            "phase",
            instrument=self,
            label=f"Channel {self._channum} phase offset",
            unit="Deg",
            get_cmd=self._get_phase_offset,
            set_cmd=self._set_phase_offset,
            vals=vals.Numbers(0, 360),
            docstring="current waveform's phase offset",
        )

        self.group_size = Parameter(
            "group_size",
            instrument=self,
            label=f"Channel {self._channum} waveform group size",
            get_cmd=self._get_group_size,
            docstring="current waveform's group size",
        )

        self.waveform = Parameter(
            "waveform",
            instrument=self,
            label=f"Channel {self._channum} waveform",
            get_cmd=self._get_preset_name,
            get_parser=ParameterParsers._lowercase_parser,
            vals=ParameterParsers._WAVEFORM_VALIDATION,
            docstring="current waveform's type",
        )

        self.default_ramprate = Parameter(
            "default_ramprate",
            instrument=self,
            label=f"Channel {self._channum} default ramp-rate",
            unit="V/s",
            get_cmd=self._get_default_ramprate,
            set_cmd=self._set_default_ramprate,
            vals=vals.Numbers(0, 100000),
            docstring="default ramp-rate used for the ramp command",
        )

        self.ramp_rate = Parameter(
            "ramp_rate",
            instrument=self,
            label=f"Channel {self._channum} ramp-rate",
            unit="V/s",
            get_cmd=self._get_ramprate,
            docstring="returns the current ramp rate\nor None if there is no active ramp",
        )

        self.ramp_destination = Parameter(
            "ramp_destination",
            instrument=self,
            label=f"Channel {self._channum} ramp end-point",
            unit="V",
            get_cmd=self._get_ramp_destination,
            docstring="returns the current ramp destination\nor None if there is no active ramp",
        )

        self.autosync = Parameter(
            "autosync",
            instrument=self,
            label=f"Channel {self._channum} waveform auto resync",
            get_cmd=self._get_autosync,
            set_cmd=self._set_autosync,
            vals=vals.Bool(),
            docstring="True, issues a resync command after a waveform update\n"
            "False, a resync must be issued using MDAC.sync()",
        )

        # Note: set_cmd intentionally links "public" method self.set_limit_max() below.
        #       This is so that, in addition to the parameter here, users have access to a
        #       function that allows them to supply a second parameter (force).
        self.limit_max = Parameter(
            "limit_max",
            instrument=self,
            label=f"Channel {self._channum} maximum voltage limit",
            unit="V",
            get_cmd=self._get_limit_max,
            set_cmd=self.set_limit_max,
            docstring="maximum voltage hardware limit",
        )

        # Note: set_cmd intentionally links "public" method self.set_limit_min() below.
        #       This is so that, in addition to the parameter here, users have access to a
        #       function that allows them to supply a second parameter (force).
        self.limit_min = Parameter(
            "limit_min",
            instrument=self,
            label=f"Channel {self._channum} minimum voltage limit",
            unit="V",
            get_cmd=self._get_limit_min,
            set_cmd=self.set_limit_min,
            docstring="minimum voltage hardware limit",
        )

        if self._check_fw_feature(FirmwareFeature.LimitRate):
            self.limit_rate = Parameter(
                "limit_rate",
                instrument=self,
                label=f"Channel {self._channum} maximum rate limit",
                unit="V/s",
                get_cmd=self._get_limit_rate,
                set_cmd=self._set_limit_rate,
                vals=vals.Numbers(0.2, 203504),
                docstring="maximum rate of change, hardware limit",
            )

        self.disabled = Parameter(
            "disabled",
            instrument=self,
            label=f"Channel {self._channum} disabled",
            set_cmd=f"DAC:{self._channum}:DISABLE {{}}",
            get_cmd=f"DAC:{self._channum}:DISABLE ?",
            vals=vals.Bool(),
            val_mapping={True: 1, False: 0},
            docstring="True, disables the channel\n"
            "False, enables the channel\n"
            "Note: this may be used to re-enable a channel that has failed the voltage_test",
        )

        self.bus: Parameter
        self.buses: list[Parameter] = []

        # If the Test Card is present, then DAC 0 can be attached to BUS 1,
        # other DACs can be connected to all available BUSes.
        if (self._cardnum != 0) or (self._channum != 0) or self.parent._test_card_present():
            if (self._cardnum != 0) and (self._channum != 0):
                num_connectable_buses = parent._num_buses
            else:
                num_connectable_buses = 1
            for bus_num in range(1, num_connectable_buses + 1):
                _param_name = f"bus{bus_num}"
                _label = f"Channel {self._channum} BUS {bus_num} relay"
                _set_cmd = functools.partial(self._set_bus, bus_num=bus_num)
                _get_cmd = functools.partial(self._get_bus, bus_num=bus_num)
                _docstring = f"bus {bus_num} relay"
                self.add_parameter(
                    _param_name,
                    label=_label,
                    set_cmd=_set_cmd,
                    get_cmd=_get_cmd,
                    val_mapping={"open": 0, "close": 1},
                    docstring=_docstring,
                )
                param = cast(
                    Parameter | None,
                    self.parameters.get(_param_name),
                )
                if param is not None:
                    self.buses.append(param)
            # For extra compatibility with existing scripts.
            self.bus = DelegateParameter(
                "bus",
                source=self.bus1,
                label="Script compatibility alias for BUS 1 output relay",
                docstring="Script compatibility alias for BUS 1 output relay",
                instrument=self,
            )
        else:
            # For extra compatibility with existing scripts.
            self.bus = Parameter(
                name="bus",
                instrument=self,
                label="Channel fake BUS relay",
                get_cmd=False,
                set_cmd=False,
            )

    def _write(self, *args: Any, **kwargs: Any):
        return self.parent._write(*args, **kwargs)

    def _ask(self, *args: Any, **kwargs: Any) -> str:
        return self.parent._ask(*args, **kwargs)

    def snapshot_base(
        self,
        update: bool | SnapshotUpdate | None = False,
        params_to_skip_update: Sequence[str] | None = None,
    ) -> dict[Any, Any]:
        if self.parent._check_fw_feature(FirmwareFeature.SnapshotSupported):
            if (
                normalize_snapshot_update(update) != "Never"
                and not self.parent._update_cache_performed
            ):
                self.parent._update_cache()
            # The call to MDAC._update_cache() above will have updated some supported
            # parameters already .. so they don't need to be updated again.
            # Note: I'm not sure whether it's safe to simply extend params_to_skip_update if
            # it's not None.  Without knowing that, we'll take the _safer_ approach.
            num_buses = self.parent._num_buses
            if params_to_skip_update is None:
                params_to_skip_update = [
                    "voltage",
                    "voltage_raw",
                    "amplitude",
                    "offset",
                    "frequency",
                    "period",
                    "phase",
                    "group_size",
                    "waveform",
                    "ramp_rate",
                    "ramp_destination",
                    "limit_max",
                    "limit_min",
                ]
                if self._check_fw_feature(FirmwareFeature.DacsStatus):
                    params_to_skip_update.append("disabled")
                if self._check_fw_feature(FirmwareFeature.LimitRate):
                    params_to_skip_update.append("limit_rate")
                # Do the MDACChannel vs MDACBasicChannel here rather than in the MDACChannel class
                # otherwise using params_to_skip_update = None starts to get complicated.
                if self._channum != 0:
                    params_to_skip_update.extend(
                        ["microd", "gnd", "filter", "dac_output", "divider"]
                    )
                    if self.parent._mdac_variant == MdacVariant.Mdac24:
                        params_to_skip_update.append("sma")
                    else:
                        params_to_skip_update.append("smc")

                if num_buses == 1:
                    params_to_skip_update.append("bus")
                else:
                    for bus_index in range(len(self.buses)):
                        bus_name = f"bus{bus_index + 1}"
                        params_to_skip_update.extend([bus_name])
        snapshot = super().snapshot_base(update=update, params_to_skip_update=params_to_skip_update)
        return snapshot

    def channel_number(self) -> int:
        """
        DAC channel number corresponding to SMC channel numbers
        """
        return self._channum

    def _get_bus(self, bus_num: int = 1) -> str:
        cmd_str = ""
        if self._check_fw_feature(FirmwareFeature.BusNumberRequired):
            cmd_str = f"DAC:{self._channum}:RELAYS:BUS:{bus_num} ?"
        elif bus_num != 1:
            self.parent._handle_error(f"Invalid bus_num supplied: {bus_num}")
        else:
            cmd_str = f"DAC:{self._channum}:RELAYS:BUS ?"
        response = self._ask(cmd_str)
        return response

    def _set_bus(self, cmd: str, bus_num: int = 1) -> None:
        cmd_str = ""
        if self._check_fw_feature(FirmwareFeature.BusNumberRequired):
            cmd_str = f"DAC:{self._channum}:RELAYS:BUS:{bus_num} {cmd}"
        elif bus_num != 1:
            self.parent._handle_error(f"Invalid bus_num supplied: {bus_num}")
        else:
            cmd_str = f"DAC:{self._channum}:RELAYS:BUS {cmd}"
        self._write(cmd_str)

    def attach_trigger(
        self,
        pulse_width: float | None = None,
        pulse_width_raw: int | None = None,
    ) -> None:
        """
        Assign this channel as the trigger source
        """
        # Trigger pulse width
        if pulse_width is not None and pulse_width_raw is not None:
            raise RuntimeError("Do NOT supply values for both 'pulse_width' and 'pulse_width_raw'.")
        elif pulse_width is not None:
            # Trigger width has been given in seconds.
            pulse_width_str = f",{pulse_width}"
        elif pulse_width_raw is not None:
            # Trigger width has been given in raw units.
            pulse_width_str = f",RAW{pulse_width_raw}"
        else:
            pulse_width_str = ""

        cmd = f"DAC:{self._channum}:TRIGGER SET{pulse_width_str}"
        self._write(cmd)

    def limit_exceeded(self) -> bool | None:
        """
        Indicates whether the channel's voltage configuration exceeds the hardware limits
        """
        ret: bool | None
        if self._check_fw_feature(FirmwareFeature.CheckLimited):
            dac_state = self._get_dac_state()
            ret = dac_state[4] == "TRUE"
        else:
            self.parent._handle_warning(
                f"limit_exceeded() not supported in firmware v{self.parent._mdac_fw_ver}"
            )
            ret = None

        return ret

    def voltage_test(self) -> bool:
        """
        Perform self-test on channel's DAC chip.
        Note that a FAILED test will result in the channel being disabled.
        """
        test_passed = False
        if self._check_fw_feature(FirmwareFeature.VoltageTest):
            cmd = f"DAC:{self._channum} TEST"
            if self.parent._test_card_present():
                reply = self._ask(cmd)
                test_passed = reply == "PASS"
            else:
                warn_str = "TEST CARD not installed"
                self.parent._handle_error(warn_str)
        else:
            self.parent._handle_warning(
                f"voltage_test() not supported in firmware v{self.parent._mdac_fw_ver}"
            )

        return test_passed

    def _get_dac(self) -> tuple[float, int]:
        resp = self._ask(f"DAC:{self._channum}:OUTPUT ?")
        volt = float(resp.split(",")[0])
        raw = int(resp.split(",")[1], 16)
        return volt, raw

    def _set_voltage(self, voltage: float) -> None:
        voltage_set_ramp_rate: float | None = self.voltage_set_ramp_rate()
        if voltage_set_ramp_rate is None:
            cmd = f"DAC:{self._channum}:OUTPUT {voltage}"
            self._write(cmd)
        else:
            self.ramp(voltage, voltage_set_ramp_rate)
            if self.voltage_set_blocking():
                timeout: float | None = self.voltage_set_block_timeout()
                self.block(timeout)

    # Enact a non-blocking voltage set operation regardless of the current settings.
    def _nonblocking_set_voltage(self, voltage: float) -> None:
        with self.voltage_set_blocking.set_to(False):
            self.voltage(voltage)

    def _get_voltage(self, voltages: tuple[float, int] | None = None) -> float:
        if voltages is None:
            voltages = self._get_dac()
        return voltages[0]

    def _process_voltage_set_ramp_rate(self, ramp_rate: float | None) -> float | None:
        if ramp_rate is not None:
            if ramp_rate > self._max_ramp_rate_capability:
                self.parent._handle_warning(
                    f"ramp_rate exceeds maximum capability ({self._max_ramp_rate_capability})"
                )
                ramp_rate = self._max_ramp_rate_capability
        return ramp_rate

    def _set_dac_raw(self, dac: int) -> None:
        cmd = f"DAC:{self._channum}:OUTPUT RAW{dac}"
        self._write(cmd)

    def _get_dac_raw(self, voltages: tuple[float, int] | None = None) -> int:
        if voltages is None:
            voltages = self._get_dac()
        return voltages[1]

    def _set_autosync(self, autosync: bool) -> None:
        self._autosync = autosync

    def _get_autosync(self) -> bool:
        return self._autosync

    def _set_default_ramprate(self, rate: float) -> None:
        self._default_ramprate = rate

    def _get_default_ramprate(self) -> float:
        return self._default_ramprate

    def _get_ramprate(self, preset: PresetType | None = None) -> float | None:
        if preset is None:
            preset = self._get_preset()
        if preset[0] != "ramp":
            rate = None
        else:
            period = float(preset[1])
            start_volts = float(preset[3])
            dest_volts = float(preset[5])
            rate = abs((dest_volts - start_volts) / period)
        return rate

    def _get_ramp_destination(self, preset: PresetType | None = None) -> float | None:
        if preset is None:
            preset = self._get_preset()
        if preset[0] != "ramp":
            dest = None
        else:
            dest = float(preset[5])
        return dest

    def _ramp(self, voltage: float, rate: float | None = None) -> None:

        if rate is None:
            rate = self._default_ramprate

        if self.parent._sanity_check_ramps:
            protection_status = self.parent.protection()
            limit_rate = self._get_limit_rate()
        else:
            protection_status = "off"
            limit_rate = None
        if (protection_status == "on") and (limit_rate is not None) and (rate > limit_rate):
            self.parent._handle_warning(
                f"Protection is on and the specified ramp rate ({rate}) "
                f"is higher than this channel's limit_rate ({limit_rate})"
            )
        SR = self._samplerate
        LSB = self._LSB

        if self.parent._mdac_fw_ver <= __v1_8__:
            # Bug 10283: divider or dac relay does not want to open after usage of channel with offset
            # There is a caveat using RATERAW with these firmware versions.  If the specified rate results in a period of 1,
            # the ramp never happens.
            self.awg_off()
            start_voltage = self.voltage.get()
            period = abs(voltage - start_voltage) / rate
            num_sample_steps = period * SR
            if num_sample_steps < 2:
                cmd = f"DAC:{self._channum}:OUTPUT {voltage}"
            else:
                cmd = f"DAC:{self._channum}:PRESET RAMP,{period},@,{voltage},S1"
        else:
            rawrate = round(abs(rate) / (LSB * SR), 3)
            if rawrate < 0.001:
                rawrate = 0.001
            cmd = f"DAC:{self._channum}:PRESET RAMP,RATERAW{rawrate},@,{voltage},S1"
        self._write(cmd)

    def _card_voltages(self) -> tuple[float, float, float]:
        # maximum range clipped in HW REV 1
        # cliped to ~ (13V, -10V)
        r = self._ask(f"SLAVE:{self._cardnum}:VOLTAGES ?")
        v3 = round(float(r.split(",")[5]), 2)
        v15 = round(float(r.split(",")[3]), 2)
        v15m = round(float(r.split(",")[1]), 2)
        return v3, v15, v15m

    def _card_voltages_raw(self) -> tuple[int, int, int]:
        r = self._ask(f"SLAVE:{self._cardnum}:VOLTAGES ?")
        i3 = int(r.split(",")[4], 16)
        i15 = int(r.split(",")[2], 16)
        i15m = int(r.split(",")[0], 16)
        return i3, i15, i15m

    def ramp(self, destn_voltage: float, ramp_rate: float | None = None) -> None:
        """
        Helper function to set a ramp from the current voltage to a
        specified destination.  If a ramp-rate is not specified, the
        default is used.

        Args:
            destn_voltage: ramp destination voltage (Volts)
            ramp_rate: ramp rate (Volts/Sec) - (default = default_ramp_rate)
        """
        if ramp_rate is None:
            ramp_rate = self._default_ramprate
        return self._ramp(destn_voltage, ramp_rate)

    def block(self, timeout: float | None = None) -> bool:
        """
        Blocks until channel is not ramping
        """
        return self.parent.wait_ramps(channels=self, timeout=timeout)

    def awg_sine(
        self,
        frequency: float,
        amplitude: float,
        offset: float = 0,
        phase: float = 0,
        group_size: int = 1,
    ) -> None:
        """
        Helper function to set the waveform
        to a sine wave at a given frequency, amplitude and offset

        Args:
            frequency: [Hz]
            amplitude: [Vpp]
            offset: [V]
            phase: [Deg]
            group_size:
        """
        assert isinstance(self.parent, MDAC)
        period = 1 / frequency
        delay = period * phase / 360
        preset_cmd: str = f"DAC:{self._channum}:PRESET SIN,{period},{offset},{amplitude},{delay}"
        if self._check_fw_feature(FirmwareFeature.MultiWaveforms) and (group_size > 1):
            preset_cmd += f",MULTI{group_size}"
        self._write(preset_cmd)
        if self._autosync:
            _last_mdac_status = self.parent._last_mdac_status
            self.parent.sync()
            self.parent._last_mdac_status = _last_mdac_status

    def awg_sawtooth(
        self,
        frequency: float,
        amplitude: float,
        offset: float = 0,
        phase: float = 0,
        group_size: int = 1,
    ) -> None:
        """
        Helper function to set the waveform
        to a rising sawtooth wave at a given frequency, amplitude and offset

        Args:
            frequency: [Hz]
            amplitude: [Vpp]
            offset: [V]
            phase: [Deg]
            group_size:
        """
        assert isinstance(self.parent, MDAC)
        period = 1 / frequency
        delay = period * phase / 360
        preset_cmd: str = f"DAC:{self._channum}:PRESET SAW,{period},{offset},{amplitude},{delay}"
        if self._check_fw_feature(FirmwareFeature.MultiWaveforms) and (group_size > 1):
            preset_cmd += f",MULTI{group_size}"
        self._write(preset_cmd)
        if self._autosync:
            _last_mdac_status = self.parent._last_mdac_status
            self.parent.sync()
            self.parent._last_mdac_status = _last_mdac_status

    def awg_sawtooth_falling(
        self,
        frequency: float,
        amplitude: float,
        offset: float = 0,
        phase: float = 0,
        group_size: int = 1,
    ) -> None:
        """
        Helper function to set the waveform
        to a falling sawtooth wave at a given frequency, amplitude and offset

        Args:
            frequency: [Hz]
            amplitude: [Vpp]
            offset: [V]
            phase: [Deg]
            group_size:
        """
        assert isinstance(self.parent, MDAC)
        period = 1 / frequency
        delay = period * phase / 360
        preset_cmd: str = f"DAC:{self._channum}:PRESET WAS,{period},{offset},{amplitude},{delay}"
        if self._check_fw_feature(FirmwareFeature.MultiWaveforms) and (group_size > 1):
            preset_cmd += f",MULTI{group_size}"
        self._write(preset_cmd)
        if self._autosync:
            _last_mdac_status = self.parent._last_mdac_status
            self.parent.sync()
            self.parent._last_mdac_status = _last_mdac_status

    def awg_square(
        self,
        frequency: float,
        amplitude: float,
        offset: float = 0,
        phase: float = 0,
        group_size: int = 1,
    ) -> None:
        """
        Helper function to set the waveform
        to a square wave at a given frequency, amplitude and offset

        Args:
            frequency: [Hz]
            amplitude: [Vpp]
            offset: [V]
            phase: [Deg]
            group_size:
        """
        assert isinstance(self.parent, MDAC)
        period = 1 / frequency
        delay = period * phase / 360
        preset_cmd: str = f"DAC:{self._channum}:PRESET SQR,{period},{offset},{amplitude},{delay}"
        if self._check_fw_feature(FirmwareFeature.MultiWaveforms) and (group_size > 1):
            preset_cmd += f",MULTI{group_size}"
        self._write(preset_cmd)
        if self._autosync:
            _last_mdac_status = self.parent._last_mdac_status
            self.parent.sync()
            self.parent._last_mdac_status = _last_mdac_status

    def awg_triangle(
        self,
        frequency: float,
        amplitude: float,
        offset: float = 0,
        phase: float = 0,
        group_size: int = 1,
    ) -> None:
        """
        Helper function to set the waveform
        to a triangle wave at a given frequency, amplitude and offset

        Args:
            frequency: [Hz]
            amplitude: [Vpp]
            offset: [V]
            phase: [Deg]
            group_size:
        """
        assert isinstance(self.parent, MDAC)
        period = 1 / frequency
        delay = period * phase / 360
        preset_cmd: str = f"DAC:{self._channum}:PRESET TRI,{period},{offset},{amplitude},{delay}"
        if self._check_fw_feature(FirmwareFeature.MultiWaveforms) and (group_size > 1):
            preset_cmd += f",MULTI{group_size}"
        self._write(preset_cmd)
        if self._autosync:
            _last_mdac_status = self.parent._last_mdac_status
            self.parent.sync()
            self.parent._last_mdac_status = _last_mdac_status

    def awg_pulse(
        self,
        frequency: float,
        width: float,
        amplitude: float,
        offset: float = 0,
        phase: float = 0,
        group_size: int = 1,
    ) -> None:
        """
        Helper function to set the waveform
        to a pulse at a given frequency, amplitude and offset

        Args:
            frequency: [Hz]
            width: [mSecs]
            amplitude: [Vpp]
            offset: [V]
            phase: [Deg]
            group_size:
        """
        period = 1 / frequency
        delay = period * phase / 360
        if amplitude >= 0:
            preset_str = "PLS"
        else:
            preset_str = "SLP"
        preset_cmd: str = (
            f"DAC:{self._channum}:PRESET {preset_str},{period},{offset},{amplitude},{width},{delay}"
        )
        if self._check_fw_feature(FirmwareFeature.MultiWaveforms) and (group_size > 1):
            preset_cmd += f",MULTI{group_size}"
        self._write(preset_cmd)
        if self._autosync:
            self.parent.sync()

    def awg_off(self) -> None:
        """Helper function to turn the waveform off"""
        assert isinstance(self.parent, MDAC)
        self._write(f"DAC:{self._channum}:PRESET NONE")
        if self._autosync:
            _last_mdac_status = self.parent._last_mdac_status
            self.parent.sync()
            self.parent._last_mdac_status = _last_mdac_status

    def _volts_to_raw(self, volts: float) -> int:
        if volts > 0:
            dac_range = self._max_dac_sample - self._zero_volts_dac
            volts_per_dac = (self._maximum_voltage * self._gain) / dac_range
        else:
            dac_range = self._min_dac_sample - self._zero_volts_dac
            volts_per_dac = (self._minimum_voltage * self._gain) / dac_range
        dac = (volts / volts_per_dac) + self._zero_volts_dac
        return round(dac)

    def _get_max_interp1d_pairs(self) -> int:
        cmd = f"DAC:{self._channum}:PRESET ARB_INTERP1D_DATA,?MAX_PAIRS"
        response = self._ask(cmd)
        return int(response)

    def _check_interp1d_data_is_monotonic_and_non_negative(
        self, sample_x_vals: Sequence[Any]
    ) -> tuple[bool, str]:
        """
        Helper validation function for arbitrary waveform vertices.

        Returns Tuple(Valid, Error String)
        """

        #
        # validate the data points
        #
        for sample_x in sample_x_vals:
            if (sample_x != "@") and (sample_x < 0):
                return (False, "Sample times/indexes must all be >= 0")

        last_sample_x = sample_x_vals[0]
        for sample_x in sample_x_vals[1:]:
            if sample_x != "@":
                if sample_x <= last_sample_x:
                    return (False, "Sample times/indexes must monotonicly increase")
                last_sample_x = sample_x

        return (True, "")

    def _check_interp1d_special_chars_rules(
        self, sample_x_vals: Sequence[Any], max_pairs_per_command: int
    ) -> tuple[bool, str]:
        """
        Helper validation function for arbitrary waveform vertices.
        - no '@' at the start or end.
        - must be int, float, or '@'
        - check number of consecutive '@' (e.g MAX_PAIRS_PER_CMD - 2 = 6)

        Returns Tuple(Valid, Error String)
        """
        if sample_x_vals[0] == "@":
            return (False, "First sample time cannot be '@'")

        num_x_vals = len(sample_x_vals)
        if sample_x_vals[num_x_vals - 1] == "@":
            return (False, "Final sample time cannot be '@'")

        for sample_x in sample_x_vals:
            if isinstance(sample_x, str) and sample_x != "@":
                return (False, f"Unrecognized special character '{sample_x}'")
            if not (
                isinstance(sample_x, int)
                or isinstance(sample_x, float)
                or isinstance(sample_x, str)
            ):
                return (False, "Sample time/index values should be int, float, or '@'")

        # to avoid having '@' at the start of any command packet, we need an actual time at the start and end of each command.
        MAX_CONSECUTIVE_SPECIAL_CHARS = max_pairs_per_command - 2

        i = 0
        while i < num_x_vals:
            if sample_x_vals[i] == "@":
                num_consecutive_specials = self._interp1d_num_leading_special_chars(
                    sample_x_vals[i:]
                )
                i += num_consecutive_specials
                if num_consecutive_specials > MAX_CONSECUTIVE_SPECIAL_CHARS:
                    return (
                        False,
                        f"No more than {MAX_CONSECUTIVE_SPECIAL_CHARS} consecutive special chars",
                    )
            else:
                i += 1

        return (True, "")

    def _interp1d_num_leading_special_chars(self, sample_times: Sequence[Any]) -> int:
        num_leading_special_chars = 0
        for sample_time in sample_times:
            if sample_time == "@":
                num_leading_special_chars += 1
            else:
                break

        return num_leading_special_chars

    def _interp1d_check_inputs(
        self, max_num_pairs: int, sample_times: Sequence[Any], arb_data: Sequence[float]
    ) -> tuple[bool, str]:
        #
        # returns true if everything is okay.  false, error_str otherwise.
        #
        if len(sample_times) != len(arb_data):
            return (False, "sample times/indexes and values lengths must match")

        if len(sample_times) <= 1:
            return (False, "data array lengths must be >= 2.")

        validated, err_str = self._check_interp1d_special_chars_rules(sample_times, max_num_pairs)
        if not validated:
            return (False, err_str)

        validated, err_str = self._check_interp1d_data_is_monotonic_and_non_negative(sample_times)
        if not validated:
            return (False, err_str)

        return (True, "")

    #
    # _interp1d_construct_and_send_packets
    #
    # low level command, expected to be called by higher level methods
    #
    def _interp1d_construct_and_send_packets(
        self,
        raw_indexes: bool,
        wave_duration: Any,
        max_num_pairs: int,
        indexes: Sequence[Any],
        dac_values: Sequence[float],
    ) -> None:
        #
        # program the sequence of points, duplicating points if multiple transactions
        # are required.
        #
        if raw_indexes:
            cmd_str = f"DAC:{self._channum}:PRESET OFF,RAW{wave_duration:d}"
        else:
            cmd_str = f"DAC:{self._channum}:PRESET OFF,{wave_duration:0.6f}"
        self._write(cmd_str)
        arb_data_str = ""
        arb_data_num_pairs = 0
        num_data_pts = len(indexes)

        for i in range(num_data_pts):
            #
            # convert to raw data and clamp
            #
            sample_time_str = "@"
            num_special_chars_from_here = 0
            if indexes[i] != "@":
                if raw_indexes:
                    sample_time_str = f"RAW{indexes[i]:d}"
                else:
                    sample_time_str = f"{indexes[i]:0.6f}"
                num_special_chars_from_here = self._interp1d_num_leading_special_chars(
                    indexes[i + 1 :]
                )

            sample_str = f",{sample_time_str},{dac_values[i]}"
            arb_data_str += sample_str
            arb_data_num_pairs += 1
            if (arb_data_num_pairs + num_special_chars_from_here) >= max_num_pairs:
                # only allow 8 pairs per command due to protocol limitations. If there are enough special chars
                # immediately after this to reach the limit, send now so we have a number starting the next
                # packet.
                cmd_str = f"DAC:{self._channum}:PRESET ARB_INTERP1D_DATA,{arb_data_num_pairs}{arb_data_str}"
                self._write(cmd_str)
                # copy the last sample to the start of next packet
                arb_data_str = sample_str
                arb_data_num_pairs = 1

        #
        # write the last set of points
        #
        # if its only a single point, its a duplicate
        #
        if arb_data_num_pairs > 1:
            cmd_str = (
                f"DAC:{self._channum}:PRESET ARB_INTERP1D_DATA,{arb_data_num_pairs}{arb_data_str}"
            )
            self._write(cmd_str)

        cmd_str = f"DAC:{self._channum}:PRESET ARB"
        self._write(cmd_str)

    #
    # arbitrary waveform, 1d interpolation
    #
    def awg_interp1d(
        self,
        arb_data: Sequence[float],
        times: Sequence[Any] | None = None,
        indexes: Sequence[Any] | None = None,
        phase: float = 0,
    ) -> None:
        """
        Helper function to set an arbitrary waveform with xy-points, intermediate samples will be linearly interpolated.

        Notes: If two sample times are closer together than the DAC update rate, the firmware will report an error.
        Use the '@' special token to specify an update on 'the next sample'.  The number of consecutive '@' tokens is
        limited by the firmware command handling.  e.g MAX_PAIRS - 2.

        times or indexes must be specified but not both.

        Args:
            arb_data: an array of 0V offsets in Volts
            times: an array of sample time anchor points. truncated to useconds.
            indexes: an array of sample index anchor points.
            phase: [Deg]
        """
        assert isinstance(self.parent, MDAC)

        timeSpecified = times is not None
        indexSpecified = indexes is not None

        if not (timeSpecified ^ indexSpecified):
            self.parent._handle_error(
                "only one of times[] or indexes[] may be specified, but not both"
            )
            return

        # explicit checking needed here for mypy?
        if times is not None:
            x_values = times
        elif indexes is not None:
            x_values = indexes
        else:
            # unreachable?
            raise AssertionError("unreachable")

        if not self._check_fw_feature(FirmwareFeature.ArbInterp1d):
            self.parent._handle_error(
                f"arb_interp1d() not supported in firmware v{self.parent._mdac_fw_ver}"
            )
            return

        MAX_NUM_PAIRS = self._get_max_interp1d_pairs()
        validated, err_str = self._interp1d_check_inputs(MAX_NUM_PAIRS, x_values, arb_data)
        if not validated:
            self.parent._handle_error(err_str)
            return

        #
        # size the waveform time / sample count to the final sample index.
        # we limit the float precision to 6 digits (microseconds), because of rounding.
        #
        num_data_pts = len(x_values)
        if timeSpecified:
            wave_duration = x_values[num_data_pts - 1]
            if wave_duration > self._wave_max_seconds:
                error_str = f"Maximum wave length ( {self._wave_max_seconds} seconds) exceeded"
                self.parent._handle_error(error_str)
                return
        else:
            #
            # size the waveform time / sample count to the final sample index + 1
            #
            wave_duration = x_values[num_data_pts - 1] + 1
            if wave_duration > self._wave_max_samples:
                error_str = f"Maximum wave length ({self._wave_max_samples} samples) exceeded"
                self.parent._handle_error(error_str)
                return

        self._interp1d_construct_and_send_packets(
            not timeSpecified, wave_duration, MAX_NUM_PAIRS, x_values, arb_data
        )

        self._set_phase_offset(phase)
        if self._autosync:
            _last_mdac_status = self.parent._last_mdac_status
            self.parent.sync()
            self.parent._last_mdac_status = _last_mdac_status

    def awg_arbitrary_wave(self, arb_data: Sequence[float], phase: float = 0) -> None:
        """
        Helper function to set an arbitrary waveform

        Args:
            arb_data: an array of 0V offsets in Volts
            phase: [Deg]
        """
        sample_count = len(arb_data)
        if sample_count > self._wave_max_samples:
            error_str = f"Maximum wave length ({self._wave_max_samples} samples) exceeded"
            self.parent._handle_error(error_str)
        else:
            arb_data_raw = []
            for volts in arb_data:
                sample = self._volts_to_raw(volts)
                offset = sample - self._zero_volts_dac
                arb_data_raw.append(int(offset))
            self.awg_arbitrary_wave_raw(arb_data_raw, phase)

    def awg_arbitrary_wave_raw(self, arb_data: Sequence[int], phase: float = 0) -> None:
        """
        Helper function to set an arbitrary waveform

        Args:
            arb_data: an array of raw DAC data
            phase: [Deg]
        """
        assert isinstance(self.parent, MDAC)
        sample_count = len(arb_data)
        self._write(f"DAC:{self._channum}:PRESET OFF,RAW{sample_count}")
        arb_data_offset = 0
        arb_data_str = ""
        arb_data_num_samples = 0
        for offset in arb_data:
            sample = self._zero_volts_dac + offset
            # Ensure sample values don't wrap.
            sample = min(max(sample, self._min_dac_sample), self._max_dac_sample)
            sample_str = f"{sample:05X}"
            arb_data_str += sample_str
            arb_data_num_samples += 1
            if arb_data_num_samples == self._arb_data_max_samples:
                # This is the max data that can be sent in a single command
                self._write(f"DAC:{self._channum}:PRESET ARB_DATA,{arb_data_offset},{arb_data_str}")
                arb_data_offset += arb_data_num_samples
                arb_data_str = ""
                arb_data_num_samples = 0
        if len(arb_data_str) > 0:
            self._write(f"DAC:{self._channum}:PRESET ARB_DATA,{arb_data_offset},{arb_data_str}")
        self._write(f"DAC:{self._channum}:PRESET ARB")
        self._set_phase_offset(phase)
        if self._autosync:
            _last_mdac_status = self.parent._last_mdac_status
            self.parent.sync()
            self.parent._last_mdac_status = _last_mdac_status

    def _get_awg_arbitrary_wave_raw(self) -> list[int]:
        """
        Internal helper function to get the data from an arbitrary waveform
        Returns:
            A tuple of length equal to the number of samples in the waveform
        """
        arb_data = []
        samples_remaining = self._get_period_raw()
        num_samples_retrieved = 0
        while samples_remaining > 0:
            # Never ask for > self._max_arb_data_read_len samples
            request_num_samples = min(samples_remaining, self.parent._max_arb_data_read_len)
            arb_data_str = self._ask(
                f"DAC:{self._channum}:PRESET ARB_DATA,{num_samples_retrieved}?{request_num_samples}"
            )
            for sample_index in range(0, request_num_samples):
                arb_data_offset = sample_index * 5
                sample_str = arb_data_str[arb_data_offset : arb_data_offset + 5]
                sample = int(sample_str, 16)
                offset = sample - self._zero_volts_dac
                arb_data.append(offset)
            num_samples_retrieved += request_num_samples
            samples_remaining -= request_num_samples
        return arb_data

    def _get_preset(self, response: str | None = None) -> PresetType:
        """
        Internal helper function to get the active preset
        Returns:
            _get_preset()[0] is the waveform
            _get_preset()[1,2] is the period in seconds, ticks
            _get_preset()[3,4] is the offset/ramp_start in volts, dac units
            _get_preset()[5,6] is the amplitude/ramp_destn in volts, dac units
            _get_preset()[7] is the number of shots
            _get_preset()[8,9] is the phase offset in seconds, ticks
            _get_preset()[10] is the next sample offset
            _get_preset()[11] is the group size (for presets that have it)

        """
        if response is None:
            response = self._ask(f"DAC:{self._channum}:PRESET ?")
        # response is [PRESET, period_secs, period_samples, offs_V, offset_DAC, amp_V,
        #              amp_DAC, num_shots, phase_secs, phase_samples]
        if response.startswith("NONE"):
            resp = self._ask(f"DAC:{self._channum}:OUTPUT ?")
            volt_str = resp.split(",")[0]
            raw_str = resp.split(",")[1]
            ret: PresetType = (
                "off",
                "0",
                "0",
                volt_str,
                raw_str,
                "0",
                "0",
                "0",
                "0",
                "0",
                "0",
                None,
            )
        else:
            values = tuple(response.lower().split(","))
            ret = cast(PresetType, values)
            if (ret is None) or (ret[0] is None):
                raise RuntimeError(f"Unrecoverable error parsing response '{response}'.")

        return ret

    def _set_preset(
        self,
        preset: tuple[
            str,
            float | None,
            int,
            float | None,
            int,
            float | None,
            int,
            int,
            float | None,
            int,
            int | None,
        ],
    ) -> None:
        """
        Internal helper function to set a preset
        Args:
            preset is a tuple comsisting of:
            - preset[0] is the waveform
            - preset[1,2] is the period in seconds, ticks
            - preset[3,4] is the offset/ramp_start in volts, dac units
            - preset[5,6] is the amplitude/ramp_destination in volts, dac units
            - preset[7] is the number of shots
            - preset[8,9] is the phase offset in seconds, ticks
            - preset[10] is the group size
        """
        if (preset[0] == "off") or (preset[0] == "none"):
            preset_cmd = "OFF"
        else:
            preset_cmd = preset[0].upper()
            # period
            if preset[1] is not None:
                preset_cmd += f",{preset[1]}"
            else:
                preset_cmd += f",RAW{preset[2]}"
            # offset/ramp_start
            if preset[3] is not None:
                preset_cmd += f",{preset[3]}"
            else:
                preset_cmd += f",RAW{preset[4]}"
            # amplitude/ramp_destination
            if preset[5] is not None:
                preset_cmd += f",{preset[5]}"
            else:
                preset_cmd += f",RAW{preset[6]}"
            # number of shots
            if preset[7] is not None:
                preset_cmd += f",S{preset[7]}"
            # phase
            if preset[8] is not None:
                preset_cmd += f",{preset[8]}"
            else:
                preset_cmd += f",RAW{preset[9]}"
            if (preset[10] is not None) and (preset[10] > 1):
                preset_cmd += f",MULTI{preset[10]}"

        self._write(f"DAC:{self._channum}:PRESET {preset_cmd}")
        if self._autosync:
            self.parent.sync()

    def _get_preset_name(self, response: str | None = None) -> str:
        """
        Internal helper function to get the active preset name
        Returns:
            the active waveform
        """
        if response is None:
            response = self._ask(f"DAC:{self._channum}:PRESET ?N")
        ret = response.split(",")[0].lower()
        if ret == "none":
            ret = "off"
        return ret

    def _get_otf_scaler(self) -> float:
        response = self._ask(f"DAC:{self._channum}:PRESET:SCALER ?")
        return float(response.split(",")[0])

    def _set_otf_scaler(self, amplitude: float) -> None:
        waveform = self.waveform()
        if (waveform == "off") or (waveform == "dc"):
            self.parent._handle_error(f"Illegal operation - waveform: {waveform}")
        else:
            self._write(f"DAC:{self._channum}:PRESET:SCALER {amplitude}")

    def _get_otf_amplitude(self) -> float:
        response = self._ask(f"DAC:{self._channum}:PRESET:AMPLITUDE ?")
        return float(response.split(",")[0])

    def _get_otf_amplitude_raw(self) -> int:
        response = self._ask(f"DAC:{self._channum}:PRESET:AMPLITUDE ?")
        return int(response.split(",")[1], 16)

    def _set_otf_amplitude(self, amplitude: float) -> None:
        waveform = self.waveform()
        if (waveform == "off") or (waveform == "dc"):
            self.parent._handle_error(f"Illegal operation - waveform: {waveform}")
        else:
            self._write(f"DAC:{self._channum}:PRESET:AMPLITUDE {amplitude}")

    def _set_otf_amplitude_raw(self, amplitude: int) -> None:
        waveform = self.waveform()
        if (waveform == "off") or (waveform == "dc"):
            self.parent._handle_error(f"Illegal operation - waveform: {waveform}")
        else:
            self._write(f"DAC:{self._channum}:PRESET:AMPLITUDE RAW{amplitude}")

    def _get_otf_offset(self) -> float:
        response = self._ask(f"DAC:{self._channum}:PRESET:OFFSET ?")
        return float(response.split(",")[0])

    def _get_otf_offset_raw(self) -> int:
        response = self._ask(f"DAC:{self._channum}:PRESET:OFFSET ?")
        return int(response.split(",")[1], 16)

    def _set_otf_offset_raw(self, offset: int) -> None:
        self._write(f"DAC:{self._channum}:PRESET:OFFSET RAW{offset}")

    def _get_dac_config(self) -> list[str]:
        """
        Internal helper function to get the current dac configuration
        Returns:
            _get_dac_config[0] is DAC update rate in Hz
            _get_dac_config[1,2] is max length (samples,secs) for all waveforms
            _get_dac_config[3,4] is max length (samples,secs) for RAMPs
            The remaining fields are _reserved_
        """
        if self._cardnum == 0:
            cmd = "MASTER:DAC_SPECS ?"
        else:
            cmd = f"SLAVE:{self._cardnum}:DAC_SPECS ?"
        response = self._ask(cmd)
        dac_config = response.split(",")
        return dac_config

    def _get_calibration(self) -> dict[str, float]:
        """
        Internal helper function that returns a dictionary representing the
        calibration data for the channel
        """
        response = self._ask(f"DAC:{self._channum}:CALIBRATION ?")
        split = response.splitlines()
        divider_filter_cal = split[4].split(",")
        cal_data_dict = {}
        cal_data_dict["min_volt"] = float(split[1].split(",")[1])
        cal_data_dict["max_volt"] = float(split[3].split(",")[1])
        cal_data_dict["zero_code"] = int(split[2].split(",")[0], 16)
        cal_data_dict["filter_1_cutoff"] = float(divider_filter_cal[2])
        cal_data_dict["filter_2_cutoff"] = float(divider_filter_cal[3])

        cal_data_dict["divider_multiplier"] = float(divider_filter_cal[1])
        cal_data_dict["output_gain"] = float(divider_filter_cal[0])
        return cal_data_dict

    def _set_calibration(self, cal_dict: dict[str, float]) -> None:
        """
        Internal helper function that accepts a  dictionary in the same form
        as the one given by calling _get_calibration(), to alter the calibration
        data of the DAC
        """
        cal_string = ""
        cal_string += f"0,{cal_dict['min_volt']},"
        cal_string += f"{cal_dict['zero_code']},0,"
        cal_string += f"1048575,{cal_dict['max_volt']}"
        self._write(f"DAC:{self._channum}:CALIBRATION {cal_string}")

        if "output_gain" in cal_dict:
            self._write(f"DAC:{self._channum}:CALIBRATION:GAIN {cal_dict['output_gain']}")

        if "divider_multiplier" in cal_dict:
            divider_fraction = Fraction(cal_dict["divider_multiplier"]).limit_denominator(65535)
            self._write(
                f"DAC:{self._channum}:CALIBRATION:DIVIDER "
                f"{divider_fraction.numerator},{divider_fraction.denominator}"
            )

        self._update_voltage_range()
        self._update_voltage_gain()

    def _store_calibration(self) -> None:
        """
        Internal helper function that stores the calibration to NV memory
        NOTE: stores the entire card's channel calibration data to memory
        """
        self._write(f"DAC:{self._channum}:CALIBRATION STORE")

    def _get_limits(
        self, replystr: str | None = None
    ) -> tuple[float, float, float | None, float, float, float | None, str]:
        if replystr is None:
            replystr = self._ask(f"DAC:{self._channum}:LIMITS ?")
            if not self._check_fw_feature(FirmwareFeature.DacLimitsIncludesChanNum):
                # Prepend reply with the DAC number
                replystr = f"{self._channum}," + replystr
        s = replystr.split(",")

        # The first field of the reply is the channel number.
        _ASSERT(int(s[0]) == self._channum)
        s.pop(0)
        if self._check_fw_feature(FirmwareFeature.LimitRate):
            minlimit = float(s[0])
            maxlimit = float(s[2])
            ratelimit: float | None = float(s[4]) * self._samplerate
            minlimit_DIVIDED = float(s[6])
            maxlimit_DIVIDED = float(s[8])

            # Prior to v1.8, the MDAC doesn't use enough precision to report the DIVIDED limit_rate.
            # Use the RAW rate and calculate it instead.
            raw_ratelimit_DIVIDED = int(s[11], 0)
            ratelimit_DIVIDED: float | None = (
                float(raw_ratelimit_DIVIDED) * self._LSB_divided * self._samplerate
            )

            divider_mode = s[12]  # "NORMAL" or "DIVIDED"
        else:
            minlimit = float(s[0])
            maxlimit = float(s[2])
            minlimit_DIVIDED = float(s[4])
            maxlimit_DIVIDED = float(s[6])
            ratelimit = None
            ratelimit_DIVIDED = None
            divider_mode = s[8]  # "NORMAL" or "DIVIDED"

        return (
            minlimit,
            maxlimit,
            ratelimit,
            minlimit_DIVIDED,
            maxlimit_DIVIDED,
            ratelimit_DIVIDED,
            divider_mode,
        )

    def _set_limits(
        self,
        minlimit: str | float = "",
        maxlimit: str | float = "",
        ratelimit: str | float = "",
    ) -> None:
        cmd_str = f"DAC:{self._channum}:LIMITS {minlimit},{maxlimit}"
        if self._check_fw_feature(FirmwareFeature.LimitRate):
            maxdelta_str = ","
            if ratelimit != "":
                if isinstance(ratelimit, str):
                    raise RuntimeError("'ratelimit' must be an empty string or a number.")
                # Note: use the UNdivided LSB
                maxdelta = ratelimit / (self._LSB_undivided * self._samplerate)
                maxdelta = max(maxdelta, 1)
                maxdelta_str += f"RAW{round(maxdelta)}"
            cmd_str += maxdelta_str
        elif ratelimit != "":
            error_str = "ratelimit not supported in this firmware"
            self.parent._handle_error(error_str)

        self._write(cmd_str)

    def _store_limits(self) -> None:
        """
        Internal helper function that stores the HW limits to NV memory
        NOTE: stores the entire card's channel limits data to memory
        """
        self._write(f"DAC:{self._channum}:LIMITS:STORE")

    def _reset_limits(self) -> None:
        self._write(f"DAC:{self._channum}:LIMITS:RESET")

    def set_limit_max(self, limit: float, force: bool | None = False) -> None:
        """
        Use this method only if you are interested in setting
        the ``force`` kwarg. Otherwise, just use the ``limit_max`` parameter.
        """
        if (not force) and self.limit_exceeded():
            warn_str = "This channel's voltage limit is being exceeded - set_limit_max() operation is blocked"
            self.parent._handle_warning(warn_str)
        else:
            self._set_limits(maxlimit=limit)
            # This (public) function is linked to a qcodes set parameter value operation,
            # ensure qcodes is aware of the change in value.
            # This is only necessary if set_limit_max() has been called explicitly (rather than via
            # a set operation on self.limit_max(V)), but I don't know how to test for that.
            self.limit_max.cache.set(limit)

    def set_limit_min(self, limit: float, force: bool | None = False) -> None:
        """
        Use this method only if you are interested in setting
        the ``force`` kwarg. Otherwise, just use the ``limit_min`` parameter.
        """
        if (not force) and self.limit_exceeded():
            warn_str = "This channel's voltage limit is being exceeded - set_limit_min() operation is blocked"
            self.parent._handle_warning(warn_str)
        else:
            self._set_limits(minlimit=limit)
            # This (public) function is linked to a qcodes set parameter value operation,
            # ensure qcodes is aware of the change in value.
            # This is only necessary if set_limit_min() has been called explicitly (rather than via
            # a set operation on self.limit_min(V)), but I don't know how to test for that.
            self.limit_min.cache.set(limit)

    def _set_limit_rate(self, limit: float) -> None:
        self._set_limits(ratelimit=limit)

    def _get_limit_max(
        self,
        current_limits: tuple[float, float, float | None, float, float, float | None, str]
        | None = None,
    ) -> float:
        if current_limits is None:
            current_limits = self._get_limits()
        if current_limits[6] == "NORMAL":
            maxlimit = current_limits[1]
        else:
            maxlimit = current_limits[4]
        return maxlimit

    def _get_limit_min(
        self,
        current_limits: tuple[float, float, float | None, float, float, float | None, str]
        | None = None,
    ) -> float:
        if current_limits is None:
            current_limits = self._get_limits()
        if current_limits[6] == "NORMAL":
            minlimit = current_limits[0]
        else:
            minlimit = current_limits[3]
        return minlimit

    def _get_limit_rate(
        self,
        current_limits: tuple[float, float, float | None, float, float, float | None, str]
        | None = None,
    ) -> float | None:
        if current_limits is None:
            current_limits = self._get_limits()
        if current_limits[6] == "NORMAL":
            ratelimit = current_limits[2]
        else:
            ratelimit = current_limits[5]
        return ratelimit

    def _get_amplitude(self, preset: PresetType | None = None) -> float:
        if preset is None:
            preset = self._get_preset()
        ret: float
        if (preset[0] == "off") or (preset[0] == "none"):
            ret = 0
        elif preset[0] == "ramp":
            ret = 0
        elif preset[0] == "arb":
            ret = self._get_otf_amplitude()
        else:
            ret = float(preset[5])
        return ret

    def _get_amplitude_raw(self, preset: PresetType | None = None) -> int:
        if preset is None:
            preset = self._get_preset()
        if (preset[0] == "off") or (preset[0] == "none"):
            ret = 0
        elif preset[0] == "ramp":
            ret = 0
        elif preset[0] == "arb":
            ret = self._get_otf_amplitude_raw()
        else:
            ret = int(preset[6], 16)
        return ret

    def _get_offset(self, preset: PresetType | None = None) -> float:
        ret: float
        if preset is None:
            preset = self._get_preset()
        if preset[0] == "ramp":
            ret = 0
        elif preset[0] == "arb":
            ret = self._get_otf_offset()
        else:
            ret = float(preset[3])
        return ret

    def _get_offset_raw(self, preset: PresetType | None = None) -> int:
        if preset is None:
            preset = self._get_preset()
        if (preset[0] == "off") or (preset[0] == "none"):
            ret = self._get_dac_raw()
        elif preset[0] == "ramp":
            ret = 0
        elif preset[0] == "arb":
            ret = self._get_otf_offset_raw()
        else:
            ret = int(preset[4], 16)
        return ret

    def _get_period(self, preset: PresetType | None = None) -> float:
        if preset is None:
            preset = self._get_preset()
        ret: float
        if (preset[0] == "off") or (preset[0] == "none"):
            ret = 0.0
        else:
            ret = float(preset[1])
        return ret

    def _get_period_raw(self, preset: PresetType | None = None) -> int:
        if preset is None:
            preset = self._get_preset()
        if (preset[0] == "off") or (preset[0] == "none"):
            ret = 0
        else:
            ret = int(preset[2])
        return ret

    def _get_frequency(self, preset: PresetType | None = None) -> float:
        period_raw = self._get_period_raw(preset)
        if period_raw == 0:
            ret = 0.0
        else:
            ret = self._samplerate / period_raw
        return ret

    def _set_phase_offset(self, phase: float) -> None:
        period = self._get_period()
        delay = period * (phase / 360)
        self._write(f"DAC:{self._channum}:PRESET:PHASE {delay}")

    def _get_phase_delay(self, preset: PresetType | None = None) -> float:
        if preset is None:
            preset = self._get_preset()
        phase_delay: float
        if (preset[0] == "off") or (preset[0] == "none"):
            phase_delay = 0
        elif preset[0] == "arb":
            phase_delay = float(preset[4])
        else:
            phase_delay = float(preset[8])
        return phase_delay

    def _get_phase_delay_raw(self, preset: PresetType | None = None) -> int:
        if preset is None:
            preset = self._get_preset()
        if (preset[0] == "off") or (preset[0] == "none"):
            phase_delay_raw = 0
        elif preset[0] == "arb":
            phase_delay_raw = int(preset[5])
        else:
            phase_delay_raw = int(preset[9])
        return phase_delay_raw

    def _get_phase_offset(self, preset: PresetType | None = None) -> float:
        period = self._get_period(preset)
        ret: float
        if period == 0:
            ret = 0
        else:
            phase_delay = self._get_phase_delay()
            phase = 360 * (phase_delay / period)
            ret = round(phase, 2)
        return ret

    def _get_group_size(self, preset: PresetType | None = None) -> int:
        if preset is None:
            preset = self._get_preset()
        if (len(preset) > 11) and (preset[11] is not None):
            group_size = int(preset[11])
        else:
            group_size = 1
        return group_size

    def _update_voltage_range(self, cal: dict[str, float] | None = None) -> None:
        if cal is None:
            cal = self._get_calibration()
        self._minimum_voltage = cal["min_volt"]
        # this cast could go if cal is a typed dict
        self._zero_volts_dac = cast(int, cal["zero_code"])
        self._maximum_voltage = cal["max_volt"]
        self._divider_multiplier = cal["divider_multiplier"]
        self._LSB_undivided = (self._maximum_voltage - self._minimum_voltage) / (2**20 - 1)
        self._LSB_divided = self._LSB_undivided * self._divider_multiplier

    def _update_voltage_gain(self) -> None:
        maximum_voltage = self._maximum_voltage
        minimum_voltage = self._minimum_voltage

        if self._get_divider() == "1":
            self._gain = self._divider_multiplier
            self._LSB = self._LSB_divided
        else:
            self._gain = 1
            self._LSB = self._LSB_undivided

        # calculate new min / max voltage taking into account the DAC resolution
        maximum_voltage = round(maximum_voltage, 6)
        minimum_voltage = round(minimum_voltage, 6)
        maximum_voltage *= self._gain
        minimum_voltage *= self._gain

        v = vals.Numbers(minimum_voltage, maximum_voltage)
        self.voltage.vals = v
        self.offset.vals = v

        v = vals.Numbers(0, maximum_voltage - minimum_voltage)
        self.amplitude.vals = v

    def _get_divider(self) -> str:
        return "0"

    def _get_dac_state(self) -> DacParseType:
        state = self.parent._get_dacs_state([self])[0]
        return state

    def _check_fw_feature(self, feature: FirmwareFeature, test_only: bool = True) -> bool:
        parent = cast(MDAC, self.parent)
        return parent._check_fw_feature(feature, test_only)


class MDACChannel(MDACBasicChannel):
    """Channel Class for MDAC "general puropose" DACs (ie: other than ch_isense)"""

    def __init__(self, parent: "MDAC", name: str, channum: int, cardnum: int) -> None:
        """
        Args:
            parent (Instrument): The instrument the channel is a part of.
            name (str): the name of the channel
            channum (int): MDAC channel number ([1-64] for populated MDAC)
        """
        super().__init__(parent, name, channum, cardnum)

        self.divider = Parameter(
            "divider",
            instrument=self,
            label=f"Channel {self._channum} divider",
            set_cmd=self._set_divider,
            get_cmd=self._get_divider,
            vals=vals.OnOff(),
            val_mapping={"on": 1, "off": 0},
            docstring="dac output voltage divider\n"
            "(currently voltage ranges not updated to new range)",
        )

        self.filter = Parameter(
            "filter",
            instrument=self,
            label=f"Channel {self._channum} filter",
            set_cmd=f"DAC:{self._channum}:FILTER {{}}",
            get_cmd=f"DAC:{self._channum}:FILTER ?",
            get_parser=int,
            vals=ParameterParsers._FILTERS,
            docstring="dac output filter\n1 -> (~ 1000 KHz LPF)\n2 -> (~ 10 Hz LPF)",
        )

        self.microd = Parameter(
            "microd",
            instrument=self,
            label=f"Channel {self._channum} Micro-D relay",
            set_cmd=f"DAC:{self._channum}:RELAYS:MAIN {{}}",
            get_cmd=f"DAC:{self._channum}:RELAYS:MAIN ?",
            val_mapping={"open": 0, "close": 1},
            docstring="micro-D output relay",
        )

        self.dac_output = Parameter(
            "dac_output",
            instrument=self,
            label=f"Channel {self._channum} output relay",
            set_cmd=f"DAC:{self._channum}:RELAYS:DAC_OUTPUT {{}}",
            get_cmd=f"DAC:{self._channum}:RELAYS:DAC_OUTPUT ?",
            val_mapping={"open": 0, "close": 1},
            docstring="dac output relay onto signal routing common point",
        )

        if self.parent._mdac_variant == MdacVariant.Mdac24:
            self.sma = Parameter(
                "sma",
                instrument=self,
                label=f"Channel {self._channum} SMA relay",
                set_cmd=f"DAC:{self._channum}:RELAYS:SMA {{}}",
                get_cmd=f"DAC:{self._channum}:RELAYS:SMA ?",
                val_mapping={"open": 0, "close": 1},
                docstring="SMA output relay",
            )
            self.smc: Parameter = DelegateParameter(
                "smc",
                source=self.sma,
                label=f"Script compatibility alias for Channel {self._channum} SMA relay",
                docstring="Script compatibility alias for SMA output relay",
                bind_to_instrument=True,
                instrument=self,
            )
            self._front_panel = self.sma
        else:
            self.smc = Parameter(
                "smc",
                instrument=self,
                label=f"Channel {self._channum} SMC relay",
                set_cmd=f"DAC:{self._channum}:RELAYS:SMC {{}}",
                get_cmd=f"DAC:{self._channum}:RELAYS:SMC ?",
                val_mapping={"open": 0, "close": 1},
                docstring="SMC output relay",
            )
            self._front_panel = self.smc

        self.gnd = Parameter(
            "gnd",
            instrument=self,
            label=f"Channel {self._channum} GND relay",
            set_cmd=f"DAC:{self._channum}:RELAYS:TERMINATE {{}}",
            get_cmd=f"DAC:{self._channum}:RELAYS:TERMINATE ?",
            val_mapping={"open": 0, "close": 1},
            docstring="internal grounding relay",
        )

    def _write(self, *args: Any, **kwargs: Any):
        return self.parent._write(*args, **kwargs)

    def _ask(self, *args: Any, **kwargs: Any):
        return self.parent._ask(*args, **kwargs)

    # We don't need a def snapshot_base() here as the parent class (ie: MDACBasicChannel) handles the
    # extra parameters that this class introduces.

    def _set_divider(self, cmd: str) -> None:
        assert isinstance(self.parent, MDAC)
        self._write(f"DAC:{self._channum}:RELAYS:DIVIDER {cmd}")
        _last_mdac_status = self.parent._last_mdac_status
        try:
            self._update_voltage_gain()
        finally:
            self.parent._last_mdac_status = _last_mdac_status

    def _get_divider(self, dac_status: str | None = None) -> str:
        if dac_status is None:
            response = self._ask(f"DAC:{self._channum}:RELAYS:DIVIDER ?")
        else:
            relays_line = dac_status.splitlines()[2]
            relays_items = relays_line.split(",")
            divider_index = 6 + self.parent._num_buses
            response = relays_items[divider_index]
        return response


class Trigger(InstrumentChannel):
    """Dedicated trigger channel class for MDAC"""

    def __init__(self, parent: "MDAC", name: str, trignum: int) -> None:
        """
        Args:
            parent (Instrument): The instrument the channel is a part of.
            name (str): the name of the trigger
            trignum (int): MDAC trigger number ([0] for v1.5 MDAC firmware)
        """
        super().__init__(parent, name)

        self._trignum = trignum
        self._autosync = __MDAC_default_autosync_state__
        self._samplerate: float = self.parent._samplerate
        response = self._ask(f"TRIGGER:{self._trignum}:CONFIG ?")
        self._pw_1raw: float
        if ("PWM" in response) or ("GPIO" in response):
            r = response.split(",")
            # r[0]/r[1] are the trigger PULSE/IDLE polarities
            # r[2] is the Trigger timebase (GPIO or PWM)
            # r[3] is the length of RAW1 when specifying the pulse-width
            self._pw_1raw = float(r[3])
        else:
            self._pw_1raw = 1 / self._samplerate

        self.autosync = Parameter(
            "autosync",
            instrument=self,
            label=f"Trigger {self._trignum} waveform auto resync",
            get_cmd=self._get_autosync,
            set_cmd=self._set_autosync,
            vals=vals.Bool(),
            docstring="True, issues a resync command after a trigger update\n"
            "False, a resync must be issued using MDAC.sync()",
        )

        self.frequency = Parameter(
            "frequency",
            instrument=self,
            label=f"Trigger {self._trignum} frequency",
            unit="Hz",
            get_cmd=self._get_frequency,
            set_cmd=None,
            docstring="trigger frequency",
        )

        self.period = Parameter(
            "period",
            instrument=self,
            label=f"Trigger {self._trignum} period",
            unit="s",
            get_cmd=self._get_period,
            set_cmd=None,
            docstring="trigger period",
        )

        self.phase = Parameter(
            "phase",
            instrument=self,
            label=f"Trigger {self._trignum} phase offset",
            unit="Deg",
            get_cmd=self._get_phase_offset,
            set_cmd=None,
            vals=vals.Numbers(-180, +180),
            docstring="trigger phase offset",
        )

        self.direction = Parameter(
            "direction",
            instrument=self,
            label=f"Trigger {self._trignum} direction ",
            get_cmd=self._get_direction,
            set_cmd=self._set_direction,
            vals=ParameterParsers._TRIGGER_DIRECTION_VALIDATION,
            docstring="trigger leading edge direction",
        )

        if self.parent._check_fw_feature(FirmwareFeature.ConfigurableTriggerOutLevels):
            self.level = Parameter(
                "level",
                instrument=self,
                label=f"Trigger {self._trignum} level control",
                set_cmd=f"TRIGGER:{self._trignum}:LEVEL {{}}",
                get_cmd=self._get_level,
                get_parser=ParameterParsers._lowercase_parser,
                set_parser=ParameterParsers._uppercase_parser,
                vals=ParameterParsers._TRIGGER_LEVEL_VALIDATION,
                docstring="trigger output level",
            )

    def _write(self, *args: Any, **kwargs: Any):
        return self.parent._write(*args, **kwargs)

    def _ask(self, *args: Any, **kwargs: Any) -> str:
        return self.parent._ask(*args, **kwargs)

    def snapshot_base(
        self,
        update: bool | SnapshotUpdate | None = False,
        params_to_skip_update: Sequence[str] | None = None,
    ) -> dict[Any, Any]:
        if self.parent._check_fw_feature(FirmwareFeature.SnapshotSupported):
            if (
                normalize_snapshot_update(update) != "Never"
                and not self.parent._update_cache_performed
            ):
                self.parent._update_cache()
            # The call to MDAC._update_cache() above will have updated some supported
            # parameters already .. so they don't need to be updated again.
            # Note: See the note in MDACBasicChannel() above about whether we could still
            # exclude these parameters in the case where params_to_skip_update is not None.
            if params_to_skip_update is None:
                if self.parent._check_fw_feature(FirmwareFeature.TriggersStatus):
                    params_to_skip_update = [
                        "direction",
                        "frequency",
                        "period",
                        "phase",
                    ]
                    if self.parent._check_fw_feature(FirmwareFeature.ConfigurableTriggerOutLevels):
                        params_to_skip_update.extend(["level"])
        snapshot = super().snapshot_base(update=update, params_to_skip_update=params_to_skip_update)
        return snapshot

    def _get_trigger(
        self, response: str | None = None
    ) -> tuple[str, float, int, float, int, int, float, int]:
        if response is None:
            response = self._ask(f"TRIGGER:{self._trignum}:SET ?")
        trigger = response.split(",")
        # trigger[0] is ACTIVE/INACTIVE
        # trigger[1,2] is the HI/LO pulse, idle state
        # trigger[3,4] is the period in seconds, ticks
        # trigger[5,6] is the pulse width in seconds, "raw units"
        # trigger[7] is the number of shots
        # trigger[8,9] is the phase offset in seconds, ticks

        period: float
        pulse_width: float
        phase_offset: float
        if response.startswith("INACTIVE"):
            active = "OFF"
            period = 0
            period_raw = 0
            pulse_width = 0
            pulse_width_raw = 0
            num_triggers = 0
            phase_offset = 0
            phase_offset_raw = 0

        elif response.startswith("ACTIVE"):
            active = "ON"
            period = float(trigger[3])
            period_raw = int(trigger[4])
            pulse_width = float(trigger[5])
            pulse_width_raw = int(trigger[6])
            phase_offset = float(trigger[8])
            phase_offset_raw = int(trigger[9])
            if trigger[7] == "INF":
                num_triggers = -1
            else:
                num_triggers = int(trigger[7])
        else:
            raise RuntimeError(
                "Got invalid trigger mode. Supported modes are 'INACTIVE' and 'ACTIVE'."
            )

        return (
            active,
            period,
            period_raw,
            pulse_width,
            pulse_width_raw,
            num_triggers,
            phase_offset,
            phase_offset_raw,
        )

    def _get_frequency(
        self,
        trigger: tuple[str, float, int, float, int, int, float, int] | None = None,
    ) -> float:
        if trigger is None:
            trigger = self._get_trigger()
        freq: float
        if (trigger[0] == "OFF") or (trigger[1] == 0):
            freq = 0
        else:
            freq = 1 / trigger[1]
        return freq

    def _get_period(
        self,
        trigger: tuple[str, float, int, float, int, int, float, int] | None = None,
    ) -> float:
        if trigger is None:
            trigger = self._get_trigger()
        ret: float
        if trigger[0] == "OFF":
            ret = 0
        else:
            ret = trigger[1]
        return ret

    def _get_period_raw(
        self,
        trigger: tuple[str, float, int, float, int, int, float, int] | None = None,
    ) -> int:
        if trigger is None:
            trigger = self._get_trigger()
        if trigger[0] == "OFF":
            ret = 0
        else:
            ret = trigger[2]
        return ret

    def _get_phase_offset(
        self,
        trigger: tuple[str, float, int, float, int, int, float, int] | None = None,
    ) -> float:
        if trigger is None:
            trigger = self._get_trigger()
        phase_offset: float
        if (trigger[0] == "OFF") or (trigger[1] == 0):
            phase_offset = 0
        else:
            phase_offset = 360 * trigger[6] / trigger[1]
        return phase_offset

    def _set_autosync(self, autosync: bool) -> None:
        self._autosync = autosync

    def _get_autosync(self) -> bool:
        return self._autosync

    def _set_trigger(
        self,
        state: str,
        period: float | None = None,
        period_raw: int | None = None,
        pulse_width: float | None = None,
        pulse_width_raw: int | None = None,
        phase: float | None = None,
        phase_raw: int | None = None,
    ) -> None:
        if state == "OFF":
            trig_cmd = f"TRIGGER:{self._trignum}:SET OFF"

        elif state == "ON":
            # Trigger period
            if period is not None:
                # Period has been given in seconds.
                period_str = f"{period}"
            elif period_raw is not None:
                # Period has been given in samples.
                period_str = f"RAW{period_raw}"
            else:
                raise RuntimeError("Neither period nor period_raw supplied")
            # Trigger pulse width
            if pulse_width is not None:
                # Pulse width has been given in seconds.
                pw_str = f",{pulse_width}"
            elif pulse_width_raw is not None:
                # Pulse width has been given in "raw units".
                pw_str = f",RAW{pulse_width_raw}"
            else:
                raise RuntimeError("Neither 'pulse_width' nor 'pulse_width_raw' supplied")
            # Trigger phase offset
            if phase is not None:
                # Phase offset has been given in seconds.
                phase_offs_str = f",{phase}"
            elif phase_raw is not None:
                # Phase offset has been given in samples.
                phase_offs_str = f",RAW{phase_raw}"
            else:
                phase_offs_str = ""

            trig_cmd = f"TRIGGER:{self._trignum}:SET "
            trig_cmd += period_str + pw_str + phase_offs_str
        else:
            raise RuntimeError(f"Trigger mode must be either 'ON' or 'OFF', not '{state}'")

        self._write(trig_cmd)
        if self._autosync:
            self.parent.sync()

    def _set_direction(self, direction: str) -> None:
        trig_cmd: str | None
        if direction == "up":
            trig_cmd = f"TRIGGER:{self._trignum}:CONFIG UP"
        elif direction == "down":
            trig_cmd = f"TRIGGER:{self._trignum}:CONFIG DOWN"
        else:
            self.parent._handle_error("Parameter direction must be 'up' or 'down'")
            trig_cmd = None

        if trig_cmd is not None:
            self._write(trig_cmd)

    def _get_direction(self, response: str | None = None) -> str | None:
        if response is None:
            response = self._ask(f"TRIGGER:{self._trignum}:CONFIG ?")
        r = response.split(",")
        ret: str | None
        if (r[0] == "1") and (r[1] == "0"):
            ret = "up"
        elif (r[0] == "0") and (r[1] == "1"):
            ret = "down"
        elif (r[0] == "0") and (r[1] == "0"):
            warn_str = f"Trigger {self._trignum} output is always LOW"
            self.parent._handle_warning(warn_str)
            ret = None
        elif (r[0] == "1") and (r[1] == "1"):
            warn_str = f"Trigger {self._trignum} output is always HIGH"
            self.parent._handle_warning(warn_str)
            ret = None
        else:
            self.parent._handle_error("Invalid response received from MDAC")
            ret = None

        return ret

    def _get_level(self, response: str | None = None) -> str | None:
        if response is None:
            response = self._ask(f"TRIGGER:{self._trignum}:LEVEL ?")
        ret = response.lower()
        return ret

    def trigger_number(self) -> int:
        """TRIG number corresponding to SMC trigger numbers"""
        return self._trignum

    def start(
        self,
        frequency: float | None = None,
        period: float | None = None,
        phase: float | None = None,
        period_raw: int | None = None,
        pulse_width_override: float | None = None,
        pulse_width_raw_override: int | None = None,
    ) -> None:
        """
        Enables the trigger generation.

        Args:
            frequency: [Hz]
            period: [Sec]
            phase: [Deg]
            period_raw: (available when period is None) - [Sample]
            pulse_width_override: [Sec]
            pulse_width_raw_override: [Sample]
        """
        # Trigger period.
        if frequency is not None:
            if (period is not None) or (period_raw is not None):
                raise RuntimeError("Do NOT supply both frequency and period values.")
            period = 1 / frequency
            period_raw = None
        elif period is not None:
            if period_raw is not None:
                raise RuntimeError("Do NOT supply values for both 'period' and 'period_raw'.")
            period_raw = None
        elif period_raw is None:
            raise RuntimeError("You must supply either a frequency or period value.")
        # Trigger pulse width
        pulse_width: float | None
        pulse_width_raw: int | None
        if pulse_width_override is not None:
            if pulse_width_raw_override is not None:
                raise RuntimeError(
                    "Do NOT supply values for both 'pulse_width_override'"
                    " and 'pulse_width_raw_override'."
                )
            pulse_width = pulse_width_override
            pulse_width_raw = None
        elif pulse_width_raw_override is not None:
            pulse_width = None
            pulse_width_raw = pulse_width_raw_override
        else:
            pulse_width = 1 / (self._samplerate / 2)
            pulse_width_raw = None
        # Phase
        phase_offset: float | None
        phase_offset_raw: float | None
        if phase is None:
            phase_offset = 0
            phase_offset_raw = None
        elif period is not None:
            phase_offset = period * phase / 360
            phase_offset_raw = None
        elif period_raw is not None:
            phase_offset = None
            phase_offset_raw = int(period_raw * phase / 360)
        else:
            # This should never happen given the logic above for
            # trigger period .. but we'll leave it here for consistency.
            raise RuntimeError(
                "Neither 'period' nor 'period_raw' is known.Cannot calculate phase offset."
            )
        self._set_trigger(
            "ON",
            period,
            period_raw,
            pulse_width,
            pulse_width_raw,
            phase_offset,
            phase_offset_raw,
        )

    def stop(self) -> None:
        """Disables trigger generation"""
        self._set_trigger("OFF")


# Fletcher-16 checksum
def calculate_checksum(source: str, calc_checkbyte: bool = False) -> int:
    checksum: int
    modulus: int = 0xFF
    c0: int = 0
    c1: int = 0

    for c in source:
        c0 += ord(c)
        c0 %= modulus
        c1 += c0
        c1 %= modulus

    if calc_checkbyte:
        cb0: int = modulus - ((c0 + c1) % modulus)
        cb1: int = modulus - ((c0 + cb0) % modulus)
        checksum = (cb0 << 8) + cb1
    else:
        checksum = (c0 << 8) + c1

    return checksum


DEFAULT_SEND_CMD_TIMEOUT_SECS = 5
DEFAULT_READ_TIMEOUT_SECS = 2.5
DEFAULT_MAX_ARB_DATA_READ_LEN = 300


class serial_MDAC:
    """MDAC communications class for serial connections"""

    def __init__(
        self,
        parent: "MDAC",
        port: str,
        baudrate: int = 460800,
        rtscts: bool = True,
        bytesize: int = serial.EIGHTBITS,
        parity: str = serial.PARITY_NONE,
        stopbits: float = serial.STOPBITS_ONE,
    ) -> None:
        self.parent = parent
        try:
            self._serial = serial.Serial(
                port,
                baudrate=baudrate,
                bytesize=bytesize,
                parity=parity,
                stopbits=stopbits,
                rtscts=rtscts,
            )
        except Exception as e:
            error_str = f"ERROR: Unable to init commport {port}\n"
            error_str = error_str + "Available ports: "
            comma_str = ""
            available_ports = serial.tools.list_ports.comports()
            for available_port in available_ports:
                error_str = error_str + comma_str + available_port.name
                comma_str = ", "
            raise RuntimeError(error_str) from e

        self._serial.timeout = 1
        self._serial.write_timeout = 0.5
        self.write_termination = ProtocolChars.CR
        self.read_termination = ProtocolChars.CRLF

    @property
    def is_open(self) -> bool:
        return self._serial.is_open

    @property
    def port(self) -> str | None:
        return self._serial.port

    def reset_input_buffer(self) -> None:
        self._serial.reset_input_buffer()

    def reset_output_buffer(self) -> None:
        self._serial.reset_output_buffer()

    def open(self) -> None:
        try:
            self._serial.open()
        except Exception as e:
            raise RuntimeError(
                f"ERROR: Unable to connect to port {self.port}: {sys.exc_info()[0]}"
            ) from e
        else:
            self.parent.log.info(f"Connected to port {self.port}")

    def close(self) -> None:
        try:
            close_attempted = False
            if self.is_open:
                close_attempted = True
                self._serial.close()
        except Exception as e:
            raise RuntimeError(
                f"ERROR: Unable to disconnect from port {self.port}: {sys.exc_info()[0]}"
            ) from e
        else:
            if close_attempted:
                self.parent.log.info(f"Disconnected from port {self.port}")

    def _close_open(self) -> None:
        self.close()
        time.sleep(1)
        self.open()

    def read(
        self,
        num_chars: int = 0,
        timeout_Secs: float = DEFAULT_READ_TIMEOUT_SECS,
        failure_is_fatal: bool = False,
    ) -> str:
        """
        Reads from the connected instrument until either:
        1) if (num_chars > 0): the requested num_chars is reached
        2) if (num_chars <= 0): the read terminator is encountered
        3) the timeout expires
        """
        if not self._serial.readable():
            self.parent.log.debug("mdac_read() - not readable, sleep(1))")
            time.sleep(1)

        start = time.time()
        done_reading = False

        read_data = b""
        cmd_timeout = False
        while not done_reading and not cmd_timeout:
            read_char = None
            try:
                read_char = self._serial.read(1)
            except (OSError, ValueError):
                self.parent.log.info(f"Unexpected error: {sys.exc_info()[0]}")
                if has_win32api:
                    self.parent.log.warning(
                        f"win32api.GetLastError(): {win32api.GetLastError()} - "
                        f"{win32api.FormatMessage(win32api.GetLastError())}"
                    )

            if read_char is not None:
                read_data += read_char
                if num_chars <= 0:
                    done_reading = bytes(self.read_termination, "utf-8") in read_data
                else:
                    done_reading = len(read_data) >= num_chars

            if (time.time() - start) > timeout_Secs:
                cmd_timeout = True
                if failure_is_fatal:
                    error_str = (
                        f"mdac_read(): timeout ({timeout_Secs} Secs) reached.\n"
                        f'Received so far: "{read_data!r}"'
                    )
                    self.parent.log.debug(error_str)
                    print(error_str)
                    raise ReadTimeoutError

        try:
            data = str(read_data, "utf-8").replace(self.read_termination, "")
        except UnicodeDecodeError:
            data = ""

        self.parent.log.debug("read:\r" + data)

        if (not done_reading) and failure_is_fatal:
            raise RuntimeError(f'ERROR: serial_MDAC.read FAILED.\nReceived so far: "{data}"')

        return data

    def write(self, cmd: str, include_terminator: bool = True) -> bool:
        """
        Writes to the connected instrument and completes with the terminator.
        """
        self.reset_output_buffer()

        if not self._serial.writable():
            self.parent.log.debug("mdac_write() - not writable, sleep(1))")
            time.sleep(1)

        try:
            write_cmd = cmd
            if include_terminator:
                write_cmd += self.write_termination
            num_bytes = self._serial.write(write_cmd.encode("utf-8"))
            success = num_bytes == len(write_cmd.encode("utf-8"))

        except (OSError, ValueError):
            success = False

        if not success:
            self.reset_output_buffer()

        return success


class zmq_MDAC:
    """MDAC communications class for ZMQ Mirrored Connections"""

    def __init__(
        self,
        parent: "MDAC",
        address: "str",
    ) -> None:
        self.address = address
        self.context = zmq.Context()
        self.client = self.context.socket(zmq.REQ)
        self.client.connect(self.address)
        self.is_open = True
        self.write_termination = ProtocolChars.CR
        self.read_termination = ProtocolChars.CRLF

    def open(self) -> None:
        if self.client is None:
            self.client = self.context.socket(zmq.REQ)
            self.client.connect(self.address)
            self.is_open = True

    def close(self) -> None:
        if self.client is not None:
            self.client.close()
            self.client = None
            self.is_open = False

    def _close_open(self) -> None:
        self.close()
        time.sleep(1)
        self.open()

    def read(
        self,
        num_chars: int = 0,
        timeout_Secs: float = DEFAULT_READ_TIMEOUT_SECS,
        failure_is_fatal: bool = False,
    ) -> str:
        """
        Reads from the connected instrument
        """
        if self.client is None:
            self.open()
            self.client = cast(zmq.Socket, self.client)

        try:
            res = self.client.recv().decode("utf-8")
            return res
        except zmq.ZMQError as e:
            if failure_is_fatal:
                raise e
            return ""

    def write(self, cmd: str, include_terminator: bool = True) -> bool:
        """
        Writes to the connected instrument and completes with the terminator.
        """
        if self.client is None:
            self.open()
            self.client = cast(zmq.Socket, self.client)

        self.client.send(f"{cmd}{self.write_termination if include_terminator else ''}".encode())
        return True


class MDAC(Instrument):
    """
    Driver for V1.x MDAC - a low noise precision voltage source
    with integrated signal routing and waveform functionality

    HW NOTES:
    don't exceed 500mA of current through any relay
    only toggle relays at zero voltage
    don't source or sink more that 20mA of current from any output
    don't connect two or more DAC outputs together

    For 64 channel MDACs:
    in QT configuration, micro-D pin 13 is not connected but pin 25 is.
    DAC channel / SMC label 13 maps to micro-D #1 pin 25
    similarly, DAC channel / SMC label 45 maps to micro-D #2 pin 25

    For 24 channel MDACs:
    The channel numbers directly correlate to pin numbers on the micro-D
    """

    CHANNEL_CLASS = MDACChannel

    # An enforceable guard time between when we rx something and our next tx (in seconds).
    DEFAULT_ease_comms_congestion_delay: float | None = None

    def __init__(
        self,
        name: str,
        address: str,
        baudrate: int = 460800,
        debug: bool = False,
        hardware_limits: bool | None = None,
        log_level: int | str | None = None,
        use_checksums_if_supported: bool = True,
        full_initialisation: bool = True,
        timeout: int = 15,
        limit_channels: Sequence[int] = (),
        **kwargs: Any,
    ) -> None:
        """
        Instantiates the MDAC.

        Note: if you happen to experience read I/O timeout errors, make sure to update to
        the latest firmware version. If the issue persist, please let the MDAC developers
        know. Additionally, you can try to set the class attribute called
        ``DEFAULT_ease_comms_congestion_delay`` to a non-None value (in seconds) that
        will be used to enforce a guard time between when we "rx something" from the MDAC and
        our next "tx", and after that try to instantiate MDAC again. A typical value to
        alleviate read I/O timeout errors is 0.005 (ie: 5 mSecs).

        Args:
            name: the instrument name for qcodes.
            address: the resource address. ``COMx`` or ``ASRLx`` for serial connections.
                For MDACs that support ethernet connection, the IP address is supplied
                as ``TCP::ip_address`` or ``ip_address::SOCKET``.
                For ZMQ mirrors, use the format ZMQ::address
            baudrate: The baudrate to use when communicating with the instrument (by serial connection).
            debug: If True, additional diagnostic output is printed.
            hardware_limits: If True, the MDAC activates its built-in h/w protection mode.
            log_level: If given, the log_level used with the logging module.
            use_checksums_if_supported: whether or not to wrap messages in checksums if the MDAC supports it.
            full_initialisation: whether or not to perform a full initialisation of the MDAC object.
                A meaningful qcodes object requires that full_initialisation be True. When set to to False,
                there's very little you can do with the MDAC beyond "chat with it"
                (eg: ``DaisyChain.find_IP_addresses()`` makes use of it to create a temporary MDAC object
                that it can message with).
            timeout: The default amount of time to wait for the MDAC to reply to a command.
            limit_channels: A sequence of channel numbers access to a limited number of channels. If empty
                all channels are accessible. Note: Channels are 1-indexed
            kwargs: Additional keyword args are passed to the base class.
        """
        self.__version__: Version = __MDAC_driver_version__
        self._min_supported_fw: Version = __MDAC_driver_min_supported_fw__
        self._max_supported_fw: Version = __MDAC_driver_max_supported_fw__
        self._master_hw_ver: int | None = None
        self._master_fw_ver: tuple[Version, datetime.datetime] | None = None
        self._mdac_fw_ver: Version | None = None

        self._last_mdac_cmd: str | None = None
        self._last_mdac_reply: str | None = None
        self._last_mdac_status: str | None = None

        self._debug: bool = debug
        self._errors_are_fatal: bool = not debug
        self._hardware_limits: bool | None = hardware_limits
        self._write_sequence_num: int = 0
        self._expect_sequence_num: bool = False
        self._num_buses: int = 1
        self._dac_chans_per_card: int = 8
        self._dac_map_dict: dict[str, int] | None = None
        self._chan13_skipped: bool = False
        self._ignore_response_errors: bool = False
        # Make these properties of the MDAC class so that they can be altered during debugging.
        self._send_last_rxd_cmd: str = "."
        self._repeat_last_txd_reply: str = ">"

        self._use_checksums_if_supported: bool = use_checksums_if_supported
        self._use_checksums: bool = False
        self._max_discover_which_cmd_to_retry_attempts: int = 0
        self._max_write_attempts: int = 0

        self._mdac_comms_id: int | None = None
        self._daisychain_state: DaisyChainState | None = None
        self._daisychain: DaisyChain | None = None

        self._average_temperature: float | None = None
        self._peak_temperature: float | None = None

        # These variables exist to facilitate testing - in general usage, they should not be altered.
        self._sanity_check_ramps = True
        self._max_arb_data_read_len = DEFAULT_MAX_ARB_DATA_READ_LEN

        super().__init__(name, **kwargs)
        self._update_cache_performed = False
        self._freeze_update_cache_performed = False

        # Give the logger a name that is both unique for the module and the
        # instance. This can be removed once we depend on a version of qcodes
        # that incorporates https://github.com/QCoDeS/Qcodes/pull/1914
        logger_name = f"{type(self).__module__}.{type(self).__name__}-{name}"
        self.log = get_instrument_logger(self, logger_name)
        if log_level is not None:
            self.log.setLevel(log_level)
        elif self._debug:
            # Make sure DEBUG level messages get to the logfile - despite what the qcodesrc.json might specify.
            # Set the log level for both the logfile handler and the overall logger to DEBUG.
            handler = qcodes.logger.get_file_handler()
            if handler is not None:
                handler.setLevel(logging.DEBUG)
            # Also set the overall log level to DEBUG.
            self.log.setLevel(logging.DEBUG)
        elif "mdac" in qcodes.config.logger.logger_levels:
            self.log.setLevel(qcodes.config.logger.logger_levels.get("mdac"))
        else:
            self.log.setLevel(logging.INFO)

        self.log.info(f"MDAC driver v{self.__version__}")

        self._address = address
        self._mdac: serial_MDAC | zmq_MDAC
        if address.startswith("ASRL") or address.startswith("COM"):
            if address.startswith("ASRL"):
                serial_address = "COM" + address[4:]
                serial_address = serial_address[: serial_address.find("::")]
            else:
                serial_address = address
            self.log.info(f"Attempting serial connection to {serial_address}")
            self._mdac = serial_MDAC(self, serial_address, baudrate)
            self.cmd_interface = "SERIAL"
        elif address.startswith("ZMQ::"):
            address = address[5:]
            self._mdac = zmq_MDAC(self, address)
            self.cmd_interface = "ZMQ"
        else:
            raise RuntimeError(f"Unrecognised MDAC address: {address}")
        self._mdac_open = self._mdac.open
        self._mdac_close = self._mdac.close

        if not (isinstance(self._mdac, serial_MDAC) and self._mdac.is_open):
            self.open()

        self.timeout = timeout
        self._ease_comms_congestion_delay = self.DEFAULT_ease_comms_congestion_delay

        _err_path = os.path.join(_driver_path, "error_codes.json")
        _warn_path = os.path.join(_driver_path, "warning_codes.json")

        try:
            self._error_dict = json.loads(Path(_err_path).read_text(encoding="utf-8"))
        except FileNotFoundError:
            self._error_dict = {}

        try:
            self._warn_dict = json.loads(Path(_warn_path).read_text(encoding="utf-8"))
        except FileNotFoundError:
            self._warn_dict = {}

        # Provide an initialisation timestamp for the "most recent rx'd data".
        self._timestamp_last_rx = time.time()

        # Set up initial communication link parameters.
        self._disable_link_checksums()
        # As this is the first command after connecting, include the "flush the cmd buffer" prefix.
        self._write("~PE0")  # Flush cmd buffer + Echo commands: OFF
        self._write("PV0")  # Verbose responses: OFF
        self._write("PLE0")  # Link echo (ie: remote-echo): OFF

        # Not all firmwares/interfaces support the PLF1 command, but we don't know what we're connecting to yet.
        self._ignore_response_errors = True
        self._write("PLF1")  # LF characters in replies: ON
        self._ignore_response_errors = False
        self.clear()

        self._mdac_variant: MdacVariant = MdacVariant.Unknown

        # get_idn() has the side effect of setting self._mdac_variant
        mdac_id = self.get_idn()
        if mdac_id["firmware"] is None:
            raise RuntimeError("Could not get MDAC FW version")
        assert isinstance(mdac_id["hardware_version"], str)
        self._master_hw_ver = int(mdac_id["hardware_version"])
        assert isinstance(mdac_id["firmware"], str)
        assert isinstance(mdac_id["build_date"], str)
        self._master_fw_ver = (
            Version(mdac_id["firmware"]),
            parser.parse(mdac_id["build_date"]),
        )
        assert self._master_hw_ver is not None
        assert self._master_fw_ver is not None

        self._mdac_fw_ver = self._master_fw_ver[0]
        self._mdac_fw_build_date = self._master_fw_ver[1]
        if (self._ease_comms_congestion_delay is None) and (
            self._mdac_fw_build_date < datetime.datetime(2022, 6, 15)
        ):
            # The _need_ for this delay was addressed in V1.9 firmware >= 15/06/2022
            self._ease_comms_congestion_delay = 0.010

        # Check that the driver is compatible with the MDAC firmware.
        error_str: str | None
        reply: str | list[str]

        if self._mdac_variant not in __MDAC_driver_supported_variants__:
            warn_str = (
                f"*** Unsupported hardware found ({self._mdac_variant.value}) ***\n"
                f"This version of the MDAC driver does not support this hardware variant.\n"
                f"Please modify your driver selection or contact the Sydney team for advice"
            )
            self._handle_warning(warn_str)

        if self._mdac_fw_ver < self._min_supported_fw:
            error_str = (
                f"*** Version mismatch ***\n"
                f"Driver v{self.__version__} and "
                f"MDAC firmware v{self._mdac_fw_ver} found.\n"
                f"The oldest supported firmware is {self._min_supported_fw}.\n"
                f"Please downgrade the driver or contact the Sydney team for a Firmware Update"
            )
            self._handle_error(error_str)
        elif self._mdac_fw_ver > self._max_supported_fw:
            warn_str = (
                f"*** Version mismatch ***\n"
                f"Driver v{self.__version__} and "
                f"MDAC firmware v{self._mdac_fw_ver} found.\n"
                f"The latest supported firmware is v{self._max_supported_fw}.\n"
                f"Please update the driver or contact the Sydney team for advice"
            )
            self._handle_warning(warn_str)
        elif (
            (self._mdac_fw_ver == self._max_supported_fw)
            and (self._mdac_fw_build_date is not None)
            and (self._mdac_fw_build_date < datetime.datetime(2021, 11, 3))
        ):
            warn_str = (
                f"*** Version mismatch ***\n"
                f"Driver v{self.__version__} and "
                f"MDAC firmware v{self._mdac_fw_ver} build date {self._mdac_fw_build_date} found.\n"
                f"This looks like an early firmware build.\n"
                f"Please update the driver or contact the Sydney team for advice"
            )
            self._handle_warning(warn_str)

        if self._check_fw_feature(FirmwareFeature.ComsChecksums):
            self._set_link_checksums(self._use_checksums_if_supported)

        self._update_daisychain_position()

        if full_initialisation:
            # Check that all cards are running the same firmware version.
            # Gather SLAVE card hw_vers whilst we're there.
            slave_fw_vers = []
            slave_hw_vers = []
            error_str = None
            for card in self._active_cards():
                reply = self._ask(f"SLAVE:{card}:STATUS ?").split(",")
                card_powered = reply[0] == "TRUE"
                card_installed = reply[1] == "TRUE"
                card_ready = card_powered and card_installed
                if card_ready:
                    slave_FW = Version(reply[4])
                    build_date_str = reply[5].strip()
                    build_date = parser.parse(build_date_str)
                    slave_fw_vers.append([slave_FW, build_date])

                    if slave_FW != self._mdac_fw_ver:
                        if error_str is None:
                            error_str = f"WARNING: MASTER has firmware v{self._mdac_fw_ver}"
                        error_str += f", SLAVE {card} has v{slave_FW}"

                    slave_HWs = reply[3].split("-")
                    slave_HW = slave_HWs[0]
                    relay_HW = slave_HWs[1]
                    slave_hw_vers.append([int(slave_HW), int(relay_HW)])
                else:
                    if error_str is None:
                        error_str = f"WARNING: MASTER has firmware v{self._mdac_fw_ver}"
                    if not card_powered:
                        error_str += f", SLAVE {card} is not powered"
                    else:
                        error_str += f", SLAVE {card} is not installed"
            if error_str is not None:
                self._handle_error(error_str)

            self._slave_hw_vers = slave_hw_vers
            self._samplerate = self._get_sample_rate()
            self._chan_range: list[int] = self._active_channels()
            if self._check_fw_feature(FirmwareFeature.NumBuses):
                self._num_buses = int(self._ask("?RB"))
            if self._num_buses > __MDAC_buses_max_supported__:
                warn_str = f"MDAC has {self._num_buses} bus channels, this driver only supports {__MDAC_buses_max_supported__}"
                self._handle_warning(warn_str)
                self._num_buses = __MDAC_buses_max_supported__

            # Create two lists, channels and triggers that will be added as submodules to the MDAC object.
            # As they will contain items that are themselves submodules, tell the snapshot framework that
            # the lists are unsnapshottable .. this stops the members' snapshot_base() being invoked twice
            # as it traverses the MDAC's list of submodules.
            channels = ChannelList(self, "Channels", MDACChannel, snapshotable=False)
            triggers = ChannelList(self, "Triggers", Trigger, snapshotable=False)

            if self._mdac_variant != MdacVariant.Mdac8:
                ch_isense = MDACBasicChannel(self, "ch_isense", 0, 0)
                ch_isense._update_voltage_range()
                ch_isense._update_voltage_gain()
                self.add_submodule("ch_isense", ch_isense)

                trig0 = Trigger(self, "trigger0", 0)
                triggers.append(trig0)
                self.add_submodule("trigger0", trig0)

                cards = self._active_cards()
                for card in cards:
                    trig_num = card
                    trig_name = f"trigger{trig_num}"
                    trig_n = Trigger(self, trig_name, trig_num)
                    triggers.append(trig_n)
                    self.add_submodule(trig_name, trig_n)
            else:
                trig_1 = Trigger(self, "trigger1", 1)
                triggers.append(trig_1)
                self.add_submodule("trigger1", trig_1)

            if qcodes_version >= __v0_32_0__:
                self.add_submodule("triggers", triggers.to_channel_tuple())
            else:
                triggers.lock()
                self.add_submodule("triggers", triggers)

            for i in self._chan_range:
                # Check if we are skipping some channels
                if limit_channels and (i not in limit_channels):
                    continue
                if self._check_fw_feature(FirmwareFeature.MapChannels):
                    reply = self._ask(f"DAC:{i}:MAP ?")
                    card = int(reply.split(",")[1])
                else:
                    # Remember that some MDACs skip channel 13
                    if self._chan13_skipped and (i >= 13):
                        card = ((i - 2) // 8) + 1
                    else:
                        card = ((i - 1) // 8) + 1
                channel = self.CHANNEL_CLASS(self, f"chan{i}", i, card)
                channel._update_voltage_range()
                channel._update_voltage_gain()
                channels.append(channel)
                self.add_submodule(f"ch{i:02}", channel)
            if qcodes_version >= __v0_32_0__:
                self.add_submodule("channels", channels.to_channel_tuple())
            else:
                channels.lock()
                self.add_submodule("channels", channels)

            self.sample_rate = Parameter(
                "sample_rate",
                instrument=self,
                label="DAC update rate",
                unit="Samples/s",
                get_cmd=self._get_sample_rate,
                docstring="DAC sample update rate for all MDAC channels",
            )

            self.bus: Parameter
            self.buses: list[Parameter] = []

            if self._mdac_variant != MdacVariant.Mdac8:
                for bus_num in range(1, self._num_buses + 1):
                    _param_name = f"bus{bus_num}"
                    _label = f"Chassis BUS {bus_num} relay"
                    _set_cmd = functools.partial(self._set_bus, bus_num=bus_num)
                    _get_cmd = functools.partial(self._get_bus, bus_num=bus_num)
                    _docstring = f"BUS {bus_num} CNX relay"
                    self.add_parameter(
                        _param_name,
                        label=_label,
                        set_cmd=_set_cmd,
                        get_cmd=_get_cmd,
                        val_mapping={"open": 0, "close": 1},
                        docstring=_docstring,
                    )
                    param = cast(
                        Parameter | None,
                        self.parameters.get(_param_name),
                    )
                    if param is not None:
                        self.buses.append(param)
                # For extra compatibility with existing scripts.
                self.bus = DelegateParameter(
                    "bus",
                    source=self.bus1,
                    label="Script compatibility alias for BUS 1 output relay",
                    docstring="Script compatibility alias for BUS 1 output relay",
                    bind_to_instrument=True,
                    instrument=self,
                )
            else:
                # For extra compatibility with existing scripts.
                self.bus = Parameter(
                    name="bus",
                    instrument=self,
                    label="Chassis fake BUS relay",
                    get_cmd=False,
                    set_cmd=False,
                )

            self.temperature = Parameter(
                "temperature",
                instrument=self,
                label="System Temperature (PEAK)",
                get_cmd=self._get_temperature,
                unit="°C",
                docstring="system peak internal temperature\na number in the 50s is not unusual",
            )

            self.protection = Parameter(
                "protection",
                instrument=self,
                label="Output Limit Protection",
                set_cmd=self._set_protection,
                get_cmd=self._get_protection,
                get_parser=ParameterParsers._lowercase_parser,
                set_parser=ParameterParsers._uppercase_parser,
                vals=vals.OnOff(),
                docstring="globally enables / disables HW based limit protection",
            )
            if self._hardware_limits is not None:
                if self._hardware_limits:
                    self.protection("on")
                else:
                    self.protection("off")

            self.supply_voltages = Parameter(
                "supply_voltages",
                instrument=self,
                label="MDAC supply voltages",
                get_cmd=self._supply_voltages,
                docstring="Average of system voltages \n(5V, 3.3V, VDD, VSS)",
            )

            self.arb_engine = Parameter(
                "arb_engine",
                instrument=self,
                label="MDAC AWG engine status",
                set_cmd=self._set_arb_engine,
                get_cmd=self._get_arb_engine,
                get_parser=ParameterParsers._lowercase_parser,
                set_parser=ParameterParsers._uppercase_parser,
                vals=ParameterParsers._ARB_VALIDATION,
                docstring="arb_engine responsible for updating dac outputs\n"
                "if off, all waveforms stop and ramps fail to update",
            )

            if self._check_fw_feature(FirmwareFeature.PowerManipulation):
                self.power = Parameter(
                    "power",
                    instrument=self,
                    label="MDAC Power Control",
                    set_cmd=self._set_power,
                    get_cmd=self._get_power,
                    get_parser=ParameterParsers._lowercase_parser,
                    set_parser=ParameterParsers._uppercase_parser,
                    vals=vals.OnOff(),
                    val_mapping={"on": "true", "off": "false"},
                    docstring="Powers UP/DOWN the MDAC DAC cards",
                )

            # Ensure that the cached parameter data is up-to-date.  This allows, for
            # example, code that is dependent on the MDAC object to use the cached data
            # during initialisation rather than retrieving the information from the MDAC.
            # This improves initialisation time by reducing the number of messages that must
            # be passed back and forth.
            # Note: Be aware, though, that the MDAC operates autonomously and there are several
            # scenarios where using cached data does not give an accurate reflection of the
            # MDAC's state.  It could be risky to use the cached data at any time other than
            # during initialisation - at which time we know that the cache is fairly fresh.
            self._update_cache()

        self.connect_message()

    def identify(self, activate: bool) -> None:
        if not self._check_fw_feature(FirmwareFeature.LocateMDAC):
            self._handle_warning(f"Firmware v{self._mdac_fw_ver} does not support this feature")
        else:
            if activate:
                self._write("IDENTIFY 1")
            else:
                self._write("IDENTIFY 0")

    def identify_is_on(self) -> bool:
        if not self._check_fw_feature(FirmwareFeature.LocateMDAC):
            self._handle_warning(f"Firmware v{self._mdac_fw_ver} does not support this feature")
            return False
        else:
            return self._ask("IDENTIFY ?") == "ON"

    def get_ip_config(self) -> tuple[str, str, str, bool] | None:
        ip_config = None
        if self._mdac_variant == MdacVariant.Mdac24:
            reply = self.ask("MASTER:NET:CONFIG ?")
            reply_items = reply.split(",")
            ip_addr = reply_items[0]
            netmask = reply_items[1]
            gateway = reply_items[2]
            dhcp = bool(reply_items[3])
            ip_config = (ip_addr, netmask, gateway, dhcp)
        return ip_config

    def set_ip_config(
        self,
        ip_addr: str | None = None,
        netmask: str | None = None,
        gateway: str | None = None,
        dhcp: bool | None = None,
        store: bool | None = False,
    ) -> None:
        if self._mdac_variant != MdacVariant.Mdac24:
            self._handle_error("MDAC does not have ethernet")
        elif self._master_fw_ver is None:
            raise RuntimeError("<<< Unrecoverable ERROR >>> MDAC firmware version is unknown")
        else:
            if ip_addr is not None:
                self.write(f"MASTER:NET:CONFIG:IP_ADDR {ip_addr}")
            if netmask is not None:
                self.write(f"MASTER:NET:CONFIG:NETMASK {netmask}")
            if gateway is not None:
                self.write(f"MASTER:NET:CONFIG:GATEWAY {gateway}")
            if dhcp is not None:
                self.write(f"MASTER:NET:CONFIG:DHCP {str(dhcp).upper()}")
            if store:
                self.write("MASTER:NET:CONFIG STORE")
            # Update local copies of IP config info.
            self.get_ip_config()

    def open(self) -> None:
        self._mdac_open()

    def close(self) -> None:
        self._mdac_close()
        super().close()

    def snapshot_base(
        self,
        update: bool | SnapshotUpdate | None = False,
        params_to_skip_update: Sequence[str] | None = None,
    ) -> dict[Any, Any]:
        if self._check_fw_feature(FirmwareFeature.SnapshotSupported):
            if normalize_snapshot_update(update) != "Never" and not self._update_cache_performed:
                self._update_cache()
                self._update_cache_performed = True
            # Call _update_cache() rather than getting the status individually for
            # each parameter. We set _update_cache_performed to True
            # to indicate that each channel update does not need to call this
            # function as opposed to when snapshot is called on an individual
            # channel.
            snapshot = super().snapshot_base(
                update=update,
                params_to_skip_update=params_to_skip_update,
            )
            if not self._freeze_update_cache_performed:
                self._update_cache_performed = False
        else:
            snapshot = super().snapshot_base(
                update=update, params_to_skip_update=params_to_skip_update
            )
        return snapshot

    def _update_cache(self) -> None:
        """
        Function to query the MDAC and get its status.
        """
        if self._check_fw_feature(FirmwareFeature.DacsStatus):
            lines = self._ask("DAC:ALL:STATUS ?").splitlines()
            for line in lines:
                line_items = line.split(",")
                # line_items[0] is dac_num
                # line_items[1] is dac_present
                # line_items[2] is dac_disabled
                # line_items[3] is dac_tested
                # line_items[4] is dac_test_passed
                # line_items[5] is a misc field that may or may not be present.  If present it includes
                #               information about why the DAC failed the self-test or why it's disabled.
                chan_num = int(line_items[0])
                if chan_num == 0:
                    chan = self.ch_isense
                else:
                    chan = self._channum_to_chan(chan_num)
                if line_items[2] == "TRUE":
                    disabled = True
                else:
                    disabled = False
                if chan is not None:
                    chan.disabled.cache.set(disabled)

        if self._mdac_variant == MdacVariant.Mdac8:
            all_channels = []
        else:
            all_channels = [self.ch_isense]
        all_channels.extend(self.channels)

        if self._check_fw_feature(FirmwareFeature.DacsLimits):
            lines = self._ask("DAC:ALL:LIMITS ?").splitlines()
        else:
            lines = []
            for ch in all_channels:
                line = self._ask(f"DAC:{ch._channum}:LIMITS ?")
                # install chassis-centric number
                line = f"{ch._channum}," + line
                lines.append(line)
        for line in lines:
            line_items = line.split(",")
            # line_items[0] is dac_num
            chan_num = int(line_items[0])
            if chan_num == 0:
                chan = self.ch_isense
            else:
                chan = self._channum_to_chan(chan_num)
            if chan is None:
                continue
            chan_limits = chan._get_limits(line)
            # _get_limits()[0] is minlimit
            # _get_limits()[1] is maxlimit
            # _get_limits()[2] is ratelimit
            # _get_limits()[3] is minlimit_DIVIDED
            # _get_limits()[4] is maxlimit_DIVIDED
            # _get_limits()[5] is ratelimit_DIVIDED
            # _get_limits()[6] is divider_mode ('NORMAL' or 'DIVIDED')
            if chan_limits[6] == "NORMAL":
                limit_min = chan_limits[0]
                limit_max = chan_limits[1]
                limit_rate = chan_limits[2]
            else:
                limit_min = chan_limits[3]
                limit_max = chan_limits[4]
                limit_rate = chan_limits[5]
            chan.limit_max.cache.set(limit_max)
            chan.limit_min.cache.set(limit_min)
            if self._check_fw_feature(FirmwareFeature.LimitRate):
                chan.limit_rate.cache.set(limit_rate)
                if self._check_fw_feature(FirmwareFeature.LimitRate):
                    chan.limit_rate.cache.set(limit_rate)

        lines = self._get_dacs_state_raw("ALL")
        line_index = 0
        while line_index < len(lines):
            dac_items = lines[line_index].split(",")
            # dac_items[0] is dac_num
            # dac_items[1] is current dac_voltage in volts
            # dac_items[2] is current dac_voltage in dac units
            # dac_items[3] is dac_voltage_limited
            # dac_items[4] is dac_arb_active
            # dac_items[5] is dac_arb_starting
            # dac_items[6] is dac_arb_stopping
            # dac_items[8] is dac_0V_ramping
            # dac_items[9] is dac_output_limited
            chan_num = int(dac_items[0])
            if chan_num == 0:
                chan = self.ch_isense
            else:
                chan = self._channum_to_chan(chan_num)
            if chan is None:
                continue
            volts = float(dac_items[1])
            dac_raw = int(dac_items[2], 16)
            chan.voltage.cache.set(volts)
            chan.voltage_raw.cache.set(dac_raw)

            line_index += 1
            preset_items = cast(PresetType, tuple(lines[line_index].lower().split(",")))
            # preset_items[0] is the waveform name
            # preset_items[1, 2] is the period in seconds, ticks
            # preset_items[3, 4] is the offset / ramp_start in volts, dac units
            # preset_items[5, 6] is the amplitude / ramp_destn in volts, dac units
            # preset_items[7] is the number of shots
            # preset_items[8, 9] is the phase offset in seconds, ticks
            # preset_items[10] is the next sample offset
            # preset_items[11] is the group size(for presets that have it)
            if preset_items[0] == "none":
                # _get_preset() layout follows that of the preset_items described above
                preset_items = (
                    "dc",
                    "0",
                    "0",
                    f"{volts}",
                    f"{dac_raw}",
                    "0",
                    "0",
                    "inf",
                    "0",
                    "0",
                    "0",
                    "1",
                )

            amplitude = chan._get_amplitude(preset_items)
            chan.amplitude.cache.set(amplitude)
            offset = chan._get_offset(preset_items)
            chan.offset.cache.set(offset)
            frequency = chan._get_frequency(preset_items)
            chan.frequency.cache.set(frequency)
            period = chan._get_period(preset_items)
            chan.period.cache.set(period)
            phase = chan._get_phase_offset(preset_items)
            chan.phase.cache.set(phase)
            group_size = chan._get_group_size(preset_items)
            chan.group_size.cache.set(group_size)
            if preset_items[0] == "dc":
                waveform = chan._get_preset_name("off")
            else:
                waveform = chan._get_preset_name(preset_items[0])
            chan.waveform.cache.set(waveform)
            ramp_rate = chan._get_ramprate(preset_items)
            chan.ramp_rate.cache.set(ramp_rate)
            ramp_destination = chan._get_ramp_destination(preset_items)
            chan.ramp_destination.cache.set(ramp_destination)

            if chan_num != 0:
                line_index += 1
                relays_items = lines[line_index].split(",")
                # relays_items[0] is the microd
                # relays_items[1] is the smc/sma
                # relays_items[2, ..] are the buses
                # relays_items[2 + num_buses] is the terminator
                # relays_items[3 + num_buses] is the F1 relay
                # relays_items[4 + num_buses] is the filter_bypass  (Note: always '0')
                # relays_items[5 + num_buses] is the dac_output
                # relays_items[6 + num_buses] is the divider

                microd = relays_items[0]
                chan.microd.cache.set(reverse_dict_lookup(chan.microd.val_mapping, int(microd)))
                smc_sma = relays_items[1]
                if self._mdac_variant == MdacVariant.Mdac24:
                    chan.sma.cache.set(reverse_dict_lookup(chan.sma.val_mapping, int(smc_sma)))
                else:
                    chan.smc.cache.set(reverse_dict_lookup(chan.smc.val_mapping, int(smc_sma)))

                relays_line_index = 2
                for chan_bus in chan.buses:
                    bus = relays_items[relays_line_index]
                    chan_bus.cache.set(reverse_dict_lookup(chan_bus.val_mapping or {}, int(bus)))
                    relays_line_index += 1

                terminator = relays_items[2 + self._num_buses]
                chan.gnd.cache.set(reverse_dict_lookup(chan.gnd.val_mapping, int(terminator)))
                f1 = relays_items[3 + self._num_buses]
                bypass = relays_items[4 + self._num_buses]
                if int(bypass) == 1:
                    # Special case where the MDAC has a filter bypass relay and it's already been activated.
                    filter_setting = 0
                    chan.filter.vals = ParameterParsers._FILTERS_INCLUDING_0
                elif int(f1) == 1:
                    filter_setting = 1
                else:
                    filter_setting = 2
                chan.filter.cache.set(filter_setting)
                dac_output = relays_items[5 + self._num_buses]
                chan.dac_output.cache.set(
                    reverse_dict_lookup(chan.gnd.val_mapping, int(dac_output))
                )
                divider = relays_items[6 + self._num_buses]
                chan.divider.cache.set(reverse_dict_lookup(chan.divider.val_mapping, int(divider)))
            line_index += 1

        if self._check_fw_feature(FirmwareFeature.TriggersStatus):
            lines = self._ask("TRIGGER:ALL ?").splitlines()
            line_index = 0
            while line_index < len(lines):
                config_line = lines[line_index]
                set_line = lines[line_index + 1]
                level_line = lines[line_index + 2]
                line_index += 3

                # Extract (and remove) the trigger num from config_line
                line_items = config_line.split(",")
                trig_num = int(line_items.pop(0))
                config_line = ""
                comma_str = ""
                for item in line_items:
                    config_line += comma_str
                    config_line += item
                    comma_str = ","

                trig = self._trignum_to_trig(trig_num)
                if trig is not None:
                    trig.direction.cache.set(trig._get_direction(config_line))

                    trigger_set = trig._get_trigger(set_line)
                    # _get_trigger()[0] is active
                    # _get_trigger()[1,2] is period, period_raw
                    # _get_trigger()[3,4] is pulse_width, pulse_width_raw
                    # _get_trigger()[5] is num_triggers
                    # _get_trigger()[6,7] is phase_offset, phase_offset_raw
                    trig.frequency.cache.set(trig._get_frequency(trigger_set))
                    trig.period.cache.set(trig._get_period(trigger_set))
                    trig.phase.cache.set(trig._get_phase_offset(trigger_set))

                    if self._check_fw_feature(FirmwareFeature.ConfigurableTriggerOutLevels):
                        trig.level.cache.set(trig._get_level(level_line))

        # Also get state of all chassis buses.
        for chassis_bus in self.buses:
            chassis_bus.get()

    def _check_fw_feature(
        self,
        feature: FirmwareFeature,
        test_only: bool = True,
        tolerate_fw_None: bool = True,
    ) -> bool:
        if not isinstance(feature, FirmwareFeature):
            raise RuntimeError("feature is not of type FirmwareFeature")

        feature_supported = True
        description: str | None = None

        if self._mdac_fw_ver is None:
            feature_supported = False
            description = "testing support for features"
            if not tolerate_fw_None:
                raise RuntimeError("Could not determine MDAC firmware version")
        elif (feature == FirmwareFeature.LimitRate) and (self._mdac_fw_ver >= __v1_8__):
            feature_supported = False
            description = "limit_rate (v1.7 or less required)"
        elif (feature == FirmwareFeature.CheckLimited) and (self._mdac_fw_ver < __v1_8__):
            feature_supported = False
            description = "chan.limit_exceeded (v1.8 or greater required)"
        elif (feature == FirmwareFeature.PowerManipulation) and (
            (self._mdac_fw_ver < __v1_8__) or (self._mdac_variant == MdacVariant.Mdac8)
        ):
            feature_supported = False
            description = "POWER operations (MDAC24 or 64 with f/w v1.8 or greater required)"
        elif (feature == FirmwareFeature.MultiBus) and (self._mdac_fw_ver < __v1_8__):
            feature_supported = False
            description = "mutiple bus connections (v1.8 or greater required)"
        elif (feature == FirmwareFeature.VoltageTest) and (self._mdac_fw_ver < __v1_8__):
            feature_supported = False
            description = "chan.voltage self-test (v1.8 or greater required)"
        elif (feature == FirmwareFeature.MdacAddr) and (self._mdac_fw_ver < __v1_8__):
            feature_supported = False
            description = "MDAC:<addr>: command addressing (v1.8 or greater required)"
        elif (feature == FirmwareFeature.CmdSeqNum) and (self._mdac_fw_ver < __v1_9__):
            feature_supported = False
            description = "command sequence numbers (v1.9 or greater required)"
        elif (feature == FirmwareFeature.ComsChecksums) and (self._mdac_fw_ver < __v1_9__):
            feature_supported = False
            description = "link communication checksums (v1.9 or greater required)"
        elif (feature == FirmwareFeature.MapChannels) and (self._mdac_fw_ver < __v1_9__):
            feature_supported = False
            description = "list DAC channels (v1.9 or greater required)"
        elif (feature == FirmwareFeature.GetPresets) and (self._mdac_fw_ver < __v1_9__):
            feature_supported = False
            description = "list DAC channel presets (v1.9 or greater required)"
        elif feature == FirmwareFeature.BusNumberRequired:
            if self._num_buses == 1:
                feature_supported = False
                description = "bus number (only 1 BUS present)"
            elif self._mdac_fw_ver < __v1_9__:
                feature_supported = False
                description = "bus number (v1.9 or greater required)"
        elif (feature == FirmwareFeature.SlavesStatus) and (self._mdac_fw_ver < __v1_9__):
            feature_supported = False
            description = "report all SLAVEs status (v1.9 or greater required)"
        elif (feature == FirmwareFeature.LocateMDAC) and (self._mdac_fw_ver < __v1_9__):
            feature_supported = False
            description = "flash MDAC front panel LEDs"
        elif feature == FirmwareFeature.ConfigurableTriggerOutLevels:
            if self._mdac_variant != MdacVariant.Mdac24:
                feature_supported = False
                description = "configurable trigger levels (MDAC24 required)"
            elif (
                (self._master_hw_ver is not None)
                and (self._master_hw_ver < 2)
                and not fake_configurable_trigger_levels
            ):
                feature_supported = False
                description = "configurable trigger levels (MASTER h/w ver 2+ required)"
        elif (feature == FirmwareFeature.DaisyChainSupported) and (
            self._mdac_variant != MdacVariant.Mdac24
        ):
            feature_supported = False
            description = "daisy-chain (MDAC24 with v1.9 or greater required)"
        elif feature == FirmwareFeature.MultiWaveforms:
            if self._mdac_fw_ver < __v1_9__:
                feature_supported = False
                description = "multi-waveforms (v1.9 or greater required)"
            elif (
                (self._mdac_fw_ver == __v1_9__)
                and (self._mdac_fw_build_date is not None)
                and (self._mdac_fw_build_date < datetime.datetime(2021, 7, 17))
            ):
                feature_supported = False
                description = "multi-waveforms (v1.9 firmware build date > 2021/07/16 required)"
        elif feature == FirmwareFeature.DacsStatus:
            if self._mdac_fw_ver < __v1_9__:
                feature_supported = False
                description = "multi-DAC status query (v1.9 or greater required)"
            elif (
                (self._mdac_fw_ver == __v1_9__)
                and (self._mdac_fw_build_date is not None)
                and (self._mdac_fw_build_date < datetime.datetime(2021, 11, 10))
            ):
                feature_supported = False
                description = (
                    "multi-DAC status query (v1.9 firmware build date > 2021/11/09 required)"
                )
        elif feature == FirmwareFeature.DacsLimits:
            if self._mdac_fw_ver < __v1_9__:
                feature_supported = False
                description = "multi-DAC limits query (v1.9 or greater required)"
            elif (
                (self._mdac_fw_ver == __v1_9__)
                and (self._mdac_fw_build_date is not None)
                and (self._mdac_fw_build_date < datetime.datetime(2021, 11, 10))
            ):
                feature_supported = False
                description = (
                    "multi-DAC limits query (v1.9 firmware build date > 2021/11/09 required)"
                )
        elif feature == FirmwareFeature.DacLimitsIncludesChanNum:
            if self._mdac_fw_ver < __v1_9__:
                feature_supported = False
            elif (
                (self._mdac_fw_ver == __v1_9__)
                and (self._mdac_fw_build_date is not None)
                and (self._mdac_fw_build_date < datetime.datetime(2022, 2, 1))
            ):
                feature_supported = False
            description = "DAC limits query includes DAC number (v1.9 2022/02/01 or later required)"
        elif feature == FirmwareFeature.TriggersStatus:
            if self._mdac_fw_ver < __v1_9__:
                feature_supported = False
                description = "TRIGGERs status query (v1.9 or greater required)"
            elif (
                (self._mdac_fw_ver == __v1_9__)
                and (self._mdac_fw_build_date is not None)
                and (self._mdac_fw_build_date < datetime.datetime(2021, 11, 11))
            ):
                feature_supported = False
                description = (
                    "TRIGGERs status query (v1.9 firmware build date > 2021/11/10 required)"
                )
        elif (feature == FirmwareFeature.NumBuses) and (self._mdac_fw_ver < __v1_9__):
            feature_supported = False
            description = "?RB status query (v1.9 or greater required)"
        elif (feature == FirmwareFeature.PeakTemperature) and (self._mdac_fw_ver < __v1_9__):
            feature_supported = False
            description = (
                "TEMPERATURE status query (v1.9 or greater required to retrieve peak temperature)"
            )
        elif (feature == FirmwareFeature.SnapshotSupported) and (self._mdac_fw_ver < __v1_9__):
            feature_supported = False
            description = "Snapshot supported query (v1.9 or greater required)"
        elif (feature == FirmwareFeature.DacAllOutputs) and (self._mdac_fw_ver < __v1_9__):
            feature_supported = False
            description = "DAC:ALL:OUTPUT ? status query (v1.9 or greater required)"
        elif feature == FirmwareFeature.DacsConfig:
            if self._mdac_fw_ver < __v1_9__:
                feature_supported = False
                description = "DAC:ALL:CONFIG ? query (v1.9 or greater required)"
            elif (
                (self._mdac_fw_ver == __v1_9__)
                and (self._mdac_fw_build_date is not None)
                and (self._mdac_fw_build_date < datetime.datetime(2022, 4, 7))
            ):
                feature_supported = False
                description = (
                    "DAC:ALL:CONFIG ? query (v1.9 firmware build date > 2022/04/06 required)"
                )
        elif feature == FirmwareFeature.AutoNumberIncludesIpAddress:
            if self._mdac_fw_ver < __v1_9__:
                feature_supported = False
                description = "COMBO:BOSS AUTO_NUMBER_MDACS command (v1.9 or greater required)"
            elif (
                (self._mdac_fw_ver == __v1_9__)
                and (self._mdac_fw_build_date is not None)
                and (self._mdac_fw_build_date < datetime.datetime(2022, 5, 25))
            ):
                feature_supported = False
                description = "COMBO:BOSS AUTO_NUMBER_MDACS command (v1.9 firmware build date > 2022/05/24 required)"
        elif feature == FirmwareFeature.ArbInterp1d:
            if self._mdac_fw_ver < __v1_9__:
                feature_supported = False
                description = "DAC:n:PRESET ARB_INTERP1D_DATA command (v1.9 or greater required)"
            elif (
                (self._mdac_fw_ver == __v1_9__)
                and (self._mdac_fw_build_date is not None)
                and (self._mdac_fw_build_date < datetime.datetime(2022, 11, 15))
            ):
                feature_supported = False
                description = "DAC:n:PRESET ARB_INTERP1D_DATA command (v1.9 firmware build date > 2022/11/15 required)"
        if not feature_supported and not test_only:
            self._handle_error(
                f"MDAC variant {self._mdac_variant} firmware Version {self._mdac_fw_ver}"
                f' does NOT support "{description}".'
            )

        return feature_supported

    def _channum_to_chan(self, chan_num: int) -> MDACBasicChannel | None:
        """
        Find which mdac channel has the supplied chan_num.
        :return:
        The channel object.
        """
        chan = None
        for ch in self.channels:
            if ch._channum == chan_num:
                chan = ch
                break
        return chan

    def _trignum_to_trig(self, trig_num: int) -> Trigger | None:
        """
        Find which mdac trigger has the supplied trig_num.
        :return:
        The trigger object.
        """
        trig = None
        for tr in self.triggers:
            if tr._trignum == trig_num:
                trig = tr
                break
        return trig

    def _disable_link_checksums(self) -> None:
        cs_query_cmd: str = "PCS0"
        if self._mdac_comms_id is not None:
            cs_query_cmd = f"MDAC:{self._mdac_comms_id}:" + cs_query_cmd
        self._use_checksums = False
        checksum_str: str = format(calculate_checksum(cs_query_cmd), "04X")
        response = self._ask(
            "~" + cs_query_cmd + checksum_str,
            None,
            decode_response=False,
            decorate_cmd=False,
        )
        if "[OK]" not in response:
            # MDAC wasn't expecting the checksum that we included,
            # resend the command without the checksum.
            self._write("~" + cs_query_cmd, None, decode_response=False, decorate_cmd=False)

    def _set_link_checksums(self, enable: bool) -> None:
        if enable:
            checksums_cmd = "PCS1"
        else:
            checksums_cmd = "PCS0"
        self._write("~" + checksums_cmd)
        self._use_checksums = enable

    def run(self) -> None:
        """
        enables arb_engine:
        waveforms will update and ramps will continue
        """
        self.arb_engine("on")

    def stop(self) -> None:
        """
        disables arb_engine:
        all waveforms / ramps will hold at their current value
        """
        self.arb_engine("off")

    def list_triggers(self) -> tuple[int, ...]:
        """lists the dac channels currently generating trigger outputs"""
        t = self._ask("DAC:ALL:TRIGGER ?").splitlines()
        ret = []
        for i in t:
            if i == "ABSENT":
                ret.append(-1)
            else:
                ret.append(int(i))
        return tuple(ret)

    def _set_arb_engine(self, on_off: str) -> None:
        if (self._daisychain is not None) and (
            self._daisychain_state != DaisyChainState.Supervisor
        ):
            self._daisychain._set_arb_engine(on_off)
        else:
            set_cmd = f"ARB {on_off}"
            self._write(set_cmd)

    def _get_arb_engine(self) -> str:
        if (self._daisychain is not None) and (
            self._daisychain_state != DaisyChainState.Supervisor
        ):
            on_off = self._daisychain._get_arb_engine()
        else:
            get_cmd = "ARB ?"
            on_off = self._ask(get_cmd).split(",")[0]
        return on_off

    def _trigger_arb_engine(self) -> None:
        set_cmd = "ARB TRIGGER"
        self._write(set_cmd)

    def _set_power(self, on_off: str) -> None:
        _ASSERT(self._check_fw_feature(FirmwareFeature.PowerManipulation, test_only=False))
        set_cmd = f"POWER {on_off}"

        # POWER ON/OFF command durations vary depending on the operation and number of DAC cards.
        if self._mdac_variant == MdacVariant.Mdac24:
            timeout = 10
        else:
            timeout = 30

        if self._daisychain_state == DaisyChainState.Supervisor:
            num_mdacs = 1
            if self._daisychain is None:
                # This covers the case where we're connected to the supervisor but the daisychain
                # configuration hasn't yet been built/retrieved.
                self._handle_error(
                    "MDAC is a daisychain supervisor, "
                    "but the daisychain has not been retrieved/built"
                )

            else:
                minions = self._daisychain._minions
                if minions is not None:
                    num_mdacs += len(minions)
                all_channels = [isense_chan for isense_chan in self._daisychain.channels_isense]
                all_channels.extend(self._daisychain.channels)

            timeout *= num_mdacs
        else:
            all_channels = [self.ch_isense]
            all_channels.extend(self.channels)

        if self._get_power() == "TRUE":
            for chan in all_channels:
                chan.voltage(0)

        # It might be better to add a timeout to the self._write() parameter list.
        # This would give us the capability to easily spec custom timeouts on each command sent rather than
        # having a global timeout that suits "almost all situations".  That could be useful given that
        # different commands take different amounts of time to complete.
        remember_timeout = self.timeout
        self.timeout = timeout
        try:
            self._write(set_cmd)
        finally:
            self.timeout = remember_timeout

    def _get_power(self) -> str:
        _ASSERT(self._check_fw_feature(FirmwareFeature.PowerManipulation, test_only=False))
        # Use the 'POWER ?p' variant to support MDACs with incomplete SLAVE card sets.
        on_off = self._ask("POWER ?p")
        return on_off

    def _set_protection(self, on_off: vals.OnOff) -> None:
        set_cmd = f"DAC:ALL:PROTECTION {on_off}"
        self._write(set_cmd)

    def _get_protection(self) -> str:
        get_cmd = "DAC:ALL:PROTECTION ?"
        on_off = self._ask(get_cmd)
        return on_off

    def _get_temperature(self) -> float | None:
        get_cmd = "STATUS:TEMPERATURE ?"
        temperatures = self._ask(get_cmd).split(",")
        self._average_temperature = float(temperatures[0])
        if self._check_fw_feature(FirmwareFeature.PeakTemperature):
            self._peak_temperature = float(temperatures[1])
            ret_val = self._peak_temperature
        else:
            self._peak_temperature = None
            ret_val = self._average_temperature
        return ret_val

    def bus_owner(self, bus_num: int = 1, force_local: bool = False) -> MDACBasicChannel | None:
        """
        Returns the channel that has its DAC output connected to the bus
        """
        if (self._daisychain is not None) and (not force_local):
            # Note: _daisychain.bus_owner() calls supervisor.bus_owner()
            bus_ch = self._daisychain.bus_owner(bus_num)
        else:
            if self._mdac_fw_ver is None:
                raise RuntimeError("MDAC firmware version is not known.")
            elif self._mdac_fw_ver < __v1_9__:
                # Note:  fw_ver < 1.9 is only on MDAC64 units (ie: a single BUS)
                cmd_str = "DAC:ALL:RELAYS:BUS ?"
            elif self._check_fw_feature(FirmwareFeature.BusNumberRequired):
                cmd_str = f"RELAYS:BUS:{bus_num}:OWNER ?"
            else:
                cmd_str = "RELAYS:BUS:OWNER ?"

            # Note: In the case of the LOCAL bus owner command, it's not enough to just set force_direct_communication.
            # When sent to the supervisor, even directly, this still equates to a daisychain-centric command.
            # To get the supervisor to reply with the LOCAL context, we need to supply the MDAC number in the command.
            # This works correctly for *all* members of the daisychain, whether they be supervisor or minion.
            b = self._ask(cmd_str, mdac_comms_id=self._mdac_comms_id).split(",")

            if b[0] == "NONE":
                bus_ch_num = -1
            else:
                bus_ch_num = int(b[0])
            if bus_ch_num == -1:
                bus_ch = None
            elif bus_ch_num == 0:
                bus_ch = self.ch_isense
            else:
                bus_ch = self._channum_to_chan(bus_ch_num)

        return bus_ch

    def bus_channels(self, bus_num: int = 1) -> tuple[MDACBasicChannel, ...] | None:
        """
        Returns the list of DAC channels that are connected to the bus
        """
        bus_connected_channels: list[MDACBasicChannel] | None
        ret: tuple[MDACBasicChannel, ...] | None
        if self._daisychain is not None:
            # Note: _daisychain.bus_channels() does not call supervisor.bus_channels()
            bus_channels = self._daisychain.bus_channels(bus_num)
            if bus_channels is None:
                ret = None
            else:
                ret = tuple(bus_channels)
        else:
            bus_connected_channels = []
            # Note: For some firmwares multiple channels may be connected to the bus
            # even though only one bus owner (ie: with dac output connected) exists.
            if (bus_num == 1) and (self.ch_isense._get_bus(bus_num) == "1"):
                bus_connected_channels.append(self.ch_isense)
            for channel in self.channels:
                if channel._get_bus(bus_num) == "1":
                    bus_connected_channels.append(channel)
            if len(bus_connected_channels) == 0:
                ret = None
            else:
                ret = tuple(bus_connected_channels)
        return ret

    def disconnect_bus(self, bus_num: int = 1, force_local: bool = False) -> None:
        """
        Disconnect every DAC channel from the bus and disconnect the chassis BUS relay.
        """
        if (self._daisychain is not None) and (not force_local):
            # Note: _daisychain.bus_owner() calls supervisor.bus_owner()
            self._daisychain.disconnect_bus(bus_num)
        else:
            bus_connected_channels = self.bus_channels(bus_num)
            if bus_connected_channels is not None:
                # Set 0 volts on all channels connected to the bus.
                for channel in bus_connected_channels:
                    channel._nonblocking_set_voltage(0)

                # Wait for any channels that were configured to auto-ramp voltages.
                self.wait_ramps(bus_connected_channels, timeout=None)

                # Open the bus relay for all channels connected to the bus.
                for channel in bus_connected_channels:
                    if self._num_buses != 1:
                        my_bus = channel.buses[bus_num - 1]
                        assert my_bus is not None
                        my_bus("open")
                    elif bus_num != 1:
                        self._handle_error(f"Invalid bus_num supplied: {bus_num}")
                    elif channel.bus is not None:
                        channel.bus("open")
                    else:
                        raise RuntimeError(
                            f"{self}.bus_channels({bus_num}) included {channel}, "
                            "for which channel.bus() is None"
                        )

            if self._num_buses != 1:
                my_bus = self.buses[bus_num - 1]
                assert my_bus is not None
                my_bus("open")
            elif bus_num != 1:
                self._handle_error(f"Invalid bus_num supplied: {bus_num}")
            elif self.bus is not None:
                self.bus("open")
            else:
                raise RuntimeError(f"{self}.bus is None")

    def _get_bus(self, bus_num: int = 1) -> str:
        if self._check_fw_feature(FirmwareFeature.BusNumberRequired):
            cmd_str = f"RELAYS:BUS:{bus_num} ?"
        elif bus_num != 1:
            self._handle_error(f"Invalid bus_num supplied: {bus_num}")
        else:
            cmd_str = "RELAYS:BUS_CALIBRATE ?"
        response = self._ask(cmd_str)
        return response

    def _set_bus(self, cmd: str, bus_num: int = 1) -> None:
        if self._check_fw_feature(FirmwareFeature.BusNumberRequired):
            cmd_str = f"RELAYS:BUS:{bus_num} {cmd}"
        elif bus_num != 1:
            self._handle_error(f"Invalid bus_num supplied: {bus_num}")
        else:
            cmd_str = f"RELAYS:BUS_CALIBRATE {cmd}"
        self._write(cmd_str)

    def sync(self) -> None:
        """
        Synchronises the waveforms running on all channels
        """
        if (self._daisychain is not None) and (
            self._daisychain_state != DaisyChainState.Supervisor
        ):
            self._daisychain.sync()
        else:
            self._write("DAC:ALL SYNC")

    def autosync(
        self,
        autosync: bool,
        channels: Sequence[MDACBasicChannel] | MDACBasicChannel | None = None,
    ) -> None:
        """
        Sets autosync on the specified channels.
        Args:
            autosync: The specified autosync state to set
            channels: The list of channels to check.  'None' equates to "all MDAC channels"
        """
        if channels is not None:
            self.check_channels_are_mine(channels)

        channels_list: list[MDACBasicChannel]
        if channels is None:
            channels_list = []
            for ch in self.channels:
                channels_list.append(ch)

        elif isinstance(channels, MDACBasicChannel):
            channels_list = [channels]

        else:
            channels_list = list(channels)

        for channel in channels_list:
            channel.autosync(autosync)

    def _read_response_and_buffer(
        self,
        expect_full_response: bool = True,
        decode_response: bool = True,
    ) -> str:
        full_reply = ""
        response = ""
        status_line = None
        continue_reply_cmd = None

        # When the MDAC sends a multi-line reply, it terminates all "internal" lines of the reply with
        # CRLF but replaces all the CR characters with '\x17'.
        # By default, MDAC uses LF as the read_terminator.  Each read() operation thus returns one line
        # of the full reply.  To mark the end of the response, an intact CR character is sent in front
        # of the LF at the end of the last line.
        # When a different terminator has been configured, the response doesn't end with a LF character,
        # so there is no longer a need to differentiate the very last LF from internal LF characters,
        # so the final CR is omitted (internal CR characters are still replaced with '\x17').  In this
        # case, the whole reply is received in one read() operation, and the ending CR is missing.

        response_complete = False
        while not response_complete:
            done_reading = False
            read_data = ""
            while not done_reading:
                read_data = self._mdac.read(timeout_Secs=self.timeout, failure_is_fatal=True)
                self.log.debug(f"Response: {read_data}")

                if not expect_full_response:
                    done_reading = True
                    response_complete = True
                    response = read_data
                elif "[OK]" in read_data:
                    done_reading = True
                elif "[ERR:0x" in read_data:
                    done_reading = True
                elif ProtocolChars.RS in read_data:
                    done_reading = True
                elif ProtocolChars.CAN in read_data:
                    done_reading = True

            if self._use_checksums:
                if len(read_data) < 4:
                    self.log.debug(f"checksum - reply ({read_data}) does not contain checksum")
                    raise ChecksumError
                recvd_checksum_str = read_data[-4:]
                calculated_checksum_str = format(calculate_checksum(read_data[:-4]), "04X")
                if recvd_checksum_str != calculated_checksum_str:
                    self.log.debug(
                        f"checksum - recvd: 0x{recvd_checksum_str}, calc:0x {calculated_checksum_str}"
                    )
                    raise ChecksumError
                else:
                    read_data = read_data[:-4]

            # There are no sequence numbers in a reply continuation.
            if self._expect_sequence_num and (continue_reply_cmd is None):
                end_of_seq_num_posn = read_data.find("|")
                reply_seq_num = int(read_data[:end_of_seq_num_posn])
                if reply_seq_num != self._write_sequence_num:
                    self.log.debug(f"seq_num - tx: {self._write_sequence_num}, rx: {reply_seq_num}")
                    raise SeqNumError

            # Any sequence nums and checksums have been stripped away.
            full_reply += read_data

            if full_reply.endswith(ProtocolChars.RS):
                full_reply = full_reply[:-1]
                char_posn = len(full_reply)
                self.log.debug(
                    f"Incomplete Response found (at posn: {char_posn}), asking for continuation"
                )
                continue_reply_cmd = self._make_cmd(
                    f"{self._repeat_last_txd_reply}{char_posn}", mdac_comms_id=None
                )
                self._mdac.write(continue_reply_cmd)
            else:
                response_complete = True

        if self._expect_sequence_num:
            end_of_seq_num_posn = full_reply.find("|")
            full_reply = full_reply[end_of_seq_num_posn + 1 :]

        response_lines = full_reply.replace(ProtocolChars.ETB, ProtocolChars.CR).split(
            ProtocolChars.LF
        )
        for response_line in response_lines:
            if "[OK]" in response_line:
                if not decode_response:
                    response += response_line
                status_line = response_line
            elif "[ERR:0x" in response_line:
                if decode_response and (not self._ignore_response_errors):
                    self._error_decode(response_line)
                else:
                    response += response_line
                status_line = response_line
                error_code: str = self._extract_error_code(response_line)
                error = self._error_dict.get(error_code)
                if (error == "PARSE_CMD_BAD_SLAVE_RELAY") or (error == "PARSE_CMD_COMBO_CMD_ERR"):
                    raise MsgRelayFailure
            else:
                response += response_line

        # Trim the last CR if necessary
        if response.endswith(ProtocolChars.CR):
            response = response[:-1]

        self._last_mdac_reply = response
        if status_line is None:
            self._last_mdac_status = None
        else:
            self._last_mdac_status = self._extract_error_code(status_line)

        return response

    def _write_cmd_and_buffer_reply(
        self,
        cmd: str,
        mdac_comms_id: int | None = _MDAC_COMMS_ID_AUTOFILL,
        expect_full_response: bool = True,
        decode_response: bool = True,
    ) -> None:
        """
        write a command to the MDAC.
        The output gets checked for [OK] or [ERR:XXXX] and is put into a
        queue to be read later.  write() has to read a response because of
        the way the qcodes parameters are implemented: setting a value causes
        the 'write' function to be invoked. Some instruments, including the
        MDAC, always return a status that has to be read.
        """
        if self._ease_comms_congestion_delay is not None:
            elapsed_since_last_rx = time.time() - self._timestamp_last_rx
            remaining_rx_tx_guard_time = self._ease_comms_congestion_delay - elapsed_since_last_rx
            if remaining_rx_tx_guard_time > 0:
                time.sleep(remaining_rx_tx_guard_time)

        self.log.debug(f"Sending: {cmd}")
        self._last_mdac_cmd = cmd
        try:
            self._mdac.write(cmd)
            self._read_response_and_buffer(expect_full_response, decode_response)
            self._timestamp_last_rx = time.time()
        except KeyboardInterrupt:
            self.clear()
            raise

    def _make_cmd(
        self,
        cmd: str,
        mdac_comms_id: int | None,
        include_flush: bool | None = False,
    ) -> str:
        # Strip any leading "flush the command buffer" characters.
        while (len(cmd) > 0) and cmd[0] == "~":
            include_flush = True
            cmd = cmd[1:]

        daisychain_msg_timeout: str = ""
        if mdac_comms_id == _MDAC_COMMS_ID_AUTOFILL:
            mdac_comms_id = self._mdac_comms_id
        elif (mdac_comms_id is not None) and (mdac_comms_id != self._mdac_comms_id):
            # This cmd is addressed to _another_ MDAC in a daisychain, it may require a
            # daisychain messaging timeout.
            if cmd == "DAC:ALL:CONFIG ?":
                daisychain_msg_timeout = "5000:"
        if mdac_comms_id is not None:
            cmd = f"MDAC:{mdac_comms_id}:{daisychain_msg_timeout}" + cmd

        # Attempt to make each command uniquely distinguishable from the previous one.
        # This facilitates, for example, dealing with IO errors.
        if cmd.startswith(self._send_last_rxd_cmd) or cmd.startswith(self._repeat_last_txd_reply):
            # Nothing to do .. these commands are sent unmodified.
            _cmd = cmd
            # self._expect_sequence_num = False
        elif self._check_fw_feature(FirmwareFeature.CmdSeqNum):
            # Alternate between command sequence numbers 0 and 1.
            if self._write_sequence_num == 1:
                self._write_sequence_num = 0
                _cmd = "0|" + cmd
            else:
                self._write_sequence_num = 1
                _cmd = "1|" + cmd
            self._expect_sequence_num = True
        elif self._check_fw_feature(FirmwareFeature.MdacAddr):
            # Alternate between sending commands with and without the 'MDAC:<MDAC_ID_NONE>:' prefix.
            if self._write_sequence_num == 1:
                self._write_sequence_num = 0
                _cmd = cmd
            else:
                if cmd[0] == "/":
                    # Move the '/' to the beginning of the command.
                    _cmd = f"/MDAC:0:{cmd[1:]}"
                else:
                    _cmd = f"MDAC:0:{cmd}"
                self._write_sequence_num = 1
        else:
            _cmd = cmd

        if self._use_checksums:
            CS = calculate_checksum(_cmd)
            _cmd += format(CS, "04X")

        if include_flush:
            _cmd = "~" + _cmd

        return _cmd

    def _discover_which_cmd_to_retry(
        self, cmd: str, _cmd: str, mdac_comms_id: int | None = None
    ) -> str:
        attempts = 0
        attempts_remaining = 20  # A somewhat arbitrary number > 1.
        success = False

        if cmd.endswith("?"):
            # There is one ? command that the MDAC is incapable of completely buffering
            # the reply .. that's "DAC:ALL:CONFIG ?" (this will possibly be fixed in a
            # future firmware release).
            # However, it's actually safe to simply resend *any* query commands.
            self._mdac._close_open()
            self._handle_warning(f"Resending the {cmd} command")
            success = True

        while (not success) and (attempts_remaining > 0):
            attempts_remaining -= 1
            attempts += 1
            try:
                self._mdac._close_open()
                # Ask the MDAC what the last command it received was so we can decide
                # whether to resend the command or just ask the MDAC to resend the reply.
                self._expect_sequence_num = False
                self._handle_warning("Attempting to discover what command to resend")
                self._write_cmd_and_buffer_reply(
                    self._make_cmd(self._send_last_rxd_cmd, mdac_comms_id=None, include_flush=True),
                    mdac_comms_id=None,
                    expect_full_response=False,
                )
                last_cmd = self._read(decode_response=False)
                # Remove the <> wrapper.
                if len(last_cmd) >= 2:
                    last_cmd = last_cmd[1:][:-1]
                # Remove any sequence numbering prefix.
                if "|" in last_cmd:
                    last_cmd = last_cmd.split("|", 1)[1]

                if last_cmd == cmd:
                    # Ask the MDAC to repeat the answer.
                    warn_str = "Asking the MDAC to repeat the answer"
                    _cmd = self._make_cmd(self._repeat_last_txd_reply, mdac_comms_id)
                    success = True
                else:
                    warn_str = "Resending the command"
                    success = True

                warn_str += f" (cmd: {cmd})"
                self._handle_warning(warn_str)

            except ChecksumError:
                if attempts_remaining > 0:
                    self._handle_warning("Checksum error seen in response from MDAC")
                else:
                    raise

            except MsgRelayFailure:
                if attempts_remaining > 0:
                    self._handle_warning("Message relay failure indicated in response from MDAC")
                else:
                    raise

            except ReadTimeoutError as e:
                if attempts_remaining > 0:
                    self._handle_warning(f"Read timeout error seen: {e.args}")
                    time.sleep(1)
                else:
                    raise

        if success:
            self._max_discover_which_cmd_to_retry_attempts = max(
                attempts, self._max_discover_which_cmd_to_retry_attempts
            )
        else:
            self._errors_are_fatal = not self._debug
            self._handle_error("Could not recover from error condition")

        return _cmd

    def _write(
        self,
        cmd: str,
        mdac_comms_id: int | None = None,
        decode_response: bool = True,
        decorate_cmd: bool = True,
        force_direct_communication: bool = False,
    ) -> None:
        if force_direct_communication:
            mdac_comms_id = None
        elif mdac_comms_id == _MDAC_COMMS_ID_AUTOFILL:
            mdac_comms_id = self._mdac_comms_id

        if (
            (self._daisychain is not None)
            and (self._daisychain_state != DaisyChainState.Supervisor)
            and (self._daisychain_state != DaisyChainState.StandAlone)
            and (not force_direct_communication)
        ):
            self._daisychain._write(self, cmd)
            self._last_mdac_status = self._daisychain._last_mdac_status
            self._last_mdac_reply = self._daisychain._last_mdac_reply
        else:
            # Strip any leading "flush the command buffer" characters
            send_flush_prefix = False
            while (len(cmd) > 0) and cmd[0] == "~":
                send_flush_prefix = True
                cmd = cmd[1:]

            attempts = 0
            if self._check_fw_feature(FirmwareFeature.CmdSeqNum) or self._check_fw_feature(
                FirmwareFeature.MdacAddr
            ):
                attempts_remaining = 3  # A somewhat arbitrary number > 1.
            else:
                # It's not possible to do any checking/retries.
                attempts_remaining = 1

            cs_attempts_remaining = 2  # A somewhat arbitrary number > 1.

            # We keep track of the original command we were asked to send as well
            # as the decorated command we actually send.  Note that they may be the same.
            if decorate_cmd:
                _cmd = self._make_cmd(cmd, mdac_comms_id, send_flush_prefix)
            else:
                _cmd = cmd

            while attempts_remaining > 0:
                attempts_remaining -= 1
                attempts += 1
                try:
                    self._write_cmd_and_buffer_reply(
                        _cmd,
                        mdac_comms_id=mdac_comms_id,
                        decode_response=decode_response,
                    )
                    self._max_write_attempts = max(attempts, self._max_write_attempts)
                    if (self._daisychain is not None) and (not force_direct_communication):
                        self._daisychain._last_mdac_status = self._last_mdac_status
                        self._daisychain._last_mdac_reply = self._last_mdac_reply
                    return

                except (SeqNumError, ChecksumError) as e:
                    if isinstance(e, SeqNumError):
                        warn_str = "SeqNumError error seen in response from MDAC"
                    else:  # ie: isinstance(e, ChecksumError)
                        warn_str = "Checksum error seen in response from MDAC"
                    cs_attempts_remaining -= 1
                    if cs_attempts_remaining > 0:
                        attempts_remaining += 1
                    else:
                        attempts_remaining = 0

                    if attempts_remaining > 0:
                        warn_str += ", asking the MDAC to repeat its response"
                        _cmd = self._make_cmd(self._repeat_last_txd_reply, mdac_comms_id=None)
                    else:
                        warn_str += ", retries exhausted"
                        self._last_mdac_reply = None
                    self._handle_warning(warn_str)

                except (ReadTimeoutError, MsgRelayFailure) as e:
                    if isinstance(e, ReadTimeoutError):
                        warn_str = f"Read timeout error seen: {e.args}"
                    else:  # ie: isinstance(e, MsgRelayFailure)
                        warn_str = "Message relay error indicated in response from MDAC"
                    if attempts_remaining > 0:
                        self._handle_warning(warn_str)
                        time.sleep(1)
                        self.clear()
                        _cmd = self._discover_which_cmd_to_retry(cmd, _cmd)
                    else:
                        self._handle_warning(warn_str + ", retries exhausted")
                        if isinstance(e, ReadTimeoutError):
                            raise

    def write(self, cmd: str) -> None:
        self._write(cmd, mdac_comms_id=_MDAC_COMMS_ID_AUTOFILL)

    def _read(self, decode_response: bool = True, force_direct_communication: bool = False) -> str:
        if (
            (self._daisychain is not None)
            and (self._daisychain_state != DaisyChainState.Supervisor)
            and (self._daisychain_state != DaisyChainState.StandAlone)
            and (not force_direct_communication)
        ):
            res = self._daisychain._read(decode_response)
        elif (self._last_mdac_reply is None) or (len(self._last_mdac_reply) == 0):
            self.log.debug("_last_mdac_reply is empty")
            res = ""
        else:
            res = self._last_mdac_reply
            if decode_response and "[ERR:0x" in res:
                self._error_decode(res)
                res = ""
        return res

    def read(self) -> str:
        raise NotImplementedError

    def _ask(
        self,
        cmd: str,
        mdac_comms_id: int | None = _MDAC_COMMS_ID_AUTOFILL,
        decode_response: bool = True,
        decorate_cmd: bool = True,
        force_direct_communication: bool = False,
    ) -> str:
        """
        write a command to the MDAC and return the response
        """
        # Info: mdac_comms_id's primary purpose is to support address resolution of MDACs within a daisychain.
        # There are three cases for mdac_comms_id:
        #   If mdac_comms_id is None, the MDAC:mdac_num command prefix is not installed
        #   If mdac_comms_id is _MDAC_COMMS_ID_AUTOFILL (ie: -1), the prefix is installed with the comms_id of the
        #   instantiated MDAC object (one object for each MDAC in a daisychain) whose method is being called.
        #   If mdac_comms_id is otherwise, the prefix is installed with whatever mdac_comms_id is in the parameter
        #   list. This is all tied in to the address resolution .. all commands are routed via the SUPERVISOR in
        #   a daisy-chain, and this parameter gets used to tell the SUPERVISOR where to route the command.
        # For example, (where mdac is an instantiated MDAC in a daisy-chain) mdac.ask(..) etc is relayed to
        # daisychain.ask(..) which almost always passes it to the SUPERVISOR of the daisychain.
        # In some circumstances the daisychain needs to speak directly to a specific MDAC in the chain, which is
        # where the force_direct_communication parameter comes into play.  When True, it stifles any rerouting,
        # forcing the command to be sent directly to the instantiated mdac rather than the daisychain.  This really only
        # becomes important for commands that behave differently when sent via a SUPERVISOR rather than directly
        # to a MINION.  Eg: the BUS OWNER query for a daisychain has a different meaning than for an individual MDAC.
        if force_direct_communication:
            mdac_comms_id = None
        elif mdac_comms_id == _MDAC_COMMS_ID_AUTOFILL:
            mdac_comms_id = self._mdac_comms_id
        try:
            self._write(
                cmd,
                mdac_comms_id,
                decode_response,
                decorate_cmd,
                force_direct_communication,
            )
            reply = self._read(decode_response, force_direct_communication)
        except KeyboardInterrupt:
            self.clear()
            raise
        return reply

    def ask(self, cmd: str) -> str:
        return self._ask(cmd, mdac_comms_id=_MDAC_COMMS_ID_AUTOFILL)

    def clear(self, timeout: float | None = 1) -> None:
        # Clears the read and write queues.
        if isinstance(self._mdac, serial_MDAC):
            self._mdac.reset_input_buffer()
            self._mdac.reset_output_buffer()
        else:
            flushing_input = True
            clear_start_time = time.time()
            while flushing_input:
                throwaway_data = self._mdac.read(timeout_Secs=0.125, failure_is_fatal=False)
                if throwaway_data == "":
                    flushing_input = False
                else:
                    self.log.debug("Flushed data: " + throwaway_data)
                if flushing_input and timeout is not None:
                    time_elapsed = time.time() - clear_start_time
                    if time_elapsed >= timeout:
                        raise RuntimeError("Timed out waiting for mdac.clear() to complete")

        self._last_mdac_reply = ""

    def check_channels_are_mine(
        self,
        channels: Sequence[MDACBasicChannel] | MDACBasicChannel,
        failure_is_fatal: bool | None = True,
    ) -> bool:
        ret_val: bool = True
        channels_list: Sequence[MDACBasicChannel]
        if isinstance(channels, MDACBasicChannel):
            channels_list = [channels]
        else:
            channels_list = channels
        for chan in channels_list:
            if chan.parent != self:
                ret_val = False
                error_str: str = f"Channel {chan} does not belong to me (ie: {self})."
                if chan.parent._daisychain is not None:
                    error_str += "\nDid you mean to reference another MDAC in the daisychain?"
                if failure_is_fatal:
                    raise RuntimeError(error_str)
                else:
                    self._handle_warning(error_str)
        return ret_val

    def wait_ramps(
        self,
        channels: Sequence[MDACBasicChannel] | MDACBasicChannel | None = None,
        timeout: float | None = None,
    ) -> bool:
        """
        Blocks until all channels in the list have finished ramping.
        Args:
            channels: The list of channels to check.  'None' equates to "all MDAC channels" (slow)
            timeout: The amount of time to wait in Seconds.  'None' equates to waiting indefinitely.
        Returns:
             True if all ramps have completed, otherwise False.
        """
        if channels is not None:
            self.check_channels_are_mine(channels)

        # This is the "by and large" get_waveforms() sweet spot ..
        # less than this, it's usually quicker to ask for individual channels.
        if self._check_fw_feature(FirmwareFeature.MapChannels):
            get_waveforms_sweetspot = 2
        else:
            get_waveforms_sweetspot = 10

        def get_waveforms() -> list[str]:
            waveforms = []
            if self._mdac_fw_ver is None:
                raise RuntimeError("Could not determine MDAC firmware version")

            if self._check_fw_feature(FirmwareFeature.MapChannels):
                dacs_preset_name = self._get_all_dacs_preset_name(include_DAC_0=False)
                for i in dacs_preset_name:
                    name = i[1]
                    waveforms.append(name)
            else:
                dacs_state = self._get_all_dacs_state()
                assert dacs_state is not None
                # Remove entry for DAC:0
                x = dacs_state[1:]

                for j in x:
                    name = j[5][0]
                    waveforms.append(name)
            return waveforms

        channels_list: list[MDACBasicChannel]
        if channels is None:
            channels_list = []
            for ch in self.channels:
                channels_list.append(ch)

        elif isinstance(channels, MDACBasicChannel):
            channels_list = [channels]
        else:
            channels_list = list(channels)

        if self._sanity_check_ramps:
            ramps_stalled = self.arb_engine() == "off"
        else:
            ramps_stalled = False
        if ramps_stalled:
            # Set an immediate timeout as no amount of waiting will see a change in state.
            timeout = 0
            if get_waveforms().count("ramp") > 0:
                error_str = "WARNING: arb_engine() is OFF, voltage ramps will never complete"
                self._handle_warning(error_str)

        wait_start_time = time.time()

        def timed_out() -> bool:
            if timeout is None:
                ret = False
            elif (time.time() - wait_start_time) >= timeout:
                ret = True
            else:
                ret = False
            return ret

        stable_channels = []

        test_channels = channels_list.copy()
        done_waiting = len(test_channels) < get_waveforms_sweetspot
        while not done_waiting:
            waveforms = get_waveforms()
            for channel in test_channels:
                # Only check if it hasn't already been eliminated.
                if channel in channels_list:
                    ch_index = self._chan_range.index(channel.channel_number())
                    if "ramp" not in waveforms[ch_index]:
                        channels_list.remove(channel)
                        stable_channels.append(channel)
            if (len(channels_list) < get_waveforms_sweetspot) or timed_out():
                done_waiting = True

        test_channels = channels_list.copy()
        done_waiting = len(test_channels) == 0
        while not done_waiting:
            for channel in test_channels:
                # Only check if it hasn't already been eliminated.
                if channel in channels_list:
                    waveform = channel._get_preset_name()
                    if "ramp" not in waveform:
                        channels_list.remove(channel)
                        stable_channels.append(channel)
            if (len(channels_list) == 0) or timed_out():
                done_waiting = True

        num_active_ramps = len(channels_list)
        if (num_active_ramps > 0) and (not ramps_stalled):
            # If ramps are stalled (ie: arb_engine() is off), a warning will already have been generated.
            if num_active_ramps > 1:
                error_str = f"Timed out - {num_active_ramps} voltage ramps still active"
            else:
                error_str = "Timed out - voltage ramp still active"
            self._handle_error(error_str)

        for chan in stable_channels:
            # We assume that all of the channels that were in the initial request had ramps
            # configured.  Update the known voltage state now that all ramps are complete.
            chan.voltage()

        return num_active_ramps == 0

    def _card_voltages(self) -> float:
        voltages = self._ask("MASTER:VOLTAGES ?").split(",")
        if voltages[0] == "N/A":
            v5 = float(voltages[2])
        else:
            v5 = float(voltages[3])
        return v5

    def _card_voltages_raw(self) -> int:
        voltages = self._ask("MASTER:VOLTAGES ?").split(",")
        if voltages[0] == "N/A":
            v5 = int(voltages[1], 16)
        else:
            v5 = int(voltages[2], 16)
        return v5

    def _supply_voltages(self) -> tuple[float, float, float, float]:
        # Returns the measured system voltages (5, 3.3, +12, -12).
        n = 0
        v1 = 0
        v2 = 0
        v3 = 0
        for i in range(0, len(self.channels), 8):
            r = self.channels[i]._card_voltages()
            v1 += r[0]
            v2 += r[1]
            v3 += r[2]
            n += 1
        v1_avg = round(v1 / n, 2)
        v2_avg = round(v2 / n, 2)
        v3_avg = round(v3 / n, 2)
        v0 = round(self._card_voltages(), 2)
        return v0, v1_avg, v2_avg, v3_avg

    def _power_good(self) -> bool:
        r = self._supply_voltages()
        if r[0] < 4:  # 5V less than 4
            return False
        if r[0] > 6:  # 5V greater than 6
            return False
        if r[1] < 3:  # 3.3V less than 3
            return False
        if r[1] > 4:  # 3.3V greater than 4
            return False
        if r[2] < 8:  # +12V less than 8
            return False
        if r[2] > 16:  # +12V greater than 16
            return False
        if r[3] > -8:  # -12 more than -8
            return False
        if r[3] < -16:  # -12 lessthan -16
            return False
        return True  # all voltages within 'safe' range

    def get_idn(self) -> dict[str, str | None]:
        """generates the ``*IDN`` dictionary for qcodes"""
        reply = self._ask("MASTER:VER ?").split(",")
        HW = reply[1]
        FW = reply[2]
        FW_ver = Version(FW)
        build_date_str = reply[3].strip()
        SN = self._ask("?H").splitlines()[0]
        SN = SN.split(",")[1].strip()

        if FW_ver >= Version("1.5"):
            mdac_variant_str = self._ask("?VV")
            if "MDAC8" in mdac_variant_str:
                self._mdac_variant = MdacVariant.Mdac8
            elif "MDAC24" in mdac_variant_str:
                self._mdac_variant = MdacVariant.Mdac24
            else:
                self._mdac_variant = MdacVariant.Mdac64
        else:
            # pre v1.5 firmware was only ever installed on MDAC1 units
            self._mdac_variant = MdacVariant.Mdac64
        id_dict: dict[str, str | None] = {
            "firmware": FW,
            "model": self._mdac_variant.value,
            "serial": SN,
            "vendor": "QNL Sydney",
            "hardware_version": HW,
            "build_date": build_date_str,
        }
        return id_dict

    def _extract_error_code(self, error_string: str) -> str:
        # Decodes error numbers returned by the MDAC.
        if error_string.find("[OK]") != -1:
            error_code = "0x0000"
        else:
            i0 = error_string.find("[ERR:")
            i1 = error_string.find("]")
            if not (i0 == -1 or i1 == -1):
                error_code = error_string[i0 + 5 : i1]
            else:
                error_code = error_string
        return error_code

    def _error_decode(self, error_string: str) -> None:
        # Decodes error numbers returned by the MDAC.
        error_code = self._extract_error_code(error_string)

        if error_code in self._warn_dict:
            error_str = (
                f"WARNING - cmd({self._last_mdac_cmd}): "
                f"{error_code}, ({self._warn_dict.get(error_code)})"
            )
            self._handle_warning(error_str)
        else:
            error_str = (
                f"ERROR - cmd({self._last_mdac_cmd}):{error_code}, "
                f"({self._error_dict.get(error_code)})"
            )
            self._handle_error(error_str)

    def _test_card_present(self) -> bool:
        r = self._ask("MASTER:TEST_CARD ?")
        return r == "1"

    def _test_card_vsense_calibrate(self, channel: MDACBasicChannel) -> None:
        """
        Calibrate the TEST_VCARD's VSENSE capability against the specified channel
        Note: The specified channel should be 'known to be good'
        """
        self.check_channels_are_mine(channel)
        if self._test_card_present():
            cmd = f"DAC:{channel.channel_number()}:VSENSE CALIBRATE"
            self._write(cmd)
        else:
            self._handle_error("Operation failed: The TEST CARD is not installed")

    def _test_card_isense_calibrate(self, channel: MDACBasicChannel) -> None:
        """
        Calibrate the TEST_VCARD's ISENSE capability against the specified channel
        Note: The specified channel should be 'known to be good'.
        """
        self.check_channels_are_mine(channel)
        if self._test_card_present():
            cmd = f"DAC:{channel.channel_number()}:ISENSE CALIBRATE"
            self._write(cmd)
        else:
            self._handle_error("Operation failed: The TEST CARD is not installed")

    def _active_cards(self) -> list[int]:
        cards = []
        if self._check_fw_feature(FirmwareFeature.SlavesStatus):
            reply = self._ask("/SLAVE:ALL:STATUS ?")
        else:
            reply = self._ask("/POWER ?")

        lines = reply.splitlines()
        for line in lines:
            if "SLAVE" in line:
                card_num = int(line.split(" - ")[0][-1:])
                installed = "installed: TRUE" in line
                if installed:
                    cards.append(card_num)
        return cards

    def _store_calibrations(self) -> None:
        for card in self._active_cards():
            self._write(f"SLAVE:{card}:CALIBRATION STORE")
        self._write("DAC:0:CALIBRATION STORE")
        self._write("MASTER:TEST_CARD:CALIBRATION STORE")

    def _active_channels(self) -> list[int]:
        """
        Returns the list of actively accessible channels in the chassis
        Note: it does NOT include DAC 0 (the isense channel)
        """
        channel_range_list = []
        # Note: Only MDACs that support MapChannels are capable of skipping channel 13
        if self._check_fw_feature(FirmwareFeature.MapChannels):
            reply = self._ask("DAC:ALL:MAP ?")
            lines = reply.splitlines()
            self._dac_map_dict = {}
            # See if channel 13 is skipped.
            self._chan13_skipped = True
            for dac_line in lines:
                dac_line_items = dac_line.split(",")
                chassis_dac_num = int(dac_line_items[0])
                slave_num = int(dac_line_items[1])
                slave_dac_num = int(dac_line_items[2])
                self._dac_map_dict[f"{slave_num},{slave_dac_num}"] = chassis_dac_num
                if chassis_dac_num != 0:
                    channel_range_list.append(chassis_dac_num)
                if chassis_dac_num == 13:
                    self._chan13_skipped = False
        else:
            cards = self._active_cards()
            for i in cards:
                for j in range(1, 9):
                    x = ((i - 1) * 8) + j
                    channel_range_list.append(x)
        return channel_range_list

    def _get_sample_rate(self) -> float:
        x = self._ask("?S")
        ls = x.split(",")
        return float(ls[0])

    def _get_dacs_state_raw(
        self, channels: Literal["ALL"] | Sequence[MDACBasicChannel]
    ) -> list[str]:
        """
        Get the configuration/status data for one or more DAC channels.
        Args:
            channels: a list of MDAC channels or 'ALL' (for all channels).
        """
        if isinstance(channels, str) and channels != "ALL":
            raise RuntimeError(
                "channels should be a Sequence of MDACBasicChannels "
                f"or the string 'ALL'. Got {channels} which is invalid."
            )

        if channels == "ALL" and self._check_fw_feature(FirmwareFeature.DacsConfig):
            response = self._ask("DAC:ALL:CONFIG ?").splitlines()
        else:
            if channels == "ALL":
                if self._mdac_variant == MdacVariant.Mdac8:
                    channels = []
                else:
                    channels = [self.ch_isense]
                channels.extend(self.channels)

            response = []
            for ch in channels:
                dac_config = self._ask(f"DAC:{ch._channum}:CONFIG ?")
                assert self._mdac_fw_ver is not None
                if self._mdac_fw_ver < __v1_9__:
                    # remove card-centric DAC number and install chassis-centric number
                    first_comma = dac_config.find(",")
                    dac_config = f"{ch._channum}" + dac_config[first_comma:]
                response.extend(dac_config.splitlines())

        return response

    def _get_dacs_preset_raw(self, channels: Literal["ALL"] | Sequence[MDACBasicChannel]) -> str:
        """
        Get the preset data for one or more DAC channels.
        Args:
            channels: a list of MDAC channels or 'ALL' (for all channels).
        """
        if channels == "ALL":
            response = self._ask("DAC:ALL:PRESET ?")
        else:
            self.check_channels_are_mine(channels)

            # Build a multi-line set of responses from each channel.  Separate them with
            # '\n' characters but make sure there is not an extra '\n' after the last
            # channel's response.
            # Do this by pre-pending each response *after* the first one with a '\n'
            response = ""
            line_prefix = ""
            for channel in channels:
                response += line_prefix
                response += self._ask(f"DAC:{channel._channum}:PRESET ?")
                line_prefix = "\n"
        return response

    def _parse_dacs_state(
        self,
        channels: Literal["ALL"] | Sequence[MDACChannel],
        config: Sequence[str],
    ) -> list[DacParseType] | None:
        """
        Helper function to get the active preset
        Returns a list of up to 65 items for dac_0 .. dac_64
        Each channel's list entry consists of:
          [0] The channel's number.
          [1,2] The current output in volts, dac units.
          [3] Whether the current output (ie: [1,2] above) has been limited.
          [4] Whether the MDAC will need to limit the output voltage.
          [5] A list describing the current waveform ("wave[]"):
          - wave[0] is the name.  If 'off', the list stops here.
          - wave[1,2] is the period in seconds, ticks.
          - wave[3,4] is the offset/ramp_start in volts, dac units.
          - wave[5,6] is the amplitude/ramp_destination in volts, dac units.
          - wave[7] is the number of shots.
          - wave[8,9] is the phase offset in seconds, ticks.
          [6] Describes the current relays (None for dac 0):
          - if not None, [main, smc, bus, term, f1, bypass, dac_output, divider]
        """
        if channels == "ALL":
            if self._mdac_variant != MdacVariant.Mdac8:
                channels = [self.ch_isense]
            else:
                channels = []
            channels.extend(self.channels)
        else:
            self.check_channels_are_mine(channels)

        num_lines = len(config)
        if num_lines > 0:
            dacs_state: list[DacParseType] | None = []
        else:
            dacs_state = None

        chan_index = 0
        chan_line_index = 0  # Ignore the local DAC number.
        while chan_line_index < num_lines:
            dac_line = config[chan_line_index].strip().split(",")

            # Start with channel number.
            if self._dac_map_dict is not None:
                channum = int(dac_line[0])
            else:
                channum = channels[chan_index]._channum

            # Add the output_volts, output_dac_raw, limited.
            output_volts, output_volts_raw, has_been_limited = dac_line[1:4]
            limited = dac_line[8]

            # Add preset data.
            chan_line_index += 1
            preset_line = config[chan_line_index].split(",")
            if preset_line[0] == "NONE":
                preset_config = ["off"]
            else:
                preset_config = preset_line[0:10]
                preset_config[0] = preset_config[0].lower()

            # Finish with relays data.
            chan_line_index += 1
            if chan_line_index < num_lines:
                relays_line = config[chan_line_index].split(",")
                relays_config_strs = relays_line[0:8]
                if ("TRUE" in relays_config_strs) or ("FALSE" in relays_config_strs):
                    # There was no relays data, this is actually data for
                    # the next channel.
                    relays_config: list[str] | None = None
                else:
                    relays_config = relays_config_strs
                    chan_line_index += 1
            else:
                relays_config = None

            dac_state = (
                channum,
                output_volts,
                output_volts_raw,
                has_been_limited,
                limited,
                preset_config,
                relays_config,
            )

            assert dacs_state is not None
            dacs_state.append(dac_state)

            chan_index += 1

        return dacs_state

    def _parse_dacs_preset(
        self,
        channels: Literal["ALL"] | Sequence[MDACChannel],
        config: Sequence[str],
    ) -> list[DacPresetType] | None:
        """
        Helper function to get the active preset
        Returns a list of up to 65 items for dac_0 .. dac_64
        Each channel's list entry consists of:
          [0] The channel's number.
          [1] A list describing the current waveform ("wave[]"):
          - wave[0] is the name.  If 'off', the list stops here.
          - wave[1,2] is the period in seconds, ticks.
          - wave[3,4] is the offset/ramp_start in volts, dac units.
          - wave[5,6] is the amplitude/ramp_destination in volts, dac units.
          - wave[7] is the number of shots.
          - wave[8,9] is the phase offset in seconds, ticks.
        """
        if channels == "ALL":
            if self._mdac_variant != MdacVariant.Mdac8:
                channels = [self.ch_isense]
            else:
                channels = []
            channels.extend(self.channels)
        else:
            self.check_channels_are_mine(channels)

        num_lines = len(config)
        if num_lines > 0:
            dacs_preset: list[DacPresetType] | None = []
        else:
            dacs_preset = None

        chan_index = 0
        chan_line_index = 0
        while chan_line_index < num_lines:
            preset_line = config[chan_line_index].split(",")
            if preset_line[0] == "NONE":
                preset_config = ["off"]
            else:
                preset_config = preset_line[0:10]
                preset_config[0] = preset_config[0].lower()

            dac_preset = (channels[chan_index]._channum, preset_config)
            assert dacs_preset is not None
            dacs_preset.append(dac_preset)

            chan_line_index += 1
            chan_index += 1

        return dacs_preset

    def _get_dacs_state(
        self, channels: Literal["ALL"] | Sequence[MDACChannel]
    ) -> list[DacParseType] | None:
        if channels != "ALL":
            self.check_channels_are_mine(channels)
        state_raw = self._get_dacs_state_raw(channels)
        dacs_state = self._parse_dacs_state(channels, state_raw)
        return dacs_state

    def _get_all_dacs_state(self) -> list[DacParseType] | None:
        all_dacs_state = self._get_dacs_state("ALL")
        return all_dacs_state

    def _get_dacs_preset(
        self, channels: Literal["ALL"] | Sequence[MDACChannel]
    ) -> list[DacPresetType] | None:
        if channels != "ALL":
            self.check_channels_are_mine(channels)
        preset_raw = self._get_dacs_preset_raw(channels)
        dacs_preset = self._parse_dacs_preset(channels, preset_raw.splitlines())
        return dacs_preset

    def _get_all_dacs_preset(self) -> list[DacPresetType] | None:
        all_dacs_preset = self._get_dacs_preset("ALL")
        return all_dacs_preset

    def _get_all_dacs_preset_name(self, include_DAC_0: bool = True) -> list[tuple[int, str]]:
        names = []
        lines = []
        if include_DAC_0:
            DAC_0_preset = self._ask("DAC:0:PRESET ?N").split(",")
            preset_entry = (0, DAC_0_preset[0].lower())
            names.append(preset_entry)
        # Sometimes this results in timeouts waiting for SLAVE cards to respond.
        ask_status = True
        while ask_status:
            lines = self._ask("SLAVE:ALL:STATUS ?PN").splitlines()
            if (self._last_mdac_status is not None) and (int(self._last_mdac_status, 16) == 0x2C):
                # SLAVE reply timeout ..
                time.sleep(1)
            else:
                ask_status = False

        ignore_card = False
        card_num = 0
        card_dac_num = 0
        for line in lines:
            line_items = line.split(",")
            if (line_items[0] == "FALSE") or (line_items[0] == "TRUE"):
                # This is a card powered/present line.
                card_num += 1
                card_dac_num = 0
                ignore_card = (line_items[0] == "FALSE") or (line_items[1] == "FALSE")
            elif (self._dac_map_dict is not None) and (not ignore_card):
                card_dac_num += 1
                chassis_dac_num = self._dac_map_dict[f"{card_num},{card_dac_num}"]
                preset_entry = (chassis_dac_num, line.lower())
                names.append(preset_entry)
        names.sort()
        return names

    def _update_mdac_comms_id(self) -> None:
        if self._check_fw_feature(FirmwareFeature.DaisyChainSupported):
            # As the daisychain supervisor uses the _mdac_comms_id to communicate with the target mdac, we need
            # to force this command to go directly to this mdac, rather than via the supervisor.
            mdac_num_str = self._ask("MASTER:MDAC_NUM ?", force_direct_communication=True)
            self._mdac_comms_id = int(mdac_num_str)
        else:
            self._mdac_comms_id = None

    def _update_daisychain_position(self) -> None:
        self._update_mdac_comms_id()
        state: DaisyChainState
        if self._check_fw_feature(FirmwareFeature.DaisyChainSupported):
            state_str = self._ask("COMBO:MODE ?", force_direct_communication=True)
            if "BOSS" in state_str:
                state = DaisyChainState.Supervisor
            elif "MINION_PENDING" in state_str:
                state = DaisyChainState.MinionPending
            elif "MINION" in state_str:
                state = DaisyChainState.Minion
            else:
                state = DaisyChainState.StandAlone
        else:
            state = DaisyChainState.StandAlone
        self._daisychain_state = state

    def _get_daisychain_position(
        self, update: bool = True
    ) -> tuple[int | None, DaisyChainState | None]:
        if update:
            self._update_daisychain_position()
        return self._mdac_comms_id, self._daisychain_state

    def _register_daisychain(self, daisychain: "DaisyChain") -> None:
        if not isinstance(daisychain, DaisyChain):
            raise RuntimeError("'daisychain' is not a DaisyChain object.")
        if (self._daisychain_state != DaisyChainState.Minion) and (
            self._daisychain_state != DaisyChainState.Supervisor
        ):
            raise RuntimeError("MDAC is not configured as a daisychain supervisor or minion.")
        else:
            if (self._daisychain is not None) and (self._daisychain != daisychain):
                self._handle_warning("Replacing previously registered daisychain.")
            self._daisychain = daisychain

    def _deregister_daisychain(self) -> None:
        if self._daisychain is None:
            self._handle_warning("No daisychain registered.")
        self._daisychain = None

    def _set_errors_are_fatal(self, fatal: bool) -> None:
        self._errors_are_fatal = fatal

    def _get_errors_are_fatal(self) -> bool:
        return self._errors_are_fatal

    def _handle_warning(self, description: str) -> None:
        warnings.warn_explicit(description, UserWarning, "MDAC", 0)

    def _handle_error(self, description: str) -> NoReturn:
        raise RuntimeError(f"<<< Unrecoverable ERROR >>> {description}")
        # if self._errors_are_fatal:
        #     raise RuntimeError(f"<<< Unrecoverable ERROR >>> {description}")
        # else:
        #     self._handle_warning(description)

    @property
    def daisychain_state(self) -> DaisyChainState | None:
        return self._daisychain_state
