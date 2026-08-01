import unittest
from unittest.mock import MagicMock, patch
import json
import os
import time
import html
from datetime import datetime

import docker
from docker.models.containers import Container

from config import Config
from docker_handler import DockerHandler, RecreationError
from health_monitor import HealthMonitor
from main import WatcherService
from models import ContainerUpdateInfo, UpdateStatus
from state_store import StateStore
from journal import Journal
from telegram_notifier import TelegramNotifier
from multi_notifier import MultiNotifier

class TestV1_7Features(unittest.TestCase):
    def setUp(self):
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
        path = "test_state_corrupted.json"
        with open(path, "w") as f:
            f.write("invalid json {")
            
        store = StateStore(path)
        self.assertEqual(store.get_cooldowns(), {})
        # check if any corrupted file exists
        corrupted_files = [f for f in os.listdir(".") if f.startswith(f"{path}.corrupted")]
        self.assertTrue(len(corrupted_files) > 0)
        for f in corrupted_files:
            os.remove(f)
        
        if os.path.exists(path):
            os.remove(path)

    def test_journal_atomic_save(self):
        """Atomare Journal Speicherung: Must write via .tmp file and replace."""
        path = "test_journal_atomic.json"
        journal = Journal(True, path)
        journal.record_cycle(1, "OK", {}, [], 1.0)
        
        # Since it replaces immediately, we can't easily assert the tmp file existence mid-flight without mocking os.replace
        with patch('os.replace') as mock_replace:
            journal.record_cycle(1, "OK", {}, [], 1.0)
            mock_replace.assert_called_once_with(f"{path}.tmp", path)
            
        if os.path.exists(path):
            os.remove(path)
        if os.path.exists(f"{path}.tmp"):
            os.remove(f"{path}.tmp")

    def test_docker_list_error(self):
        """Fehler bei Docker Container Listing: Must abort cycle, not report 0 containers."""
        handler = DockerHandler(self.mock_client, self.config)
        self.mock_client.containers.list.side_effect = Exception("API Down")
        
        with self.assertRaises(Exception):
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
        import json
        with open(self.config.state_path, 'w') as f:
            f.write("{invalid_json:")
        
        store = StateStore(self.config.state_path)
        self.assertEqual(store.get_cooldowns(), {})
        self.assertEqual(store.get_transactions(), {})
        
        store.start_transaction("my_app", "id_123", "img_123")
        txs = store.get_transactions()
        self.assertIn("my_app", txs)
        self.assertEqual(txs["my_app"]["phase"], "prepared")
        
        store.update_transaction("my_app", "update_in_progress", backup_container_id="backup_456")
        self.assertEqual(store.get_transactions()["my_app"]["phase"], "update_in_progress")
        
        store.end_transaction("my_app")
        self.assertEqual(store.get_transactions(), {})

if __name__ == '__main__':


    unittest.main()
