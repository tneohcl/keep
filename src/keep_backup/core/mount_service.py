"""Repository-open coordination with explicit states and injected operations.

This module has no Qt dependency. Recovery prompts and backend calls are supplied
by the caller, so cancellation, retries and re-entry can be tested in isolation.
"""
from enum import Enum

class MountState(Enum):
    IDLE = "idle"
    OPENING = "opening"
    RECOVERING = "recovering"
    READY = "ready"
    FAILED = "failed"

class MountCoordinator:
    def __init__(self):
        self.state = MountState.IDLE
        self.last_error = ""

    @property
    def busy(self):
        return self.state in (MountState.OPENING, MountState.RECOVERING)

    def open(self, operation):
        """Reject nested opens; always release the busy state after failure."""
        if self.busy:
            return False
        self.state = MountState.OPENING
        self.last_error = ""
        try:
            result = operation()
            self.state = MountState.READY if result else MountState.FAILED
            return result
        except Exception as error:
            self.state = MountState.FAILED
            self.last_error = str(error)
            raise

    def mount_with_recovery(self, attempt, classify, recover_key, unlock):
        """Each recovery kind gets one retry; cancellation never implies success."""
        ok, error = attempt()
        if not ok and classify(error) == "key_missing":
            self.state = MountState.RECOVERING
            if recover_key():
                self.state = MountState.OPENING
                ok, error = attempt()
        if not ok and classify(error) == "wrong_passphrase":
            self.state = MountState.RECOVERING
            if unlock():
                self.state = MountState.OPENING
                ok, error = attempt()
        self.last_error = "" if ok else error
        return ok, error

    def closed(self):
        # Unmounting an old archive is part of an in-flight open transaction.
        if not self.busy:
            self.state = MountState.IDLE
        self.last_error = ""
