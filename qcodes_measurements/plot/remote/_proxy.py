"""Small compound operations; the underlying proxy transport remains shallow."""

from pyqtgraph.multiprocess.remoteproxy import LocalObjectProxy, ObjectProxy

_VALUE_TYPES = (
    type(None),
    str,
    bytes,
    int,
    float,
    tuple,
    list,
    dict,
    LocalObjectProxy,
    ObjectProxy,
)


def describe(obj):
    return type(obj).__name__, id(obj), callable(obj)


def create(factory, /, *args, **kwargs):
    obj = factory(*args, **kwargs)
    return LocalObjectProxy(obj), describe(obj)


def get_attribute(obj, name, return_type="auto"):
    value = getattr(obj, name)
    info = describe(value)
    if return_type == "proxy" or (return_type == "auto" and not isinstance(value, _VALUE_TYPES)):
        value = LocalObjectProxy(value)
    return value, info


def describe_sequence(sequence):
    # Only this explicitly requested sequence is traversed, never arbitrary results.
    return tuple(
        (value if isinstance(value, _VALUE_TYPES) else LocalObjectProxy(value), describe(value))
        for value in sequence
    )


def trace_data(item):
    return item.xData, item.yData


def set_trace_x(item, x):
    if hasattr(item, "setpoint_x"):
        item.setpoint_x = x
    if item.yData is not None:
        item.setData(x, item.yData)


def window_size(window):
    size = window.size()
    return size.width(), size.height()
