from PyQt5 import QtCore
from pyqtgraph import PlotCurveItem, PlotDataItem, ImageItem

from .DataItem import ExtendedDataItem
from ...logging import get_logger
logger = get_logger("PlotMenu")

class PlotMenuMixin:
    def raiseContextMenu(self, ev):
        """
        Raise the context menu, removing extra separators as they are added pretty recklessly
        """
        menu = self.getContextMenus(ev)
        # Let the scene add on to the end of our context menu
        # (this is optional)
        menu = self.scene().addParentContextMenus(self, menu, ev)
        # Collapse sequential separators
        i = 1
        actions = menu.actions()
        while i < len(actions):
            if actions[i].isSeparator() and actions[i-1].isSeparator():
                menu.removeAction(actions[i])
                actions.remove(actions[i])
                continue
            i += 1

        # Display the separator
        pos = ev.screenPos()
        logger.debug("Screen pos: %r, %r", pos.x(), pos.y())
        menu.popup(QtCore.QPoint(int(pos.x()), int(pos.y())))
        ev.accept()
        return True

    def addPlotContextMenus(self, items, itemNumbers, menu, rect=None):
        """
        Add plot items to the menu

        Args:
            items: List of plot items to add to the menu
            itemNumbers: Dictionary mapping items to the index in the plot
            menu: The menu to add items to
        """
        # If there are added items, remove them all
        menuItems = getattr(self, "addedMenuItems", None)
        if menuItems is not None:
            for item in menuItems:
                menu.removeAction(item)
            menuItems.clear()
        else:
            menuItems = []
            self.addedMenuItems = menuItems

        # And create a sorted list of items under the rectangle
        itemsToAdd = []
        for item in items:
            if not isinstance(item, (PlotCurveItem, PlotDataItem, ImageItem)):
                continue
            if isinstance(item, PlotCurveItem):
                dataitem = item.parentObject()
            else:
                dataitem = item

            if not hasattr(dataitem, "getContextMenus"):
                continue

            # Figure out the name and references of this item
            if hasattr(dataitem, "name"):
                name = dataitem.name()
            else:
                name = None
            ind = itemNumbers[dataitem]
            if name is None:
                name = f"(Trace: {ind+1})"
            else:
                name = f"{name} (Trace: {ind+1})"

            # Create menus for each of the items
            if isinstance(dataitem, ExtendedDataItem):
                menu = dataitem.getContextMenus(rect=rect, event=None)
            else:
                menu = dataitem.getContextMenus(event=None)
            menu.setTitle(name)
            itemsToAdd.append((ind, menu))

        # Sort the items by the index
        itemsToAdd.sort(key=lambda x: x[0])

        # Add each of the items in to the menu
        if itemsToAdd:
            menuItems.append(self.menu.addSeparator())
            if len(itemsToAdd) == 1:
                for item in itemsToAdd[0][1].actions():
                    menuItems.append(item)
                    self.menu.addAction(item)
            else:
                for item in itemsToAdd:
                    menuItems.append(self.menu.addMenu(item[1]))

        return itemsToAdd

class ImageMenuMixin:
    pass