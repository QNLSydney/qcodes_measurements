import io
import os
import sys
import logging

__all__ = ["LoggingStream", "get_logger", "set_log_level"]

# Get access to module level variables
this = sys.modules[__name__]

def get_logger(name=None, debug=False):
    # Disable logging to stderr and capture warnings
    logging.lastResort = None
    logging.captureWarnings(True)

    # Create a root logger the first time this module is called
    if getattr(this, "logger", None) is None:
        this.logger = logging.getLogger("qcm")
        if debug:
            this.logger.setLevel(logging.DEBUG)
        else:
            this.logger.setLevel(logging.INFO)
        # Create a handler for the logger
        log_handler = logging.FileHandler("rpyplot.log")
        log_handler.setLevel(logging.DEBUG)
        log_format = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        log_handler.setFormatter(log_format)
        this.logger.addHandler(log_handler)

    # Get the requested logger
    if "QCM_REMOTE" in os.environ:
        base = f"remote_{os.getpid()}"
        if name is None:
            return this.logger.getChild(base)
        return this.logger.getChild(f"{base}.{name}")
    if name is None:
        return this.logger
    return this.logger.getChild(name)

def set_log_level(level="INFO", name=None):
    """
    Set the log level for a given module (name) to the level.
    """
    # Get the logger
    logger = get_logger(name)
    logger.setLevel(level)
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
