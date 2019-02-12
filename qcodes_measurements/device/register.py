"""
Represents a dense bitfield register within a digital device
"""
from collections import namedtuple
from functools import wraps

RegisterField = namedtuple("RegisterField", ("start_bit", "end_bit", "value"))

def _check_change(f):
    """
    Check whether the value of this register changed after running
    the wrapped function, and mark dirty if necessary.
    """
    @wraps(f)
    def check_change_wrapper(self, *args, **kwargs):
        old_val = self.value
        f(self, *args, **kwargs)
        if old_val != self.value:
            self._dirty = True
    return check_change_wrapper

class Register:
    """
    Create a register with a certain set of named fields. This class keeps
    track of the contents of a register, including whether the register is
    modified or committed to the device.
    """
    def __init__(self, name, address, fields, length=32, require_sync=False):
        """
        Initialize a register.

        Args:
            name (str): The name of the register
            address (int): The address of the register on the device.
            fields (Tuple(str, int, int)): The name and bit indices of values in the register.
            length (int): The length of the register in bits
            require_sync (bool): Mark whether we should keep track of whether the register has been sync'd with
                                 the underlying device.
        """
        if length%8 != 0:
            raise ValueError("Length must be an integer multiple of 8")

        self.name = name
        self.length = length
        self.address = address
        self.require_sync = require_sync
        self._dirty = False

        self.fields = {}
        self.bits = [None]*self.length
        for name, start_bit, end_bit in fields:
            self.fields[name] = RegisterField(start_bit, end_bit+1, 0)
            for bit in range(start_bit, end_bit+1):
                if self.bits[bit] is not None:
                    raise ValueError("Overlapping bit ranges in register")
                self.bits[bit] = name

    def __bytes__(self):
        return self.value.to_bytes(self.length//8, "big")

    def __repr__(self):
        return f"<Register({self.name})@0x{self.address:X} 0x{bytes(self).hex().upper()}>"

    def get_by_field(self, name):
        if not isinstance(name, str):
            raise TypeError("Field names must be strings")
        return self.fields[name].value

    def get_by_bitind(self, ind):
        if isinstance(ind, int):
            return (self.value >> (ind % self.length)) & 1
        elif isinstance(ind, slice):
            if ind.step is not None and ind.step != 1:
                raise IndexError("Registers do not support steps other than 1 in a slice")
            # Calculate bit indices
            start, stop, _ = ind.indices(self.length)
            # Retrieve range from register
            return (self.value >> start) & ((1 << (stop-start))-1)

    def __getitem__(self, ind):
        if isinstance(ind, str):
            return self.get_by_field(ind)
        elif isinstance(ind, (int, slice)):
            return self.get_by_bitind(ind)
        else:
            raise TypeError("Can retrieve by field name, or with regular indexing only")

    @_check_change
    def set_by_field(self, name, val):
        max_val = self.fields[name].end_bit - self.fields[name].start_bit
        max_val = (1 << max_val)-1
        if not isinstance(val, int):
            raise TypeError("Value must be an integer in the range 0 - {max_val}.")
        if val > max_val:
            raise ValueError(f"Value out of range for {name}. Should be in range 0 - {max_val}, got {val}.")
        self.fields[name] = self.fields[name]._replace(value=val)

    @_check_change
    def set_by_bitind(self, ind, val):
        # Calculate bit indices in register
        if isinstance(ind, int):
            indices = (ind, )
        elif isinstance(ind, slice):
            if ind.step is not None and ind.step != 1:
                raise IndexError("Registers do not support steps other than 1 in a slice")
            indices = range(*ind.indices(self.length))

        # Validate value
        start, stop = indices[0], indices[-1]+1
        max_val = (1 << (stop-start))-1
        if val > max_val:
            raise ValueError(f"Value out of range for field. Should be in range 0 - {max_val}, got {val}.")

        # Set each bit
        for i, index in enumerate(indices):
            bval = (val >> i) & 1
            field_name = self.bits[index]
            field = self.fields[field_name]
            bloc = index - field.start_bit
            if bval:
                new_val = field.value | (1 << bloc)
            else:
                new_val = field.value & (((1 << (field.end_bit - field.start_bit))-1) ^ (1 << bloc))
            self.fields[field_name] = field._replace(value=new_val)

    def __setitem__(self, ind, val):
        if isinstance(ind, str):
            self.set_by_field(ind, val)
        elif isinstance(ind, (int, slice)):
            self.set_by_bitind(ind, val)
        else:
            raise TypeError("Can retrieve by field name, or with regular indexing only")

    @property
    def value(self):
        value = 0
        for field in self.fields.values():
            value |= (field.value << field.start_bit)
        return value & ((1 << self.length)-1)

    @property
    def dirty(self):
        """
        Checks whether the register is in sync with the device.
        """
        if not self.require_sync:
            return False
        return self._dirty

    def commit(self):
        """
        Marks the register as clean (committed to the device).
        """
        self._dirty = False
