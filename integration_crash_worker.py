"""Instrumented Linux test worker; not the production Watcher entry point.

Uses the real update/recovery code and daemon, but constrains discovery to resources
owned by one integration run. The parent kills/restarts this container via Docker.
"""

import os
import re
import threading
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import docker

os.environ["UNITTEST_MODE"] = "1"

from config import Config
from integration_test_docker import OwnedClient
from main import WatcherService


def main():
    owner = os.environ["WATCHER_TEST_OWNER"]
    prefix = os.environ["WATCHER_TEST_PREFIX"]
    if not re.fullmatch(r"[0-9a-f]{32}", owner) or prefix != f"watcher-v17-test-{owner[:12]}-":
        raise ValueError("Invalid integration ownership scope")
    mode = os.environ["WATCHER_TEST_MODE"]
    target = os.environ.get("WATCHER_TEST_TARGET", "")
    phase = os.environ.get("WATCHER_TEST_PHASE", "")
    if mode not in {"update", "hold"} or (mode == "update" and not target.startswith(prefix)):
        raise ValueError("Invalid integration worker request")
    if mode == "update" and phase not in {"backup_renamed", "replacement_created", "replacement_started"}:
        raise ValueError("Invalid crash checkpoint")
    with closing(docker.from_env(timeout=120)) as raw_client:
        client = OwnedClient(raw_client, prefix, owner)
        with patch("main.docker.from_env", return_value=client):
            service = WatcherService(Config())
        if mode == "hold":
            print("WATCHER_TEST_READY", flush=True)
            threading.Event().wait()
        armed = Path("/state/crash-armed")
        if armed.exists():
            service.startup_recovery()
            if target in service.state_store.get_transactions():
                raise AssertionError("Recovery left the transaction unresolved")
            print("WATCHER_TEST_RECOVERED", flush=True)
            threading.Event().wait()
        persist = service.state_store.update_transaction

        def checkpoint(name, saved_phase, **kwargs):
            persist(name, saved_phase, **kwargs)
            if name == target and saved_phase == phase:
                with armed.open("w", encoding="ascii") as handle:
                    handle.write(phase)
                    handle.flush()
                    os.fsync(handle.fileno())
                print(f"WATCHER_TEST_PAUSED:{phase}", flush=True)
                threading.Event().wait()

        with patch.object(service.state_store, "update_transaction", side_effect=checkpoint):
            result = service.process_container(client.containers.get(target), auto_update=True)
        raise AssertionError(f"Update exited before crash checkpoint: {result}")


if __name__ == "__main__":
    main()
