"""Opt-in Linux Docker lifecycle checks with disposable, ownership-scoped resources.

Default update pulls use local fixture tags. WATCHER_RELEASE_GATES=1 additionally
enables a real private registry and instrumented Linux worker SIGKILL/restart tests.
"""

import io
import ipaddress
import json
import os
import re
import shutil
import tarfile
import tempfile
import time
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import docker
import requests
from docker.models.containers import Container
from docker.models.images import ImageCollection

os.environ["UNITTEST_MODE"] = "1"

from config import Config
from main import WatcherService
from models import ContainerUpdateInfo, UpdateStatus

OWNER_LABEL = "watcher.integration.owner"


class OwnedContainers:
    """Prevent Watcher from discovering or creating containers outside this run."""

    def __init__(self, client, prefix, owner):
        self.client = client
        self.prefix = prefix
        self.owner = owner

    def list(self, *args, **kwargs):
        filters = dict(kwargs.pop("filters", {}) or {})
        labels = filters.get("label", [])
        if isinstance(labels, str):
            labels = [labels]
        filters["label"] = [*labels, f"{OWNER_LABEL}={self.owner}"]
        return self.client.containers.list(*args, filters=filters, **kwargs)

    def get(self, name):
        container = self.client.containers.get(name)
        if not container.name.startswith(self.prefix) or container.labels.get(OWNER_LABEL) != self.owner:
            raise docker.errors.NotFound("Container is outside the integration-test scope")
        return container

    def create(self, *args, **kwargs):
        if not str(kwargs.get("name", "")).startswith(self.prefix):
            raise ValueError("Refusing to create a container outside the test prefix")
        if (kwargs.get("labels") or {}).get(OWNER_LABEL) != self.owner:
            raise ValueError("Refusing to create an unowned container")
        return self.client.containers.create(*args, **kwargs)


class OwnedClient:
    def __init__(self, client, prefix, owner):
        self.containers = OwnedContainers(client, prefix, owner)
        self.client = client

    def __getattr__(self, name):
        return getattr(self.client, name)


