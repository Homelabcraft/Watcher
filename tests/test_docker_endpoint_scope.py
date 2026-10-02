import json
import unittest
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

from integration_docker_endpoint import (
    OWNER_LABEL,
    ScopedDockerEndpoint,
    ScopeViolation,
)


class TestDockerEndpointScope(unittest.TestCase):
    def setUp(self):
        self.client = Mock()
        self.owner = "a" * 32
        self.prefix = f"watcher-v17-test-{self.owner[:12]}-"
        self.registry = "127.0.0.1:23456"
        self.endpoint = ScopedDockerEndpoint(self.client, self.owner, self.prefix, self.registry)
        self.client.api.inspect_container.return_value = {
            "Id": "b" * 64, "Name": f"/{self.prefix}app", "Config": {"Labels": {OWNER_LABEL: self.owner}},
        }

    def create(self, **host):
        body = json.dumps({
            "Image": f"{self.registry}/{self.prefix}app:latest", "Labels": {OWNER_LABEL: self.owner},
            "HostConfig": host,
        }).encode()
        return self.endpoint.authorize("POST", f"/v1.45/containers/create?name={self.prefix}app", body)

    def test_invalid_owner_is_rejected(self):
        with self.assertRaises(ScopeViolation):
            ScopedDockerEndpoint(self.client, "bad", self.prefix, self.registry)

    def test_prefix_must_match_exact_owner(self):
        with self.assertRaises(ScopeViolation):
            ScopedDockerEndpoint(self.client, self.owner, "wrong-prefix", self.registry)

    def test_non_loopback_registry_is_rejected(self):
        with self.assertRaises(ScopeViolation):
            ScopedDockerEndpoint(self.client, self.owner, self.prefix, "external.example:443")

    def test_list_injects_owner_even_without_requested_filter(self):
        result = self.endpoint.authorize("GET", "/v1.45/containers/json?all=1")
        filters = json.loads(parse_qs(urlsplit(result).query)["filters"][0])
        self.assertEqual(filters["label"], [f"{OWNER_LABEL}={self.owner}"])

    def test_metadata_and_mutations_use_verified_immutable_id(self):
        for method, suffix in (("GET", "/json"), ("POST", "/stop?t=15"), ("DELETE", "?force=True")):
            with self.subTest(method=method):
                result = self.endpoint.authorize(method, f"/v1.45/containers/{self.prefix}app{suffix}")
                self.assertIn("b" * 64, result)
                self.assertNotIn(self.prefix, result)

    def test_wrong_owner_cannot_be_stopped(self):
        self.client.api.inspect_container.return_value["Config"]["Labels"][OWNER_LABEL] = "someone-else"
        with self.assertRaises(ScopeViolation):
            self.endpoint.authorize("POST", "/containers/target/stop")

    def test_wrong_prefix_cannot_be_inspected(self):
        self.client.api.inspect_container.return_value["Name"] = "/production-app"
        with self.assertRaises(ScopeViolation):
            self.endpoint.authorize("GET", "/containers/target/json")

    def test_rename_cannot_escape_scope(self):
        with self.assertRaises(ScopeViolation):
            self.endpoint.authorize("POST", "/containers/target/rename?name=production-app")

    def test_create_rejects_missing_owner(self):
        body = json.dumps({"Image": f"{self.registry}/{self.prefix}app:latest"}).encode()
        with self.assertRaises(ScopeViolation):
            self.endpoint.authorize("POST", f"/containers/create?name={self.prefix}app", body)

    def test_create_allows_basic_fixture_config(self):
        self.assertIn("/containers/create", self.create(NetworkMode="bridge", IpcMode="private"))

    def test_create_rejects_host_mounts_privileges_and_namespace_sharing(self):
        for host in (
            {"Privileged": True}, {"Binds": ["/:/host"]}, {"Mounts": [{"Source": "/"}]},
            {"VolumesFrom": ["production"]}, {"NetworkMode": "host"}, {"PidMode": "host"},
            {"IpcMode": "host"}, {"CapAdd": ["SYS_ADMIN"]}, {"DeviceRequests": [{"Count": -1}]},
        ):
            with self.subTest(host=host), self.assertRaises(ScopeViolation):
                self.create(**host)

    def test_pull_cannot_reach_other_registries_or_repositories(self):
        for reference in ("docker.io/alpine", f"{self.registry}/production", f"{self.registry}/{self.prefix}app:fixed"):
            with self.subTest(reference=reference), self.assertRaises(ScopeViolation):
                self.endpoint.authorize("POST", f"/images/create?fromImage={reference}&tag=latest")

    def test_pull_allows_private_fixture_reference(self):
        target = f"/images/create?fromImage={self.registry}/{self.prefix}app&tag=latest"
        self.assertEqual(self.endpoint.authorize("POST", target), target)

    def test_global_destructive_and_exec_operations_are_denied(self):
        for method, target in (("POST", "/containers/prune"), ("POST", "/images/prune"), ("POST", "/containers/target/exec"), ("DELETE", "/images/target")):
            with self.subTest(target=target), self.assertRaises(ScopeViolation):
                self.endpoint.authorize(method, target)

    def test_duplicate_queries_and_absolute_urls_are_denied(self):
        for target in ("/containers/json?all=1&all=0", "http://external.example/version"):
            with self.subTest(target=target), self.assertRaises(ScopeViolation):
                self.endpoint.authorize("GET", target)
