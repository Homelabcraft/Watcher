import unittest
from types import SimpleNamespace
from unittest.mock import patch

from multi_notifier import MultiNotifier
from noop_notifier import NoopNotifier
from notifier_factory import build_notifier


class TestNotifierFactory(unittest.TestCase):
    def test_build_notifier_noop_when_no_backends_configured(self):
        cfg = SimpleNamespace(discord_webhook_url=None, slack_webhook_url=None)
        n = build_notifier(cfg)
        self.assertIsInstance(n, NoopNotifier)

    def test_build_notifier_multi_when_discord_and_slack(self):
        cfg = SimpleNamespace(
            discord_webhook_url="https://discord.example/hook",
            slack_webhook_url="https://hooks.slack.com/services/xxx",
        )
        n = build_notifier(cfg)
        self.assertIsInstance(n, MultiNotifier)


@patch("slack_notifier.requests.post")
class TestSlackNotifier(unittest.TestCase):
    def test_notify_failure_sends_text(self, mock_post):
        from slack_notifier import SlackNotifier

        SlackNotifier("http://mock").notify_failure("app", "bad")
        mock_post.assert_called_once()
        self.assertIn("FAILED", mock_post.call_args[1]["json"]["text"])
        self.assertIn("app", mock_post.call_args[1]["json"]["text"])


if __name__ == "__main__":
    unittest.main()
