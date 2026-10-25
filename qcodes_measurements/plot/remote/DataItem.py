from typing import TYPE_CHECKING, Any


class ExtendedDataItem:
    if TYPE_CHECKING:

        def scene(self) -> Any:
            raise NotImplementedError

    def _updates_enabled(self):
        scene = self.scene()
        if scene is None:
            return True
        views = scene.views()
        return not views or any(not getattr(view, "_plot_closed", False) for view in views)
