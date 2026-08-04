import unittest
from unittest.mock import MagicMock

from base_test import BaseTest

from config import Config
from docker_handler import DockerHandler, RecreationError


class TestNetworking(BaseTest):
    def setUp(self):
        super().setUp()
        self.mock_client = MagicMock()
        self.config = Config()
        self.handler = DockerHandler(self.mock_client, self.config)

    def test_dynamic_primary_network(self):
        c = MagicMock()
        c.attrs = {
            "NetworkSettings": {
                "Networks": {
                    "bridge": {
                        "IPAMConfig": None,
                        "Aliases": ["app"]
                    }
                }
            },
            "HostConfig": {"NetworkMode": "bridge"}
        }
        plan = self.handler.get_recreation_plan(c)
        self.assertNotIn("ipv4_address", plan["networks"]["bridge"])
        self.assertNotIn("ipv6_address", plan["networks"]["bridge"])

    def test_multiple_dynamic_networks(self):
        c = MagicMock()
        c.attrs = {
            "NetworkSettings": {
                "Networks": {
                    "net1": {"IPAMConfig": None},
                    "net2": {"IPAMConfig": None}
                }
            },
            "HostConfig": {"NetworkMode": "net1"}
        }
        plan = self.handler.get_recreation_plan(c)
        self.assertIn("net1", plan["networks"])
        self.assertIn("net2", plan["networks"])
        self.assertNotIn("ipv4_address", plan["networks"]["net1"])
        self.assertNotIn("ipv4_address", plan["networks"]["net2"])

    def test_static_ipv4_rejected(self):
        c = MagicMock()
        c.name = "app"
        c.attrs = {
            "NetworkSettings": {
                "Networks": {
                    "custom_net": {
                        "IPAMConfig": {"IPv4Address": "192.168.1.100"}
                    }
                }
            },
            "HostConfig": {"NetworkMode": "custom_net"}
        }
        with self.assertRaises(RecreationError) as e:
            self.handler.get_recreation_plan(c)
        self.assertIn("static IP", str(e.exception))

    def test_static_ipv6_rejected(self):
        c = MagicMock()
        c.name = "app"
        c.attrs = {
            "NetworkSettings": {
                "Networks": {
                    "custom_net": {
                        "IPAMConfig": {"IPv6Address": "2001:db8::1"}
                    }
                }
            },
            "HostConfig": {"NetworkMode": "custom_net"}
        }
        with self.assertRaises(RecreationError) as e:
            self.handler.get_recreation_plan(c)
        self.assertIn("static IP", str(e.exception))

    def test_host_none_container_networks(self):
        # host mode
        c = MagicMock()
        c.attrs = {"NetworkSettings": {"Networks": {"host": {}}}, "HostConfig": {"NetworkMode": "host"}}
        plan = self.handler.get_recreation_plan(c)
        self.assertEqual(plan["networks"], {"host": {}})

        # none mode
        c.attrs = {"NetworkSettings": {"Networks": {"none": {}}}, "HostConfig": {"NetworkMode": "none"}}
        plan = self.handler.get_recreation_plan(c)
        self.assertEqual(plan["networks"], {"none": {}})

        # container mode
        c.attrs = {"NetworkSettings": {"Networks": {}}, "HostConfig": {"NetworkMode": "container:12345"}}
        plan = self.handler.get_recreation_plan(c)
        self.assertEqual(plan["networks"], {})

if __name__ == '__main__':
    unittest.main()
