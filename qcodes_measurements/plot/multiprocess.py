# pylint: disable=import-outside-toplevel,invalid-name,wrong-import-position
import os
import sys
import time
import atexit
import multiprocessing
import multiprocessing.connection

import pyqtgraph.multiprocess.remoteproxy
from pyqtgraph.multiprocess.remoteproxy import ClosedError, NoResultError, ObjectProxy

# Allow a speedy import of logging from qcodes_measurements
PREV_REMOTE = os.environ.get("QCM_REMOTE", None)
os.environ["QCM_REMOTE"] = "IMP_LOGGING"
from qcodes_measurements.logging import get_logger, LoggingStream
if PREV_REMOTE is None:
    del os.environ["QCM_REMOTE"]
else:
    os.environ["QCM_REMOTE"] = PREV_REMOTE

__all__ = ['Process', 'QtProcess', 'ClosedError', 'NoResultError', 'ObjectProxy']

class RemoteEventHandler(pyqtgraph.multiprocess.remoteproxy.RemoteEventHandler):
    """
    Reimplementation of RemoteEventHandler that uses logging module for debug instead of
    cprint. This works properly on windows and ipython.
    """
    def __init__(self, connection, name, pid, debug=False, logger=None):
        super().__init__(connection, name, pid, debug=False)
        del self.debug

        # Set up logger if it does not exist
        if logger is not None:
            self.logger = logger
        elif not hasattr(self, "logger"):
            self.logger = get_logger(name, debug=debug)

    def debugMsg(self, msg, *args):
        """
        Use the logger for debugging instead
        """
        self.logger.debug(msg.strip("\r\n"), *args)

class Process(RemoteEventHandler):
    """
    Bases: RemoteEventHandler

    This class is used to spawn a process using the multiprocessing library.

    By default, the remote process will immediately enter an event-processing
    loop that carries out requests send from the parent process.

    Remote control works mainly through proxy objects::

        proc = Process()              ## starts process, returns handle
        rsys = proc._import('sys')    ## asks remote process to import 'sys', returns
                                      ## a proxy which references the imported module
        rsys.stdout.write('hello\n')  ## This message will be printed from the remote
                                      ## process. Proxy objects can usually be used
                                      ## exactly as regular objects are.
        proc.close()                  ## Request the remote process shut down

    Requests made via proxy objects may be synchronous or asynchronous and may
    return objects either by proxy or by value (if they are picklable). See
    ProxyObject for more information.
    """

    def __init__(self, name=None, target=None, debug=False):
        """
        ==============  =============================================================
        **Arguments:**
        name            Optional name for this process used when printing messages
                        from the remote process.
        target          Optional function to call after starting remote process.
                        By default, this is startEventLoop(), which causes the remote
                        process to handle requests from the parent process until it
                        is asked to quit. If you wish to specify a different target,
                        it must be picklable (bound methods are not).
        debug           If True, print detailed information about communication
                        with the child process.
        ==============  =============================================================
        """
        if target is None:
            target = startEventLoop
        if name is None:
            name = str(os.getpid())
        self.logger = get_logger(f"local_{os.getpid()}", debug=debug)

        ## Create a connection for the client/server
        self.conn, child_conn = multiprocessing.Pipe(True)

        self.logger.info('Starting child process')

        # we must send pid to child because windows only implemented getppid in Python 3.2
        pid = os.getpid()

        ## Send everything the remote process needs to start correctly
        data = dict(
            name=name,
            conn=child_conn,
            ppid=pid,
            debug=debug,
            )

        # Start the process. We'll set the file that multiprocessing loads to this file
        old_main = getattr(sys.modules['__main__'], '__file__', None)
        sys.modules['__main__'].__file__ = __file__
        self.proc = multiprocessing.Process(target=target, kwargs=data)
        self.proc.start()
        if old_main is not None:
            sys.modules['__main__'].__file__ = old_main

        ## Close one end of pipe to ensure there is only one writer per pipe
        child_conn.close()

        ## Connect the child process event handler to self.conn
        RemoteEventHandler.__init__(self, self.conn, name+'_parent',
                                    pid=self.proc.pid, logger=self.logger)
        self.logger.debug('Connected to child process.')

        atexit.register(self.join)

    def join(self, timeout=10):
        self.logger.debug('Joining child process..')

        if self.proc.is_alive():
            self.close()
            start = time.time()
            while self.proc.is_alive():
                if timeout is not None and time.time() - start > timeout:
                    raise Exception('Timed out waiting for remote process to end.')
                self.proc.join(0.05)
            self.conn.close()
            self.logger.info('Child process exited with exit code: %d', self.proc.exitcode)


