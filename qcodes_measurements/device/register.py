"""
Represents a dense bitfield register within a digital device
"""
from collections import namedtuple
from qcodes.utils.metadata import Metadatable

RegisterField = namedtuple("RegisterField", ("start_bit", "end_bit", "value"))

class Register(Metadatable):
    """
    Create a register with a certain set of named fields. This class keeps
    track of the contents of a register, including whether the register is
    modified or committed to the device.
    """
    def __init__(self, name, address, fields, length=32, require_sync=False):
        """
        Initialize a register.
        Note: To start off with, the register is marked DIRTY.

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
        self._committed_val = None

        self.fields = {}
        self.bits = [None]*self.length
        for field_name, start_bit, end_bit in fields:
            if start_bit < 0 or start_bit > length:
                raise ValueError(f"start_bit ({start_bit}) for field {field_name} out of bounds for register of length {length}.")
            if end_bit < 0 or end_bit > length:
                raise ValueError(f"end_bit ({end_bit}) for field {field_name} out of bounds for register of length {length}.")
            self.fields[field_name] = RegisterField(start_bit, end_bit+1, 0)
            for bit in range(start_bit, end_bit+1):
                if self.bits[bit] is not None:
                    raise ValueError("Overlapping bit ranges in register")
                self.bits[bit] = field_name

        super().__init__()

    def __bytes__(self):
        return self.value.to_bytes(self.length//8, "big")

    def __repr__(self):
        d_flag = "D" if self.dirty else ""
        return f"<Register({self.name})@0x{self.address:X} 0x{bytes(self).hex().upper()}<{d_flag}>>"

    def snapshot_base(self, update=False, params_to_skip_update=None):
        """
        Create a snapshot of the register
        """
        snap = {}
        snap['name'] = self.name
        snap['address'] = self.address
        snap['length'] = self.length
        snap['require_sync'] = self.require_sync
        if self.require_sync:
            snap['committed_val'] = self.committed_val
        snap['fields'] = tuple((name, start_bit, stop_bit, value) for
                               name, (start_bit, stop_bit, value) in self.fields.items())
        snap['value'] = self.value
        return snap

    def get_by_field(self, name):
        """
        Return value of field in register by name
        """
        if not isinstance(name, str):
            raise TypeError("Field names must be strings")
        return self.fields[name].value

    def get_by_bitind(self, ind):
        """
        Return bit values in register by index or slice
        """
        if isinstance(ind, int):
            if ind > self.length:
                raise IndexError(f"Index out of bounds for register of length {self.length}.")
            return (self.value >> (ind % self.length)) & 1
        if isinstance(ind, slice):
            if ind.step is not None and ind.step != 1:
                raise IndexError("Registers do not support steps other than 1 in a slice")
            # Calculate bit indices
            start, stop, _ = ind.indices(self.length)
            # Retrieve range from register
            return (self.value >> start) & ((1 << (stop-start))-1)
        raise TypeError("Index must be an integer or a slice")

    def __getitem__(self, ind):
        if isinstance(ind, str):
            return self.get_by_field(ind)
        if isinstance(ind, (int, slice)):
            return self.get_by_bitind(ind)
        raise TypeError("Can retrieve by field name, or with regular indexing only")

    def set_by_field(self, name, val):
        """
        Set value of register by field name
        """
        max_val = self.fields[name].end_bit - self.fields[name].start_bit
        max_val = (1 << max_val)-1
        if not isinstance(val, int):
            raise TypeError("Value must be an integer in the range 0 - {max_val}.")
        if val > max_val:
            raise ValueError(f"Value out of range for {name}. Should be in range 0 - {max_val}, got {val}.")
        self.fields[name] = self.fields[name]._replace(value=val)

    def set_by_bitind(self, ind, val):
        """
        Set value of bits in register by index or slice
        """
        # Calculate bit indices in register
        if isinstance(ind, int):
            indices = (ind, )
        elif isinstance(ind, slice):
            if ind.step is not None and ind.step != 1:
                raise IndexError("Registers do not support steps other than 1 in a slice")
            indices = range(*ind.indices(self.length))

        # Validate value
        val = int(val)
        start, stop = indices[0], indices[-1]+1
        max_val = (1 << (stop-start))-1
        if val > max_val:
            raise ValueError(f"Value out of range for field. Should be in range 0 - {max_val}, got {val}.")

        # Set each bit
        for i, index in enumerate(indices):
            bval = (val >> i) & 1
            field_name = self.bits[index]
            if field_name is None:
                if bval:
                    raise ValueError("Can't set a bit outside a specified field")
                else:
                    continue
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
        """
        Return register value as integer
        """
        value = 0
        for field in self.fields.values():
            value |= (field.value << field.start_bit)
        return value & ((1 << self.length)-1)

    @property
    def committed_val(self):
        """
        Returns the last committed value.
        """
        if not self.require_sync:
            return self.value
        return self._committed_val

    @property
    def dirty(self):
        """
        Checks whether the register is in sync with the device.
        """
        if not self.require_sync:
            return False
        return (self.committed_val is None) or (self.value != self.committed_val)

    def mark_dirty(self):
        """
        Forcibly marks the register dirty.
        NOTE: This has no effect if self.require_sync is False.
        """
        self.committed_val = None

    def commit(self):
        """
        Marks the register as clean (committed to the device).
        """
        self._committed_val = self.value
