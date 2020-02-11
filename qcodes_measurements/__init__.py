__version__ = "0.1.0"

import os as _os

__all__ = ["do0d", "linear1d", "linear2d", "sweep_time"]

if "QCM_REMOTE" not in _os.environ:
    # Import shortcuts to measurements
    from .tools.measure import do0d, linear1d, linear2d
    from .tools.time import sweep_time
