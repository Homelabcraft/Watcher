import copy
import threading
import unittest
from unittest.mock import MagicMock, PropertyMock, patch

import docker
from base_test import BaseTest
from docker.models.containers import _create_container_args

from config import Config
from docker_handler import DockerHandler
from exceptions import RecreationError
from main import WatcherService
from models import ContainerUpdateInfo, UpdateStatus


class TestStabilization(BaseTest):
    def setUp(self):
        super().setUp()
        self.client = MagicMock()
        self.config = Config()
        self.handler = DockerHandler(self.client, self.config)
        self.handler.self_id = None
        self.service = WatcherService.__new__(WatcherService)
        self.service.shutdown_event = threading.Event()
        self.service.client = self.client
        self.service.config = self.config
        self.service.docker = self.handler
        self.service.notifier = MagicMock()

    def container(self, config=None, image_config=None, name="app"):
        container = MagicMock()
        container.name = name
        container.id = f"{name}-id"
        container.attrs = {
            "Config": {"Image": "app:latest", **(config or {})},
            "HostConfig": {},
            "NetworkSettings": {"Networks": {}},
        }
        container.image.attrs = {"Config": image_config or {}}
        container.labels = container.attrs["Config"].get("Labels", {})
        return container

    def test_inherited_image_defaults_are_not_pinned(self):
        defaults = {
            "Cmd": ["old-argument"],
            "Entrypoint": ["/old-entrypoint"],
            "Env": ["IMAGE_DEFAULT=old"],
            "User": "old-user",
            "WorkingDir": "/old-directory",
            "StopSignal": "SIGQUIT",
            "Healthcheck": {"Test": ["CMD", "/old-healthcheck"]},
        }
        container = self.container(defaults, defaults)
        args = self.handler.get_recreation_plan(container)["create_args"]
        for key in (
            "command", "entrypoint", "environment", "user", "working_dir",
            "stop_signal", "healthcheck",
        ):
            with self.subTest(key=key):
                self.assertNotIn(key, args)

    def test_explicit_overrides_are_preserved(self):
        defaults = {
            "Cmd": ["image-command"], "Entrypoint": ["/image-entrypoint"],
            "User": "image-user", "WorkingDir": "/image-directory",
            "StopSignal": "SIGTERM",
        }
        overrides = {
            "Cmd": ["custom-command"], "Entrypoint": ["/custom-entrypoint"],
            "User": "custom-user", "WorkingDir": "/custom-directory",
            "StopSignal": "SIGQUIT",
        }
        args = self.handler.get_recreation_plan(
            self.container(overrides, defaults)
        )["create_args"]
        for field, argument in (
            ("Cmd", "command"), ("Entrypoint", "entrypoint"),
            ("User", "user"), ("WorkingDir", "working_dir"),
            ("StopSignal", "stop_signal"),
        ):
            with self.subTest(field=field):
                self.assertEqual(args[argument], overrides[field])

    def test_environment_keeps_only_container_overrides(self):
        container = self.container(
            {"Env": ["DEFAULT=old", "CHANGED=custom", "CUSTOM=a=b", "EMPTY="]},
            {"Env": ["DEFAULT=old", "CHANGED=image", "REMOVED=old"]},
        )
        args = self.handler.get_recreation_plan(container)["create_args"]
        self.assertEqual(args["environment"], ["CHANGED=custom", "CUSTOM=a=b", "EMPTY="])

    def test_image_labels_are_not_pinned(self):
        container = self.container(
            {"Labels": {"image.version": "old", "changed": "custom", "watcher.enable": "true"}},
            {"Labels": {"image.version": "old", "changed": "image"}},
        )
        args = self.handler.get_recreation_plan(container)["create_args"]
        self.assertEqual(args["labels"], {"changed": "custom", "watcher.enable": "true"})

    def test_fields_without_image_defaults_are_preserved(self):
        container = self.container({"Cmd": ["custom"], "Env": ["CUSTOM=1"]})
        args = self.handler.get_recreation_plan(container)["create_args"]
        self.assertEqual(args["command"], ["custom"])
        self.assertEqual(args["environment"], ["CUSTOM=1"])

    def test_explicit_empty_entrypoint_is_preserved(self):
        args = self.handler.get_recreation_plan(
            self.container({"Entrypoint": [""]}, {"Entrypoint": ["/image-entrypoint"]})
        )["create_args"]
        self.assertEqual(args["entrypoint"], [""])

    def test_custom_healthcheck_is_preserved(self):
        args = self.handler.get_recreation_plan(self.container(
            {"Healthcheck": {"Test": ["NONE"]}},
            {"Healthcheck": {"Test": ["CMD", "image-check"]}},
        ))["create_args"]
        self.assertEqual(args["healthcheck"], {"test": ["NONE"]})

    def test_inspect_data_is_not_modified(self):
        container = self.container(
            {"Cmd": ["old"], "Env": ["DEFAULT=1", "CUSTOM=2"], "Labels": {"image": "old"}},
            {"Cmd": ["old"], "Env": ["DEFAULT=1"], "Labels": {"image": "old"}},
        )
        original_container = copy.deepcopy(container.attrs)
        original_image = copy.deepcopy(container.image.attrs)
        self.handler.get_recreation_plan(container)
        self.assertEqual(container.attrs, original_container)
        self.assertEqual(container.image.attrs, original_image)

    def test_image_inspection_failure_aborts_before_stop(self):
        container = self.container()
        with patch.object(type(container), "image", new_callable=PropertyMock, create=True) as image:
            image.side_effect = docker.errors.NotFound("Original image unavailable")
            with self.assertRaises(RecreationError):
                self.handler.get_recreation_plan(container)
        container.stop.assert_not_called()
        self.client.containers.create.assert_not_called()

    def test_invalid_image_config_aborts_before_stop(self):
        container = self.container()
        container.image.attrs = {"Config": ["invalid"]}
        with self.assertRaises(RecreationError):
            self.handler.get_recreation_plan(container)
        container.stop.assert_not_called()

    def test_regex_excludes_label_dependent_from_restart(self):
        self.config.exclude_regex = r"^protected-"
        dependent = self.container({"Labels": {"watcher.depends_on": "app"}}, name="protected-db")
        self.client.containers.list.return_value = [dependent]
        self.assertEqual(self.handler.get_watched_containers(), ([], []))
        self.service.restart_dependents([ContainerUpdateInfo("app", "old-id", UpdateStatus.UPDATED)])
        dependent.restart.assert_not_called()

    def test_regex_excludes_network_dependent_from_restart(self):
        self.config.exclude_regex = r"^protected-"
        dependent = self.container(name="protected-network")
        dependent.attrs["HostConfig"]["NetworkMode"] = "container:app"
        self.client.containers.list.return_value = [dependent]
        self.assertEqual(self.service.get_dependents(["app"], ["old-id"]), [])

    def test_names_and_self_protection_apply_to_dependents(self):
        self.config.exclude_names.append("excluded")
        excluded = self.container({"Labels": {"watcher.depends_on": "app"}}, name="excluded")
        marked_self = self.container({"Labels": {"watcher.depends_on": "app", "watcher.self": "true"}}, name="self")
        detected_self = self.container({"Labels": {"watcher.depends_on": "app"}}, name="detected-self")
        self.handler.self_id = detected_self.id
        self.client.containers.list.return_value = [excluded, marked_self, detected_self]
        self.assertEqual(self.service.get_dependents(["app"], ["old-id"]), [])

    def test_monitor_only_label_does_not_disable_explicit_dependency(self):
        dependent = self.container({"Labels": {"watcher.enable": "false", "watcher.depends_on": "app"}}, name="dependent")
        self.client.containers.list.return_value = [dependent]
        self.assertEqual(self.service.get_dependents(["app"], ["old-id"]), ["dependent"])

    def recreation_args(self, network_mode, networks):
        original = self.container()
        original.attrs["Config"]["StopTimeout"] = 1

        def get_container(name):
            if name.endswith("_backup"):
                raise docker.errors.NotFound("No backup")
            return original

        self.client.containers.get.side_effect = get_container
        self.client.api.create_endpoint_config.side_effect = lambda **kwargs: docker.types.EndpointConfig(version="1.45", **kwargs)
        self.handler.recreate("app", {
            "create_args": {"name": "app", "image": "app:latest", "network_mode": network_mode},
            "networks": networks,
        })
        return dict(self.client.containers.create.call_args.kwargs)

    def test_primary_network_and_aliases_survive_sdk_conversion(self):
        args = self.recreation_args("primary", {
            "secondary": {"Aliases": ["secondary-alias"]},
            "primary": {"Aliases": ["primary-alias"]},
        })
        self.assertEqual(args["network"], "primary")
        self.assertNotIn("network_mode", args)
        args["version"] = "1.45"
        payload = _create_container_args(args)
        self.assertEqual(payload["host_config"]["NetworkMode"], "primary")
        endpoint = payload["networking_config"]["EndpointsConfig"]["primary"]
        self.assertEqual(endpoint["Aliases"], ["primary-alias"])

    def test_special_network_modes_do_not_receive_custom_network(self):
        for mode in ("host", "none", "container:other"):
            with self.subTest(mode=mode):
                args = self.recreation_args(mode, {mode: {}})
                self.assertNotIn("network", args)
                self.assertEqual(args["network_mode"], mode)
                self.assertIsNone(args["networking_config"])

    def test_default_bridge_keeps_secondary_networks(self):
        for mode in ("bridge", "default"):
            with self.subTest(mode=mode):
                self.client.networks.get.return_value.connect.reset_mock()
                args = self.recreation_args(mode, {"secondary": {}, "bridge": {}})
                self.assertEqual(args["network"], "bridge")
                self.client.networks.get.return_value.connect.assert_called_once()

    def test_stop_timeout_uses_supported_low_level_creation(self):
        self.client.api._version = "1.45"
        self.client.api.create_container.return_value = {"Id": "new-id"}
        args = {"name": "app", "image": "app:latest", "stop_timeout": 42}
        original_args = args.copy()
        self.handler._create_replacement(args, None)
        self.client.containers.create.assert_not_called()
        self.client.api.create_container.assert_called_once()
        self.assertEqual(self.client.api.create_container.call_args.kwargs["stop_timeout"], 42)
        self.client.containers.get.assert_called_with("new-id")
        self.assertEqual(args, original_args)


if __name__ == "__main__":
    unittest.main()
