import os
import unittest
from datetime import datetime
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

import docker
from base_test import BaseTest

from config import Config
from discord_notifier import DiscordNotifier
from docker_handler import DockerHandler
from exceptions import (
    ConfigurationError,
    ImageRecreationError,
    RecreationError,
    StateStoreError,
    UpdateWindowClosed,
)
from main import WatcherService
from models import UpdateStatus
from ntfy_notifier import NtfyNotifier
from slack_notifier import SlackNotifier
from state_store import StateStore
from telegram_notifier import TelegramNotifier
from update_window import UpdateWindow


class TestUpdateWindow(unittest.TestCase):
    def setUp(self):
        self.tz = ZoneInfo("Europe/Zurich")
        self.window = UpdateWindow("02:00", "05:00", "Europe/Zurich")

    def now(self, hour, minute=0):
        return datetime(2026, 10, 2, hour, minute, tzinfo=self.tz)

    def test_start_inclusive_end_exclusive(self):
        for hour, minute, expected in ((1, 59, False), (2, 0, True), (4, 59, True), (5, 0, False)):
            with self.subTest(hour=hour, minute=minute):
                self.assertEqual(self.window.is_open(self.now(hour, minute)), expected)

    def test_disabled_window_keeps_interval(self):
        window = UpdateWindow(None, None, "UTC")
        self.assertTrue(window.is_open(self.now(12)))
        self.assertEqual(window.sleep_duration(86400, self.now(12)), 86400)

    def test_window_can_cross_midnight(self):
        window = UpdateWindow("23:00", "02:00", "Europe/Zurich")
        for hour, expected in ((23, True), (0, True), (1, True), (2, False), (12, False)):
            with self.subTest(hour=hour):
                self.assertEqual(window.is_open(self.now(hour)), expected)

    def test_sleep_outside_window_waits_for_opening_not_full_interval(self):
        self.assertEqual(self.window.sleep_duration(86400, self.now(1)), 3600)

    def test_sleep_inside_window_keeps_interval(self):
        self.assertEqual(self.window.sleep_duration(1800, self.now(2, 10)), 1800)

    def test_interval_ending_outside_window_waits_for_next_opening(self):
        self.assertEqual(self.window.sleep_duration(1800, self.now(4, 45)), 21 * 3600 + 15 * 60)

    def test_spring_gap_moves_opening_to_first_real_time(self):
        now = datetime(2026, 3, 29, 1, tzinfo=self.tz)
        self.assertEqual(self.window.next_open_delay(now), 3600)

    def test_window_entirely_inside_spring_gap_skips_that_day(self):
        window = UpdateWindow("02:00", "02:30", "Europe/Zurich")
        now = datetime(2026, 3, 29, 1, tzinfo=self.tz)
        self.assertEqual(window.next_open_delay(now), 24 * 3600)

    def test_autumn_fold_uses_next_real_opening(self):
        window = UpdateWindow("02:00", "02:30", "Europe/Zurich")
        now = datetime(2026, 10, 25, 2, 40, tzinfo=self.tz, fold=0)
        self.assertEqual(window.next_open_delay(now), 20 * 60)


class TestHomelabConfig(BaseTest):
    def config(self, **values):
        with patch.dict(os.environ, {"UNITTEST_MODE": "1", **values}, clear=True):
            return Config()

    def test_window_is_optional_and_failed_image_policy_enabled(self):
        config = self.config()
        self.assertIsNone(config.update_window_start)
        self.assertTrue(config.skip_failed_images)

    def test_window_inputs_are_trimmed(self):
        config = self.config(UPDATE_WINDOW_START=" 02:00 ", UPDATE_WINDOW_END=" 05:00 ")
        self.assertEqual(config.update_window_start, "02:00")
        self.assertEqual(config.update_window_end, "05:00")

    def test_invalid_or_partial_window_rejected(self):
        for values in (
            {"UPDATE_WINDOW_START": "02:00"},
            {"UPDATE_WINDOW_END": "05:00"},
            {"UPDATE_WINDOW_START": "02:00", "UPDATE_WINDOW_END": "02:00"},
            {"UPDATE_WINDOW_START": "2:00", "UPDATE_WINDOW_END": "05:00"},
            {"UPDATE_WINDOW_START": "02:00", "UPDATE_WINDOW_END": "24:00"},
        ):
            with self.subTest(values=values), self.assertRaises(ConfigurationError):
                self.config(**values)

    def test_daily_schedule_must_be_inside_window(self):
        for schedule in ("01:59", "05:00", "12:00"):
            with self.subTest(schedule=schedule), self.assertRaises(ConfigurationError):
                self.config(UPDATE_WINDOW_START="02:00", UPDATE_WINDOW_END="05:00", SCHEDULE_TIME=schedule)
        self.config(UPDATE_WINDOW_START="02:00", UPDATE_WINDOW_END="05:00", SCHEDULE_TIME="02:00")
        self.config(UPDATE_WINDOW_START="23:00", UPDATE_WINDOW_END="02:00", SCHEDULE_TIME="01:00")


