import unittest
from unittest.mock import MagicMock, patch
import docker
import os

# Mock environment variables to prevent loading real config
os.environ["DISCORD_WEBHOOK_URL"] = "http://mock"
os.environ["CHECK_INTERVAL"] = "1"

from main import WatcherService
from discord_notifier import DiscordNotifier

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
        mock_container.id = "old_id"
        mock_container.image.id = "old_image_hash"
        
        self.service.docker.get_image_ref = MagicMock(return_value="test_app:latest")
        self.service.docker.check_for_update = MagicMock(return_value="update_available")
        self.service.docker.get_recreation_plan = MagicMock(return_value={"create_args": {}, "networks": {}})
        self.service.docker.recreate = MagicMock()
        self.service.docker.remove_backup = MagicMock()
        self.service.health.wait_for_health = MagicMock(return_value=True)
        
        status, old_id = self.service.process_container(mock_container, auto_update=True)
        
        self.assertEqual(status, "updated")
        self.assertEqual(old_id, "old_id")
        self.service.docker.recreate.assert_called_once()
        self.service.docker.remove_backup.assert_called_once_with("test_app")
        self.assertEqual(mock_post.call_count, 2)
        second_payload = mock_post.call_args_list[1][1]["json"]
        self.assertIn("Update OK", second_payload["embeds"][0]["title"])
        self.assertIn("test_app:latest", second_payload["embeds"][0]["description"])

    def test_process_container_rollback(self, mock_post):
        mock_container = MagicMock()
        mock_container.name = "test_app"
        mock_container.id = "old_id"
        mock_container.image.id = "old_image_hash"
        
        self.service.docker.get_image_ref = MagicMock(return_value="test_app:latest")
        self.service.docker.check_for_update = MagicMock(return_value="update_available")
        self.service.docker.get_recreation_plan = MagicMock(return_value={"create_args": {}, "networks": {}})
        self.service.docker.recreate = MagicMock()
        
        self.service.health.wait_for_health = MagicMock(return_value=False)
        self.service.perform_rollback = MagicMock()
        
        status, old_id = self.service.process_container(mock_container, auto_update=True)
        
        self.assertEqual(status, "rolled_back")
        self.assertIsNone(old_id)
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

    def test_restart_dependents_network_mode_name(self, mock_post):
        dep = MagicMock()
        dep.name = "dep"
        dep.id = "dep_id"
        dep.labels = {}
        dep.attrs = {"HostConfig": {"NetworkMode": "container:app1"}}

        self.mock_client.containers.list.return_value = [dep]
        
        restarted, failures = self.service.restart_dependents([{'name': 'app1', 'old_id': 'id1'}])
        
        dep.restart.assert_called_once()
        self.assertEqual(restarted, ["dep"])
        self.assertEqual(failures, [])

    def test_restart_dependents_network_mode_id_warning(self, mock_post):
        dep = MagicMock()
        dep.name = "dep"
        dep.id = "dep_id"
        dep.labels = {}
        dep.attrs = {"HostConfig": {"NetworkMode": "container:id1"}}

        self.mock_client.containers.list.return_value = [dep]
        
        with self.assertLogs('Watcher', level='WARNING') as cm:
            restarted, failures = self.service.restart_dependents([{'name': 'app1', 'old_id': 'id1'}])
            self.assertTrue(any("UNSUPPORTED DEPENDENCY" in output for output in cm.output))
        
        dep.restart.assert_not_called()
        self.assertEqual(restarted, [])
        self.assertEqual(failures, [])

    def test_get_recreation_plan_advanced_fields(self, mock_post):
        mock_container = MagicMock()
        mock_container.attrs = {
            'Config': {'Image': 'app:latest'},
            'HostConfig': {
                'Ulimits': [{'Name': 'nofile', 'Soft': 1024, 'Hard': 2048}],
                'LogConfig': {'Type': 'json-file', 'Config': {'max-size': '10m'}},
                'ShmSize': 67108864,
                'IpcMode': 'shareable',
                'PidMode': 'host',
                'Sysctls': {'net.core.somaxconn': '1024'}
            },
            'NetworkSettings': {'Networks': {}}
        }
        
        plan = self.service.docker.get_recreation_plan(mock_container)
        ca = plan["create_args"]
        
        self.assertEqual(len(ca["ulimits"]), 1)
        self.assertEqual(ca["ulimits"][0].name, 'nofile')
        self.assertEqual(ca["log_config"].type, 'json-file')
        self.assertEqual(ca["shm_size"], 67108864)
        self.assertEqual(ca["ipc_mode"], 'shareable')
        self.assertEqual(ca["pid_mode"], 'host')
        self.assertEqual(ca["sysctls"]['net.core.somaxconn'], '1024')

    def test_restart_dependents_deduplication(self, mock_post):
        dep1 = MagicMock()
        dep1.name = "dep1"
        dep1.id = "id1"
        dep1.labels = {"watcher.depends_on": "app1, app2"}
        dep1.attrs = {"HostConfig": {"NetworkMode": ""}}
        
        dep2 = MagicMock()
        dep2.name = "dep2"
        dep2.id = "id2"
        dep2.labels = {"watcher.depends_on": "app2"}
        dep2.attrs = {"HostConfig": {"NetworkMode": ""}}

        self.mock_client.containers.list.return_value = [dep1, dep2]
        
        restarted, failures = self.service.restart_dependents(
            [{'name': 'app1', 'old_id': 'id1'}, {'name': 'app2', 'old_id': 'id2'}]
        )
        
        dep1.restart.assert_called_once()
        dep2.restart.assert_called_once()
        self.assertEqual(sorted(restarted), ["dep1", "dep2"])
        self.assertEqual(failures, [])

    def test_process_container_reported(self, mock_post):
        mock_container = MagicMock()
        mock_container.name = "test_app"
        self.service.docker.check_for_update = MagicMock(return_value="update_available")
        self.service.docker.recreate = MagicMock()
        
        status, _ = self.service.process_container(mock_container, auto_update=False)
        
        self.assertEqual(status, "reported")
        self.service.docker.recreate.assert_not_called()

    def test_get_watched_containers_watch_by_label_true(self, mock_post):
        self.service.config.watch_by_label = True
        self.service.config.watch_label_key = "watcher.enable"
        self.service.config.watch_label_value = "true"
        
        c_unlabeled = MagicMock()
        c_unlabeled.labels = {}
        c_unlabeled.image.tags = ["app:latest"]
        
        c_false = MagicMock()
        c_false.labels = {"watcher.enable": "false"}
        c_false.image.tags = ["app:latest"]
        
        c_true = MagicMock()
        c_true.labels = {"watcher.enable": "true"}
        c_true.image.tags = ["app:latest"]
        
        self.mock_client.containers.list.return_value = [c_unlabeled, c_false, c_true]
        
        auto_update, monitor_only = self.service.docker.get_watched_containers()
        
        self.assertIn(c_true, auto_update)
        self.assertNotIn(c_unlabeled, auto_update)
        self.assertNotIn(c_false, auto_update)
        
        self.assertIn(c_unlabeled, monitor_only)
        self.assertIn(c_false, monitor_only)

    def test_get_watched_containers_watch_by_label_false(self, mock_post):
        self.service.config.watch_by_label = False
        self.service.config.watch_label_key = "watcher.enable"
        self.service.config.watch_label_value = "true"
        
        c_unlabeled = MagicMock()
        c_unlabeled.labels = {}
        c_unlabeled.image.tags = ["app:latest"]
        
        c_false = MagicMock()
        c_false.labels = {"watcher.enable": "false"}
        c_false.image.tags = ["app:latest"]
        
        c_true = MagicMock()
        c_true.labels = {"watcher.enable": "true"}
        c_true.image.tags = ["app:latest"]
        
        self.mock_client.containers.list.return_value = [c_unlabeled, c_false, c_true]
        
        auto_update, monitor_only = self.service.docker.get_watched_containers()
        
        self.assertIn(c_unlabeled, auto_update)
        self.assertIn(c_true, auto_update)
        self.assertIn(c_false, monitor_only)

    def test_restart_dependents_skip_just_updated(self, mock_post):
        """Verify that containers updated in the current cycle are not restarted as dependents."""
        dep1 = MagicMock()
        dep1.name = "just_updated"
        dep1.id = "id1"
        dep1.labels = {"watcher.depends_on": "other_app"}
        dep1.attrs = {"HostConfig": {"NetworkMode": ""}}
        
        self.mock_client.containers.list.return_value = [dep1]
        
        # 'just_updated' is in the updated_containers list
        restarted, failures = self.service.restart_dependents(
            [{'name': 'other_app', 'old_id': 'id_other'}, {'name': 'just_updated', 'old_id': 'id1'}]
        )
        
        # Should NOT be restarted because it was just updated
        dep1.restart.assert_not_called()
        self.assertEqual(restarted, [])
        self.assertEqual(failures, [])

    def test_summary_includes_reported(self, mock_post):
        summary = {"updated": [], "failed": [], "rolled_back": [], "reported": ["app1", "app2"]}
        self.service.notifier.notify_summary_report(summary)
        
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        payload = kwargs.get('json')
        description = payload["embeds"][0]["description"]
        self.assertIn("Updates Available", description)
        self.assertIn("app1", description)
        self.assertIn("app2", description)

    def test_restart_dependents_records_failure(self, mock_post):
        dep = MagicMock()
        dep.name = "bad_dep"
        dep.id = "x"
        dep.labels = {"watcher.depends_on": "app1"}
        dep.attrs = {"HostConfig": {"NetworkMode": ""}}
        dep.restart.side_effect = RuntimeError("restart failed")
        self.mock_client.containers.list.return_value = [dep]
        restarted, failures = self.service.restart_dependents([{"name": "app1", "old_id": "id1"}])
        self.assertEqual(restarted, [])
        self.assertEqual(failures, [("bad_dep", "restart failed")])


