from typing import Any, Unpack
from warnings import warn

from qcodes.instrument import ChannelList, Instrument, InstrumentChannel
from qcodes.instrument.instrument_base import InstrumentBaseKWArgs


class BB(Instrument):
    """
    Breakout box instance, makes a channel list out of all
    channels on the breakout box, keeps track of numbering
    for snapshot purposes
    """

    def __init__(self, name: str, chan_count: int = 25, **kwargs: Unpack[InstrumentBaseKWArgs]):
        super().__init__(name, **kwargs)

        self.chan_count = chan_count

        channels = ChannelList(self, "channels", BBChan)
        for i in range(chan_count):
            channel = BBChan(self, f"ch{i + 1:02}")
            channels.append(channel)
            self.add_submodule(f"ch{i + 1:02}", channel)
        channels.lock()
        self.add_submodule("channels", channels)

    def get_idn(self) -> dict[str, str | None]:
        return dict(
            vendor="qcodes_measurements", model=type(self).__name__, serial=None, firmware=None
        )


class BBChan(InstrumentChannel):
    def __init__(self, parent, name, dac_source=None):
        super().__init__(parent, name)
        self.dac_source = dac_source

        self.add_parameter("voltage", get_cmd=self.dummy_voltage, set_cmd=self.dummy_voltage)

    def __getattr__(self, key: str) -> Any:
        try:
            return super().__getattr__(key)
        except AttributeError:
            source = self.__dict__.get("dac_source")
            if source is not None:
                return getattr(source, key)
            raise

    def connect_dac(self, dac_source):
        self.dac_source = dac_source
        if "voltage" in self.parameters:
            self.remove_parameter("voltage")
        return self

    def dummy_voltage(self, val=None):
        if val is not None and val != 0:
            raise NotImplementedError("This gate is not connected to a DAC!")
        return 0


class BB37(BB):
    def __init__(self, name: str, **kwargs: Unpack[InstrumentBaseKWArgs]):
        super().__init__(name, chan_count=37, **kwargs)
        warn("Use BB with chan_count=37 instead", DeprecationWarning, stacklevel=2)


class BB37Chan(BBChan):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        warn("Use BBChan instead...", DeprecationWarning, stacklevel=2)