def startEventLoop(name, conn, ppid, debug=False):
    # Set up environment
    os.environ["QCM_REMOTE"] = name

    # Set up logger
    logger = get_logger(debug=debug)

    # Redirect stdout and stderr to the logger
    sys.stdout = LoggingStream(logger.info)
    sys.stderr = LoggingStream(logger.error)

    logger.info('Connected; starting remote proxy.\n')

    handler = RemoteEventHandler(conn, name, ppid, logger=logger)
    while True:
        try:
            handler.processRequests()  # exception raised when the loop should exit
            time.sleep(0.01)
        except (ClosedError, BrokenPipeError):
            logger.debug('Exiting server loop.')
            sys.exit(0)


##Special set of subclasses that implement a Qt event loop instead.

class RemoteQtEventHandler(RemoteEventHandler):
    def __init__(self, *args, **kwds):
        RemoteEventHandler.__init__(self, *args, **kwds)
        self.timer = None

    def startEventTimer(self):
        from pyqtgraph.Qt import QtCore
        self.timer = QtCore.QTimer()
        self.timer.timeout.connect(self.processRequests)
        self.timer.start(1)

    def processRequests(self):
        try:
            RemoteEventHandler.processRequests(self)
        except (ClosedError, BrokenPipeError):
            from pyqtgraph.Qt import QtWidgets
            self.timer.stop()
            QtWidgets.QApplication.instance().quit()

class QtProcess(Process):
    """
    QtProcess is essentially the same as Process, with two major differences:

    - The remote process starts by running startQtEventLoop() which creates a
      QApplication in the remote process and uses a QTimer to trigger
      remote event processing. This allows the remote process to have its own
      GUI.
    - A QTimer is also started on the parent process which polls for requests
      from the child process. This allows Qt signals emitted within the child
      process to invoke slots on the parent process and vice-versa. This can
      be disabled using processRequests=False in the constructor.

    Example::

        proc = QtProcess()
        rQtGui = proc._import('PyQt4.QtGui')
        btn = rQtGui.QPushButton('button on child process')
        btn.show()

        def slot():
            print('slot invoked on parent process')
        btn.clicked.connect(proxy(slot))   # be sure to send a proxy of the slot
    """

    def __init__(self, **kwds):
        if 'target' not in kwds:
            kwds['target'] = startQtEventLoop

        from pyqtgraph.Qt import QtWidgets  ## avoid module-level import to keep bootstrap snappy.
        self._processRequests = kwds.pop('processRequests', True)
        if self._processRequests and QtWidgets.QApplication.instance() is None:
            raise Exception("Must create QApplication before starting QtProcess, "
                            "or use QtProcess(processRequests=False)")

        super().__init__(**kwds)
        self.startEventTimer()

    def startEventTimer(self):
        from pyqtgraph.Qt import QtCore  ## avoid module-level import to keep bootstrap snappy.
        self.timer = QtCore.QTimer()
        if self._processRequests:
            self.startRequestProcessing()

    def startRequestProcessing(self, interval=0.01):
        """Start listening for requests coming from the child process.
        This allows signals to be connected from the child process to the parent.
        """
        self.timer.timeout.connect(self.processRequests)
        self.timer.start(max(1, int(interval*1000)))

    def stopRequestProcessing(self):
        self.timer.stop()

    def processRequests(self):
        try:
            Process.processRequests(self)
        except ClosedError:
            self.timer.stop()

def startQtEventLoop(name, conn, ppid, debug=False):
    # Set up environment
    os.environ["QCM_REMOTE"] = name

    # Get logger
    logger = get_logger(debug=debug)

    # Redirect stdout and stderr to the logger
    sys.stdout = LoggingStream(logger, "info")
    sys.stderr = LoggingStream(logger, "error")

    logger.info('Connected; starting remote proxy.')
    from pyqtgraph.Qt import QtWidgets
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([])
        ## generally we want the event loop to stay open
        ## until it is explicitly closed by the parent process.
        app.setQuitOnLastWindowClosed(False)

    handler = RemoteQtEventHandler(conn, name, ppid, logger=logger)
    handler.startEventTimer()
    sys.exit(app.exec_())