@unittest.skipUnless(os.getenv("WATCHER_DOCKER_INTEGRATION") == "1", "Set WATCHER_DOCKER_INTEGRATION=1 to opt in")
class TestDockerIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.owner = uuid.uuid4().hex
        cls.prefix = f"watcher-v17-test-{cls.owner[:12]}-"
        cls.raw_client = docker.from_env(timeout=120)
        cls.addClassCleanup(cls.raw_client.close)
        if cls.raw_client.info().get("OSType") != "linux":
            raise unittest.SkipTest("These lifecycle tests require a Linux Docker daemon")
        cls.client = OwnedClient(cls.raw_client, cls.prefix, cls.owner)
        cls.networks = []
        cls.volumes = []
        cls.image_tags = set()
        cls.addClassCleanup(cls.cleanup_resources)
        cls.images = {
            version: cls.build_image(version)
            for version in ("old", "new", "broken")
        }
        print(f"Docker integration scope: {cls.prefix}", flush=True)

    @classmethod
    def build_image(cls, version):
        path = "/old-start" if version == "old" else "/new-start"
        check = "exit 1" if version == "broken" else "test -f /tmp/ready"
        dockerfile = (
            "FROM alpine:3.23\n"
            f"LABEL watcher.integration.image={cls.owner} image.version={version}\n"
            f"ENV IMAGE_DEFAULT={version}\n"
            f"WORKDIR /{version}\n"
            f"COPY start.sh {path}\n"
            f"RUN chmod +x {path}\n"
            f"HEALTHCHECK --interval=1s --timeout=1s --retries=1 CMD {check}\n"
            f'ENTRYPOINT ["{path}"]\n'
            f'CMD ["{version}"]\n'
        )
        script = (
            '#!/bin/sh\nprintf "%s" "$1" >/tmp/version\ntouch /tmp/ready\n'
            'sleep 3600 &\nchild=$!\ntrap "kill $child; exit 0" TERM INT\nwait "$child"\n'
        )
        context = io.BytesIO()
        with tarfile.open(fileobj=context, mode="w") as archive:
            for name, contents in (("Dockerfile", dockerfile), ("start.sh", script)):
                data = contents.encode("ascii")
                entry = tarfile.TarInfo(name)
                entry.size = len(data)
                archive.addfile(entry, io.BytesIO(data))
        context.seek(0)
        tag = f"{cls.prefix}image-{version}:fixture"
        cls.image_tags.add(tag)
        image, _logs = cls.raw_client.images.build(fileobj=context, custom_context=True, tag=tag, rm=True)
        return image

    @classmethod
    def cleanup_resources(cls):
        errors = []
        for container in cls.client.containers.list(all=True):
            try:
                if container.name.startswith(cls.prefix) and container.labels.get(OWNER_LABEL) == cls.owner:
                    container.remove(force=True, v=True)
            except docker.errors.DockerException as error:
                errors.append(str(error))
        for collection, names in ((cls.raw_client.networks, cls.networks), (cls.raw_client.volumes, cls.volumes)):
            for name in reversed(names):
                try:
                    resource = collection.get(name)
                    if resource.attrs.get("Labels", {}).get(OWNER_LABEL) == cls.owner:
                        resource.remove()
                except docker.errors.NotFound:
                    pass
                except docker.errors.DockerException as error:
                    errors.append(str(error))
        for tag in cls.image_tags:
            try:
                image = cls.raw_client.images.get(tag)
                if image.labels.get("watcher.integration.image") == cls.owner:
                    cls.raw_client.images.remove(tag)
            except docker.errors.ImageNotFound:
                pass
            except docker.errors.DockerException as error:
                errors.append(str(error))
        if errors:
            raise AssertionError("Integration cleanup failed: " + "; ".join(errors))

    def setUp(self):
        self.storage = tempfile.TemporaryDirectory()
        self.addCleanup(self.storage.cleanup)
        environment = {
            "STATE_PATH": os.path.join(self.storage.name, "state.json"),
            "JOURNAL_PATH": os.path.join(self.storage.name, "journal.json"),
            "CHECK_INTERVAL": "60", "SCHEDULE_TIME": "", "TZ": "UTC",
            "WATCH_BY_LABEL": "true", "WATCH_LABEL_KEY": "watcher.enable",
            "WATCH_LABEL_VALUE": "true", "DRY_RUN": "false",
            "EXCLUDE_CONTAINER_NAMES": "", "EXCLUDE_CONTAINER_REGEX": f"^(?!{re.escape(self.prefix)}).*$",
            "DISCORD_WEBHOOK_URL": "", "SLACK_WEBHOOK_URL": "", "NTFY_URL": "",
            "TELEGRAM_BOT_TOKEN": "", "TELEGRAM_CHAT_ID": "",
            "REGISTRY_USERNAME": "", "REGISTRY_PASSWORD": "",
            "HEALTH_CHECK_RETRIES": "5", "HEALTH_CHECK_DELAY": "1",
            "CLEANUP_OLD_IMAGES": "false", "ALLOW_USER_FALLBACK": "false",
            "MAX_UPDATES_PER_CYCLE": "0", "RESTART_DEPENDENTS": "true",
            "NOTIFY_ON_STARTUP": "false", "NOTIFY_ON_UPDATE_START": "false",
        }
        env_patch = patch.dict(os.environ, environment)
        env_patch.start()
        self.addCleanup(env_patch.stop)
        self.config = Config()
        self.service = self.new_service()
        self.ref = f"{self.prefix}{self._testMethodName}:latest"
        self.image_tags.add(self.ref)

    def new_service(self):
        with patch("main.docker.from_env", return_value=self.client), patch.object(WatcherService, "_setup_signals"):
            return WatcherService(self.config)

    def create_container(self, suffix="app", **kwargs):
        self.images["old"].tag(self.ref)
        labels = {OWNER_LABEL: self.owner, "watcher.enable": "true", **kwargs.pop("labels", {})}
        container = self.client.containers.create(
            image=self.ref, name=f"{self.prefix}{self._testMethodName}-{suffix}", labels=labels, **kwargs
        )
        container.start()
        self.addCleanup(self.remove_test_container, container.name)
        return container

    def remove_test_container(self, name):
        for candidate in (name, f"{name}_backup"):
            try:
                self.client.containers.get(candidate).remove(force=True, v=True)
            except docker.errors.NotFound:
                pass

    def update(self, container, version="new"):
        self.images[version].tag(self.ref)

        def local_pull(_collection, reference, *args, **kwargs):
            if reference != self.ref:
                raise AssertionError(f"Unexpected image pull outside test fixture: {reference}")
            return self.raw_client.images.get(reference)

        with patch.object(ImageCollection, "pull", autospec=True, side_effect=local_pull):
            return self.service.process_container(container, auto_update=True)

    def network(self, suffix):
        name = f"{self.prefix}{self._testMethodName}-{suffix}"
        network = self.raw_client.networks.create(name, labels={OWNER_LABEL: self.owner})
        self.networks.append(name)
        return network

    def test_update_inherits_new_image_defaults(self):
        original = self.create_container()
        result = self.update(original)
        self.assertEqual(result.status, UpdateStatus.UPDATED, result.error_message)
        current = self.client.containers.get(original.name)
        config = current.attrs["Config"]
        self.assertEqual(config["Entrypoint"], ["/new-start"])
        self.assertEqual(config["Cmd"], ["new"])
        self.assertIn("IMAGE_DEFAULT=new", config["Env"])
        self.assertEqual(config["WorkingDir"], "/new")
        self.assertEqual(current.labels["image.version"], "new")
        self.assertEqual(current.exec_run("cat /tmp/version").output, b"new")
        self.assertNotIn(original.name, self.service.state_store.get_transactions())
        with self.assertRaises(docker.errors.NotFound):
            self.client.containers.get(f"{original.name}_backup")

    def test_custom_command_and_environment_are_preserved(self):
        original = self.create_container(command=["custom"], environment={"IMAGE_DEFAULT": "custom", "EXTRA": "keep"})
        result = self.update(original)
        self.assertEqual(result.status, UpdateStatus.UPDATED, result.error_message)
        current = self.client.containers.get(original.name)
        self.assertEqual(current.attrs["Config"]["Cmd"], ["custom"])
        self.assertIn("IMAGE_DEFAULT=custom", current.attrs["Config"]["Env"])
        self.assertIn("EXTRA=keep", current.attrs["Config"]["Env"])

    def test_unhealthy_new_image_rolls_back(self):
        original = self.create_container()
        name, original_id = original.name, original.id
        result = self.update(original, "broken")
        self.assertEqual(result.status, UpdateStatus.ROLLED_BACK, result.error_message)
        self.assertTrue(result.rollback_success, result.rollback_details)
        restored = self.client.containers.get(name)
        self.assertEqual(restored.id, original_id)
        self.assertEqual(restored.status, "running")

    def test_named_volume_data_is_preserved(self):
        name = f"{self.prefix}data"
        volume = self.raw_client.volumes.create(name=name, labels={OWNER_LABEL: self.owner})
        self.volumes.append(name)
        original = self.create_container(mounts=[docker.types.Mount("/data", volume.name, type="volume")])
        self.assertEqual(original.exec_run(["sh", "-c", "printf preserved >/data/probe"]).exit_code, 0)
        result = self.update(original)
        self.assertEqual(result.status, UpdateStatus.UPDATED, result.error_message)
        current = self.client.containers.get(original.name)
        self.assertEqual(current.exec_run("cat /data/probe").output, b"preserved")
        self.assertEqual(current.attrs["Mounts"][0]["Name"], volume.name)

    def test_multiple_dynamic_networks_are_preserved(self):
        first, second = self.network("first"), self.network("second")
        original = self.create_container(network=first.name)
        second.connect(original, aliases=["custom-alias"])
        result = self.update(original)
        self.assertEqual(result.status, UpdateStatus.UPDATED, result.error_message)
        current = self.client.containers.get(original.name)
        networks = current.attrs["NetworkSettings"]["Networks"]
        self.assertEqual(set(networks), {first.name, second.name})
        self.assertIn("custom-alias", networks[second.name]["Aliases"])

    def test_static_ipv4_is_rejected_before_stop(self):
        network = self.network("static")
        network.reload()
        subnet = ipaddress.ip_network(network.attrs["IPAM"]["Config"][0]["Subnet"])
        endpoint = self.raw_client.api.create_endpoint_config(ipv4_address=str(subnet.network_address + 42))
        original = self.create_container(network=network.name, networking_config={network.name: endpoint})
        result = self.update(original)
        self.assertEqual(result.status, UpdateStatus.FAILED)
        self.assertIn("static IP", result.error_message)
        original.reload()
        self.assertEqual(original.status, "running")

    def test_default_bridge_and_secondary_network_are_preserved(self):
        secondary = self.network("secondary")
        original = self.create_container(network_mode="bridge")
        secondary.connect(original, aliases=["secondary-alias"])
        result = self.update(original)
        self.assertEqual(result.status, UpdateStatus.UPDATED, result.error_message)
        networks = self.client.containers.get(original.name).attrs["NetworkSettings"]["Networks"]
        self.assertEqual(set(networks), {"bridge", secondary.name})
        self.assertIn("secondary-alias", networks[secondary.name]["Aliases"])

    def test_regex_excluded_dependent_is_not_restarted(self):
        target = self.create_container()
        dependent = self.create_container("protected", labels={"watcher.depends_on": target.name})
        dependent.reload()
        started = dependent.attrs["State"]["StartedAt"]
        self.config.exclude_regex += f"|^{re.escape(dependent.name)}$"
        self.service.restart_dependents([ContainerUpdateInfo(target.name, target.id, UpdateStatus.UPDATED)])
        dependent.reload()
        self.assertEqual(dependent.attrs["State"]["StartedAt"], started)

    def test_shared_network_dependent_blocks_update(self):
        target = self.create_container()
        self.create_container("dependent", network_mode=f"container:{target.id}")
        result = self.update(target)
        self.assertEqual(result.status, UpdateStatus.FAILED)
        self.assertEqual(result.error_step, "dependency_check")
        target.reload()
        self.assertEqual(target.status, "running")

    def test_existing_backup_is_never_deleted(self):
        target = self.create_container()
        backup = self.create_container("app_backup")
        backup_id = backup.id
        result = self.update(target)
        self.assertEqual(result.status, UpdateStatus.FAILED)
        self.assertEqual(self.client.containers.get(backup.name).id, backup_id)
        target.reload()
        self.assertEqual(target.status, "running")

    def test_recovery_from_persisted_backup_rename(self):
        target = self.create_container()
        name, original_id = target.name, target.id
        self.service.state_store.start_transaction(name, original_id, target.image.id, self.images["new"].id)
        target.stop(timeout=2)
        target.rename(f"{name}_backup")
        self.service.state_store.update_transaction(name, "backup_renamed", backup_container_id=original_id)
        recovered = self.new_service()
        recovered.startup_recovery()
        self.assertEqual(self.client.containers.get(name).id, original_id)
        self.assertNotIn(name, recovered.state_store.get_transactions())

    def test_recovery_from_persisted_replacement(self):
        target = self.create_container()
        name = target.name
        self.images["new"].tag(self.ref)
        transaction = self.service.state_store.start_transaction(name, target.id, target.image.id, self.images["new"].id)
        plan = self.service.docker.get_recreation_plan(target)
        replacement = self.service.docker.recreate(name, plan, self.service.state_store, transaction_id=transaction)
        recovered = self.new_service()
        recovered.startup_recovery()
        self.assertEqual(self.client.containers.get(name).id, replacement.id)
        self.assertNotIn(name, recovered.state_store.get_transactions())

    def test_fixed_tag_is_ignored_despite_latest_alias(self):
        self.images["old"].tag(self.ref)
        stable = self.ref.replace(":latest", ":fixed")
        self.image_tags.add(stable)
        self.images["old"].tag(stable)
        container = self.client.containers.create(image=stable, name=f"{self.prefix}fixed", labels={OWNER_LABEL: self.owner, "watcher.enable": "true"})
        container.start()
        self.addCleanup(self.remove_test_container, container.name)
        self.assertIsNone(self.service.docker.get_image_ref(container))
        auto, monitor = self.service.docker.get_watched_containers()
        self.assertNotIn(container.id, [item.id for item in [*auto, *monitor]])

    def test_multiple_marked_instances_are_rejected(self):
        self.create_container("watcher-one", labels={"watcher.self": "true"})
        self.create_container("watcher-two", labels={"watcher.self": "true"})
        with self.assertRaises(SystemExit) as error:
            self.new_service()
        self.assertEqual(error.exception.code, 1)

    def test_explicit_stop_timeout_is_preserved(self):
        self.images["old"].tag(self.ref)
        name = f"{self.prefix}{self._testMethodName}-app"
        response = self.raw_client.api.create_container(
            image=self.ref, name=name, stop_timeout=42,
            labels={OWNER_LABEL: self.owner, "watcher.enable": "true"},
        )
        original = self.client.containers.get(response["Id"])
        original.start()
        self.addCleanup(self.remove_test_container, name)
        result = self.update(original)
        self.assertEqual(result.status, UpdateStatus.UPDATED, result.error_message)
        self.assertEqual(self.client.containers.get(name).attrs["Config"]["StopTimeout"], 42)

    def owned_volume(self, suffix):
        name = f"{self.prefix}{self._testMethodName}-{suffix}"
        volume = self.raw_client.volumes.create(name=name, labels={OWNER_LABEL: self.owner})
        self.volumes.append(name)
        return volume

    def fixture_helper(self, suffix, **kwargs):
        container = self.client.containers.create(
            image=self.images["old"].id, name=f"{self.prefix}{self._testMethodName}-{suffix}",
            labels={OWNER_LABEL: self.owner, "watcher.enable": "false"}, **kwargs,
        )
        container.start()
        self.addCleanup(self.remove_test_container, container.name)
        return container

    def assert_bind_preserved(self, read_only):
        volume = self.owned_volume("bind-storage")
        helper = self.fixture_helper("bind-helper", mounts=[docker.types.Mount("/fixture", volume.name)])
        result = helper.exec_run(["sh", "-c", "mkdir /fixture/bind-data && printf preserved >/fixture/bind-data/probe"])
        self.assertEqual(result.exit_code, 0, result.output)
        # Bind a private Linux host path, never a user's directory or the host root.
        source = f"{volume.attrs['Mountpoint']}/bind-data"
        original = self.create_container(mounts=[docker.types.Mount("/data", source, type="bind", read_only=read_only)])
        result = self.update(original)
        self.assertEqual(result.status, UpdateStatus.UPDATED, result.error_message)
        current = self.client.containers.get(original.name)
        mount = next(item for item in current.attrs["Mounts"] if item["Destination"] == "/data")
        self.assertEqual((mount["Type"], mount["Source"], mount["RW"]), ("bind", source, not read_only))
        self.assertEqual(current.exec_run("cat /data/probe").output, b"preserved")
        write = current.exec_run(["sh", "-c", "printf after >/data/after"])
        if read_only:
            self.assertNotEqual(write.exit_code, 0)
        else:
            self.assertEqual(write.exit_code, 0, write.output)
            self.assertEqual(helper.exec_run("cat /fixture/bind-data/after").output, b"after")

    def test_bind_mount_read_write_is_preserved(self):
        self.assert_bind_preserved(False)

    def test_bind_mount_read_only_is_preserved(self):
        self.assert_bind_preserved(True)

    def test_client_stop_timeout_after_daemon_stop_recovers(self):
        original = self.create_container()
        real_stop = Container.stop

        def timed_out_stop(container, *args, **kwargs):
            real_stop(container, *args, **kwargs)
            raise requests.exceptions.ReadTimeout("Injected client timeout after a real daemon stop")

        with patch.object(Container, "stop", autospec=True, side_effect=timed_out_stop):
            result = self.update(original)
        self.assertEqual(result.status, UpdateStatus.UPDATED, result.error_message)

    def test_client_stop_timeout_while_running_refuses_replacement(self):
        original = self.create_container()
        original_id, name = original.id, original.name
        with (
            patch.object(Container, "stop", autospec=True, side_effect=requests.exceptions.ReadTimeout("Injected stop failure")),
            patch("time.sleep", return_value=None),
        ):
            result = self.update(original)
        self.assertEqual(result.status, UpdateStatus.FAILED)
        self.assertIn("failed to stop", result.error_message)
        current = self.client.containers.get(name)
        self.assertEqual((current.id, current.status), (original_id, "running"))
        with self.assertRaises(docker.errors.NotFound):
            self.client.containers.get(f"{name}_backup")

    @classmethod
    def ensure_registry(cls):
        if hasattr(cls, "registry"):
            return
        cls.raw_client.images.pull("registry:3")
        volume_name = f"{cls.prefix}registry-data"
        volume = cls.raw_client.volumes.create(name=volume_name, labels={OWNER_LABEL: cls.owner})
        cls.volumes.append(volume_name)
        port = 20000 + int(cls.owner[:8], 16) % 30000
        cls.registry = cls.client.containers.create(
            image="registry:3", name=f"{cls.prefix}registry", labels={OWNER_LABEL: cls.owner},
            network_mode="host", environment={"REGISTRY_HTTP_ADDR": f"127.0.0.1:{port}"},
            mounts=[docker.types.Mount("/var/lib/registry", volume.name)],
        )
        cls.registry.start()
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if b"listening on" in cls.registry.logs():
                break
            cls.registry.reload()
            if cls.registry.status != "running":
                raise AssertionError(f"Private registry failed to start: {cls.registry.logs()!r}")
            time.sleep(0.2)
        else:
            raise AssertionError(f"Private registry startup timed out: {cls.registry.logs()!r}")
        cls.registry_address = f"127.0.0.1:{port}"

    def publish(self, version):
        self.images[version].tag(self.ref)
        for attempt in range(10):
            try:
                events = list(self.raw_client.images.push(self.ref, stream=True, decode=True))
                errors = [event["error"] for event in events if "error" in event]
                if errors:
                    raise AssertionError("Registry push failed: " + "; ".join(errors))
                self.assertTrue(any(
                    event.get("aux", {}).get("Digest")
                    or "digest: sha256:" in event.get("status", "")
                    for event in events
                ), events)
                return
            except docker.errors.APIError:
                if attempt == 9:
                    raise
                time.sleep(1)

    def registry_target(self, version="new"):
        self.ensure_registry()
        self.ref = f"{self.registry_address}/{self.prefix}{self._testMethodName}:latest"
        self.image_tags.add(self.ref)
        self.publish("old")
        original = self.create_container()
        self.publish(version)
        # Publishing changes the local tag too. Restore it so only an actual pull
        # can make Watcher's update detection observe the new registry version.
        self.images["old"].tag(self.ref)
        self.assertEqual(self.raw_client.images.get(self.ref).id, self.images["old"].id)
        return original

    @unittest.skipUnless(os.getenv("WATCHER_RELEASE_GATES") == "1", "Set WATCHER_RELEASE_GATES=1 for registry/process checks")
    def test_live_registry_update(self):
        original = self.registry_target()
        result = self.service.process_container(original, auto_update=True)
        self.assertEqual(result.status, UpdateStatus.UPDATED, result.error_message)
        self.assertEqual(self.client.containers.get(original.name).image.id, self.images["new"].id)
        self.assertNotIn(original.name, self.service.state_store.get_transactions())
        with self.assertRaises(docker.errors.NotFound):
            self.client.containers.get(f"{original.name}_backup")

    @unittest.skipUnless(os.getenv("WATCHER_RELEASE_GATES") == "1", "Set WATCHER_RELEASE_GATES=1 for registry/process checks")
    def test_live_registry_unhealthy_image_rolls_back(self):
        original = self.registry_target("broken")
        name, original_id = original.name, original.id
        result = self.service.process_container(original, auto_update=True)
        self.assertEqual(result.status, UpdateStatus.ROLLED_BACK, result.error_message)
        self.assertTrue(result.rollback_success, result.rollback_details)
        self.assertEqual(self.client.containers.get(name).id, original_id)
        self.assertNotIn(name, self.service.state_store.get_transactions())

    @classmethod
    def ensure_worker_image(cls):
        if hasattr(cls, "worker_image"):
            return
        context = io.BytesIO()
        dockerfile = (
            "FROM python:3.12-slim\nWORKDIR /app\nENV PYTHONUNBUFFERED=1\n"
            "COPY requirements.txt .\nRUN pip install --no-cache-dir -r requirements.txt\n"
            'COPY *.py /app/\n'
            f"LABEL watcher.integration.image={cls.owner}\n"
            'ENTRYPOINT ["python", "integration_crash_worker.py"]\n'
        )
        with tarfile.open(fileobj=context, mode="w") as archive:
            files = {path.name: path.read_bytes() for path in Path(__file__).parent.glob("*.py")}
            files["requirements.txt"] = Path(__file__).with_name("requirements.txt").read_bytes()
            files["Dockerfile"] = dockerfile.encode("ascii")
            for name, contents in files.items():
                entry = tarfile.TarInfo(name)
                entry.size = len(contents)
                archive.addfile(entry, io.BytesIO(contents))
        context.seek(0)
        tag = f"{cls.prefix}worker:fixture"
        cls.image_tags.add(tag)
        cls.worker_image, _logs = cls.raw_client.images.build(fileobj=context, custom_context=True, tag=tag, rm=True, forcerm=True)

    def worker(self, suffix, mode, volume, target="", phase=""):
        self.ensure_worker_image()
        container = self.client.containers.create(
            image=self.worker_image.id, name=f"{self.prefix}{self._testMethodName}-{suffix}",
            labels={OWNER_LABEL: self.owner, "watcher.self": "true"},
            environment={
                "UNITTEST_MODE": "1", "DOCKER_HOST": "unix:///var/run/docker.sock", "TZ": "UTC",
                "WATCHER_TEST_OWNER": self.owner, "WATCHER_TEST_PREFIX": self.prefix,
                "WATCHER_TEST_MODE": mode, "WATCHER_TEST_TARGET": target, "WATCHER_TEST_PHASE": phase,
                "STATE_PATH": "/state/state.json", "JOURNAL_PATH": "/state/journal.json",
                "HEALTH_CHECK_RETRIES": "5", "HEALTH_CHECK_DELAY": "1", "CHECK_INTERVAL": "60",
                "NOTIFY_ON_STARTUP": "false", "NOTIFY_ON_UPDATE_START": "false",
                "EXCLUDE_CONTAINER_REGEX": f"^(?!{re.escape(self.prefix)}).*$",
            },
            mounts=[docker.types.Mount("/state", volume.name), docker.types.Mount("/var/run/docker.sock", "/var/run/docker.sock", type="bind")],
        )
        self.addCleanup(self.remove_test_container, container.name)
        container.start()
        return container

    def wait_worker_marker(self, container, marker):
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            logs = container.logs().decode("utf-8", errors="replace")
            if marker in logs:
                return logs
            container.reload()
            self.assertEqual(container.status, "running", logs)
            time.sleep(0.2)
        self.fail(f"Timed out waiting for {marker}: {container.logs()!r}")

    def assert_kill_and_recover(self, phase, version="new"):
        original = self.registry_target(version)
        name, original_id = original.name, original.id
        volume = self.owned_volume("state")
        worker = self.worker("worker", "update", volume, name, phase)
        self.wait_worker_marker(worker, f"WATCHER_TEST_PAUSED:{phase}")
        state = worker.exec_run("cat /state/state.json")
        self.assertEqual(state.exit_code, 0, state.output)
        self.assertEqual(json.loads(state.output)["transactions"][name]["phase"], phase)
        self.assertEqual(self.client.containers.get(f"{name}_backup").id, original_id)
        replacement_id = None
        if phase == "backup_renamed":
            with self.assertRaises(docker.errors.NotFound):
                self.client.containers.get(name)
        else:
            replacement = self.client.containers.get(name)
            replacement_id = replacement.id
            self.assertNotEqual(replacement_id, original_id)
            self.assertEqual(replacement.status, "created" if phase == "replacement_created" else "running")
        worker.kill(signal="SIGKILL")
        self.assertEqual(worker.wait(timeout=15)["StatusCode"], 137)
        worker.start()
        self.wait_worker_marker(worker, "WATCHER_TEST_RECOVERED")
        current = self.client.containers.get(name)
        self.assertEqual(current.status, "running")
        if phase != "backup_renamed" and version == "new":
            self.assertEqual(current.id, replacement_id)
            self.assertEqual(current.image.id, self.images["new"].id)
        else:
            self.assertEqual(current.id, original_id)
        state = worker.exec_run("cat /state/state.json")
        self.assertEqual(state.exit_code, 0, state.output)
        self.assertNotIn(name, json.loads(state.output)["transactions"])
        with self.assertRaises(docker.errors.NotFound):
            self.client.containers.get(f"{name}_backup")

    @unittest.skipUnless(os.getenv("WATCHER_RELEASE_GATES") == "1", "Set WATCHER_RELEASE_GATES=1 for registry/process checks")
    def test_sigkill_after_backup_rename_and_container_restart(self):
        self.assert_kill_and_recover("backup_renamed")

    @unittest.skipUnless(os.getenv("WATCHER_RELEASE_GATES") == "1", "Set WATCHER_RELEASE_GATES=1 for registry/process checks")
    def test_sigkill_after_replacement_create_and_container_restart(self):
        self.assert_kill_and_recover("replacement_created")

    @unittest.skipUnless(os.getenv("WATCHER_RELEASE_GATES") == "1", "Set WATCHER_RELEASE_GATES=1 for registry/process checks")
    def test_sigkill_after_replacement_start_and_container_restart(self):
        self.assert_kill_and_recover("replacement_started")

    @unittest.skipUnless(os.getenv("WATCHER_RELEASE_GATES") == "1", "Set WATCHER_RELEASE_GATES=1 for registry/process checks")
    def test_sigkill_with_unhealthy_replacement_rolls_back_on_restart(self):
        self.assert_kill_and_recover("replacement_created", "broken")

    @unittest.skipUnless(os.getenv("WATCHER_RELEASE_GATES") == "1", "Set WATCHER_RELEASE_GATES=1 for registry/process checks")
    def test_second_real_worker_instance_is_rejected(self):
        first = self.worker("first", "hold", self.owned_volume("first-state"))
        self.wait_worker_marker(first, "WATCHER_TEST_READY")
        second = self.worker("second", "hold", self.owned_volume("second-state"))
        self.assertEqual(second.wait(timeout=20)["StatusCode"], 1, second.logs())
        self.assertIn(b"Multiple Watcher instances detected", second.logs())
        first.reload()
        self.assertEqual(first.status, "running")

    @classmethod
    def ensure_production_endpoint(cls):
        if hasattr(cls, "production_image"):
            return
        cls.ensure_registry()
        cls.ensure_worker_image()
        name = f"{cls.prefix}entrypoint-network"
        cls.networks.append(name)
        network = cls.raw_client.networks.create(name, internal=True, labels={OWNER_LABEL: cls.owner})
        cls.endpoint_network = network
        cls.endpoint = cls.client.containers.create(
            image=cls.worker_image.id, name=f"{cls.prefix}docker-endpoint",
            labels={OWNER_LABEL: cls.owner}, network=network.name,
            entrypoint=["python", "integration_docker_endpoint.py"],
            environment={"WATCHER_TEST_OWNER": cls.owner, "WATCHER_TEST_PREFIX": cls.prefix, "WATCHER_TEST_REGISTRY": cls.registry_address},
            mounts=[docker.types.Mount("/var/run/docker.sock", "/var/run/docker.sock", type="bind")],
        )
        cls.endpoint.start()
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if b"WATCHER_TEST_ENDPOINT_READY" in cls.endpoint.logs():
                break
            cls.endpoint.reload()
            if cls.endpoint.status != "running":
                raise AssertionError(f"Test endpoint exited: {cls.endpoint.logs()!r}")
            time.sleep(0.2)
        else:
            raise AssertionError("Test endpoint did not become ready")
        tag = f"{cls.prefix}production:fixture"
        cls.image_tags.add(tag)
        with tempfile.TemporaryDirectory() as context:
            shutil.copytree(Path(__file__).parent, context, dirs_exist_ok=True, ignore=shutil.ignore_patterns(".git", ".env"))
            # A harmless sentinel proves .dockerignore excludes an actual .env
            # present in the build context, without copying the user's secrets.
            Path(context, ".env").write_text("WATCHER_TEST_SENTINEL=not-a-secret\n", encoding="ascii")
            cls.production_image, _logs = cls.raw_client.images.build(
                path=context, tag=tag, rm=True, forcerm=True,
                labels={"watcher.integration.image": cls.owner},
            )

    def production_watcher(self, suffix="watcher", **environment):
        self.ensure_production_endpoint()
        volume = self.owned_volume(f"{suffix}-state")
        env = {
            "DOCKER_HOST": f"tcp://{self.endpoint.name}:2375", "TZ": "UTC", "CHECK_INTERVAL": "10",
            "STATE_PATH": "/app/data/state.json", "JOURNAL_PATH": "/app/data/journal.json",
            "HEALTH_CHECK_RETRIES": "5", "HEALTH_CHECK_DELAY": "1",
            "NOTIFY_ON_STARTUP": "false", "NOTIFY_ON_UPDATE_START": "false", "WATCH_BY_LABEL": "true",
            "EXCLUDE_CONTAINER_REGEX": f"^(?!{re.escape(self.prefix)}).*$", **environment,
        }
        watcher = self.client.containers.create(
            image=self.production_image.id, name=f"{self.prefix}{self._testMethodName}-{suffix}",
            labels={OWNER_LABEL: self.owner, "watcher.self": "true"},
            environment=env, network=self.endpoint_network.name,
            mounts=[docker.types.Mount("/app/data", volume.name)],
        )
        self.addCleanup(self.remove_test_container, watcher.name)
        watcher.start()
        self.assertEqual(watcher.attrs["Config"]["Cmd"], ["python", "main.py"])
        self.assertFalse(watcher.attrs["Config"]["Entrypoint"])
        return watcher

    def read_watcher_json(self, watcher, filename):
        result = watcher.exec_run(["cat", f"/app/data/{filename}"])
        if result.exit_code:
            return None
        return json.loads(result.output)

    def read_stopped_watcher_json(self, watcher, filename):
        chunks, _metadata = watcher.get_archive(f"/app/data/{filename}")
        with (
            tarfile.open(fileobj=io.BytesIO(b"".join(chunks))) as archive,
            archive.extractfile(archive.getmembers()[0]) as contents,
        ):
            return json.load(contents)

    def wait_watcher_journal(self, watcher, predicate, timeout=60):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            watcher.reload()
            self.assertEqual(watcher.status, "running", watcher.logs())
            journal = self.read_watcher_json(watcher, "journal.json") or []
            if predicate(journal):
                return journal
            time.sleep(0.2)
        self.fail(f"Journal condition timed out: {watcher.logs()!r}")

    def stop_watcher(self, watcher):
        started = time.monotonic()
        watcher.stop(timeout=10)
        self.assertEqual(watcher.wait(timeout=15)["StatusCode"], 0, watcher.logs())
        self.assertLess(time.monotonic() - started, 8, watcher.logs())
        self.assertIn(b"Received SIGTERM", watcher.logs())

    @unittest.skipUnless(os.getenv("WATCHER_ENTRYPOINT_GATES") == "1", "Set WATCHER_ENTRYPOINT_GATES=1 for unmodified process checks")
    def test_production_image_runs_repeated_interval_cycles(self):
        watcher = self.production_watcher()
        journal = self.wait_watcher_journal(watcher, lambda entries: len(entries) >= 2)
        self.assertTrue(all(entry["run_mode"] == "LIVE" for entry in journal))
        # Dockerfile/.dockerignore must not ship the test workers or local secrets.
        check = watcher.exec_run(["python", "-c", "from pathlib import Path; assert not Path('.env').exists(); assert not list(Path('.').glob('integration_*.py')); assert not Path('tests').exists()"])
        self.assertEqual(check.exit_code, 0, check.output)
        self.stop_watcher(watcher)

    @unittest.skipUnless(os.getenv("WATCHER_ENTRYPOINT_GATES") == "1", "Set WATCHER_ENTRYPOINT_GATES=1 for unmodified process checks")
    def test_production_process_applies_registry_update(self):
        original = self.registry_target()
        watcher = self.production_watcher()
        journal = self.wait_watcher_journal(watcher, lambda entries: any(original.name in entry["summary"]["updated"] for entry in entries))
        self.assertEqual(self.client.containers.get(original.name).image.id, self.images["new"].id)
        self.assertEqual(journal[-1]["checked_containers"], 1)
        self.stop_watcher(watcher)

    @unittest.skipUnless(os.getenv("WATCHER_ENTRYPOINT_GATES") == "1", "Set WATCHER_ENTRYPOINT_GATES=1 for unmodified process checks")
    def test_production_process_rolls_back_unhealthy_registry_image(self):
        original = self.registry_target("broken")
        name, original_id = original.name, original.id
        watcher = self.production_watcher()
        self.wait_watcher_journal(watcher, lambda entries: any(name in entry["summary"]["rolled_back"] for entry in entries))
        self.assertEqual(self.client.containers.get(name).id, original_id)
        self.stop_watcher(watcher)

    @unittest.skipUnless(os.getenv("WATCHER_ENTRYPOINT_GATES") == "1", "Set WATCHER_ENTRYPOINT_GATES=1 for unmodified process checks")
    def test_production_dry_run_pulls_but_never_replaces(self):
        original = self.registry_target()
        name, original_id = original.name, original.id
        watcher = self.production_watcher(DRY_RUN="true")
        journal = self.wait_watcher_journal(watcher, lambda entries: bool(entries))
        self.assertEqual(journal[-1]["run_mode"], "DRY_RUN")
        self.assertEqual(journal[-1]["events"][0]["status"], "UPDATE_AVAILABLE")
        self.assertEqual(self.raw_client.images.get(self.ref).id, self.images["new"].id)
        self.assertEqual(self.client.containers.get(name).id, original_id)
        self.stop_watcher(watcher)

    @unittest.skipUnless(os.getenv("WATCHER_ENTRYPOINT_GATES") == "1", "Set WATCHER_ENTRYPOINT_GATES=1 for unmodified process checks")
    def test_production_sigterm_defers_update_and_recovery_verification(self):
        original = self.registry_target()
        name, original_id = original.name, original.id
        # This label is a real user override, so it survives recreation.
        original.remove(force=True)
        original = self.create_container(labels={"watcher.health.start_period": "10"})
        original_id = original.id
        watcher = self.production_watcher()
        self.wait_worker_marker(watcher, "start_period=10s label. Waiting")
        replacement_id = self.client.containers.get(name).id
        self.assertNotEqual(replacement_id, original_id)
        self.stop_watcher(watcher)
        state = self.read_stopped_watcher_json(watcher, "state.json")
        self.assertEqual(state["transactions"][name]["phase"], "replacement_started")
        self.assertNotIn(name, state["cooldowns"])
        watcher.start()
        # On the second process, interrupt startup_recovery too. Count markers
        # because Docker keeps the first process's logs across container starts.
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if watcher.logs().count(b"start_period=10s label. Waiting") >= 2:
                break
            time.sleep(0.2)
        else:
            self.fail(f"Recovery did not reach health verification: {watcher.logs()!r}")
        self.stop_watcher(watcher)
        state = self.read_stopped_watcher_json(watcher, "state.json")
        self.assertEqual(state["transactions"][name]["phase"], "replacement_started")
        self.assertEqual(self.client.containers.get(name).id, replacement_id)
        self.assertEqual(self.client.containers.get(f"{name}_backup").id, original_id)
        watcher.start()
        self.wait_watcher_journal(watcher, lambda entries: len(entries) >= 2)
        state = self.read_watcher_json(watcher, "state.json")
        self.assertNotIn(name, state["transactions"])
        self.assertNotIn(name, state["cooldowns"])
        journal = self.read_watcher_json(watcher, "journal.json")
        self.assertEqual(journal[0]["events"][0]["status"], "INTERRUPTED")
        self.assertEqual(self.client.containers.get(name).id, replacement_id)
        with self.assertRaises(docker.errors.NotFound):
            self.client.containers.get(f"{name}_backup")
        self.stop_watcher(watcher)

    @unittest.skipUnless(os.getenv("WATCHER_ENTRYPOINT_GATES") == "1", "Set WATCHER_ENTRYPOINT_GATES=1 for unmodified process checks")
    def test_second_production_process_is_rejected(self):
        first = self.production_watcher("first")
        self.wait_worker_marker(first, "Watcher started.")
        second = self.production_watcher("second")
        self.assertEqual(second.wait(timeout=20)["StatusCode"], 1, second.logs())
        self.assertIn(b"Multiple Watcher instances detected", second.logs())
        first.reload()
        self.assertEqual(first.status, "running")
        self.stop_watcher(first)

    @unittest.skipUnless(os.getenv("WATCHER_ENTRYPOINT_GATES") == "1", "Set WATCHER_ENTRYPOINT_GATES=1 for unmodified process checks")
    def test_production_scheduled_wait_is_interruptible(self):
        scheduled = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime("%H:%M")
        watcher = self.production_watcher(SCHEDULE_TIME=scheduled)
        self.wait_worker_marker(watcher, "Scheduled mode: Sleeping until first run")
        self.assertIsNone(self.read_watcher_json(watcher, "journal.json"))
        self.assertNotIn(b"--- Cycle Start ---", watcher.logs())
        self.stop_watcher(watcher)

    @unittest.skipUnless(os.getenv("WATCHER_ENTRYPOINT_GATES") == "1", "Set WATCHER_ENTRYPOINT_GATES=1 for unmodified process checks")
    def test_production_schedule_executes_at_next_utc_minute(self):
        self.ensure_production_endpoint()
        scheduled = (datetime.now(timezone.utc) + timedelta(minutes=1)).replace(second=0, microsecond=0)
        if (scheduled - datetime.now(timezone.utc)).total_seconds() < 5:
            scheduled += timedelta(minutes=1)
        watcher = self.production_watcher(SCHEDULE_TIME=scheduled.strftime("%H:%M"))
        self.wait_worker_marker(watcher, "Scheduled mode: Sleeping until first run")
        journal = self.wait_watcher_journal(watcher, lambda entries: bool(entries), timeout=75)
        executed = datetime.fromisoformat(journal[0]["timestamp"])
        self.assertGreaterEqual(executed, scheduled)
        self.assertLess((executed - scheduled).total_seconds(), 10)
        self.stop_watcher(watcher)


if __name__ == "__main__":
    unittest.main()
