import io
import os
import sys
import logging
from typing import Optional

__all__ = ["LoggingStream", "get_logger", "set_log_level"]

# Get access to module level variables
logger: Optional[logging.Logger] = None

def get_logger(name=None, debug=False) -> logging.Logger:
    # Disable logging to stderr and capture warnings
    logging.lastResort = None
    logging.captureWarnings(True)

    # Create a root logger the first time this module is called
    if logger is None:
        local_logger = logging.getLogger("qcm")
        globals()["logger"] = logging.getLogger("qcm")

        if debug:
            local_logger.setLevel(logging.DEBUG)
        else:
            local_logger.setLevel(logging.INFO)
        # Create a handler for the logger
        log_handler = logging.FileHandler("rpyplot.log")
        log_handler.setLevel(logging.DEBUG)
        log_format = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        log_handler.setFormatter(log_format)
        local_logger.addHandler(log_handler)
    else:
        local_logger = logger

    # Get the requested logger
    if "QCM_REMOTE" in os.environ:
        base = f"remote_{os.getpid()}"
        if name is None:
            return local_logger.getChild(base)
        return local_logger.getChild(f"{base}.{name}")
    if name is None:
        return local_logger
    return local_logger.getChild(name)

def set_log_level(level="INFO", name=None):
    """
    Set the log level for a given module (name) to the level.
    """
    # Get the logger
    local_logger = get_logger(name)
    local_logger.setLevel(level)
    get_logger().debug(f"Set log level of %r to %r", logger, level)

class LoggingStream(io.IOBase):
    """
    Implement a stream handler that redirects all writes to a logger.
    """

    def __init__(self, logger, level="debug"):
        """
        Store the logger to which we pass all output.
        """
        super().__init__()
        if not isinstance(logger, logging.Logger):
            raise TypeError(f"logger must be a Logger. Is a {type(logger)}.")

        self.logger = logger
        self.level = level

    def readable(self):
        """
        Can't read from a logger
        """
        return False

    def writable(self):
        """
        Can write to a logger
        """
        return True

    def write(self, msg):
        msg = msg.strip("\r\n")
        if msg:
            getattr(self.logger, self.level)(msg)
