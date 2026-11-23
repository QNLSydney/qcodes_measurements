import os as _os
from typing import TYPE_CHECKING as _TYPE_CHECKING

if _TYPE_CHECKING:
    from .tools import do0d as do0d
    from .tools import do1d as do1d
    from .tools import do2d as do2d
    from .tools import sweep_time as sweep_time

__version__ = "0.1.0"
__all__ = ["do0d", "do1d", "do2d", "sweep_time"]
_is_remote = "QCM_REMOTE" in _os.environ


def __getattr__(name: str):
    if not _is_remote and name in __all__:
        from . import tools

        measurement = getattr(tools, name)
        globals()[name] = measurement
        return measurement
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted(set(globals()).union(() if _is_remote else __all__))