@patch('discord_notifier.requests.post')
class TestDiscordNotifier(unittest.TestCase):
    def test_notify_dependents_restarted_skips_when_empty(self, mock_post):
        DiscordNotifier("http://mock").notify_dependents_restarted([], [])
        mock_post.assert_not_called()

    def test_notify_dependents_restarted_embed(self, mock_post):
        DiscordNotifier("http://mock").notify_dependents_restarted(["dep1"], [("dep2", "boom")])
        mock_post.assert_called_once()
        desc = mock_post.call_args[1]["json"]["embeds"][0]["description"]
        self.assertIn("dep1", desc)
        self.assertIn("dep2", desc)
        self.assertIn("boom", desc)

    def test_notify_startup_embed(self, mock_post):
        DiscordNotifier("http://mock").notify_startup(86400, "my-host")
        mock_post.assert_called_once()
        embed = mock_post.call_args[1]["json"]["embeds"][0]
        self.assertIn("Watcher started", embed["title"])
        field_names = {f["name"] for f in embed["fields"]}
        self.assertIn("Host", field_names)
        self.assertIn("Scan interval", field_names)
        self.assertIn("my-host", str(embed["fields"]))

    def test_notify_shutdown_embed(self, mock_post):
        DiscordNotifier("http://mock").notify_shutdown("Custom stop reason")
        mock_post.assert_called_once()
        embed = mock_post.call_args[1]["json"]["embeds"][0]
        self.assertIn("stopped", embed["title"].lower())
        self.assertIn("Custom stop reason", embed["description"])


if __name__ == '__main__':
    unittest.main()
