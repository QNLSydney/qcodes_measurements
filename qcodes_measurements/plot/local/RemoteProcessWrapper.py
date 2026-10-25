from __future__ import annotations

import re
import threading
from typing import Any, ClassVar
from weakref import WeakValueDictionary

import numpy as np
from PySide6 import QtCore, QtWidgets

from ...logging import get_logger
from ..multiprocess import ObjectProxy, QtProcess


# Get access to module level variables
class _RemoteSession:
    """Own one plotting process without owning the host application's event loop."""

    app: QtCore.QCoreApplication | None = None
    proc: QtProcess | None = None
    rpg: Any = None
    rbuiltins: Any = None

    def __init__(self):
        self.lock = threading.RLock()
        self.process_requests = True

    def is_alive(self):
        return (
            self.proc is not None
            and not self.proc.exited
            and not self.proc.conn.closed
            and self.proc.proc.is_alive()
        )

    def close(self, timeout=10):
        with self.lock:
            if self.proc is None:
                return
            _require_main_thread()
            proc = self.proc
            try:
                proc.join(timeout)
            finally:
                if self.app is not None and QtCore.QCoreApplication.instance() is self.app:
                    self.app.aboutToQuit.disconnect(self.close)
                self.proc = self.rpg = self.rbuiltins = None
                with RPGWrappedBase._wrapper_lock:
                    for key in tuple(RPGWrappedBase._wrappers):
                        if key[0] is proc:
                            RPGWrappedBase._wrappers.pop(key, None)
                from .ColorMap import ColorMap

                for name, cmap in tuple(ColorMap._all_colors.items()):
                    if cmap._base_inst._handler is proc:
                        del ColorMap._all_colors[name]


this = _RemoteSession()
rpg = None
logger = get_logger("RPGWrapper")

__all__ = ["ensure_ndarray", "auto_wrap", "RPGWrappedBase", "get_remote", "stop_remote"]


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
    remote._deferredAttr("setConfigOptions")(
        background="w", foreground="k", leftButtonPan=False, antialias=True
    )
    remote._setProxyOptions(deferGetattr=False)


def _require_main_thread():
    app = QtCore.QCoreApplication.instance()
    if threading.current_thread() is not threading.main_thread() or (
        app is not None and QtCore.QThread.currentThread() != app.thread()
    ):
        raise RuntimeError("Start, stop, and restart plotting from the Qt main thread")


def start_remote(*, process_requests=True):
    """Start plotting once. Parent callbacks require the host's Qt event loop.

    Set process_requests=False for scripts that only send plotting commands;
    these do not need a QApplication in the parent. This function never enters
    or replaces a host application's event loop.
    """
    with this.lock:
        if this.is_alive():
            return
        _require_main_thread()
        this.close()
        app = QtWidgets.QApplication.instance()
        if process_requests:
            if app is None:
                app = QtWidgets.QApplication([])
            if not isinstance(app, QtWidgets.QApplication):
                raise RuntimeError("Parent Qt callbacks require a QApplication")
        this.app = app
        proc = QtProcess(debug=False, processRequests=process_requests)
        try:
            remote = proc._import("qcodes_measurements.plot.rpyplot", timeout=20)
            proc._qcm_proxy_helpers = remote._deferredAttr("_proxy_helpers")
            builtins = proc._import("builtins")
            _set_defaults(remote)
        except BaseException:
            try:
                proc.join(timeout=1)
            except TimeoutError:
                logger.warning("Terminated plotting process after failed startup")
            raise
        this.proc, this.rpg, this.rbuiltins = proc, remote, builtins
        this.process_requests = process_requests
        if app is not None:
            app.aboutToQuit.connect(this.close)


def stop_remote(timeout=10):
    """Close the owned plotting session, leaving the host application running."""
    this.close(timeout)


def restart_remote():
    _require_main_thread()
    with this.lock:
        try:
            this.close()
        except TimeoutError:
            logger.warning("Terminated unresponsive plotting process before restart")
        start_remote(process_requests=this.process_requests)


def get_remote():
    if not this.is_alive():
        start_remote(process_requests=this.process_requests)
    return this.rpg


def remote_callable(remote_obj):
    # If the object is local, shortcut to the local callable
    if not isinstance(remote_obj, ObjectProxy):
        return callable(remote_obj)

    return RPGWrappedBase._call_helper("describe", remote_obj, _handler=remote_obj._handler)[2]


