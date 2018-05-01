# -*- coding: utf-8 -*-

import pyqtgraph as pg
import pyqtgraph.multiprocess as mp

proc = mp.QtProcess()
rpg = proc._import('pyqtgraph')
windows = []