class TestHomelabPolicy(BaseTest):
    def setUp(self):
        super().setUp()
        self.env = patch.dict(os.environ, {
            "SCHEDULE_TIME": "", "UPDATE_WINDOW_START": "", "UPDATE_WINDOW_END": "",
            "SKIP_FAILED_IMAGES": "true", "FAILURE_COOLDOWN_SECONDS": "0", "TZ": "Europe/Zurich",
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.client = Mock()
        self.client.containers.get.side_effect = docker.errors.NotFound("No self container")
        self.client.containers.list.return_value = []
        self.service = self.new_service()
        self.original = Mock()
        self.original.name, self.original.id, self.original.image.id = "app", "original", "old-image"
        self.original.attrs = {"Config": {"Image": "app:latest"}}
        self.service.docker = Mock()
        self.service.docker.get_image_ref.return_value = "app:latest"
        self.service.docker.check_for_update.return_value = (UpdateStatus.UPDATE_AVAILABLE, "old-image", "new-image")
        self.service.docker.remove_backup.return_value = True
        self.service.docker.recreate.return_value = Mock(id="replacement")
        self.service.health.wait_for_health = Mock(return_value=True)
        self.service.perform_rollback = Mock(return_value=(True, "restored"))

    def new_service(self):
        with patch("main.docker.from_env", return_value=self.client), patch.object(WatcherService, "_setup_signals"):
            service = WatcherService()
        service.notifier = Mock()
        return service

    def fail_update(self):
        self.service.health.wait_for_health.return_value = False
        info = self.service.process_container(self.original, auto_update=True)
        self.assertEqual(info.status, UpdateStatus.ROLLED_BACK)
        self.service.state_store.end_transaction("app")
        self.service.docker.recreate.reset_mock()
        self.service.health.wait_for_health.return_value = True

    def test_failed_image_is_persisted_and_survives_restart(self):
        self.fail_update()
        restarted = self.new_service()
        self.assertEqual(restarted.failure_tracker["app"]["failed_image_id"], "new-image")
        self.assertEqual(restarted.failure_tracker["app"]["failed_image_ref"], "app:latest")

    def test_same_failed_image_is_skipped_after_cooldown_expires(self):
        self.fail_update()
        info = self.service.process_container(self.original, auto_update=True)
        self.assertEqual(info.status, UpdateStatus.SKIPPED_FAILED_IMAGE)
        self.service.docker.recreate.assert_not_called()
        self.assertEqual(self.service.state_store.get_transactions(), {})

    def test_different_image_is_allowed_and_clears_failure(self):
        self.fail_update()
        self.service.docker.check_for_update.return_value = (UpdateStatus.UPDATE_AVAILABLE, "old-image", "fixed-image")
        info = self.service.process_container(self.original, auto_update=True)
        self.assertEqual(info.status, UpdateStatus.UPDATED)
        self.assertEqual(self.service.state_store.get_cooldowns(), {})

    def test_changed_reference_is_not_blocked_by_old_failure(self):
        self.fail_update()
        self.service.docker.get_image_ref.return_value = "other:latest"
        info = self.service.process_container(self.original, auto_update=True)
        self.assertEqual(info.status, UpdateStatus.UPDATED)

    def test_monitor_only_still_reports_failed_image(self):
        self.fail_update()
        info = self.service.process_container(self.original, auto_update=False)
        self.assertEqual(info.status, UpdateStatus.REPORTED)
        self.service.docker.recreate.assert_not_called()

    def test_dry_run_does_not_propose_known_failed_image(self):
        self.fail_update()
        self.service.config.dry_run = True
        info = self.service.process_container(self.original, auto_update=True)
        self.assertEqual(info.status, UpdateStatus.SKIPPED_FAILED_IMAGE)
        self.service.docker.recreate.assert_not_called()

    def test_failed_image_policy_can_be_disabled(self):
        self.fail_update()
        self.service.config.skip_failed_images = False
        info = self.service.process_container(self.original, auto_update=True)
        self.assertEqual(info.status, UpdateStatus.UPDATED)

    def test_registry_errors_do_not_blacklist_an_image(self):
        self.service.docker.check_for_update.return_value = (UpdateStatus.FAILED, None, None)
        self.service.process_container(self.original, auto_update=True)
        self.assertNotIn("failed_image_id", self.service.failure_tracker["app"])

    def test_preparation_errors_do_not_blacklist_an_image(self):
        self.service.docker.get_recreation_plan.side_effect = RecreationError("cannot inspect original")
        self.service.process_container(self.original, auto_update=True)
        self.assertNotIn("failed_image_id", self.service.failure_tracker["app"])

    def test_image_compatibility_failure_blocks_candidate(self):
        self.service.docker.recreate.side_effect = ImageRecreationError("user missing in new image")
        self.service.process_container(self.original, auto_update=True)
        self.assertEqual(self.service.failure_tracker["app"]["failed_image_id"], "new-image")

    def test_generic_recreation_error_does_not_blacklist_candidate(self):
        self.service.docker.recreate.side_effect = RecreationError("network unavailable")
        self.service.process_container(self.original, auto_update=True)
        self.assertNotIn("failed_image_id", self.service.failure_tracker["app"])

    def test_failure_is_persisted_before_rollback_ends_transaction(self):
        def rollback(name):
            self.assertEqual(StateStore(self.state_path).get_cooldowns()[name]["failed_image_id"], "new-image")
            self.service.state_store.end_transaction(name)
            return True, "restored"
        self.service.perform_rollback.side_effect = rollback
        self.service.health.wait_for_health.return_value = False
        self.service.process_container(self.original, auto_update=True)

    def test_image_identity_uses_full_hash_not_display_prefix(self):
        self.service._record_failure("app", "sha256:123456789abc-bad", "app:latest")
        self.service.docker.check_for_update.return_value = (UpdateStatus.UPDATE_AVAILABLE, "old-image", "sha256:123456789abc-fixed")
        info = self.service.process_container(self.original, auto_update=True)
        self.assertEqual(info.status, UpdateStatus.UPDATED)

    def test_docker_handler_classifies_missing_image_user(self):
        self.client.containers.get.side_effect = lambda name: self.original if name == "app" else self.missing()
        handler = DockerHandler(self.client, self.service.config)
        handler._create_replacement = Mock(side_effect=docker.errors.APIError("unable to find user app"))
        with self.assertRaises(ImageRecreationError):
            handler.recreate("app", {"create_args": {"user": "app"}, "networks": {}})

    @staticmethod
    def missing():
        raise docker.errors.NotFound("missing")

    def test_backup_conflict_is_not_an_image_failure(self):
        self.client.containers.get.side_effect = None
        self.client.containers.get.return_value = self.original
        handler = DockerHandler(self.client, self.service.config)
        with self.assertRaises(RecreationError) as caught:
            handler.recreate("app", {"create_args": {}, "networks": {}})
        self.assertNotIsInstance(caught.exception, ImageRecreationError)
        self.original.stop.assert_not_called()

    def test_docker_handler_rechecks_window_immediately_before_stop(self):
        self.service.config.update_window_start, self.service.config.update_window_end = "02:00", "05:00"
        self.client.containers.get.side_effect = lambda name: self.original if name == "app" else self.missing()
        handler = DockerHandler(self.client, self.service.config)
        with patch("update_window.UpdateWindow.is_open", return_value=False), self.assertRaises(UpdateWindowClosed):
            handler.recreate("app", {"create_args": {}, "networks": {}})
        self.original.stop.assert_not_called()
        self.original.rename.assert_not_called()

    def test_window_deferral_from_handler_cancels_transaction_without_failure(self):
        self.service.docker.recreate.side_effect = UpdateWindowClosed("window closed")
        info = self.service.process_container(self.original, auto_update=True)
        self.assertEqual(info.status, UpdateStatus.SKIPPED_WINDOW)
        self.assertEqual(self.service.state_store.get_transactions(), {})
        self.assertEqual(self.service.failure_tracker, {})
        self.service.perform_rollback.assert_not_called()

    def test_window_deferral_state_failure_is_reported_and_retained(self):
        self.service.docker.recreate.side_effect = UpdateWindowClosed("window closed")
        self.service.state_store.end_transaction = Mock(side_effect=StateStoreError("disk full"))
        info = self.service.process_container(self.original, auto_update=True)
        self.assertEqual(info.status, UpdateStatus.FAILED)
        self.assertTrue(self.service.abort_updates_for_cycle)
        self.assertEqual(self.service.state_store.get_transactions()["app"]["phase"], "prepared")
        self.service.perform_rollback.assert_not_called()

    def test_service_interval_sleep_respects_window(self):
        self.service.config.update_window_start, self.service.config.update_window_end = "02:00", "05:00"
        with patch("update_window.datetime", wraps=datetime) as clock:
            clock.now.return_value = datetime(2026, 10, 2, 1, tzinfo=ZoneInfo("Europe/Zurich"))
            self.assertEqual(self.service._get_sleep_duration(), 3600)

    def test_daily_schedule_never_returns_negative_wait_in_autumn_fold(self):
        self.service.config.schedule_time = "02:30"
        with patch("main.datetime", wraps=datetime) as clock:
            clock.now.return_value = datetime(2026, 10, 25, 2, 15, tzinfo=ZoneInfo("Europe/Zurich"), fold=1)
            self.assertEqual(self.service._get_sleep_duration(), 24 * 3600 + 15 * 60)

    def test_daily_schedule_uses_real_timestamp_in_spring_gap(self):
        self.service.config.schedule_time = "02:30"
        with patch("main.datetime", wraps=datetime) as clock:
            clock.now.return_value = datetime(2026, 3, 29, 3, 10, tzinfo=ZoneInfo("Europe/Zurich"))
            self.assertEqual(self.service._get_sleep_duration(), 20 * 60)

    def test_temporary_registry_reversion_does_not_erase_block(self):
        self.fail_update()
        self.service.docker.check_for_update.return_value = (UpdateStatus.NO_UPDATE, "old-image", "old-image")
        self.service.process_container(self.original, auto_update=True)
        self.assertEqual(StateStore(self.state_path).get_cooldowns()["app"]["failed_image_id"], "new-image")
        self.assertFalse(self.service._is_in_cooldown("app"))

    def test_unchanged_rejection_state_is_not_rewritten_each_scan(self):
        self.fail_update()
        self.service.docker.check_for_update.return_value = (UpdateStatus.NO_UPDATE, "old-image", "old-image")
        self.service.process_container(self.original, auto_update=True)
        with patch.object(self.service.state_store, "set_cooldowns") as write:
            self.service.process_container(self.original, auto_update=True)
        write.assert_not_called()

    def test_registry_failure_preserves_existing_image_block(self):
        self.fail_update()
        self.service.docker.check_for_update.return_value = (UpdateStatus.FAILED, None, None)
        self.service.process_container(self.original, auto_update=True)
        self.assertEqual(StateStore(self.state_path).get_cooldowns()["app"]["failed_image_id"], "new-image")

    def test_state_write_failure_aborts_further_updates(self):
        self.service.state_store.set_cooldowns = Mock(side_effect=StateStoreError("disk full"))
        self.service.health.wait_for_health.return_value = False
        self.service.process_container(self.original, auto_update=True)
        self.assertTrue(self.service.abort_updates_for_cycle)
        self.assertEqual(self.service.failure_tracker, {})

    def test_closed_window_never_pulls_or_recreates(self):
        with patch("update_window.UpdateWindow.is_open", return_value=False):
            info = self.service.process_container(self.original, auto_update=True)
        self.assertEqual(info.status, UpdateStatus.SKIPPED_WINDOW)
        self.service.docker.check_for_update.assert_not_called()
        self.service.docker.recreate.assert_not_called()

    def test_window_closing_during_pull_never_captures_state(self):
        with patch("update_window.UpdateWindow.is_open", side_effect=[True, False]):
            info = self.service.process_container(self.original, auto_update=True)
        self.assertEqual(info.status, UpdateStatus.SKIPPED_WINDOW)
        self.service.docker.get_recreation_plan.assert_not_called()

    def test_window_closing_during_state_capture_never_starts_transaction(self):
        with patch("update_window.UpdateWindow.is_open", side_effect=[True, True, False]):
            info = self.service.process_container(self.original, auto_update=True)
        self.assertEqual(info.status, UpdateStatus.SKIPPED_WINDOW)
        self.assertEqual(self.service.state_store.get_transactions(), {})
        self.service.docker.recreate.assert_not_called()

    def test_window_closing_during_notification_cancels_prepared_transaction(self):
        with patch("update_window.UpdateWindow.is_open", side_effect=[True, True, True, False]):
            info = self.service.process_container(self.original, auto_update=True)
        self.assertEqual(info.status, UpdateStatus.SKIPPED_WINDOW)
        self.assertEqual(self.service.state_store.get_transactions(), {})
        self.service.docker.recreate.assert_not_called()

    def test_active_update_finishes_even_after_window_closes(self):
        with patch("update_window.UpdateWindow.is_open", side_effect=[True, True, True, True]):
            info = self.service.process_container(self.original, auto_update=True)
        self.assertEqual(info.status, UpdateStatus.UPDATED)
        self.service.docker.remove_backup.assert_called_once_with("app")

    def test_closed_window_does_not_scan_or_notify(self):
        with patch("update_window.UpdateWindow.is_open", return_value=False):
            self.service.run_cycle()
        self.service.docker.get_watched_containers.assert_not_called()
        self.service.notifier.notify_scan_started.assert_not_called()

    def test_skipped_image_is_visible_in_cycle_and_journal(self):
        self.fail_update()
        self.service.docker.get_watched_containers.return_value = ([self.original], [])
        self.service.run_cycle()
        summary = self.service.notifier.notify_summary_report.call_args.args[0]
        self.assertEqual(summary["skipped"], ["app"])
        self.assertEqual(self.service.journal._history[-1]["events"][0]["status"], "SKIPPED_FAILED_IMAGE")

    def test_recovery_blocks_unhealthy_replacement_before_rollback(self):
        tx_id = self.service.state_store.start_transaction("app", "original", "old-image", "new-image", image_ref="app:latest")
        self.service.state_store.update_transaction("app", "replacement_started", new_container_id="replacement", new_image_id="new-image")
        replacement = Mock()
        replacement.id, replacement.image.id, replacement.status = "replacement", "new-image", "running"
        replacement.labels = {"watcher.transaction_id": tx_id}
        self.client.containers.get.side_effect = lambda name: {"app": replacement, "app_backup": self.original}[name]
        self.service.health.wait_for_health.side_effect = [False, True]
        self.service._process_single_recovery("app", self.service.state_store.get_transactions()["app"])
        self.assertEqual(StateStore(self.state_path).get_cooldowns()["app"]["failed_image_id"], "new-image")
        self.assertEqual(self.service.state_store.get_transactions(), {})


class TestSkippedNotifications(unittest.TestCase):
    def check_summary(self, notifier_class, method):
        notifier = notifier_class.__new__(notifier_class)
        callback = Mock()
        setattr(notifier, method, callback)
        notifier.notify_summary_report({
            "updated": [], "failed": [], "rolled_back": [], "reported": [],
            "skipped": ["app<private>"], "interrupted": [],
        })
        message = str(callback.call_args)
        self.assertIn("Skipped", message)
        self.assertNotIn("No updates or errors detected", message)
        return message

    def test_discord_shows_policy_skips(self):
        self.check_summary(DiscordNotifier, "send_event")

    def test_slack_shows_policy_skips(self):
        self.check_summary(SlackNotifier, "_send")

    def test_ntfy_shows_policy_skips(self):
        self.check_summary(NtfyNotifier, "_send")

    def test_telegram_escapes_skipped_name(self):
        message = self.check_summary(TelegramNotifier, "_send")
        self.assertIn("app&lt;private&gt;", message)
        self.assertNotIn("app<private>", message)


if __name__ == "__main__":
    unittest.main()
