"""Argument validators shared by instrument mirrors."""

import argparse
import math


def port_number(value: str) -> int:
    """Parse a TCP port, excluding the OS-assigned port zero."""
    port = int(value)
    if not 0 < port <= 65535:
        raise argparse.ArgumentTypeError("must be between 1 and 65535")
    return port


def positive_seconds(value: str) -> float:
    """Reject nonpositive and nonfinite timeout values."""
    seconds = float(value)
    if not math.isfinite(seconds) or seconds <= 0:
        raise argparse.ArgumentTypeError("must be a finite positive number")
    return seconds
