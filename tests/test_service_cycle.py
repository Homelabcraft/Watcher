import unittest
from unittest.mock import MagicMock, patch

import docker
from base_test import BaseTest

# Env setup moved to setUp
from main import WatcherService
from models import ContainerUpdateInfo, UpdateStatus


@patch('discord_notifier.requests.post')
class TestWatcherService(BaseTest):
    @patch('main.docker.from_env')
    def setUp(self, mock_docker):
        super().setUp()

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
        self.service.docker.check_for_update = MagicMock(return_value=(UpdateStatus.UPDATE_AVAILABLE, "old_hash", "new_hash"))
        self.service.docker.get_recreation_plan = MagicMock(return_value={"create_args": {}, "networks": {}})
        self.service.docker.recreate = MagicMock()
        self.service.docker.remove_backup = MagicMock()
        self.service.health.wait_for_health = MagicMock(return_value=True)

        info = self.service.process_container(mock_container, auto_update=True)

        self.assertEqual(info.status, UpdateStatus.UPDATED)
        self.assertEqual(info.old_id, "old_id")
        self.service.docker.recreate.assert_called_once()
        self.service.docker.remove_backup.assert_called_once_with("test_app")

    def test_process_container_rollback(self, mock_post):
        mock_container = MagicMock()
        mock_container.name = "test_app"
        mock_container.id = "old_id"
        mock_container.image.id = "old_image_hash"

        self.service.docker.get_image_ref = MagicMock(return_value="test_app:latest")
        self.service.docker.check_for_update = MagicMock(return_value=(UpdateStatus.UPDATE_AVAILABLE, "old_hash", "new_hash"))
        self.service.docker.get_recreation_plan = MagicMock(return_value={"create_args": {}, "networks": {}})
        self.service.docker.recreate = MagicMock()

        self.service.health.wait_for_health = MagicMock(return_value=False)
        self.service.perform_rollback = MagicMock(return_value=(True, "Mock rollback success"))

        info = self.service.process_container(mock_container, auto_update=True)
        self.assertEqual(info.status, UpdateStatus.ROLLED_BACK)
        self.service.perform_rollback.assert_called_once_with("test_app")

    def test_perform_rollback_rename_failure(self, mock_post):
        self.service.state_store.start_transaction("test_app", "old_image_hash", "img_1", "img_1_new")
        self.service.state_store.update_transaction("test_app", "replacement_verified", backup_container_id="old_image_hash", original_image_id="img_1")

        mock_current = MagicMock()
        mock_current.id = "old_image_hash"
        mock_current.image.id = "img_1"
        def mock_get(n):
            import docker
            if n == "test_app_backup":
                b = MagicMock()
                b.id = "old_image_hash"
                b.image.id = "img_1"
                b.rename.side_effect = Exception("Rename failed")
                return b
            if n == "test_app": raise docker.errors.NotFound("Not found")
            raise docker.errors.NotFound("NotFound")
        self.mock_client.containers.get.side_effect = mock_get

        self.service.perform_rollback("test_app")

        mock_current.start.assert_not_called()
        mock_current.remove.assert_not_called()

    def test_perform_rollback_success(self, mock_post):
        self.service.state_store.start_transaction("test_app", "old_image_hash", "img_1", "img_1_new")
        self.service.state_store.update_transaction("test_app", "replacement_verified", backup_container_id="old_image_hash", new_container_id="new_123", original_image_id="img_1")

        mock_current = MagicMock()
        mock_current.id = "new_123"
        mock_current.image.id = "new_image_hash"

        mock_backup = MagicMock()
        mock_backup.id = "old_image_hash"
        mock_backup.image.id = "img_1"

        def mock_get(n):
            if n == "test_app": return mock_current
            if n == "test_app_backup": return mock_backup
            raise docker.errors.NotFound("NotFound")
        self.mock_client.containers.get.side_effect = mock_get
        self.service.health.wait_for_health = MagicMock(return_value=True)

        self.service.perform_rollback("test_app")

        mock_current.remove.assert_called_once_with(force=True)
        mock_backup.rename.assert_called_once_with("test_app")
        mock_backup.start.assert_called_once()

    def test_perform_rollback_incomplete_rename_success(self, mock_post):
        self.service.state_store.start_transaction("test_app", "old_image_hash", "img_1", "img_1_new")
        self.service.state_store.update_transaction("test_app", "backup_created", original_container_id="old_image_hash", original_image_id="img_1")

        mock_current = MagicMock()
        mock_current.id = "old_image_hash"
        mock_current.image.id = "img_1"
        def mock_get(n):
            import docker
            if n == "test_app": return mock_current
            if n == "test_app_backup": raise docker.errors.NotFound("Not found")
            raise docker.errors.NotFound("NotFound")
        self.mock_client.containers.get.side_effect = mock_get
        self.service.health.wait_for_health = MagicMock(return_value=True)

        res, msg = self.service.perform_rollback("test_app")

        self.assertTrue(res)
        mock_current.start.assert_called_once()
        mock_current.remove.assert_not_called()

    def test_perform_rollback_incomplete_rename_failed(self, mock_post):
        self.service.state_store.start_transaction("test_app", "old_image_hash", "img_1", "img_1_new")
        self.service.state_store.update_transaction("test_app", "backup_created", original_container_id="old_image_hash", original_image_id="img_1")

        mock_current = MagicMock()
        mock_current.id = "old_image_hash"
        mock_current.image.id = "img_1"
        def mock_get(n):
            import docker
            if n == "test_app": return mock_current
            if n == "test_app_backup": raise docker.errors.NotFound("Not found")
            raise docker.errors.NotFound("NotFound")
        self.mock_client.containers.get.side_effect = mock_get
        self.service.health.wait_for_health = MagicMock(return_value=False)

        res, msg = self.service.perform_rollback("test_app")

        self.assertFalse(res)
        mock_current.start.assert_called_once()
        mock_current.remove.assert_not_called()

    def test_restart_dependents_network_mode_name(self, mock_post):
        dep = MagicMock()
        dep.name = "dep"
        dep.id = "dep_id"
        dep.labels = {}
        dep.attrs = {"HostConfig": {"NetworkMode": "container:app1"}}

        self.mock_client.containers.list.return_value = [dep]

        self.service.restart_dependents([ContainerUpdateInfo('app1', 'id1', UpdateStatus.UPDATED)])

        dep.restart.assert_called_once()

    def test_restart_dependents_network_mode_id_warning(self, mock_post):
        dep = MagicMock()
        dep.name = "dep"
        dep.id = "dep_id"
        dep.labels = {}
        dep.attrs = {"HostConfig": {"NetworkMode": "container:id1"}}

        self.mock_client.containers.list.return_value = [dep]

        with self.assertLogs('Watcher', level='WARNING') as cm:
            self.service.restart_dependents([ContainerUpdateInfo('app1', 'id1', UpdateStatus.UPDATED)])
            self.assertTrue(any("UNSUPPORTED DEPENDENCY" in output for output in cm.output))

        dep.restart.assert_not_called()

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

        self.service.restart_dependents([
            ContainerUpdateInfo('app1', 'id1', UpdateStatus.UPDATED),
            ContainerUpdateInfo('app2', 'id2', UpdateStatus.UPDATED)
        ])

        dep1.restart.assert_called_once()
        dep2.restart.assert_called_once()

    def test_process_container_reported(self, mock_post):
        mock_container = MagicMock()
        mock_container.name = "test_app"
        self.service.docker.check_for_update = MagicMock(return_value=(UpdateStatus.UPDATE_AVAILABLE, "old_hash", "new_hash"))
        self.service.docker.recreate = MagicMock()

        info = self.service.process_container(mock_container, auto_update=False)

        self.assertEqual(info.status, UpdateStatus.REPORTED)
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
        self.service.restart_dependents([
            ContainerUpdateInfo('other_app', 'id_other', UpdateStatus.UPDATED),
            ContainerUpdateInfo('just_updated', 'id1', UpdateStatus.UPDATED)
        ])

        # Should NOT be restarted because it was just updated
        dep1.restart.assert_not_called()

    def test_summary_includes_reported(self, mock_post):
        self.service.config.discord_webhook_url = "http://mock"
        from notifier_factory import build_notifier
        self.service.notifier = build_notifier(self.service.config)
        summary = {"updated": [], "failed": [], "rolled_back": [], "reported": ["app1", "app2"]}
        self.service.notifier.notify_summary_report(summary)

        mock_post.assert_called_once()
        _args, kwargs = mock_post.call_args
        self.assertIn("app1", kwargs["json"]["embeds"][0]["description"])

    def test_get_sleep_duration(self, mock_post):
        from datetime import datetime, timedelta
        import zoneinfo

        self.service.config.schedule_time = "14:30"
        self.service.config.tz = "Europe/Zurich"
        
        tz = zoneinfo.ZoneInfo("Europe/Zurich")
        now = datetime.now(tz)
        target_time = datetime.strptime("14:30", "%H:%M").time()
        target_dt = datetime.combine(now.date(), target_time).replace(tzinfo=tz)
        if now >= target_dt:
            target_dt += timedelta(days=1)
        expected_duration = (target_dt - now).total_seconds()
        
        duration = self.service._get_sleep_duration()
        self.assertAlmostEqual(duration, expected_duration, delta=2.0)

if __name__ == '__main__':
    unittest.main()
