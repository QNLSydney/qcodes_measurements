# pylint: disable=import-outside-toplevel,invalid-name,wrong-import-position
import atexit
import multiprocessing
import os
import sys
import threading
import time
from typing import Any

# Import the binding before pyqtgraph chooses one, including in spawned children.
from PySide6 import QtCore, QtWidgets

# isort: split

import pyqtgraph.multiprocess.remoteproxy
from pyqtgraph.multiprocess.remoteproxy import ClosedError, NoResultError, ObjectProxy

# Allow a speedy import of logging from qcodes_measurements
PREV_REMOTE = os.environ.get("QCM_REMOTE", None)
os.environ["QCM_REMOTE"] = "IMP_LOGGING"
from qcodes_measurements.logging import LoggingStream, get_logger  # noqa: E402

if PREV_REMOTE is None:
    del os.environ["QCM_REMOTE"]
else:
    os.environ["QCM_REMOTE"] = PREV_REMOTE

__all__ = ["Process", "QtProcess", "ClosedError", "NoResultError", "ObjectProxy"]

_spawn_lock = threading.RLock()


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

    def processRequests(self, max_time=None):
        """Drain requests, optionally yielding so Qt can paint and handle input."""
        deadline = None if max_time is None else time.monotonic() + max_time
        with self.processLock:
            if self.exited:
                raise ClosedError()
            count = 0
            try:
                while self.conn.poll():
                    try:
                        self.handleRequest()
                    except ClosedError:
                        raise
                    except Exception:  # pylint: disable=broad-exception-caught
                        self.logger.exception("Error processing remote request")
                    count += 1
                    if deadline is not None and time.monotonic() >= deadline:
                        break
            except (ClosedError, OSError, EOFError) as exc:
                self.exited = True
                raise ClosedError() from exc
            return count


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

        self.logger.info("Starting child process")

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
        try:
            with _spawn_lock:
                old_main = getattr(sys.modules["__main__"], "__file__", None)
                previous_remote = os.environ.get("QCM_REMOTE")
                sys.modules["__main__"].__file__ = __file__
                os.environ["QCM_REMOTE"] = name
                try:
                    self.proc = multiprocessing.get_context("spawn").Process(
                        target=target, kwargs=data
                    )
                    self.proc.start()
                finally:
                    if previous_remote is None:
                        os.environ.pop("QCM_REMOTE", None)
                    else:
                        os.environ["QCM_REMOTE"] = previous_remote
                    if old_main is None:
                        del sys.modules["__main__"].__file__
                    else:
                        sys.modules["__main__"].__file__ = old_main
        except BaseException:
            self.conn.close()
            raise
        finally:
            child_conn.close()

        ## Connect the child process event handler to self.conn
        RemoteEventHandler.__init__(
            self, self.conn, name + "_parent", pid=self.proc.pid, logger=self.logger
        )
        self.logger.debug("Connected to child process.")

        atexit.register(self.join)

    def join(self, timeout=10):
        """Request shutdown; terminate an unresponsive child and always release IPC."""
        try:
            if self.proc.is_alive():
                # A full pipe or another sender holding sendLock must not prevent
                # the shutdown timeout from taking effect.
                closer = threading.Thread(target=self._request_close, daemon=True)
                closer.start()
                deadline = None if timeout is None else time.monotonic() + timeout
                while self.proc.is_alive():
                    if deadline is not None and time.monotonic() >= deadline:
                        break
                    try:
                        RemoteEventHandler.processRequests(self, max_time=0.005)
                    except ClosedError:
                        pass
                    self.proc.join(0.01)
                if self.proc.is_alive():
                    self.proc.terminate()
                    self.proc.join(1)
                    if self.proc.is_alive():
                        self.proc.kill()
                        self.proc.join(1)
                    raise TimeoutError("Terminated unresponsive remote process during shutdown")
            else:
                self.proc.join()
        finally:
            self.exited = True
            self.conn.close()
            if self.handlers.get(self.proc.pid) is self:
                self.handlers.pop(self.proc.pid)
            atexit.unregister(self.join)

    def _request_close(self):
        try:
            # Keep receiving until the child exits: it may be completing a
            # callback to the parent before it can consume the close request.
            self.send(request="close", callSync="off")
        except (ClosedError, OSError):
            pass


def startEventLoop(name, conn, ppid, debug=False):
    # Set up environment
    os.environ["QCM_REMOTE"] = name

    # Set up logger
    logger = get_logger(debug=debug)

    # Redirect stdout and stderr to the logger
    sys.stdout = LoggingStream(logger, "info")
    sys.stderr = LoggingStream(logger, "error")

    logger.info("Connected; starting remote proxy.\n")

    handler = RemoteEventHandler(conn, name, ppid, logger=logger)
    while True:
        try:
            handler.processRequests()  # exception raised when the loop should exit
            time.sleep(0.01)
        except (ClosedError, BrokenPipeError):
            logger.debug("Exiting server loop.")
            sys.exit(0)


##Special set of subclasses that implement a Qt event loop instead.


