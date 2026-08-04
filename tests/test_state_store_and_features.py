import os
import unittest
from unittest.mock import MagicMock, patch

import docker
from base_test import BaseTest
from docker.models.containers import Container

from config import Config
from docker_handler import DockerHandler, RecreationError
from health_monitor import HealthMonitor
from journal import Journal
from main import WatcherService
from models import ContainerUpdateInfo, UpdateStatus
from multi_notifier import MultiNotifier
from state_store import StateStore
from telegram_notifier import TelegramNotifier


class TestStateStoreAndFeatures(BaseTest):
    def setUp(self):
        super().setUp()
        self.mock_client = MagicMock()
        self.config = Config()
        self.config.dry_run = False

    def test_telegram_html_escape(self):
        """Telegram HTML Formatting: Ensure < > & are escaped but <pre> remains."""
        self.config.telegram_bot_token = "mock"
        notifier = TelegramNotifier("mock", "mock")

        info = ContainerUpdateInfo(name="test", old_id="a", status=UpdateStatus.FAILED, image_ref="test:latest")
        info.error_step = "pull"
        # Tricky error message with raw HTML characters
        info.error_message = 'Failed to pull <my_image> & "other"'
        info.rollback_attempted = True
        info.rollback_success = False
        info.rollback_details = '<rollback & fail>'

        with patch('requests.post') as mock_post:
            notifier.notify_update_failure(info)

            mock_post.assert_called_once()
            payload = mock_post.call_args[1].get('json', {})
            body = payload.get('text', '')

            # Escaped parts should be in there
            self.assertIn('&lt;my_image&gt; &amp; &quot;other&quot;', body)
            self.assertIn('&lt;rollback &amp; fail&gt;', body)
            # Pre tags should still exist as raw tags
            self.assertIn('<pre>', body)
            self.assertIn('</pre>', body)

    def test_multinotifier_isolation(self):
        """Ausfall eines einzelnen Notifiers darf die anderen nicht stoppen."""
        mock1 = MagicMock()
        mock2 = MagicMock()
        mock3 = MagicMock()

        # Mock 2 fails on notify_shutdown
        mock2.notify_shutdown.side_effect = Exception("Mock2 failed")

        multi = MultiNotifier([mock1, mock2, mock3])
        multi.notify_shutdown("Test")

        mock1.notify_shutdown.assert_called_once()
        mock2.notify_shutdown.assert_called_once()
        mock3.notify_shutdown.assert_called_once()

    def test_state_store_corrupted_file(self):
        """Beschädigte State Datei: Should backup and reset to empty state."""
        path = self.state_path
        with open(path, "w") as f:
            f.write("invalid json {")

        store = StateStore(path)
        self.assertEqual(store.get_cooldowns(), {})
        # check if any corrupted file exists
        dir_name = os.path.dirname(path)
        base_name = os.path.basename(path)
        corrupted_files = [f for f in os.listdir(dir_name) if f.startswith(f"{base_name}.corrupted")]
        self.assertTrue(len(corrupted_files) > 0)
        for f in corrupted_files:
            os.remove(os.path.join(dir_name, f))


    def test_journal_atomic_save(self):
        """Journal schreibt temp file und macht os.replace"""
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            self.addCleanup(lambda p=f.name: __import__('os').remove(p) if __import__('os').path.exists(p) else None)
            path = f.name

        try:
            journal = Journal(True, path)
            journal.record_cycle(1, "OK", {}, [], 1.0)

            # Since it replaces immediately, we can't easily assert the tmp file existence mid-flight without mocking os.replace
            with patch('os.replace') as mock_replace:
                journal.record_cycle(1, "OK", {}, [], 1.0)
                mock_replace.assert_called_with(path + ".tmp", path)
        finally:
            if os.path.exists(path):
                os.remove(path)

    def test_docker_list_error(self):
        """Fehler bei Docker Container Listing: Must abort cycle, not report 0 containers."""
        handler = DockerHandler(self.mock_client, self.config)
        self.mock_client.containers.list.side_effect = Exception("API Down")

        with self.assertRaises(Exception): # noqa: B017
            handler.get_watched_containers()

    def test_user_fallback_disabled(self):
        """User Fallback standardmässig deaktiviert"""
        self.assertFalse(self.config.allow_user_fallback)

    def test_container_without_latest_ignored(self):
        """Container ohne :latest bleibt ignoriert."""
        c = MagicMock()
        c.labels = {"watcher.enable": "true"}
        c.image.tags = ["myimage:1.0"]
        c.attrs = {"Config": {"Image": "myimage:1.0"}}

        self.mock_client.containers.list.return_value = [c]
        handler = DockerHandler(self.mock_client, self.config)

        auto, monitor = handler.get_watched_containers()
        self.assertEqual(len(auto), 0)
        self.assertEqual(len(monitor), 0)

    def test_backup_container_prevents_update(self):
        """Vorhandener Backup Container verhindert Update."""
        handler = DockerHandler(self.mock_client, self.config)
        self.mock_client.containers.get.return_value = MagicMock() # backup exists

        with self.assertRaises(RecreationError) as e:
            handler.recreate("my_container", MagicMock())

        self.assertIn("Backup container my_container_backup already exists", str(e.exception))

    def test_volume_preservation(self):
        """Volumes und Mounts: Anonyme Volumes müssen erhalten bleiben."""
        handler = DockerHandler(self.mock_client, self.config)
        c = MagicMock()
        c.attrs = {
            "HostConfig": {},
            "Config": {"Image": "test:latest"},
            "Mounts": [
                {"Type": "volume", "Name": "my_named_volume", "Destination": "/data1", "RW": True},
                {"Type": "volume", "Name": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef", "Destination": "/data2", "RW": True},
                {"Type": "bind", "Source": "/host/path", "Destination": "/data3", "RW": False}
            ]
        }

        plan = handler.get_recreation_plan(c)
        mounts = plan["create_args"]["mounts"]

        self.assertEqual(len(mounts), 3)
        self.assertEqual(mounts[0]["Source"], "my_named_volume")
        self.assertEqual(mounts[1]["Source"], "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef")
        self.assertEqual(mounts[2]["Source"], "/host/path")
        self.assertTrue(mounts[2]["ReadOnly"])

    def test_gpu_device_requests(self):
        """Device Requests: Convert HostConfig.DeviceRequests to docker.types.DeviceRequest."""
        handler = DockerHandler(self.mock_client, self.config)
        c = MagicMock()
        c.name = "gpu_app"
        c.attrs = {
            "HostConfig": {
                "DeviceRequests": [
                    {
                        "Driver": "nvidia",
                        "Count": -1,
                        "Capabilities": [["gpu"]]
                    }
                ]
            },
            "Config": {"Image": "test:latest"}
        }

        plan = handler.get_recreation_plan(c)
        device_reqs = plan["create_args"]["device_requests"]
        self.assertIsNotNone(device_reqs)
        self.assertEqual(len(device_reqs), 1)
        self.assertEqual(device_reqs[0].driver, "nvidia")
        self.assertEqual(device_reqs[0].count, -1)
        self.assertEqual(device_reqs[0].capabilities, [["gpu"]])

    def test_mount_propagation(self):
        """Mount Propagation: Direct mapping from Mounts array."""
        handler = DockerHandler(self.mock_client, self.config)
        c = MagicMock()
        c.name = "prop_app"
        c.attrs = {
            "HostConfig": {},
            "Config": {"Image": "test:latest"},
            "Mounts": [
                {
                    "Type": "bind",
                    "Source": "/host/path",
                    "Destination": "/container/path",
                    "RW": True,
                    "Propagation": "rshared"
                }
            ]
        }

        plan = handler.get_recreation_plan(c)
        mounts = plan["create_args"]["mounts"]
        self.assertEqual(len(mounts), 1)
        self.assertEqual(mounts[0].get('BindOptions', {}).get('Propagation'), "rshared")

    def test_health_monitor_start_period_invalid(self):
        """Health Monitor: Invalid start_period should warn but continue."""
        hm = HealthMonitor(self.mock_client)
        c = MagicMock()
        c.labels = {'watcher.health.start_period': '-5'}
        self.mock_client.containers.get.return_value = c

        # _sleep should not be called with negative values
        with patch.object(hm, '_sleep', return_value=True) as mock_sleep:
            hm.wait_for_health("test", 1, 1)
            # Only the initial 2s sleep and the delay sleep should be called
            mock_sleep.assert_any_call(2)

    def test_health_monitor_start_period_valid(self):
        """Health Monitor: Valid start_period should sleep correctly."""
        hm = HealthMonitor(self.mock_client)
        c = MagicMock()
        c.labels = {'watcher.health.start_period': '10'}
        self.mock_client.containers.get.return_value = c

        with patch.object(hm, '_sleep', return_value=True) as mock_sleep:
            hm.wait_for_health("test", 1, 1)
            # Should sleep 10s. Should NOT sleep the initial 2s.
            mock_sleep.assert_any_call(10)
            with self.assertRaises(AssertionError):
                mock_sleep.assert_any_call(2)

    def test_health_monitor_shutdown_interrupt(self):
        """Health Monitor: Should abort wait if shutdown is triggered."""
        import threading
        event = threading.Event()
        hm = HealthMonitor(self.mock_client, event)
        c = MagicMock()
        c.labels = {'watcher.health.start_period': '10'}
        self.mock_client.containers.get.return_value = c

        event.set() # Trigger shutdown immediately
        result = hm.wait_for_health("test", 1, 1)

        self.assertFalse(result) # Should return false early

    def test_state_store_transactions(self):
        """StateStore: Transaction lifecycle and JSON robustness."""
        with open(self.config.state_path, 'w') as f:
            f.write("{invalid_json:")

        store = StateStore(self.config.state_path)
        self.assertEqual(store.get_cooldowns(), {})
        self.assertEqual(store.get_transactions(), {})

        store.start_transaction("my_app", "id_123", "img_123", "img_123_new")
        txs = store.get_transactions()
        self.assertIn("my_app", txs)
        self.assertEqual(txs["my_app"]["phase"], "prepared")

        store.update_transaction("my_app", "update_in_progress", backup_container_id="backup_456")
        self.assertEqual(store.get_transactions()["my_app"]["phase"], "update_in_progress")

        store.end_transaction("my_app")
        self.assertEqual(store.get_transactions(), {})


    def test_state_store_write_error_prevents_update(self):
        """StateStore Schreibfehler verhindert Update"""
        from unittest.mock import MagicMock

        import config
        from exceptions import StateStoreError
        from state_store import StateStore

        cfg = config.Config()
        cfg.dry_run = False
        MagicMock()
        store = StateStore(self.state_path)

        # mock store._save to fail
        store._save = MagicMock(side_effect=StateStoreError("Disk full"))

        # Should raise StateStoreError on start_transaction
        with self.assertRaises(StateStoreError):
            store.start_transaction("app", "123", "img_1", "img_1_new")


    def test_state_store_start_tx_rollback(self):
        """fehlgeschlagenes start_transaction verändert In-Memory-State nicht"""
        if os.path.exists(self.state_path): os.remove(self.state_path)
        from exceptions import StateStoreError
        from state_store import StateStore
        store = StateStore(self.state_path)
        store._save = MagicMock(side_effect=StateStoreError("fail"))
        with self.assertRaises(StateStoreError):
            store.start_transaction("app", "1", "2", "2_new")
        self.assertNotIn("app", store.get_transactions())

    def test_update_transaction_keeps_previous_on_fail(self):
        """fehlgeschlagenes update_transaction behält vorherige Phase"""
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            self.addCleanup(lambda p=f.name: __import__('os').remove(p) if __import__('os').path.exists(p) else None)
            path = f.name

        try:
            from exceptions import StateStoreError
            from state_store import StateStore
            store = StateStore(path)
            store.start_transaction("app", "orig", "img", "img_new")

            # mock atomarer save schlägt fehl
            def mock_save():
                raise StateStoreError("Disk full")

            with patch.object(store, '_save', side_effect=mock_save): # noqa: SIM117
                with self.assertRaises(StateStoreError):
                    store.update_transaction("app", "hacked")

            internal = store.get_transactions()
            self.assertEqual(internal["app"]["phase"], "prepared")
        finally:
            if os.path.exists(path):
                os.remove(path)

    def test_end_transaction_keeps_transaction_on_fail(self):
        """fehlgeschlagenes end_transaction behält Transaktion"""
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            self.addCleanup(lambda p=f.name: __import__('os').remove(p) if __import__('os').path.exists(p) else None)
            path = f.name

        try:
            from exceptions import StateStoreError
            from state_store import StateStore
            store = StateStore(path)
            store.start_transaction("app", "1", "2", "2_new")
            store._save = MagicMock(side_effect=StateStoreError("fail"))
            with self.assertRaises(StateStoreError):
                store.end_transaction("app")
            self.assertIn("app", store.get_transactions())
        finally:
            if os.path.exists(path):
                os.remove(path)

    def test_set_cooldowns_keeps_previous_on_fail(self):
        """fehlgeschlagenes set_cooldowns behält vorherige Cooldowns"""
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            self.addCleanup(lambda p=f.name: __import__('os').remove(p) if __import__('os').path.exists(p) else None)
            path = f.name

        try:
            from exceptions import StateStoreError
            from state_store import StateStore
            store = StateStore(path)
            store._save = MagicMock(side_effect=StateStoreError("fail"))
            with self.assertRaises(StateStoreError):
                store.set_cooldowns({"app": {"count": 1}})
            self.assertEqual(store.get_cooldowns(), {})
        finally:
            if os.path.exists(path):
                os.remove(path)

    def test_defensive_cooldown_getter(self):
        """get_cooldowns() gibt defensive Kopie zurück"""
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            self.addCleanup(lambda p=f.name: __import__('os').remove(p) if __import__('os').path.exists(p) else None)
            path = f.name

        try:
            from state_store import StateStore
            store = StateStore(path)
            store.set_cooldowns({"app": {"count": 1, "cooldown_until": "time"}})

            cooldowns = store.get_cooldowns()
            cooldowns["app"]["count"] = 999
            cooldowns["new_app"] = {"count": 1}

            internal = store.get_cooldowns()
            self.assertEqual(internal["app"]["count"], 1)
            self.assertNotIn("new_app", internal)
        finally:
            if os.path.exists(path):
                os.remove(path)

    def test_set_cooldowns_aliasing(self):
        """set_cooldowns() behält interne Kopie, Ändern des übergebenen dicts ändert internen Status nicht"""
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            self.addCleanup(lambda p=f.name: __import__('os').remove(p) if __import__('os').path.exists(p) else None)
            path = f.name

        try:
            from state_store import StateStore
            store = StateStore(path)

            my_cooldowns = {"app": {"count": 1}}
            store.set_cooldowns(my_cooldowns)

            # Verändere das übergebene Dictionary
            my_cooldowns["app"]["count"] = 999

            # Interner Store darf nicht verändert worden sein
            internal = store.get_cooldowns()
            self.assertEqual(internal["app"]["count"], 1)
        finally:
            if os.path.exists(path):
                os.remove(path)

    def test_legacy_cooldown_timestamps(self):
        """Legacy naive cooldown timestamps sind local time and parsed UTC."""
        import json
        import tempfile
        from datetime import datetime, timezone

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            self.addCleanup(lambda p=f.name: __import__('os').remove(p) if __import__('os').path.exists(p) else None)
            path = f.name

        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump({
                    "cooldowns": {
                        "app_legacy": {"count": 1, "cooldown_until": "2024-01-01T12:00:00"},
                        "app_utc": {"count": 1, "cooldown_until": "2024-01-01T12:00:00+00:00"}
                    }
                }, f)

            from state_store import StateStore
            store = StateStore(path)
            cooldowns = store.get_cooldowns()

            # app_utc was already UTC
            self.assertEqual(cooldowns["app_utc"]["cooldown_until"], datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc))

            # app_legacy was naive local, now converted to UTC
            legacy_dt = cooldowns["app_legacy"]["cooldown_until"]
            self.assertIsNotNone(legacy_dt.tzinfo)

            # _is_in_cooldown should not raise TypeError
            from main import WatcherService
            with patch('docker.from_env', return_value=self.mock_client):
                service = WatcherService(self.config)

            service.failure_tracker = cooldowns
            service._is_in_cooldown("app_legacy")
            service._is_in_cooldown("app_utc")
        finally:
            if os.path.exists(path):
                os.remove(path)

    def test_defensive_transaction_getter(self):
        """get_transactions() gibt defensive Kopie zurück"""
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            self.addCleanup(lambda p=f.name: __import__('os').remove(p) if __import__('os').path.exists(p) else None)
            path = f.name

        try:
            from state_store import StateStore
            store = StateStore(path)
            store.start_transaction("app", "orig", "img", "img_new")

            txs = store.get_transactions()
            txs["app"]["phase"] = "hacked"
            txs["new_app"] = {}

            internal = store.get_transactions()
            self.assertEqual(internal["app"]["phase"], "prepared")
            self.assertNotIn("new_app", internal)
        finally:
            if os.path.exists(path):
                os.remove(path)

    def test_end_transaction_error_after_cleanup(self):
        """Fehler bei end_transaction nach erfolgreichem Cleanup löst keinen Rollback aus."""
        with patch('docker.from_env', return_value=self.mock_client):
            service = WatcherService(self.config)

        c = MagicMock(spec=Container)
        c.name = "app"
        c.id = "orig_123"
        c.image.id = "img_1"
        c.status = "running"
        c.attrs = {"Config": {"Image": "img_1"}}

        service.docker.check_for_update = MagicMock(return_value=(UpdateStatus.UPDATE_AVAILABLE, "img_1", "img_2"))
        service.docker.get_recreation_plan = MagicMock(return_value={})
        service.docker.recreate = MagicMock(return_value=MagicMock(id="new_456"))
        service.health.wait_for_health = MagicMock(return_value=True)
        service.docker.remove_backup = MagicMock(return_value=True)

        from exceptions import StateStoreError
        service.state_store.end_transaction = MagicMock(side_effect=StateStoreError("fail"))
        service.perform_rollback = MagicMock()

        info = service.process_container(c, True)

        self.assertEqual(info.status, UpdateStatus.FAILED)
        self.assertEqual(info.error_step, "state_save")
        self.assertTrue(service.abort_updates_for_cycle)
        service.perform_rollback.assert_not_called()

    def test_update_transaction_error_aborts_cleanup(self):
        """StateStoreError during replacement_verified aborts cleanup but retains backup."""
        with patch('docker.from_env', return_value=self.mock_client):
            service = WatcherService(self.config)

        c = MagicMock(spec=Container)
        c.name = "app"
        c.id = "orig_123"
        c.image.id = "img_1"
        c.status = "running"
        c.attrs = {"Config": {"Image": "img_1"}}

        service.docker.check_for_update = MagicMock(return_value=(UpdateStatus.UPDATE_AVAILABLE, "img_1", "img_2"))
        service.docker.get_recreation_plan = MagicMock(return_value={})
        service.docker.recreate = MagicMock(return_value=MagicMock(id="new_456"))
        service.health.wait_for_health = MagicMock(return_value=True)
        service.docker.remove_backup = MagicMock()

        from exceptions import StateStoreError
        service.state_store.update_transaction = MagicMock(side_effect=StateStoreError("fail"))
        service.perform_rollback = MagicMock()

        info = service.process_container(c, True)

        self.assertEqual(info.status, UpdateStatus.FAILED)
        self.assertEqual(info.error_step, "state_save")
        self.assertTrue(service.abort_updates_for_cycle)
        service.docker.remove_backup.assert_not_called()
        service.perform_rollback.assert_not_called()

    def test_record_failure_sets_abort_flag(self):
        """StateStoreError during set_cooldowns sets abort flag."""
        with patch('docker.from_env', return_value=self.mock_client):
            service = WatcherService(self.config)

        c = MagicMock(spec=Container)
        c.name = "app"
        c.id = "orig_123"
        c.image.id = "img_1"
        c.status = "running"
        c.attrs = {"Config": {"Image": "img_1"}}

        # Force a failure during update
        service.docker.check_for_update = MagicMock(return_value=(UpdateStatus.FAILED, None, None))

        from exceptions import StateStoreError
        service.state_store.set_cooldowns = MagicMock(side_effect=StateStoreError("fail cooldown"))

        info = service.process_container(c, True)

        self.assertEqual(info.status, UpdateStatus.FAILED)
        self.assertTrue(service.abort_updates_for_cycle)

    def test_state_store_error_stops_cycle_updates(self):
        """Ein StateStoreError stoppt weitere Auto-Updates im selben Zyklus."""
        with patch('docker.from_env', return_value=self.mock_client):
            service = WatcherService(self.config)

        c1 = MagicMock(spec=Container)
        c1.name = "app1"
        c1.id = "orig_1"
        c1.image = MagicMock()
        c1.attrs = {"Config": {"Image": "img_1"}}

        c2 = MagicMock(spec=Container)
        c2.name = "app2"
        c2.id = "orig_2"
        c2.image = MagicMock()
        c2.attrs = {"Config": {"Image": "img_2"}}

        service.docker.get_watched_containers = MagicMock(return_value=([c1, c2], []))

        original_pc = service.process_container
        def mock_process(c, auto_update):
            if c.name == "app1":
                # Simulate a StateStoreError during process_container
                service.abort_updates_for_cycle = True
                return ContainerUpdateInfo(c.name, c.id, UpdateStatus.FAILED)
            return original_pc(c, auto_update)

        with patch.object(service, 'process_container', side_effect=mock_process) as mock_pc:
            service.run_cycle()

            # c1 should be called with auto_update=True
            # c2 should be called with auto_update=False because c1 set the flag
            self.assertEqual(mock_pc.call_count, 2)
            mock_pc.assert_any_call(c1, auto_update=True)
            mock_pc.assert_any_call(c2, auto_update=False)

    def test_missing_original_container_prevents_recreate(self):
        """fehlender Originalcontainer verhindert recreate"""
        from unittest.mock import MagicMock

        from docker_handler import DockerHandler
        from exceptions import RecreationError

        client = MagicMock()
        client.containers.get.side_effect = docker.errors.NotFound("Not found")
        handler = DockerHandler(client)

        with self.assertRaises(RecreationError) as ctx:
            handler.recreate("app", {"create_args": {}, "networks": {}})

        self.assertIn("Original container app not found", str(ctx.exception))

    def test_compose_no_duplicate_vars(self):
        """Compose enthält keine doppelten Variablen"""
        if os.path.exists("docker-compose.yml"):
            with open("docker-compose.yml", "r", encoding="utf-8") as f:
                content = f.read()
                vars_list = [line.split("=")[0].strip("- ") for line in content.splitlines() if "-" in line and "=" in line and "TZ" in content]

                # count NOTIFY_UPDATES_AVAILABLE
                self.assertEqual(vars_list.count("NOTIFY_UPDATES_AVAILABLE"), 1)

    def test_telegram_execution_plan_escapes(self):
        """Telegram Execution Plan escaped Sonderzeichen"""
        from unittest.mock import patch

        from models import ContainerUpdateInfo, ExecutionPlan
        from telegram_notifier import TelegramNotifier

        notifier = TelegramNotifier("token", "chat")
        plan = ExecutionPlan()
        plan.checked_containers = 1

        u = ContainerUpdateInfo("app<bad>", "old_id", "status")
        u.old_image_short_id = "v1&"
        u.new_image_short_id = "v2'"
        plan.updates_available.append(u)

        with patch('requests.post') as mock_post:
            notifier.notify_execution_plan(plan)

            mock_post.assert_called_once()
            _args, kwargs = mock_post.call_args
            text = kwargs['json']['text']

            self.assertIn("app&lt;bad&gt;", text)
            self.assertIn("v1&amp;", text)
            self.assertNotIn("app<bad>", text)

if __name__ == '__main__':


    unittest.main()
