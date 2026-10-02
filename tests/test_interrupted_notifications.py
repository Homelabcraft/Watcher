import unittest
from unittest.mock import Mock

from discord_notifier import DiscordNotifier
from ntfy_notifier import NtfyNotifier
from slack_notifier import SlackNotifier
from telegram_notifier import TelegramNotifier


class TestInterruptedNotifications(unittest.TestCase):
    def check_summary(self, notifier_class, method):
        notifier = notifier_class.__new__(notifier_class)
        callback = Mock()
        setattr(notifier, method, callback)
        notifier.notify_summary_report({
            "updated": [], "failed": [], "rolled_back": [], "reported": [],
            "skipped": [], "interrupted": ["app<private>"],
        })
        message = str(callback.call_args)
        self.assertIn("Interrupted", message)
        self.assertNotIn("No updates or errors detected", message)
        return message

    def test_discord_does_not_report_no_changes_on_interruption(self):
        self.check_summary(DiscordNotifier, "send_event")

    def test_slack_does_not_report_no_changes_on_interruption(self):
        self.check_summary(SlackNotifier, "_send")

    def test_ntfy_does_not_report_no_changes_on_interruption(self):
        self.check_summary(NtfyNotifier, "_send")

    def test_telegram_escapes_interrupted_container_name(self):
        message = self.check_summary(TelegramNotifier, "_send")
        self.assertIn("app&lt;private&gt;", message)
        self.assertNotIn("app<private>", message)
