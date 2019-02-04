from .channel_wrapper import ChannelWrapper
from .states import ConnState

class MDACWrapper(ChannelWrapper):
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
                state = ConnState.FLOAT
        return state

    def set_state(self, val):
        if val in ConnState.INPUT_MODES:
            self.parent.voltage(0)
        if val == ConnState.GND:
            self.parent.gnd('close')
            self.parent.smc('open')
            self.parent.bus('open')
            self.parent.dac_output('open')
        elif val == ConnState.BUS:
            self.parent.bus('close')
            self.parent.smc('open')
            self.parent.gnd('open')
            self.parent.dac_output('open')
        elif val == ConnState.DAC:
            self.parent.dac_output('close')
            self.parent.bus('open')
            self.parent.smc('open')
            self.parent.gnd('open')
        elif val == ConnState.SMC:
            self.parent.smc('close')
            self.parent.dac_output('open')
            self.parent.bus('open')
            self.parent.gnd('open')
        elif val == ConnState.PROBE:
            self.parent.dac_output('close')
            self.parent.smc('close')
            self.parent.bus('open')
            self.parent.gnd('open')
        elif val == ConnState.FLOAT:
            self.parent.dac_output('open')
            self.parent.smc('open')
            self.parent.bus('open')
            self.parent.gnd('open')
        elif val == ConnState.DAC_BUS:
            self.parent.bus('close')
            self.parent.dac_output('close')
            self.parent.smc('open')
            self.parent.gnd('open')
        else:
            super().set_state(val)

    def ground(self):
        self.state(ConnState.GND)

    def bus(self):
        self.state(ConnState.BUS)

    def dac(self):
        self.state(ConnState.DAC)

    def open(self):
        self.state(ConnState.FLOAT)

    def smc(self):
        self.state(ConnState.SMC)

    def probe(self):
        self.state(ConnState.PROBE)
