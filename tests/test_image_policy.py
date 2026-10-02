import unittest
from unittest.mock import MagicMock

from base_test import BaseTest

from config import Config
from docker_handler import DockerHandler


class TestImagePolicy(BaseTest):
    def setUp(self):
        super().setUp()
        self.mock_client = MagicMock()
        self.config = Config()
        self.handler = DockerHandler(self.mock_client, self.config)

    def test_image_policy_ignored_tags(self):
        # Config.Image=app:1.2 and image.tags includes app:latest -> ignored
        c1 = MagicMock()
        c1.attrs = {"Config": {"Image": "app:1.2"}}
        c1.image.tags = ["app:latest", "app:1.2"]
        self.assertIsNone(self.handler.get_image_ref(c1))

        # Config.Image=app@sha256:... and image.tags includes app:latest -> ignored
        c2 = MagicMock()
        c2.attrs = {"Config": {"Image": "app@sha256:12345"}}
        c2.image.tags = ["app:latest"]
        self.assertIsNone(self.handler.get_image_ref(c2))

        # Config.Image=app and image.tags includes app:latest -> ignored
        c3 = MagicMock()
        c3.attrs = {"Config": {"Image": "app"}}
        c3.image.tags = ["app:latest"]
        self.assertIsNone(self.handler.get_image_ref(c3))

    def test_image_policy_accepted_tag(self):
        # Config.Image=app:latest -> accepted
        c4 = MagicMock()
        c4.attrs = {"Config": {"Image": "app:latest"}}
        c4.image.tags = ["app:latest"]
        self.assertEqual(self.handler.get_image_ref(c4), "app:latest")

if __name__ == '__main__':
    unittest.main()