class _RequestPump(QtCore.QObject):
    """Wake on pipe activity on POSIX; retain a timer fallback for Windows."""

    stopRequested = QtCore.Signal()

    def __init__(self, connection, callback):
        app = QtCore.QCoreApplication.instance()
        if app is None:
            raise RuntimeError("Qt request processing requires an application")
        super().__init__(app)
        self._app = app
        self.callback = callback
        self.running = False
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self._process)
        self.notifier = None
        if os.name != "nt":
            self.notifier = QtCore.QSocketNotifier(
                connection.fileno(), QtCore.QSocketNotifier.Type.Read, self
            )
            self.notifier.setEnabled(False)
            self.notifier.activated.connect(self._process)
        self.stopRequested.connect(self.stop)
        app.aboutToQuit.connect(self.stop)

    def _check_thread(self):
        if QtCore.QThread.currentThread() != self.thread():
            raise RuntimeError("Control Qt request processing from its owning thread")

    def start(self, interval=0.01):
        self._check_thread()
        self.running = True
        if self.notifier is not None:
            self.notifier.setEnabled(True)
        else:
            self.timer.start(max(1, int(interval * 1000)))

    @QtCore.Slot()
    def stop(self):
        self._check_thread()
        self.running = False
        self.timer.stop()
        if self.notifier is not None:
            self.notifier.setEnabled(False)

    def request_stop(self):
        if QtCore.QThread.currentThread() == self.thread():
            self.stop()
        else:
            self.stopRequested.emit()

    def close(self):
        if self._app is None:
            return
        if QtCore.QCoreApplication.instance() is not self._app:
            # The host may already have destroyed the C++ QObject tree.
            self.running = False
            self.callback = None
            self._app = None
            return
        self.stop()
        self._app.aboutToQuit.disconnect(self.stop)
        self._app = None
        self.callback = None
        self.setParent(None)

    def _process(self, *args):
        if not self.running:
            return
        if self.notifier is not None:
            self.notifier.setEnabled(False)
        try:
            if self.callback is not None:
                self.callback()
        finally:
            if self.running and self.notifier is not None:
                self.notifier.setEnabled(True)


class RemoteQtEventHandler(RemoteEventHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._pump = None
        self.timer = None

    def startEventTimer(self):
        if self._pump is None:
            self._pump = _RequestPump(self.conn, self.processRequests)
            self.timer = self._pump.timer
        self._pump.start(interval=0.001)

    def processRequests(self, max_time=0.005):
        try:
            return super().processRequests(max_time=max_time)
        except ClosedError:
            if self._pump is not None:
                self._pump.request_stop()
            instance = QtWidgets.QApplication.instance()
            if instance is not None:
                instance.quit()
            return 0


class QtProcess(Process):
    """A spawned Qt GUI with optional parent callbacks.

    The child always runs its own event loop. Parent callbacks are dispatched
    by the host's Qt event loop, which must already be integrated by the host.
    With processRequests=False, no QApplication or event pump is needed in
    the parent; synchronous RPCs still process incoming requests while waiting.
    """

    _qcm_proxy_helpers: Any

    def __init__(self, **kwds):
        kwds.setdefault("target", startQtEventLoop)
        self._processRequests = kwds.pop("processRequests", True)
        self._pump = None
        if self._processRequests:
            app = QtWidgets.QApplication.instance()
            if not isinstance(app, QtWidgets.QApplication):
                raise RuntimeError(
                    "Create QApplication before QtProcess, or use processRequests=False"
                )
            if QtCore.QThread.currentThread() != app.thread():
                raise RuntimeError("Create QtProcess with parent callbacks in the Qt main thread")
        super().__init__(**kwds)
        try:
            self.startEventTimer()
        except BaseException:
            super().join(timeout=1)
            raise

    def join(self, timeout=10):
        if self._pump is not None:
            self._pump.close()
        super().join(timeout)

    def startEventTimer(self):
        if self._processRequests and self._pump is None:
            self._pump = _RequestPump(self.conn, self.processRequests)
            self.timer = self._pump.timer
            self.startRequestProcessing()

    def startRequestProcessing(self, interval=0.01):
        """Enable the existing connection once, without accumulating slots."""
        if self.exited:
            raise ClosedError()
        if self._pump is None:
            raise RuntimeError("Parent callbacks were disabled for this QtProcess")
        self._pump.start(interval)

    def stopRequestProcessing(self):
        if self._pump is not None:
            self._pump.stop()

    def processRequests(self, max_time=0.005):
        try:
            return super().processRequests(max_time=max_time)
        except ClosedError:
            if self._pump is not None:
                self._pump.request_stop()
            return 0


def startQtEventLoop(name, conn, ppid, debug=False):
    # Set up environment
    os.environ["QCM_REMOTE"] = name

    # Get logger
    logger = get_logger(debug=debug)

    # Redirect stdout and stderr to the logger
    sys.stdout = LoggingStream(logger, "info")
    sys.stderr = LoggingStream(logger, "error")

    logger.info("Connected; starting remote proxy.")
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([])
    if not isinstance(app, QtWidgets.QApplication):
        raise RuntimeError("The plotting process requires a QApplication")
    # Keep the child available when all plot windows are closed.
    app.setQuitOnLastWindowClosed(False)

    handler = RemoteQtEventHandler(conn, name, ppid, logger=logger)
    handler.startEventTimer()
    sys.exit(app.exec())