class RPGWrappedBase(ObjectProxy):
    # Keep track of children so they aren't recomputed each time
    _subclass_types: ClassVar[dict[str, type[RPGWrappedBase]]] = {}
    _wrappers: ClassVar[WeakValueDictionary] = WeakValueDictionary()
    _wrapper_lock: ClassVar = threading.RLock()

    # Reserve names for local variables, so they aren't proxied.
    _base: str = ""
    _base_inst: ObjectProxy

    # Cache remote functions, allowing proxy options for each to be set
    _remote_functions: dict = {}
    _remote_function_options: dict[str, Any] = {}

    def __init__(self, *args, **kwargs):  # pylint: disable=super-init-not-called
        self._remote_functions = {}
        self._remote_function_options = {}

        # Check that the remote process has been started, and is still alive
        remote = get_remote()

        if "_base" in self.__class__.__dict__:
            base, info = self._call_helper(
                "create", remote._deferredAttr(self.__class__._base), *args, **kwargs
            )
            self._base_inst = base
            with self._wrapper_lock:
                self._wrappers[(base._handler, info[1])] = self
        else:
            raise TypeError("Base instance not defined. Don't know how to create remote object.")

    def __init_subclass__(cls) -> None:
        base = getattr(cls, "_base", None)
        if base:
            typestr = base
            RPGWrappedBase._subclass_types[typestr] = cls
        return super().__init_subclass__()

    def __wrap__(self, *args, **kwargs):
        if args or kwargs:
            raise TypeError(
                f"RPGWrappedBase.__wrap__ expects no arguments. Got args={args}, kwargs={kwargs}"
            )
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
        base_inst.__dict__ = {**base_inst.__dict__, **instance.__dict__}
        base_inst._base_inst = instance

        # If we do want to initialize some instance variables, we can do it in
        # the special __wrap__ method
        __wrap__ = getattr(base_inst, "__wrap__", None)
        if __wrap__ is not None:
            __wrap__(*args, **kwargs)

        return base_inst

    @staticmethod
    def _call_helper(name, /, *args, _handler=None, **kwargs):
        handler = _handler if _handler is not None else this.proc
        if handler is None:
            raise RuntimeError("No plotting session is running")
        kwargs.setdefault("_returnType", "value")
        kwargs.setdefault("_callSync", "sync")
        return handler._qcm_proxy_helpers._deferredAttr(name)(*args, **kwargs)

    @staticmethod
    def autowrap(inst, info=None):
        logger.debug("Trying to autowrap %r.", inst)

        # If we have an object proxy, wrap it if it is in the list of wrappable types
        if isinstance(inst, ObjectProxy):
            if isinstance(inst, RPGWrappedBase):
                logger.debug("Object already wrapped. Has types: %r.", inst.__class__.__mro__)
                return inst

            # Get name of remote type
            if info is None:
                info = RPGWrappedBase._call_helper("describe", inst, _handler=inst._handler)
            remote_type, remote_id, _ = info

            # Check if we have a list of objects:
            if remote_type in ("tuple", "list"):
                logger.debug("Wrapping remote list.")
                values = RPGWrappedBase._call_helper(
                    "describe_sequence", inst, _handler=inst._handler
                )
                return tuple(RPGWrappedBase.autowrap(value, meta) for value, meta in values)

            # Try to match the remote type
            logger.debug("Extracted remote type: %s.", remote_type)
            if remote_type in RPGWrappedBase._subclass_types:
                key = (inst._handler, remote_id)
                with RPGWrappedBase._wrapper_lock:
                    wrapped = RPGWrappedBase._wrappers.get(key)
                    if wrapped is None:
                        wrapped = RPGWrappedBase._subclass_types[remote_type].wrap(inst)
                        RPGWrappedBase._wrappers[key] = wrapped
                return wrapped

            # Otherwise look to see if we have an extended type
            logger.debug("Fallback matching %s", inst._typeStr)
            typestr = re.match(
                r"^<[a-zA-Z_.]+\.([a-zA-Z_]+)(?:\(.*\))? (?:object )?at 0x[0-9A-Fa-f]+>$",
                str(inst._typeStr),
            )
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

    def __setattr__(self, *args):
        name, val = args
        for cls in self.__class__.__mro__:
            if name in cls.__dict__ or name in getattr(cls, "__annotations__", {}):
                object.__setattr__(self, name, val)
                return
        if name in self.__dict__:
            object.__setattr__(self, name, val)
        elif name == "__dict__":
            object.__setattr__(self, name, val)
        else:
            super().__setattr__(name, val)

    def __getattr__(self, attr, **kwargs):
        name = attr
        if re.match(r"_(repr|ipython)_.*_", name):
            raise AttributeError("Ignoring IPython special methods")
        location = kwargs.pop("_location", "both")
        if location not in ("local", "remote", "both"):
            raise ValueError(f"Unknown attribute location: {location}")
        if location != "remote":
            try:
                return object.__getattribute__(self, name)
            except AttributeError:
                base = object.__getattribute__(self, "_base_inst")
                if name in base.__dict__:
                    return base.__dict__[name]
                if location == "local":
                    raise AttributeError(name) from None

        if not kwargs and name in self._remote_functions:
            return self._remote_functions[name]
        base = self._base_inst
        if kwargs.keys() - {"_returnType"}:
            return self.autowrap(base.__getattr__(name, **kwargs))
        attr, info = self._call_helper(
            "get_attribute",
            base,
            name,
            kwargs.get("_returnType", base._getProxyOption("returnType")),
            _handler=base._handler,
        )
        if not info[2]:
            return self.autowrap(attr, info)

        # Cache only callables. Mutable attributes are always read remotely.
        def invoke(*args, **call_kwargs):
            for option, value in self._remote_function_options.get(name, {}).items():
                call_kwargs.setdefault("_" + option, value)
            return attr(*args, **call_kwargs)

        call = self.wrap_adders(invoke) if name.startswith("add") else self.wrap_getters(invoke)
        if not kwargs:
            self._remote_functions[name] = call
        return call

    def __repr__(self):
        return f"<{self.__class__.__name__} for {super().__repr__()}>"
