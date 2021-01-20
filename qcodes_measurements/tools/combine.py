from qcodes import Parameter

class CombinedParameter(Parameter):
    def __init__(self, *parameters,
                 name=None, label=None, unit=None):

        if name is None:
            raise ValueError("name is required. Got None.")
        if label is None:
            label = parameters[0].label
        if unit is None:
            unit = parameters[0].unit
        if not all(isinstance(x, Parameter) for x in parameters):
            raise TypeError("All parameters to combine must be qcodes parameters.")
        self._parameters = parameters

        super().__init__(name=name, label=label, unit=unit)

    def get_raw(self):
        """
        Return the value of the first parmeter. Since they are combined, we assume that they all have
        the same value.
        """
        return self._parameters[0]()

    def set_raw(self, val):
        """
        Set each parameter to val
        """
        for parameter in self._parameters:
            parameter(val)
