import enum

class GateMode(str, enum.Enum):
    BIAS = enum.auto()
    COLD = enum.auto()
    FREE = enum.auto()


class ConnState(str, enum.Enum):
    BUS = enum.auto()
    GND = enum.auto()
    DAC = enum.auto()
    SMC = enum.auto()
    FLOAT = enum.auto()
    PROBE = enum.auto()
    UNDEF = enum.auto()


class DigitalMode(str, enum.Enum):
    """
    Analog to ConnState with states for digital logic

    Note: HIGH/LOW/GND cause the gate value to be locked, and will not
    allow changes through a set
    """
    IN = enum.auto() # Connect SMC, Disconnect DAC
    OUT = enum.auto() # Disconnect SMC, Connect DAC
    PROBE_OUT = enum.auto() # Connect SMC, Connect DAC
    HIGH = enum.auto()
    LOW = enum.auto()
    GND = enum.auto()

DigitalMode.OUTPUT_MODES = (DigitalMode.OUT, DigitalMode.PROBE_OUT, DigitalMode.HIGH,
                            DigitalMode.LOW)
DigitalMode.INPUT_MODES = (DigitalMode.IN, DigitalMode.GND)
