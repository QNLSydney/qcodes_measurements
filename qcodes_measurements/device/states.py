import enum

class GateMode(str, enum.Enum):
    BIAS = enum.auto()
    COLD = enum.auto()
    FREE = enum.auto()


class ConnState(str, enum.Enum):
    BUS = enum.auto()
    GND = enum.auto()
    DAC = enum.auto()
    DAC_BUS = enum.auto()
    SMC = enum.auto()
    FLOAT = enum.auto()
    PROBE = enum.auto()
    UNDEF = enum.auto()

ConnState.OUTPUT_MODES = (ConnState.GND, ConnState.DAC, ConnState.DAC_BUS, ConnState.PROBE)
ConnState.INPUT_MODES  = (ConnState.BUS, ConnState.SMC, ConnState.FLOAT)

class DigitalMode(str, enum.Enum):
    """
    Analog to ConnState with states for digital logic

    Note: HIGH/LOW/GND cause the gate value to be locked, and will not
    allow changes through a set
    """
    IN = enum.auto() # Connect SMC, Disconnect DAC
    OUT = enum.auto() # Disconnect SMC, Connect DAC
    PROBE_OUT = enum.auto() # Connect SMC, Connect DAC
    BUS_OUT = enum.auto()
    HIGH = enum.auto()
    LOW = enum.auto()
    GND = enum.auto()
    FLOAT = enum.auto()
    UNDEF = enum.auto()

    @staticmethod
    def map_conn_state_to_digital_mode(conn_state):
        """
        Convert ConnState to DigitalMode
        """
        if conn_state == ConnState.BUS:
            return DigitalMode.IN
        elif conn_state == ConnState.GND:
            return DigitalMode.GND
        elif conn_state == ConnState.DAC:
            return DigitalMode.OUT
        elif conn_state == ConnState.DAC_BUS:
            return DigitalMode.BUS_OUT
        elif conn_state == ConnState.SMC:
            return DigitalMode.OUT
        elif conn_state == ConnState.FLOAT:
            return DigitalMode.FLOAT
        elif conn_state == ConnState.PROBE:
            return DigitalMode.PROBE_OUT
        else:
            return DigitalMode.UNDEF

DigitalMode.OUTPUT_MODES = (DigitalMode.OUT, DigitalMode.PROBE_OUT, DigitalMode.HIGH,
                            DigitalMode.LOW, DigitalMode.GND)
DigitalMode.INPUT_MODES = (DigitalMode.IN, DigitalMode.FLOAT)
