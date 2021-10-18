import re
import sys
import numpy as np
import PyQt5.QtGui
import PyQt5.QtWidgets

from ...logging import get_logger
from ..multiprocess import ObjectProxy, QtProcess, ClosedError

# Get access to module level variables
this = sys.modules[__name__]
rpg = None
logger = get_logger("RPGWrapper")

__all__ = ["ensure_ndarray", "auto_wrap", "RPGWrappedBase", "get_remote"]

def ensure_ndarray(array):
    """
    Ensure the given array is a numpy array. Necessary for some parts of pyqtgraph.
    """
    if array is None:
        return None
    if not isinstance(array, np.ndarray):
        return np.array(array)
    return array

def auto_wrap(f):
    """
    Decorator to ensure values are wrapped by RPGWrappedBase
    """
    def wrap(*args, **kwargs):
        val = f(*args, **kwargs)
        if val is None:
            return val
        return RPGWrappedBase.autowrap(val)
    return wrap

def _set_defaults(remote):
    """
    Set up the default state of the plot windows. Add any other global config options here.
    """
    remote.setConfigOption('background', 'w')
    remote.setConfigOption('foreground', 'k')
    remote.setConfigOption('leftButtonPan', False)
    remote.setConfigOption('antialias', True)
    remote._setProxyOptions(deferGetattr=False)

def start_remote():
    # Check that a QApplication has been created
    if PyQt5.QtWidgets.QApplication.instance() is None:
        this.app = PyQt5.QtWidgets.QApplication([])
    else:
        this.app = PyQt5.QtWidgets.QApplication.instance()

    this.proc = QtProcess(debug=False)
    this.rpg = this.proc._import('qcodes_measurements.plot.rpyplot', timeout=20)
    this.rbuiltins = this.proc._import("builtins")
    _set_defaults(this.rpg)

def restart_remote():
    if len(QtProcess.handlers) == 0:
        start_remote()
    else:
        for pid in QtProcess.handlers:
            try:
                proc = QtProcess.handlers[pid]
                if isinstance(proc, QtProcess):
                    if not proc.exited:
                        QtProcess.handlers[pid].join()
            except ClosedError:
                continue
        QtProcess.handlers.clear()
        start_remote()

def get_remote():
    return this.rpg

def remote_callable(remote_obj):
    # If the object is local, shortcut to the local callable
    if not isinstance(remote_obj, ObjectProxy):
        return callable(remote_obj)

    # Call callable on the remote
    return this.rbuiltins.callable(remote_obj)


