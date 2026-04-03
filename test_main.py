import unittest
from unittest.mock import MagicMock, patch
import docker
import os

# Mock environment variables to prevent loading real config
os.environ["DISCORD_WEBHOOK_URL"] = "http://mock"
os.environ["CHECK_INTERVAL"] = "1"

from main import WatcherService

@patch('discord_notifier.requests.post')
class TestWatcherService(unittest.TestCase):
    @patch('main.docker.from_env')
    def setUp(self, mock_docker):
        self.mock_client = MagicMock()
        mock_docker.return_value = self.mock_client
        self.service = WatcherService()
        self.service.config.health_check_retries = 1
        self.service.config.health_check_delay = 0

    def test_process_container_success(self, mock_post):
        mock_container = MagicMock()
        mock_container.name = "test_app"
        mock_container.image.id = "old_image_hash"
        
        self.service.docker.get_image_ref = MagicMock(return_value="test_app:latest")
        self.service.docker.check_for_update = MagicMock(return_value="update_available")
        self.service.docker.get_recreation_plan = MagicMock(return_value={"create_args": {}, "networks": {}})
        self.service.docker.recreate = MagicMock()
        self.service.docker.remove_backup = MagicMock()
        self.service.health.wait_for_health = MagicMock(return_value=True)
        
        status = self.service.process_container(mock_container, auto_update=True)
        
        self.assertEqual(status, "updated")
        self.service.docker.recreate.assert_called_once()
        self.service.docker.remove_backup.assert_called_once_with("test_app")

    def test_process_container_rollback(self, mock_post):
        mock_container = MagicMock()
        mock_container.name = "test_app"
        mock_container.image.id = "old_image_hash"
        
        self.service.docker.get_image_ref = MagicMock(return_value="test_app:latest")
        self.service.docker.check_for_update = MagicMock(return_value="update_available")
        self.service.docker.get_recreation_plan = MagicMock(return_value={"create_args": {}, "networks": {}})
        self.service.docker.recreate = MagicMock()
        
        self.service.health.wait_for_health = MagicMock(return_value=False)
        self.service.perform_rollback = MagicMock()
        
        status = self.service.process_container(mock_container, auto_update=True)
        
        self.assertEqual(status, "rolled_back")
        self.service.perform_rollback.assert_called_once_with("test_app", "old_image_hash")

    def test_perform_rollback_rename_failure(self, mock_post):
        mock_current = MagicMock()
        mock_current.image.id = "old_image_hash"
        self.mock_client.containers.get.side_effect = [mock_current]
        
        self.service.perform_rollback("test_app", "old_image_hash")
        
        mock_current.start.assert_called_once()
        mock_current.remove.assert_not_called()

    def test_perform_rollback_success(self, mock_post):
        mock_current = MagicMock()
        mock_current.image.id = "new_image_hash"
        
        mock_backup = MagicMock()
        
        self.mock_client.containers.get.side_effect = [mock_current, mock_backup]
        self.service.health.wait_for_health = MagicMock(return_value=True)
        
        self.service.perform_rollback("test_app", "old_image_hash")
        
        mock_current.remove.assert_called_once_with(force=True)
        mock_backup.rename.assert_called_once_with("test_app")
        mock_backup.start.assert_called_once()

    def test_restart_dependents_deduplication(self, mock_post):
        dep1 = MagicMock()
        dep1.name = "dep1"
        dep1.id = "id1"
        dep1.labels = {"watcher.depends_on": "app1, app2"}
        
        dep2 = MagicMock()
        dep2.name = "dep2"
        dep2.id = "id2"
        dep2.labels = {"watcher.depends_on": "app2"}

        self.mock_client.containers.list.return_value = [dep1, dep2]
        
        self.service.restart_dependents(["app1", "app2"])
        
        dep1.restart.assert_called_once()
        dep2.restart.assert_called_once()

if __name__ == '__main__':
    unittest.main()
