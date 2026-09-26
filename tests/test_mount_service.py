import unittest
from unittest.mock import Mock
from mount_service import MountCoordinator, MountState

class MountCoordinatorTests(unittest.TestCase):
    def test_reentry_and_exception_release(self):
        service = MountCoordinator()
        def operation():
            self.assertEqual(service.state, MountState.OPENING)
            nested = Mock()
            self.assertFalse(service.open(nested))
            nested.assert_not_called()
            raise OSError("offline")
        with self.assertRaises(OSError):
            service.open(operation)
        self.assertEqual(service.state, MountState.FAILED)
        self.assertFalse(service.busy)
        self.assertTrue(service.open(lambda: True))
        self.assertEqual(service.state, MountState.READY)
        service.closed()
        self.assertEqual(service.state, MountState.IDLE)

    def test_key_then_passphrase_recovery(self):
        service = MountCoordinator()
        attempt = Mock(side_effect=[(False, "key_missing"), (False, "wrong_passphrase"), (True, "")])
        key, unlock = Mock(return_value=True), Mock(return_value=True)
        self.assertTrue(service.open(lambda: service.mount_with_recovery(attempt, lambda error: error, key, unlock)[0]))
        self.assertEqual(attempt.call_count, 3)
        key.assert_called_once()
        unlock.assert_called_once()
        self.assertEqual(service.state, MountState.READY)

    def test_cancel_and_failed_retry_do_not_loop(self):
        for approved in (False, True):
            service = MountCoordinator()
            attempt = Mock(return_value=(False, "wrong_passphrase"))
            unlock = Mock(return_value=approved)
            self.assertFalse(service.open(lambda: service.mount_with_recovery(attempt, lambda error: error, Mock(), unlock)[0]))
            self.assertEqual(attempt.call_count, 2 if approved else 1)
            self.assertEqual(service.state, MountState.FAILED)
            self.assertEqual(service.last_error, "wrong_passphrase")

    def test_closing_old_mount_preserves_open_guard(self):
        service = MountCoordinator()
        def operation():
            service.closed()
            self.assertTrue(service.busy)
            return True
        self.assertTrue(service.open(operation))