class RPGWrappedBase(ObjectProxy):
    # Keep track of children so they aren't recomputed each time
    _subclass_types = None

    # Reserve names for local variables, so they aren't proxied.
    _base_inst = None

    # Cache remote functions, allowing proxy options for each to be set
    _remote_functions = None
    _remote_function_options = None

    def __init__(self, *args, **kwargs): # pylint: disable=super-init-not-called
        self._remote_functions = {}
        self._remote_function_options = {}

        # Check that the remote process has been started, and is still alive
        if getattr(this, "rpg", None) is None:
            start_remote()
        if this.rpg._handler.proc.is_alive() is False:
            restart_remote()

        if '_base' in self.__class__.__dict__:
            base = getattr(this.rpg, self.__class__._base)
            base = base(*args, **kwargs)
            self._base_inst = base
        else:
            raise TypeError("Base instance not defined. Don't know how to create remote object.")

    def __wrap__(self, *args, **kwargs):
        if args or kwargs:
            raise TypeError(f"RPGWrappedBase.__wrap__ expects no arguments. Got args={args}, kwargs={kwargs}")
        self._remote_functions = {}
        self._remote_function_options = {}
        # And make sure that ndarrays are still proxied

    @classmethod
    def wrap(cls, instance, *args, **kwargs):
        if not isinstance(instance, ObjectProxy):
            raise TypeError("We can only wrap ObjectProxies")

        # Create an empty instance of RPGWrappedBase,
        # and copy over instance variables
        base_inst = cls.__new__(cls)
        base_inst.__dict__ = {**base_inst.__dict__,
                              **instance.__dict__}
        base_inst._base_inst = instance

        # If we do want to initialize some instance variables, we can do it in
        # the special __wrap__ method
        __wrap__ = getattr(base_inst, '__wrap__', None)
        if __wrap__ is not None:
            __wrap__(*args, **kwargs)

        return base_inst

    @staticmethod
    def autowrap(inst):
        logger.debug("Trying to autowrap %r.", inst)
        # Figure out the types that we know how to autowrap
        if RPGWrappedBase._subclass_types is None:
            logger.debug("Populating subclass types")
            RPGWrappedBase._subclass_types = {}
            def append_subclasses(sc_dict, cls):
                for typ in cls.__subclasses__():
                    append_subclasses(sc_dict, typ)
                    base = getattr(typ, '_base', None)
                    if base is None:
                        continue
                    typestr = base
                    sc_dict[typestr] = typ
            append_subclasses(RPGWrappedBase._subclass_types, RPGWrappedBase)

        # Then, if we have an object proxy, wrap it if it is in the list of wrappable types
        if isinstance(inst, ObjectProxy):
            if isinstance(inst, RPGWrappedBase):
                logger.debug("Object already wrapped. Has types: %r.", inst.__class__.__mro__)
                return inst

            # Check if we have a list of objects:
            if inst.__getattr__("__class__").__name__ in ("tuple", "list"):
                logger.debug("Wrapping remote list.")
                # Need to iterate over things in a dumb way to suppress warnings
                return tuple(RPGWrappedBase.autowrap(inst[i]) for i in range(len(inst)))

            # Otherwise look to see if we have an extended type
            typestr = re.match(r"<[a-zA-Z_.]+\.([a-zA-Z_]+) object at 0x[0-9A-Fa-f]+>", inst._typeStr)
            if typestr:
                logger.debug("Extracted remote type: %s.", typestr.groups()[0])
                typestr = typestr.groups()[0]
                if typestr in RPGWrappedBase._subclass_types:
                    return RPGWrappedBase._subclass_types[typestr].wrap(inst)
        else:
            logger.debug("Object is not an ObjectProxy. Has types: %r.", inst.__class__.__mro__)
        # Otherwise, just return the bare instance
        return inst

    def wrap_adders(self, f):
        def save(*args, **kwargs):
            res = f(*args, **kwargs)
            # If the adder doesn't return the newly added item, we can assume the added item
            # was passed as the first non-keyword argument
            if res is None:
                if len(args) > 0:
                    res = args[0]
                else:
                    return
            # Try to translate to one of the friendly names
            res = RPGWrappedBase.autowrap(res)
            return res
        return save

    def wrap_getters(self, f):
        return auto_wrap(f)

    def __setattr__(self, name, val, **kwargs): # pylint: disable=arguments-differ
        for cls in self.__class__.__mro__:
            if name in cls.__dict__:
                object.__setattr__(self, name, val)
                return
        if name in self.__dict__:
            object.__setattr__(self, name, val)
        elif name == "__dict__":
            object.__setattr__(self, name, val)
        else:
            super().__setattr__(name, val, **kwargs)

    def __getattr__(self, name, **kwargs):
        # Check for ipython special methods
        if re.match("_repr_.*_", name):
            raise AttributeError("Ignoring iPython special methods")
        if re.match("_ipython_.*_", name):
            raise AttributeError("Ignoring iPython special methods")

        # Figure out where we should look for the attribute ("remote", "local" or "both")
        search_location = kwargs.pop("_location", "both")
        attr = None
        # Get attribute from object proxy, checking if it exists locally first
        if search_location in ("local", "both"):
            logger.debug("Looking for attr %s locally.", name)
            for cls in self.__class__.__mro__:
                if name in cls.__dict__:
                    v = cls.__dict__[name]
                    if hasattr(v, '__get__'):
                        attr = v.__get__(None, self)
                        logger.debug("Found attr %s locally in subclass %r, and is a property. Returns value: %r.", name, cls, attr)
                    else:
                        attr = v
                        logger.debug("Found attr %s locally in subclass %r. Returns value: %r.", name, cls, attr)
                    break
            else:
                if name in self._base_inst.__dict__:
                    attr = self._base_inst.__dict__[name]
                    logger.debug("Found attr %s locally in base_inst (%r). Returns value: %r", name, self._base_inst, attr)
        # Otherwise check whether the attribute exists on the remote
        if search_location in ("remote", "both") and attr is None:
            logger.debug("Looking for attr %s remotely.", name)
            # Check whether this function has been cached
            if name in self._remote_functions:
                logger.debug("Found cached value for %s.", name)
                return self._remote_functions[name]
            # Otherwise look for it on the remote
            attr = self._base_inst.__getattr__(name, **kwargs)
            logger.debug("Found attr %s remotely. Returns value %r.", name, attr)

        # If we didn't find the attribute in either location, raise an AttributeError
        if attr is None:
            raise AttributeError(f"'{self.__class__.__name__}' object has no attribute '{name}'")

        # Check if item is a wrappable type
        attr = self.autowrap(attr)

        # Wrap adders and getters
        if name.startswith("add") and remote_callable(attr):
            return self.wrap_adders(attr)
        elif name.startswith("get") and remote_callable(attr):
            return self.wrap_getters(attr)

        # Save a cached copy, if we have a function with specific options
        if remote_callable(attr) and name in self._remote_function_options:
            attr._setProxyOptions(**self._remote_function_options[name])
            self._remote_functions[name] = self.wrap_getters(attr)
            return self._remote_functions[name]
        if remote_callable(attr):
            return self.wrap_getters(attr)

        return attr

    def __repr__(self):
        return "<%s for %s>" % (self.__class__.__name__, super().__repr__())
