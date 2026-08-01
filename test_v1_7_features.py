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
        notifier._send = MagicMock()
        
        info = ContainerUpdateInfo(name="test", old_id="a", status=UpdateStatus.FAILED, image_ref="test:latest")
        info.error_step = "pull"
        # Tricky error message with raw HTML characters
        info.error_message = 'Failed to pull <my_image> & "other"'
        
        notifier.notify_update_failure(info)
        
        notifier._send.assert_called_once()
        body = notifier._send.call_args[0][1]
        
        # Escaped parts should be in there
        self.assertIn('&lt;my_image&gt; &amp; &quot;other&quot;', body)
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
        self.assertTrue(os.path.exists(f"{path}.corrupted"))
        
        os.remove(path)
        os.remove(f"{path}.corrupted")

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

if __name__ == '__main__':
    unittest.main()
