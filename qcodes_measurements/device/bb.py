from warnings import warn

from qcodes import InstrumentChannel, ChannelList
from qcodes.instrument.base import InstrumentBase

class BB(InstrumentBase):
    """
    Breakout box instance, makes a channel list out of all
    channels on the breakout box, keeps track of numbering
    for snapshot purposes
    """
    def __init__(self, name, chan_count=25):
        super().__init__(name)

        self.chan_count = chan_count

        channels = ChannelList(self, "channels", BBChan)
        for i in range(chan_count):
            channel = BBChan(self, f"ch{i+1:02}")
            channels.append(channel)
            self.add_submodule(f"ch{i+1:02}", channel)
        channels.lock()
        self.add_submodule("channels", channels)


class BBChan(InstrumentChannel):
    def __init__(self, parent, name, dac_source=None):
        super().__init__(parent, name)
        self.dac_source = dac_source

        self.add_parameter("voltage",
                           get_cmd=self.dummy_voltage,
                           set_cmd=self.dummy_voltage)

    def __getattr__(self, name):
        if name in self.parameters:
            return self.parameters[name]
        elif self.dac_source:
            return getattr(self.dac_source, name)
        raise AttributeError()

    def connect_dac(self, dac_source):
        self.dac_source = dac_source
        if 'voltage' in self.parameters:
            del self.parameters['voltage']
        return self

    def dummy_voltage(self, val=None):
        if val is not None and val != 0:
            raise NotImplementedError("This gate is not connected to a DAC!")
        return 0

class BB37(BB):
    def __init__(self, name):
        super().__init__(name, chan_count=37)
        warn("Use BB with chan_count=37 instead", DeprecationWarning)


class BB37Chan(BBChan):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        warn("Use BBChan instead...", DeprecationWarning)
