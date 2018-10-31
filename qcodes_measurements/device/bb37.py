from .bb import BB, BBChan
from warnings import warn

class BB37(BB):
    def __init__(self, name):
        super().__init__(name, chan_count=37)
        warn("Use BB with chan_count=37 instead", DeprecationWarning)


class BB37Chan(BBChan):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        warn("Use BBChan instead...", DeprecationWarning)