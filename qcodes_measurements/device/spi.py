"""
SPI controller for a digital device
"""
import time

from qcodes import InstrumentChannel
from qcodes.utils.validators import Numbers, Enum

from .digital import DigitalGate

class SPIController(InstrumentChannel):
    """
    Bitbang'ed SPI controller for a digital device.
    Expects a set of digital gates for mosi, miso, sclk, ss
    and a clock speed.

    If there is no ss, it may be set to None
    """
    def __init__(self, parent, name, mosi, miso, sclk, ss,
                 clk_rate=1):
        """
        Paramters:
            parent: digital device
            name: Name of SPI device
            mosi: MOSI DigitalGate
            miso: MISO DigitalGate
            SCLK: SCLK DigitalGate
            SS: SS DigitalGate (optional)
            clk_rate: clock_rate in Hz
        """
        if not isinstance(mosi, DigitalGate):
            raise TypeError("mosi must be a DigitalGate")
        if not isinstance(miso, DigitalGate):
            raise TypeError("miso must be a DigitalGate")
        if not isinstance(sclk, DigitalGate):
            raise TypeError("sclk must be a DigitalGate")
        if ss is not None and not isinstance(ss, DigitalGate):
            raise TypeError("ss must be a DigitalGate")

        super().__init__(parent, name)

        self.add_parameter("clk_rate",
                           vals=Numbers(min_value=0.1),
                           get_cmd=None,
                           set_cmd=None,
                           initial_value=clk_rate)
        self.add_parameter("clk_polarity",
                           vals=Enum(0, 1),
                           get_cmd=None,
                           set_cmd=None,
                           initial_value=0)
        self.add_parameter("bit_order",
                           val_mapping={"MSBFirst": 1, "LSBFirst": 0},
                           get_cmd=None,
                           set_cmd=None,
                           initial_value="MSBFirst")

        self.mosi = parent.get_channel_controller(mosi)
        self.miso = parent.get_channel_controller(miso)
        self.sclk = parent.get_channel_controller(sclk)
        if ss is not None:
            self.ss = parent.get_channel_controller(ss)
        else:
            self.ss = None

    @staticmethod
    def _sleep_until(t):
        while time.monotonic() < t:
            time.sleep(0.000001)

    def _ss_on(self, toggle_ss=True):
        if self.ss is not None and toggle_ss:
            self.ss.out(0)
    def _ss_off(self, toggle_ss=True):
        if self.ss is not None and toggle_ss:
            self.ss.out(1)

    def _get_bit(self, byte, bit):
        if self.bit_order.raw_value:
            return (byte >> (7-bit)) & 0x01
        else:
            return (byte >> bit) & 0x01

    def transfer_byte(self, byte, toggle_ss=True):
        if byte != byte&0xFF:
            raise ValueError("Value should be less than 255")

        self._ss_on(toggle_ss)

        baud_time = 1/self.clk_rate()
        start_time = time.monotonic()
        for i in range(8):
            self.sclk.out(1 if self.clk_polarity() else 0)
            self.mosi.out(self._get_bit(byte, i))
            self._sleep_until(start_time + (baud_time*(i + 0.5)))
            self.sclk.out(0 if self.clk_polarity() else 1)
            self._sleep_until(start_time + (baud_time*(i + 1)))
        self.sclk.out(1 if self.clk_polarity() else 0)

        if toggle_ss:
            self._ss_off(toggle_ss)
            self.mosi.out(0)

    def transfer_bytes(self, data_bytes):
        if not isinstance(data_bytes, (bytes, bytearray)):
            raise TypeError("Data to transfer must be bytes or a bytearray")

        self._ss_on()
        for byte in data_bytes:
            self.transfer_byte(byte, toggle_ss=False)
        self._ss_off()
        self.mosi.out(0)
