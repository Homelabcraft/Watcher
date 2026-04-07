import unittest
from unittest.mock import MagicMock, patch
import docker
from main import WatcherService
from docker_handler import DockerHandler

class TestRollbackBug(unittest.TestCase):
    def setUp(self):
        self.mock_client = MagicMock()
        with patch('docker.from_env', return_value=self.mock_client):
            self.service = WatcherService()
            self.service.docker.client = self.mock_client

    def test_user_handling_empty_string(self):
        """Verify that empty user strings are not passed to create_args."""
        mock_container = MagicMock()
        mock_container.attrs = {
            'Config': {'User': ''}, # Typical empty user
            'HostConfig': {},
            'NetworkSettings': {'Networks': {}}
        }
        mock_container.image.tags = ['app:latest']
        
        plan = self.service.docker.get_recreation_plan(mock_container)
        # 'user' should NOT be in create_args if it was an empty string
        self.assertNotIn('user', plan['create_args'])

    def test_recreate_index_error_prevention(self):
        """Verify that containers without networks don't cause IndexError."""
        plan = {
            "create_args": {"name": "test", "image": "img", "network_mode": "host"},
            "networks": {} # Empty networks
        }
        
        # This should NOT raise IndexError
        try:
            self.service.docker.recreate("test", plan)
        except Exception as e:
            if isinstance(e, IndexError):
                self.fail("recreate() raised IndexError unexpectedly!")
            # Other errors are expected because we use mocks incorrectly here, 
            # but we only care about IndexError.
            pass

    def test_rollback_defensive_flow(self):
        """Verify that rollback handles missing current container and missing backup gracefully."""
        self.service.client.containers.get.side_effect = docker.errors.NotFound("Not found")
        
        # This should NOT raise IndexError
        try:
            self.service.perform_rollback("test", "old_id")
        except Exception as e:
            self.fail(f"perform_rollback() raised {type(e).__name__} unexpectedly: {e}")

if __name__ == '__main__':
    unittest.main()
