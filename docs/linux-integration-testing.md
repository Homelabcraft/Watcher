# Linux Integration Testing Guide

**Status: NOT YET EXECUTED**

This document provides a safe procedure for executing Docker integration tests for Watcher v1.7.0 on a Linux Docker host. Because Watcher directly manipulates Docker containers, testing must be isolated from production workloads.

## Safety Guidelines

We highly recommend running these tests on a **separate temporary Linux VM or a completely isolated Docker daemon**. 

If testing must happen on your existing homelab Linux server, you must adhere strictly to these rules to avoid modifying production containers:

1. **Use Only Disposable Test Containers:** Never test with production images or mounts.
2. **Use a Unique Prefix:** All test containers must begin with `watcher-v17-test-`.
3. **Dedicated Compose Project:** Use a unique project name (e.g., `-p watcher-integration`).
4. **Dedicated Networks & Volumes:** Create and use isolated user-defined test networks and test volumes.
5. **Opt-in Labeling:** Always run Watcher with `WATCH_BY_LABEL=true`.
6. **Strict Exclusion Regex:** Use `EXCLUDE_CONTAINER_REGEX="^(?!watcher-v17-test-).*$"` to instruct Watcher to completely ignore and exclude any container whose name does not begin with the test prefix.

## Test Environment Setup

1. Create an isolated environment for testing:
```bash
mkdir watcher-test
cd watcher-test
```

2. Configure `.env`:
```env
WATCH_BY_LABEL=true
EXCLUDE_CONTAINER_REGEX="^(?!watcher-v17-test-).*$"
CHECK_INTERVAL=60
DISCORD_WEBHOOK_URL=...
```

3. Configure a test `docker-compose.yml`:
```yaml
version: '3.8'
services:
  watcher-v17-test-watcher:
    build: /path/to/watcher/source
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - watcher-v17-test-data:/app/data
    env_file: .env
    labels:
      - "watcher.self=true"

  watcher-v17-test-app:
    image: nginx:latest
    networks:
      - watcher-v17-test-net
    labels:
      - "watcher.enable=true"

volumes:
  watcher-v17-test-data:

networks:
  watcher-v17-test-net:
```

## Test Scenarios (NOT YET EXECUTED)

The following scenarios must be manually executed and verified on the Linux Docker host:

* [ ] **Successful update:** Verify an update is detected, applied, and the backup is removed.
* [ ] **Native Docker healthcheck:** Verify Watcher respects native `HEALTHCHECK` instructions.
* [ ] **Forced unhealthy replacement and rollback:** Push a broken image, verify Watcher detects the healthcheck failure and performs a container rollback to the backup.
* [ ] **Named volume preservation:** Verify data in a named volume is preserved across updates.
* [ ] **Bind mount preservation:** Verify host bind mounts remain attached.
* [ ] **Multiple dynamic networks:** Verify a container attached to multiple user-defined networks is recreated with all networks intact.
* [ ] **Fixed tag with local latest alias remains ignored:** Verify a container running `nginx:1.24` is ignored even if `nginx:latest` exists locally.
* [ ] **Explicit static IP is blocked before stop:** Configure a container with `ipv4_address` and verify Watcher raises a `RecreationError` before stopping it.
* [ ] **Container network dependent is blocked before stop:** Configure a container with `network_mode: "container:watcher-v17-test-target"` and verify Watcher refuses to update the target.
* [ ] **Stop timeout handling:** Verify Watcher respects the container's `StopTimeout` and handles `ReadTimeout` correctly if it refuses to stop.
* [ ] **Process termination after backup rename:** Kill the Watcher process via `docker kill` immediately after the original container is renamed to `_backup`. Restart Watcher and verify `startup_recovery()` handles it.
* [ ] **Process termination after replacement creation:** Kill the Watcher process immediately after the new container is created but before health verification finishes. Restart Watcher and verify `startup_recovery()`.
* [ ] **Watcher restart and startup recovery:** Verify Watcher reconciles state on boot.
* [ ] **Second Watcher instance is rejected:** Attempt to start a second container with `watcher.self=true` and verify it aborts safely.

## Cleanup and Verification

After executing the tests, completely clean up the test environment:

```bash
docker compose -p watcher-integration down -v
docker network rm watcher-v17-test-net
docker volume rm watcher-v17-test-data
docker ps -a | grep watcher-v17-test- | awk '{print $1}' | xargs -r docker rm -f
```

**To confirm no production container was modified:**
Check the Watcher test logs and the `journal.json` in the test data volume. Verify that no container names outside of the `watcher-v17-test-` prefix appear in the update history.
