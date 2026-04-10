import unittest
from types import SimpleNamespace

from noop_notifier import NoopNotifier
from notifier_factory import build_notifier


class TestNotifierFactory(unittest.TestCase):
    def test_build_notifier_noop_when_no_backends_configured(self):
        cfg = SimpleNamespace(discord_webhook_url=None)
        n = build_notifier(cfg)
        self.assertIsInstance(n, NoopNotifier)


if __name__ == "__main__":
    unittest.main()
