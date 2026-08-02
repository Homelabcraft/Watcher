import os
os.environ["UNITTEST_MODE"] = "1"
import tempfile
import unittest
from unittest.mock import patch

class BaseTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        
        self.state_path = os.path.join(self.temp_dir.name, "state.json")
        self.journal_path = os.path.join(self.temp_dir.name, "journal.json")
        
        # Isolate environment
        self.env_patcher = patch.dict(os.environ, {
            "STATE_PATH": self.state_path,
            "JOURNAL_PATH": self.journal_path,
            "DISCORD_WEBHOOK_URL": "http://mock",
            "CHECK_INTERVAL": "60",
            "WATCH_BY_LABEL": "false",
            "DRY_RUN": "false"
        })
        self.env_patcher.start()
        self.addCleanup(self.env_patcher.stop)
