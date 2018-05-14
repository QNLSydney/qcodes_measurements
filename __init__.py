from .plot import ChildProcessImportError
try:
    from .plot import pyplot

    from .tools.measure import linear1d, linear2d
    from .tools.mdac import *
except ChildProcessImportError:
    # Don't do this if we are the child process
    pass