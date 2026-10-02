"""Test-only Docker endpoint for the unmodified Watcher process.

Not a production security proxy. No published ports; run only on a private test
network. It deliberately supports just the API subset used by these fixtures.
"""

import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlencode, urlsplit

import docker
import requests

OWNER_LABEL = "watcher.integration.owner"
IMAGE_LABEL = "watcher.integration.image"


class ScopeViolation(ValueError):
    pass


class ScopedDockerEndpoint:
    def __init__(self, client, owner, prefix, registry):
        if not re.fullmatch(r"[0-9a-f]{32}", owner) or prefix != f"watcher-v17-test-{owner[:12]}-":
            raise ScopeViolation("Invalid test ownership scope")
        if not re.fullmatch(r"127\.0\.0\.1:[0-9]{1,5}", registry):
            raise ScopeViolation("Only a daemon-loopback test registry is allowed")
        self.client, self.owner, self.prefix, self.registry = client, owner, prefix, registry

    def container(self, identifier):
        container = self.client.api.inspect_container(identifier)
        if (
            not container.get("Name", "").lstrip("/").startswith(self.prefix)
            or container.get("Config", {}).get("Labels", {}).get(OWNER_LABEL) != self.owner
        ):
            raise ScopeViolation("Container is outside this integration run")
        return container

    def image(self, identifier):
        image = self.client.api.inspect_image(identifier)
        if image.get("Config", {}).get("Labels", {}).get(IMAGE_LABEL) != self.owner:
            raise ScopeViolation("Image is outside this integration run")
        return image

    def registry_ref(self, reference):
        if not re.fullmatch(re.escape(f"{self.registry}/{self.prefix}") + r"[a-z0-9_.-]+(?::latest)?", reference):
            raise ScopeViolation("Image reference is outside the private test registry")

    def authorize(self, method, target, body=b""):
        parsed = urlsplit(target)
        if parsed.scheme or parsed.netloc or parsed.fragment:
            raise ScopeViolation("Only origin-form Docker requests are supported")
        path = re.sub(r"^/v[0-9]+\.[0-9]+(?=/)", "", parsed.path)
        query = parse_qs(parsed.query, keep_blank_values=True)
        if any(len(values) != 1 for values in query.values()):
            raise ScopeViolation("Duplicate query parameters are not supported")
        if method == "GET" and path in {"/_ping", "/version"} and not query:
            return target
        if method == "GET" and path == "/containers/json":
            if set(query) - {"all", "limit", "size", "trunc_cmd", "filters"}:
                raise ScopeViolation("Unsupported container list query")
            filters = json.loads(query.get("filters", ["{}"])[0])
            if not isinstance(filters, dict):
                raise ScopeViolation("Invalid container filters")
            labels = filters.get("label", [])
            if not isinstance(labels, list) or not all(isinstance(label, str) for label in labels):
                raise ScopeViolation("Invalid label filters")
            filters["label"] = [*labels, f"{OWNER_LABEL}={self.owner}"]
            query["filters"] = [json.dumps(filters)]
            return parsed.path + "?" + urlencode({key: values[0] for key, values in query.items()})
        match = re.fullmatch(r"/containers/([^/]+)/json", path)
        if method == "GET" and match and not query:
            identifier = self.container(unquote(match[1]))["Id"]
            return parsed.path.replace(match[1], identifier)
        match = re.fullmatch(r"/images/(.+)/json", path)
        if method == "GET" and match and not query:
            self.image(unquote(match[1]))
            return target
        if method == "POST" and path == "/images/create":
            if set(query) - {"fromImage", "tag"} or query.get("tag", ["latest"])[0] != "latest":
                raise ScopeViolation("Only test latest-tag pulls are supported")
            self.registry_ref(query.get("fromImage", [""])[0])
            return target
        if method == "POST" and path == "/containers/create":
            if set(query) != {"name"} or not query["name"][0].startswith(self.prefix):
                raise ScopeViolation("Invalid replacement name")
            config = json.loads(body)
            if config.get("Labels", {}).get(OWNER_LABEL) != self.owner:
                raise ScopeViolation("Replacement lacks the test ownership label")
            self.registry_ref(config.get("Image", ""))
            host = config.get("HostConfig") or {}
            if any(host.get(key) for key in (
                "Privileged", "Binds", "Mounts", "VolumesFrom", "Devices", "DeviceRequests",
                "CapAdd", "SecurityOpt", "PidMode", "UsernsMode",
            )):
                # The application fixtures are intentionally mount/capability-free.
                # State/socket mounts are created only by the trusted parent harness.
                raise ScopeViolation("Unsupported replacement host privileges or mounts")
            if host.get("IpcMode") not in {None, "", "private"}:
                raise ScopeViolation("Unsupported replacement IPC namespace")
            if host.get("NetworkMode", "default") not in {"default", "bridge", "none"}:
                raise ScopeViolation("Only fixture network modes are allowed")
            endpoints = (config.get("NetworkingConfig") or {}).get("EndpointsConfig", {})
            if set(endpoints) - {"default", "bridge", "none"}:
                raise ScopeViolation("Unsupported replacement network")
            return target
        match = re.fullmatch(r"/containers/([^/]+)(?:/(start|stop|restart|rename))?", path)
        if match and (
            (method == "DELETE" and match[2] is None and not (set(query) - {"v", "force", "link"}))
            or (method == "POST" and match[2] in {"start", "stop", "restart", "rename"})
        ):
            action = match[2]
            allowed = {"name"} if action == "rename" else {"t", "signal"} if action in {"stop", "restart"} else set()
            if method == "POST" and set(query) - allowed:
                raise ScopeViolation("Unsupported container action query")
            if action == "rename" and not query.get("name", [""])[0].startswith(self.prefix):
                raise ScopeViolation("Rename escapes this test run")
            identifier = self.container(unquote(match[1]))["Id"]
            # Forward mutations by verified immutable ID, not a reusable name.
            return parsed.path.replace(match[1], identifier) + ("?" + parsed.query if parsed.query else "")
        raise ScopeViolation("Docker operation is not allowed by the test endpoint")


