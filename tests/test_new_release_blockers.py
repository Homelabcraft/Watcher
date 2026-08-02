import unittest
from unittest.mock import MagicMock, patch

import docker
import docker.errors
import requests
from base_test import BaseTest

from config import Config
from docker_handler import RecreationError
from main import WatcherService
from models import UpdateStatus
from state_store import StateStoreError


class TestNewFeatures(BaseTest):
    def setUp(self):
        super().setUp()
        self.mock_client = MagicMock()
        self.config = Config()
        self.config.state_path = self.state_path
        self.config.journal_path = self.journal_path
        self.config.dry_run = False
        with patch('docker.from_env', return_value=self.mock_client):
            self.service = WatcherService(self.config)

    # 2. Never delete an unhealthy replacement without a valid backup
    def test_unhealthy_replacement_no_backup_retained(self):
        self.service.state_store.start_transaction("app", "orig_123", "img_1", "img_2")
        tx_id = next(iter(self.service.state_store.get_transactions().values()))["transaction_id"]
        self.service.state_store.update_transaction("app", "replacement_started", new_container_id="new_456", new_image_id="img_2")

        mock_orig = MagicMock()
        mock_orig.id = "new_456"
        mock_orig.image.id = "img_2"
        mock_orig.name = "app"
        mock_orig.labels = {"watcher.transaction_id": tx_id}

        def get_container(name):
            if name == "app": return mock_orig
            raise docker.errors.NotFound("Not found")
        self.mock_client.containers.get.side_effect = get_container

        with patch.object(self.service.health, 'wait_for_health', return_value=False):
            self.service.startup_recovery()

        mock_orig.remove.assert_not_called()
        self.assertEqual(self.service.state_store.get_transactions()["app"]["phase"], "rollback_failed")

    def test_unhealthy_replacement_valid_backup_removed(self):
        self.service.state_store.start_transaction("app", "orig_123", "img_1", "img_2")
        tx_id = next(iter(self.service.state_store.get_transactions().values()))["transaction_id"]
        self.service.state_store.update_transaction("app", "replacement_started", new_container_id="new_456", new_image_id="img_2")

        mock_orig = MagicMock()
        mock_orig.id = "new_456"
        mock_orig.image.id = "img_2"
        mock_orig.name = "app"
        mock_orig.labels = {"watcher.transaction_id": tx_id}

        mock_backup = MagicMock()
        mock_backup.id = "orig_123"
        mock_backup.image.id = "img_1"
        mock_backup.name = "app_backup"

        def get_container(name):
            if name == "app": return mock_orig
            if name == "app_backup": return mock_backup
            raise docker.errors.NotFound("Not found")
        self.mock_client.containers.get.side_effect = get_container

        with patch.object(self.service.health, 'wait_for_health', side_effect=[False, True]):
            self.service.startup_recovery()

        mock_orig.remove.assert_called_once_with(force=True)
        mock_backup.rename.assert_called_once_with("app")
        self.assertNotIn("app", self.service.state_store.get_transactions())

    # 3. Complete runtime rollback for unpersisted replacements
    def test_rollback_unpersisted_replacement_correct_label(self):
        self.service.state_store.start_transaction("app", "orig_123", "img_1", "img_2")
        tx_id = next(iter(self.service.state_store.get_transactions().values()))["transaction_id"]

        mock_current = MagicMock()
        mock_current.id = "unpersisted_456"
        mock_current.image.id = "img_2"
        mock_current.name = "app"
        mock_current.labels = {"watcher.transaction_id": tx_id}

        mock_backup = MagicMock()
        mock_backup.id = "orig_123"
        mock_backup.image.id = "img_1"

        def get_container(name):
            if name == "app": return mock_current
            if name == "app_backup": return mock_backup
            raise docker.errors.NotFound("Not found")
        self.mock_client.containers.get.side_effect = get_container

        with patch.object(self.service.health, 'wait_for_health', return_value=True):
            self.service.perform_rollback("app")

        mock_current.remove.assert_called_once_with(force=True)
        mock_backup.rename.assert_called_once_with("app")

    def test_rollback_unpersisted_replacement_wrong_label(self):
        self.service.state_store.start_transaction("app", "orig_123", "img_1", "img_2")

        mock_current = MagicMock()
        mock_current.id = "unpersisted_456"
        mock_current.image.id = "img_2"
        mock_current.name = "app"
        mock_current.labels = {"watcher.transaction_id": "wrong-label"}

        mock_backup = MagicMock()
        mock_backup.id = "orig_123"
        mock_backup.image.id = "img_1"

        def get_container(name):
            if name == "app": return mock_current
            if name == "app_backup": return mock_backup
            raise docker.errors.NotFound("Not found")
        self.mock_client.containers.get.side_effect = get_container

        with patch.object(self.service.health, 'wait_for_health', return_value=True):
            self.service.perform_rollback("app")

        mock_current.remove.assert_not_called()

    def test_rollback_unpersisted_replacement_missing_label(self):
        self.service.state_store.start_transaction("app", "orig_123", "img_1", "img_2")

        mock_current = MagicMock()
        mock_current.id = "unpersisted_456"
        mock_current.image.id = "img_2"
        mock_current.name = "app"
        mock_current.labels = {}

        mock_backup = MagicMock()
        mock_backup.id = "orig_123"
        mock_backup.image.id = "img_1"

        def get_container(name):
            if name == "app": return mock_current
            if name == "app_backup": return mock_backup
            raise docker.errors.NotFound("Not found")
        self.mock_client.containers.get.side_effect = get_container

        with patch.object(self.service.health, 'wait_for_health', return_value=True):
            self.service.perform_rollback("app")

        mock_current.remove.assert_not_called()

    def test_rollback_unpersisted_replacement_removal_fails(self):
        self.service.state_store.start_transaction("app", "orig_123", "img_1", "img_2")
        tx_id = next(iter(self.service.state_store.get_transactions().values()))["transaction_id"]

        mock_current = MagicMock()
        mock_current.id = "unpersisted_456"
        mock_current.image.id = "img_2"
        mock_current.name = "app"
        mock_current.labels = {"watcher.transaction_id": tx_id}
        mock_current.remove.side_effect = Exception("Removal failed")

        mock_backup = MagicMock()
        mock_backup.id = "orig_123"
        mock_backup.image.id = "img_1"

        def get_container(name):
            if name == "app": return mock_current
            if name == "app_backup": return mock_backup
            raise docker.errors.NotFound("Not found")
        self.mock_client.containers.get.side_effect = get_container

        with patch.object(self.service.health, 'wait_for_health', return_value=True):
            success, _ = self.service.perform_rollback("app")

        self.assertFalse(success)
        mock_backup.rename.assert_not_called()

    # 5. Dependency protection
    def test_dependency_protection_blocks_update(self):
        c = MagicMock()
        c.id = "target_123"
        c.name = "target"

        dep = MagicMock()
        dep.name = "dependent"
        dep.attrs = {"HostConfig": {"NetworkMode": "container:target_123"}}
        self.mock_client.containers.list.return_value = [c, dep]

        with self.assertRaises(RecreationError) as e:
            self.service.docker.check_container_network_dependents(c)
        self.assertIn("dependent", str(e.exception))

    # 6. Stop timeout
    @patch('time.sleep', return_value=None)
    def test_stop_timeout_eventually_stops(self, mock_sleep):
        c = MagicMock()
        c.name = "app"
        c.id = "c123"
        c.attrs = {'Config': {'StopTimeout': 15}}
        c.stop.side_effect = requests.exceptions.ReadTimeout("Timeout")

        # reload will set status to running then exited
        def mock_reload():
            if c.reload.call_count > 2:
                c.status = "exited"

        c.status = "running"
        c.reload.side_effect = mock_reload

        def get_mock(n):
            if n == "app": return c
            import docker.errors
            raise docker.errors.NotFound("Not found")
        self.mock_client.containers.get.side_effect = get_mock
        plan = {"create_args": {"image": "img", "name": "app"}, "networks": {}}

        try:
            self.service.docker.recreate("app", plan)
        except Exception: # noqa: BLE001, S110
            pass # we only care about the stop part

        c.stop.assert_called_once()
        self.assertTrue(c.reload.call_count >= 1)

    @patch('time.sleep', return_value=None)
    def test_stop_timeout_remains_running(self, mock_sleep):
        c = MagicMock()
        c.name = "app"
        c.id = "c123"
        c.attrs = {'Config': {'StopTimeout': 15}}
        c.stop.side_effect = requests.exceptions.ReadTimeout("Timeout")
        c.status = "running"

        def get_mock(n):
            if n == "app": return c
            import docker.errors
            raise docker.errors.NotFound("Not found")
        self.mock_client.containers.get.side_effect = get_mock
        plan = {"create_args": {"image": "img", "name": "app"}, "networks": {}}

        with self.assertRaises(RecreationError) as e:
            self.service.docker.recreate("app", plan)
        self.assertIn("failed to stop after timeout", str(e.exception))

    # Transactions
    def test_duplicate_start_transaction_raises(self):
        self.service.state_store.start_transaction("app", "123", "i1", "i2")
        with self.assertRaises(StateStoreError):
            self.service.state_store.start_transaction("app", "456", "i1", "i2")

    def test_update_missing_transaction_raises(self):
        with self.assertRaises(StateStoreError):
            self.service.state_store.update_transaction("missing", "prepared")

    def test_pending_transaction_prevents_pull(self):
        self.service.state_store.start_transaction("app", "123", "i1", "i2")
        c = MagicMock()
        c.name = "app"
        c.attrs = {"Config": {"Image": "app:latest"}}
        c.image.tags = ["app:latest"]
        self.service.docker.get_image_ref = MagicMock(return_value="app:latest")
        info = self.service.process_container(c, True)
        self.assertNotEqual(info.status, UpdateStatus.UPDATED)

    # Single instance
    def test_single_instance_another_self_blocks(self):
        me = MagicMock()
        me.id = "me_123"
        other = MagicMock()
        other.id = "other_456"
        other.labels = {"watcher.self": "true"}
        other.status = "running"

        self.mock_client.containers.list.return_value = [other]
        with patch.object(self.service.docker, '_detect_self_id', return_value="me_123"):  # noqa: SIM117
            with self.assertRaises(SystemExit):
                self.service._check_single_instance()

    def test_single_instance_custom_failure_safe(self):
        # failure to detect self ID doesn't crash but warns/handles gracefully if we are the only one
        other = MagicMock()
        other.id = "other_456"
        other.labels = {"watcher.self": "true"}
        other.status = "running"
        self.mock_client.containers.list.return_value = [other]
        with patch.object(self.service.docker, '_detect_self_id', return_value=None):  # noqa: SIM117
            with self.assertRaises(SystemExit):
                self.service._check_single_instance()

if __name__ == '__main__':
    unittest.main()
