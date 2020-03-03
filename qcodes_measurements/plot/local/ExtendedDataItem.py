class ExtendedDataItem:
    """
    Base class for trace-like objects (1d or 2d plots)
    """
    def update(self, data, *args, **kwargs):
        """
        Define a common way of updating plots for 1D and 2D plots
        """
        raise NotImplementedError("Can't update this")

    @property
    def data(self):
        """
        Return the data underlying this plot
        """
        raise NotImplementedError("This should be implemented by the actual plot item")