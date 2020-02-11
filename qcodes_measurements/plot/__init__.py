import os as _os

if "QCM_REMOTE" in _os.environ:
    # Don't import anything if we are in the remote process
    pass
else:
    from .plot_tools import *
    from .pyplot import *
