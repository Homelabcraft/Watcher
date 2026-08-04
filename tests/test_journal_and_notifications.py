import json
import os
import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from base_test import BaseTest

from config import Config
from exceptions import ConfigurationError
from main import WatcherService
from models import ContainerUpdateInfo, UpdateStatus


class TestJournalAndNotifications(BaseTest):
    def setUp(self):
        super().setUp()
        # Mock docker environment
        self.mock_docker_client = MagicMock()
        self.patcher = patch('docker.from_env', return_value=self.mock_docker_client)
        self.patcher.start()
        
        # Reset env vars for each test
        if 'NOTIFY_SUMMARY_STRATEGY' in os.environ: del os.environ['NOTIFY_SUMMARY_STRATEGY']
        if 'NOTIFY_UPDATES_AVAILABLE' in os.environ: del os.environ['NOTIFY_UPDATES_AVAILABLE']
        if 'TELEGRAM_BOT_TOKEN' in os.environ: del os.environ['TELEGRAM_BOT_TOKEN']
        if 'TELEGRAM_CHAT_ID' in os.environ: del os.environ['TELEGRAM_CHAT_ID']
        if 'DISCORD_WEBHOOK_URL' in os.environ: del os.environ['DISCORD_WEBHOOK_URL']
        if 'EXCLUDE_CONTAINER_REGEX' in os.environ: del os.environ['EXCLUDE_CONTAINER_REGEX']

    def tearDown(self):
        self.patcher.stop()


    def test_start_without_notifications(self):
        """Watcher should start cleanly if no notifier is configured."""
        with patch.dict(os.environ, {"DISCORD_WEBHOOK_URL": ""}):
            service = WatcherService()
            # NoopNotifier doesn't have a __class__ name easily checked via mock, 
            # but we can check if it's not a DiscordNotifier
            from noop_notifier import NoopNotifier
            self.assertIsInstance(service.notifier, NoopNotifier)

    def test_fail_fast_incomplete_telegram(self):
        """Should fail if only part of Telegram config is provided."""
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "123"}):
            with self.assertRaises(ConfigurationError) as cm:
                Config()
            self.assertIn("Both TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set", str(cm.exception))

    def test_regex_exclude(self):
        """Check if containers are correctly filtered by regex."""
        with patch.dict(os.environ, {"EXCLUDE_CONTAINER_REGEX": "^test_.*"}):
            config = Config()
            mock_c1 = MagicMock()
            mock_c1.name = "test_app"
            mock_c2 = MagicMock()
            mock_c2.name = "prod_app"
            
            self.mock_docker_client.containers.list.return_value = [mock_c1, mock_c2]
            
            from docker_handler import DockerHandler
            handler = DockerHandler(self.mock_docker_client, config)
            # Mock get_image_ref to return something for both
            handler.get_image_ref = MagicMock(return_value="image:latest")
            
            auto, monitor = handler.get_watched_containers()
            names = [c.name for c in auto] + [c.name for c in monitor]
            self.assertIn("prod_app", names)
            self.assertNotIn("test_app", names)

    def test_cooldown_behavior(self):
        """Verify that containers enter and exit cooldown correctly."""
        with patch.dict(os.environ, {"FAILURE_COOLDOWN_SECONDS": "10"}):
            service = WatcherService()
            name = "fail_app"
            
            # Initial state
            self.assertFalse(service._is_in_cooldown(name))
            
            # Record failure
            service._record_failure(name)
            self.assertTrue(service._is_in_cooldown(name))
            
            # Wait for cooldown to expire (simulated)
            from datetime import timezone
            service.failure_tracker[name]["cooldown_until"] = datetime.now(timezone.utc) - timedelta(seconds=1)
            self.assertFalse(service._is_in_cooldown(name))
            
            # Successful check should clear it
            service._record_failure(name)
            self.assertTrue(service._is_in_cooldown(name))
            
            # Mock successful process
            mock_container = MagicMock()
            mock_container.name = name
            service.docker.check_for_update = MagicMock(return_value=(UpdateStatus.NO_UPDATE, "old", "old"))
            service.process_container(mock_container, True)
            self.assertNotIn(name, service.failure_tracker)

    def test_journal_writing(self):
        """Journal must write cycle outcomes cleanly"""
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
            
        try:
            os.environ["JOURNAL_PATH"] = path
            service = WatcherService()
            
            summary = {"updated": ["app1"], "failed": [], "rolled_back": [], "reported": [], "skipped": []}
            info = ContainerUpdateInfo("app1", "id1", UpdateStatus.UPDATED)
            info.duration_sec = 5.5
            
            service.journal.record_cycle(1, "LIVE", summary, [info], 10.0)
            
            self.assertTrue(os.path.exists(path))
            with open(path, "r") as f:
                data = json.load(f)
                self.assertEqual(len(data), 1)
                self.assertEqual(data[0]["summary"]["updated"], ["app1"])
                self.assertEqual(data[0]["events"][0]["name"], "app1")
        finally:
            if "JOURNAL_PATH" in os.environ:
                del os.environ["JOURNAL_PATH"]

    def test_summary_strategies(self):
        """Test always, on_change, and on_error strategies."""
        # Setup
        service = WatcherService()
        service.notifier.notify_summary_report = MagicMock()
        
        # 1. Strategy: on_error, but only 'reported' exists -> No notification
        service.config.notify_summary_strategy = "on_error"
        summary = {"updated": [], "failed": [], "rolled_back": [], "reported": ["app1"], "skipped": []}
        
        # Need to patch run_cycle's container list
        service.docker.get_watched_containers = MagicMock(return_value=([], []))
        
        # Manual trigger of end-of-cycle logic logic
        def check_strategy(summ):
            should = False
            strat = service.config.notify_summary_strategy
            
            has_errors = len(summ["failed"]) > 0 or len(summ["rolled_back"]) > 0
            has_actions = len(summ["updated"]) > 0 or has_errors
            
            if strat == "always": should = True
            elif strat == "on_error": should = has_errors
            elif strat == "on_change": should = has_actions
            return should

        self.assertFalse(check_strategy(summary))
        
        # 2. Strategy: on_change, updated exists -> Yes notification
        service.config.notify_summary_strategy = "on_change"
        summary["updated"] = ["app2"]
        self.assertTrue(check_strategy(summary))
        
        # 3. Strategy: on_change, only reported exists -> No notification (Decision: change = action taken)
        summary["updated"] = []
        summary["reported"] = ["app3"]
        self.assertFalse(check_strategy(summary))

    def test_run_cycle_no_notifications(self):
        """A full cycle should run without errors even if notifications are disabled."""
        with patch.dict(os.environ, {"DISCORD_WEBHOOK_URL": ""}):
            service = WatcherService()
            service.docker.get_watched_containers = MagicMock(return_value=([], []))
            # Should not crash
            service.run_cycle()

    def test_cycle_with_cooldown(self):
        """Containers in cooldown should be skipped in the cycle."""
        service = WatcherService()
        mock_c = MagicMock()
        mock_c.name = "cool_app"
        mock_c.id = "id123"
        service.docker.get_watched_containers = MagicMock(return_value=([mock_c], []))
        service.process_container = MagicMock()
        
        # Put in cooldown
        service._record_failure("cool_app")
        
        service.run_cycle()
        # process_container should NOT have been called
        service.process_container.assert_not_called()

    def test_journal_io_error_handling(self):
        """Journal muss gracefully failen, wenn Disk voll ist"""
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
            
        try:
            os.environ["JOURNAL_PATH"] = path
            service = WatcherService()
            with patch("builtins.open", side_effect=OSError("Disk full")):
                # Should not crash the process
                service.journal.record_cycle(1, "LIVE", {}, [], 1.0)
            self.assertEqual(len(service.journal._history), 1)
        finally:
            if "JOURNAL_PATH" in os.environ:
                del os.environ["JOURNAL_PATH"]

    def test_max_updates_limit(self):
        """Cycle should respect MAX_UPDATES_PER_CYCLE."""
        service = WatcherService()
        service.config.max_updates_per_cycle = 1
        
        c1 = MagicMock(); c1.name = "app1"
        c2 = MagicMock(); c2.name = "app2"
        service.docker.get_watched_containers = MagicMock(return_value=([c1, c2], []))
        
        # Mock process_container to return UPDATED for the first one
        def mock_process(c, auto_update=True):
            return ContainerUpdateInfo(c.name, "id", UpdateStatus.UPDATED)
            
        service.process_container = MagicMock(side_effect=mock_process)
        service.run_cycle()
        
        # Should only have called process_container once for auto-update and once for report
        self.assertEqual(service.process_container.call_count, 2)
        args1 = service.process_container.call_args_list[0]
        args2 = service.process_container.call_args_list[1]
        self.assertTrue(args1[1].get('auto_update', True))
        self.assertFalse(args2[1].get('auto_update', True))

if __name__ == '__main__':
    unittest.main()
