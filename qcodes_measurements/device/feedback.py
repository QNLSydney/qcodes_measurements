"""
Feedback controller
"""
import time

class Feedback:
    """
    Define a feedback object that will do live feedback on a gate during a measurement.

    This is responsible for restoring the state of the gate at the end of a sweep
    as well, when used in a 1d/2d sweep.
    """
    def __init__(self, gate, meas, target,
                 wait=0.05, tolerance=0.01e-9, step=0.5e-3, max_d=5e-3):
        self.gate = gate # Gate to perform feedback on
        self.meas = meas # Measured parameter to feedback to
        self.target = target # Target value of measured parameter
        self.wait = wait # Wait time after set to do check effect of feedback
        self.tolerance = tolerance # How close do we need to be before we've succeeded
        self.step = step # Step size in either direction
        self.max_d = max_d # Maximum distance to move before we give up

        self.active = False
        self.start_val = None

    def start(self):
        """
        Start feedback
        """
        if self.active:
            raise RuntimeError("Feedback already active... Should have been stopped.")
        # Save the starting gate voltage
        self.start_val = self.gate()
        self.active = True

    def feedback(self, once=False):
        """
        Perform feedback on gate to target

        Parameters:
            once (bool): Perform feedback once, without being active
        """
        if not once and not self.active:
            raise RuntimeError("Feedback not started...")
        # Do feedback
        start_v = self.gate()
        curr_v = start_v
        meas = self.meas()
        while not abs(meas - self.target) < self.tolerance:
            if meas < self.target:
                curr_v += self.step
                self.gate(curr_v)
            else:
                curr_v -= self.step
                self.gate(curr_v)
            if abs(curr_v - start_v) > self.max_d:
                raise RuntimeError("Can't feedback gate to reach target")
            time.sleep(self.wait)
            meas = self.meas()

    def stop(self):
        """
        Stop feedback and restore starting state
        """
        if not self.active:
            raise RuntimeError("Feedback not started...")
        self.gate(self.start_val)
        self.active = False
        self.start_val = None