class EndpointHandler(BaseHTTPRequestHandler):
    def handle_request(self):
        try:
            if self.headers.get("Transfer-Encoding"):
                raise ScopeViolation("Chunked request bodies are not supported")
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 <= length <= 1024 * 1024:
                raise ScopeViolation("Request body is too large")
            body = self.rfile.read(length)
            guard = self.server.guard
            target = guard.authorize(self.command, self.path, body)
            headers = {"Content-Type": "application/json"} if body else {}
            with guard.client.api.request(
                self.command, guard.client.api.base_url + target, data=body or None,
                headers=headers, timeout=120,
            ) as response:
                content = response.content
                if self.command == "GET" and re.search(r"/containers/json(?:\?|$)", self.path) and response.ok:
                    containers = [container for container in json.loads(content) if (
                        container.get("Labels", {}).get(OWNER_LABEL) == guard.owner
                        and any(name.lstrip("/").startswith(guard.prefix) for name in container.get("Names", []))
                    )]
                    content = json.dumps(containers).encode("utf-8")
                self.send_response(response.status_code)
                self.send_header("Content-Type", response.headers.get("Content-Type", "application/json"))
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
        except (ScopeViolation, ValueError, TypeError, KeyError) as error:
            self.reject(403, str(error))
        except docker.errors.NotFound:
            self.reject(404, "Docker resource not found")
        except (docker.errors.DockerException, requests.exceptions.RequestException) as error:
            self.reject(502, str(error))

    def reject(self, status, message):
        content = json.dumps({"message": message}).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    do_GET = handle_request
    do_POST = handle_request
    do_DELETE = handle_request
    do_PUT = handle_request
    do_HEAD = handle_request


def main():
    client = docker.DockerClient(base_url="unix:///var/run/docker.sock", timeout=120)
    try:
        guard = ScopedDockerEndpoint(client, os.environ["WATCHER_TEST_OWNER"], os.environ["WATCHER_TEST_PREFIX"], os.environ["WATCHER_TEST_REGISTRY"])
        with ThreadingHTTPServer(("0.0.0.0", 2375), EndpointHandler) as server:
            server.guard = guard
            print("WATCHER_TEST_ENDPOINT_READY", flush=True)
            server.serve_forever()
    finally:
        client.close()


if __name__ == "__main__":
    main()
