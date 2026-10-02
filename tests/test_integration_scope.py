"""The live-test client must reject resources outside its ownership scope."""

import unittest
from unittest.mock import Mock

import docker

from integration_test_docker import OWNER_LABEL, OwnedClient


class TestIntegrationScope(unittest.TestCase):
    def setUp(self):
        self.raw = Mock()
        self.owner = "test-owner"
        self.prefix = "test-prefix-"
        self.client = OwnedClient(self.raw, self.prefix, self.owner)

    def test_list_adds_owner_to_string_label_filter(self):
        self.client.containers.list(all=True, filters={"label": "watcher.self=true"})
        self.raw.containers.list.assert_called_once_with(
            all=True, filters={"label": ["watcher.self=true", f"{OWNER_LABEL}={self.owner}"]}
        )

    def test_list_preserves_filters_without_mutating_them(self):
        filters = {"label": ["watcher.enable=true"], "status": "running"}
        self.client.containers.list(filters=filters)
        self.assertEqual(filters, {"label": ["watcher.enable=true"], "status": "running"})
        self.raw.containers.list.assert_called_once_with(filters={
            "label": ["watcher.enable=true", f"{OWNER_LABEL}={self.owner}"], "status": "running",
        })

    def test_get_rejects_wrong_prefix(self):
        self.raw.containers.get.return_value = Mock(name="unrelated", labels={OWNER_LABEL: self.owner})
        self.raw.containers.get.return_value.name = "unrelated"
        with self.assertRaises(docker.errors.NotFound):
            self.client.containers.get("id")

    def test_get_rejects_wrong_owner(self):
        self.raw.containers.get.return_value = Mock(labels={OWNER_LABEL: "someone-else"})
        self.raw.containers.get.return_value.name = f"{self.prefix}app"
        with self.assertRaises(docker.errors.NotFound):
            self.client.containers.get("id")

    def test_get_returns_owned_container(self):
        container = Mock(labels={OWNER_LABEL: self.owner})
        container.name = f"{self.prefix}app"
        self.raw.containers.get.return_value = container
        self.assertIs(self.client.containers.get("id"), container)

    def test_create_rejects_wrong_prefix_before_api_call(self):
        with self.assertRaises(ValueError):
            self.client.containers.create(image="fixture", name="unrelated", labels={OWNER_LABEL: self.owner})
        self.raw.containers.create.assert_not_called()

    def test_create_rejects_wrong_owner_before_api_call(self):
        with self.assertRaises(ValueError):
            self.client.containers.create(image="fixture", name=f"{self.prefix}app", labels={OWNER_LABEL: "else"})
        self.raw.containers.create.assert_not_called()

    def test_create_passes_owned_request_to_api(self):
        kwargs = {"image": "fixture", "name": f"{self.prefix}app", "labels": {OWNER_LABEL: self.owner}}
        self.assertIs(self.client.containers.create(**kwargs), self.raw.containers.create.return_value)
        self.raw.containers.create.assert_called_once_with(**kwargs)
