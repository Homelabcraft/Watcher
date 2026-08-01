import unittest
from unittest.mock import MagicMock, patch
import docker
from main import WatcherService
from docker_handler import DockerHandler

class TestRollbackBug(unittest.TestCase):
    def setUp(self):
        self.mock_client = MagicMock()
        self.config = MagicMock()
        self.config.allow_user_fallback = True
        self.config.watch_by_label = False
        self.config.dry_run = False
        with patch('docker.from_env', return_value=self.mock_client):
            self.service = WatcherService()
            self.service.docker = DockerHandler(self.mock_client, self.config)
        
        # Prevent "backup already exists" by throwing NotFound for _backup queries
        def mock_get(name):
            if name.endswith("_backup"):
                import docker
                raise docker.errors.NotFound("Not found")
            return MagicMock()
        self.mock_client.containers.get.side_effect = mock_get

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

    def test_recreate_retry_on_user_error_success(self):
        """
        Verify that recreation retries without 'user' if the first attempt fails 
        with a user-resolution error.
        """
        plan = {
            "create_args": {"name": "test", "image": "img", "user": "root"},
            "networks": {}
        }
        
        # Create a mock container that fails to start once
        mock_container = MagicMock()
        # First call fails, second succeeds
        # We will set the side_effect after defining the fake error
        
        # Setup create to return the mock container twice
        self.mock_client.containers.create.return_value = mock_container
        
        # This should call create twice and return the mock_container on the second try
        import docker
        class FakeAPIError(docker.errors.APIError):
            def __init__(self, msg):
                super().__init__(msg, response=None)
                self.msg = msg
            def __str__(self):
                return self.msg
                
        api_error = FakeAPIError("unable to find user root: no matching entries in passwd file")
        mock_container.start.side_effect = [api_error, None]
        
        result = self.service.docker.recreate("test", plan)
        
        self.assertEqual(result, mock_container)
        # Verify user was popped from the plan for the second attempt
        create_calls = self.mock_client.containers.create.call_args_list
        self.assertEqual(len(create_calls), 2)
        
        # First call should have user='root'
        self.assertEqual(create_calls[0][1]['user'], 'root')
        # Second call should NOT have 'user'
        self.assertNotIn('user', create_calls[1][1])
        
        # Verify first container was removed
        mock_container.remove.assert_called()

    def test_recreate_no_retry_on_other_error(self):
        """Verify that recreation does NOT retry on non-user errors."""
        plan = {
            "create_args": {"name": "test", "image": "img", "user": "root"},
            "networks": {}
        }
        
        mock_container = MagicMock()
        mock_container.start.side_effect = Exception("Some random docker error")
        self.mock_client.containers.create.return_value = mock_container
        
        # Ensure rollback doesn't crash
        self.service.perform_rollback = MagicMock(return_value=(True, ""))
        
        from docker_handler import RecreationError
        with self.assertRaisesRegex(RecreationError, "Unexpected Error: Some random docker error"):
            self.service.docker.recreate("test", plan)
            
        # Should only have called create once
        self.assertEqual(self.mock_client.containers.create.call_count, 1)

    def test_get_image_ref_from_config(self):
        """Verify that get_image_ref uses Config.Image if RepoTags are empty."""
        mock_container = MagicMock()
        mock_container.attrs = {'Config': {'Image': 'repo/app:latest'}}
        mock_container.image.tags = [] # Empty RepoTags
        
        ref = self.service.docker.get_image_ref(mock_container)
        self.assertEqual(ref, 'repo/app:latest')

    def test_get_image_ref_skip_non_latest(self):
        """Verify that get_image_ref skips images without :latest."""
        mock_container = MagicMock()
        mock_container.attrs = {'Config': {'Image': 'repo/app:1.2.3'}}
        mock_container.image.tags = ['repo/app:1.2.3']
        
        ref = self.service.docker.get_image_ref(mock_container)
        self.assertIsNone(ref)

    def test_get_watched_containers_empty_tags_but_valid_config(self):
        """Verify that containers with empty image tags but valid Config.Image are selected."""
        c = MagicMock()
        c.name = "crafty"
        c.id = "id1"
        c.labels = {}
        c.attrs = {'Config': {'Image': 'crafty:latest'}}
        c.image.tags = [] # The reported bug case
        
        self.mock_client.containers.list.return_value = [c]
        self.service.config.watch_by_label = False # Default mode
        
        auto, monitor = self.service.docker.get_watched_containers()
        self.assertIn(c, auto)
        self.assertEqual(len(auto), 1)

if __name__ == '__main__':
    unittest.main()
