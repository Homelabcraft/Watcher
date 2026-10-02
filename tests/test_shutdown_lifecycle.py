"""Shutdown is not an unhealthy image, and unexpected loop crashes must fail."""

import json
import unittest
from unittest.mock import Mock, patch

import docker
from base_test import BaseTest

from main import WatcherService
from models import ContainerUpdateInfo, UpdateStatus


class TestShutdownLifecycle(BaseTest):
    def setUp(self):
        super().setUp()
        self.client = Mock()
        self.client.containers.get.side_effect = docker.errors.NotFound("No self container")
        self.client.containers.list.return_value = []
        with patch("main.docker.from_env", return_value=self.client), patch.object(WatcherService, "_setup_signals"):
            self.service = WatcherService()
        self.service.notifier = Mock()

    def prepare_update(self):
        original = Mock()
        original.name, original.id, original.image.id = "app", "old-container", "old-image"
        self.service.docker = Mock()
        self.service.docker.get_image_ref.return_value = "app:latest"
        self.service.docker.check_for_update.return_value = (UpdateStatus.UPDATE_AVAILABLE, "old-image", "new-image")
        replacement = Mock()
        replacement.id = "new-container"

        def recreate(*args, **kwargs):
            self.service.state_store.update_transaction(
                "app", "replacement_started", new_container_id="new-container", new_image_id="new-image"
            )
            return replacement

        self.service.docker.recreate.side_effect = recreate
        return original

    def interrupt_health(self, *args, **kwargs):
        self.service.shutdown_event.set()
        return False

    def test_interrupted_update_keeps_backup_without_rollback_or_cooldown(self):
        original = self.prepare_update()
        self.service.health.wait_for_health = Mock(side_effect=self.interrupt_health)
        self.service.perform_rollback = Mock(return_value=(False, "Must not be called"))
        info = self.service.process_container(original, auto_update=True)
        self.service.perform_rollback.assert_not_called()
        self.service.docker.remove_backup.assert_not_called()
        self.assertEqual(info.status.name, "INTERRUPTED")
        self.assertEqual(info.error_step, "shutdown")
        self.assertEqual(self.service.state_store.get_transactions()["app"]["phase"], "replacement_started")
        self.assertNotIn("app", self.service.state_store.get_cooldowns())

    def test_shutdown_after_pull_never_stops_original(self):
        original = self.prepare_update()

        def pulled(*args):
            self.service.shutdown_event.set()
            return UpdateStatus.UPDATE_AVAILABLE, "old-image", "new-image"

        self.service.docker.check_for_update.side_effect = pulled
        info = self.service.process_container(original, auto_update=True)
        self.service.docker.recreate.assert_not_called()
        self.assertEqual(info.status.name, "INTERRUPTED")
        self.assertEqual(self.service.state_store.get_transactions(), {})

    def test_interrupted_recovery_keeps_healthy_candidate_and_backup(self):
        transaction = self.service.state_store.start_transaction("app", "old-container", "old-image", "new-image")
        self.service.state_store.update_transaction(
            "app", "replacement_started", new_container_id="new-container", new_image_id="new-image"
        )
        replacement = Mock()
        replacement.id, replacement.image.id, replacement.status = "new-container", "new-image", "running"
        replacement.labels = {"watcher.transaction_id": transaction}
        backup = Mock()
        backup.id, backup.image.id = "old-container", "old-image"
        self.client.containers.get.side_effect = lambda name: {"app": replacement, "app_backup": backup}[name]
        self.service.health.wait_for_health = Mock(side_effect=self.interrupt_health)
        self.service._process_single_recovery("app", self.service.state_store.get_transactions()["app"])
        replacement.remove.assert_not_called()
        backup.rename.assert_not_called()
        self.assertEqual(self.service.state_store.get_transactions()["app"]["phase"], "replacement_started")

    def test_shutdown_does_not_start_another_recovery(self):
        self.service.state_store.start_transaction("app", "old-container", "old-image", "new-image")
        self.service.shutdown_event.set()
        self.service._process_single_recovery = Mock()
        self.service.startup_recovery()
        self.service._process_single_recovery.assert_not_called()

    def test_shutdown_does_not_restart_dependents(self):
        self.service.shutdown_event.set()
        dependent = Mock()
        dependent.name = "dependent"
        self.client.containers.list.return_value = [dependent]
        self.service.get_dependents = Mock(return_value=[dependent.name])
        self.service.restart_dependents([ContainerUpdateInfo("app", "old-container", UpdateStatus.UPDATED)])
        dependent.restart.assert_not_called()

    def test_loop_exception_propagates_and_closes_client(self):
        self.service.startup_recovery = Mock()
        self.service.run_cycle = Mock(side_effect=RuntimeError("Unexpected loop failure"))
        with self.assertRaisesRegex(RuntimeError, "Unexpected loop failure"):
            self.service.start()
        self.client.close.assert_called_once()

    def test_shutdown_notification_does_not_mask_original_loop_error(self):
        self.service.startup_recovery = Mock()
        self.service.run_cycle = Mock(side_effect=RuntimeError("Original loop failure"))
        self.service.notifier.notify_shutdown.side_effect = ValueError("Notification failure")
        with self.assertRaisesRegex(RuntimeError, "Original loop failure"):
            self.service.start()
        self.client.close.assert_called_once()

    def test_clean_stop_closes_client(self):
        self.service.startup_recovery = Mock()
        self.service.run_cycle = Mock(side_effect=self.service.shutdown_event.set)
        self.service.start()
        self.client.close.assert_called_once()

    def test_interrupted_cycle_is_journaled_and_notified_as_action(self):
        original = self.prepare_update()
        self.service.docker.get_watched_containers.return_value = ([original], [])
        self.service.config.notify_summary_strategy = "on_change"

        def interrupted(*args, **kwargs):
            self.service.shutdown_event.set()
            return ContainerUpdateInfo("app", original.id, UpdateStatus.INTERRUPTED, error_step="shutdown")

        self.service.process_container = Mock(side_effect=interrupted)
        self.service.run_cycle()
        summary = self.service.notifier.notify_summary_report.call_args.args[0]
        self.assertEqual(summary["interrupted"], ["app"])
        self.assertEqual(summary["failed"], [])
        with open(self.journal_path, encoding="utf-8") as handle:
            entry = json.load(handle)[0]
        self.assertEqual(entry["events"][0]["status"], "INTERRUPTED")

    def test_interrupted_rollback_retains_original_and_pending_phase(self):
        self.service.state_store.start_transaction("app", "old-container", "old-image", "new-image")
        original = Mock()
        original.id, original.image.id, original.status = "old-container", "old-image", "running"

        def get(name):
            if name == "app":
                return original
            raise docker.errors.NotFound("No backup")

        self.client.containers.get.side_effect = get
        self.service.health.wait_for_health = Mock(side_effect=self.interrupt_health)
        success, details = self.service.perform_rollback("app")
        self.assertFalse(success)
        self.assertIn("interrupted", details)
        self.assertEqual(self.service.state_store.get_transactions()["app"]["phase"], "rollback_started")
        original.remove.assert_not_called()


if __name__ == "__main__":
    unittest.main()
