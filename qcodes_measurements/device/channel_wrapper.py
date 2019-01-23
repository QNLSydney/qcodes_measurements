from qcodes import InstrumentChannel

from .states import ConnState

class ChannelWrapper(InstrumentChannel):
    """
    Channel wrapper around a gate or ohmic, allowing access of some of the underlying
    states as parameters.

    Note: The accesses for various attributes can be confusing here:
        - self.gate - The underlying Gate object
        - self.parent - The underlying DAC/BB channel
    """
    def __init__(self, parent, name):
        super().__init__(parent.source, name)
        self.gate = parent
        self._state = ConnState.UNDEF
        self.add_parameter('state',
                           get_cmd=self.get_state,
                           set_cmd=self.set_state)

    def get_state(self):
        return self._state

    def set_state(self, val):
        if val == ConnState.GND:
            print(f"Manually Ground {self.name}")
            self._state = ConnState.GND
        elif val == ConnState.BUS:
            print(f"Manually Bus {self.name}")
            self._state = ConnState.BUS
        elif val == ConnState.SMC:
            print(f'Manually Open {self.name}')
            self._state = ConnState.SMC
        elif val == ConnState.DAC:
            print(f"Manually Connect DAC to {self.name}")
            self._state = ConnState.DAC
        elif val == ConnState.FLOAT:
            print(f"Manually Float {self.name}")
            self._state = ConnState.FLOAT
        elif val == ConnState.PROBE:
            raise ValueError("Probe doesn't make sense except on a DAC")
        else:
            raise ValueError(f"Not sure how to set state {val} on this channel")

    def ground(self):
        self.state(ConnState.GND)

    def bus(self):
        self.state(ConnState.BUS)

    def open(self):
        self.state(ConnState.FLOAT)

    def smc(self):
        self.state(ConnState.SMC)

    def dac(self):
        self.state(ConnState.DAC)

    def probe(self):
        self.state(ConnState.PROBE)
