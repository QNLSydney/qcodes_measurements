__version__ = "0.1.0"

import os as _os

__all__ = ["do0d", "do1d", "do2d", "linear1d", "linear2d", "sweep_time"]

if "QCM_REMOTE" not in _os.environ:
    # Import shortcuts to measurements
    from .tools.doNd import do0d, do1d, do2d
    from .tools.measure import linear1d, linear2d
    from .tools.time import sweep_time
