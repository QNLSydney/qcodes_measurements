"""Legacy import path for the separately installed MDAC driver."""

import sys

from ._mdac import require_mdac

sys.modules[__name__] = require_mdac()
