from functools import partial

import requests
from qcodes import Instrument

__all__ = ["FridgeTemps"]

class FridgeTemps(Instrument):
    """
    Instrument definition for a fridge logged on the thermometry site.
    """
    def __init__(self, name, url, refresh_interval=60):
        super().__init__(name)
        self.url = url

        params = requests.get(url)
        if params.status_code != 200:
            raise RuntimeError("Unable to query fridge")
        params = set(params.json().keys())
        params.remove("Time")
        params = tuple(params)
        self.params = params

        for param in params:
            self.add_parameter(f"{param}_temp",
                               unit="K",
                               label=f"{param}",
                               get_cmd=partial(self.get_param, param),
                               snapshot_get=True,
                               max_val_age=refresh_interval)

    def get_param(self, param):
        temps = requests.get(self.url)
        if temps.status_code != 200:
            raise RuntimeError("Unable to query fridge")
        temps = temps.json()

        # Update all parameter cache values
        for p in self.params:
            if p in temps:
                if f"{p}_temp" in self.parameters:
                    self.parameters[f"{p}_temp"].cache.set(temps[p])
        # Return the current value
        return temps[param]

    def ask_raw(self, cmd):
        """
        Override for pylint
        """
        raise NotImplementedError("Can't communicate directly with a fridge.")

    def write_raw(self, cmd):
        """
        Override for pylint
        """
        raise NotImplementedError("Can't communicate directly with a fridge.")
