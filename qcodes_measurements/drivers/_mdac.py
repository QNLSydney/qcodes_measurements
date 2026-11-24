"""Detect channels from the optional MDAC driver."""

import sys
from importlib import import_module
from types import ModuleType

_DRIVER_MODULE = "mdac.MDAC"


def require_mdac() -> ModuleType:
    try:
        return import_module(_DRIVER_MODULE)
    except ModuleNotFoundError as exc:
        if exc.name != "mdac":
            raise
        raise ModuleNotFoundError(
            "MDAC support requires the optional 'mdac' package from "
            "https://github.com/emergencequantum/drivers.git",
            name=exc.name,
        ) from exc


def is_mdac_channel(source: object, *, basic: bool = False) -> bool:
    # An existing MDAC channel already has its defining module loaded.
    driver = sys.modules.get(_DRIVER_MODULE)
    if driver is None:
        return False
    channel_class = "MDACBasicChannel" if basic else "MDACChannel"
    return isinstance(source, getattr(driver, channel_class, ()))
