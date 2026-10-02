# Linux Integration Testing Guide

**Status: PARTIALLY VERIFIED (2026-10-02)**

This document provides a safe procedure for executing Docker integration tests for Watcher v1.7.0 on a Linux Docker host. Because Watcher directly manipulates Docker containers, testing must be isolated from production workloads.

## Automated Lifecycle Verification

On 2026-10-02, **34 automated Docker integration tests passed** against Docker Desktop's Linux engine 29.6.2 (API 1.55) from a Windows/Python 3.12 client, with process checks running inside Linux/Python 3.12 containers. Separately, **176 mocked unit tests passed**, Ruff reported no findings, and `pip check` reported no broken requirements.

Run the opt-in harness on a Linux daemon:
```bash
WATCHER_DOCKER_INTEGRATION=1 python integration_test_docker.py -v
```

The default suite runs 19 lifecycle/mount/timeout checks and skips 15 optional checks. `WATCHER_RELEASE_GATES=1` adds 7 registry/instrumented-process cases (26 total); `WATCHER_ENTRYPOINT_GATES=1` adds 8 normal-entry-point cases. To run all 34:
```bash
WATCHER_DOCKER_INTEGRATION=1 WATCHER_RELEASE_GATES=1 WATCHER_ENTRYPOINT_GATES=1 python integration_test_docker.py -v
```

With Docker Desktop and PowerShell, explicitly select the active CLI endpoint for SDK 7.1:
```powershell
$env:WATCHER_DOCKER_INTEGRATION = '1'
$env:WATCHER_RELEASE_GATES = '1' # Omit for the default lifecycle suite.
$env:WATCHER_ENTRYPOINT_GATES = '1' # Normal Dockerfile/main.py through the scoped endpoint.
$env:DOCKER_HOST = docker context inspect (docker context show) --format '{{.Endpoints.docker.Host}}'
python integration_test_docker.py -v
```

The harness uses a unique name prefix and ownership labels. Its scoped client only discovers or creates owned test containers, and cleanup removes only its own containers (including attached anonymous volumes), networks, volumes and image tags. Only the separately enabled entry-point suite runs Watcher's background loop, through a private test-scoped Docker endpoint rather than directly against the shared daemon. Existing workloads are outside the test scope. Base-image/build caches may remain.

Verified cases include new image defaults, preserved command/environment overrides, unhealthy-image rollback, named-volume contents, primary/secondary network aliases, default bridge plus a secondary network, static-IP refusal, dependency exclusions, shared-network refusal, backup conflicts, fixed-tag filtering and explicit StopTimeout preservation. Recovery checks recreate the service against deliberately persisted transaction phases and real containers; the singleton guard is checked inside the ownership scope.

Extended cases include real push/pull through a disposable `registry:3`, healthy replacement and unhealthy-image rollback, read-write/read-only Linux bind mounts, two injected `requests.ReadTimeout` scenarios, four Linux worker SIGKILL/restart checks, and rejection of a second actual worker process. Before update detection, the local tag is explicitly restored to the old image: only a real registry pull can expose the new version.

The registry binds only to a unique `127.0.0.1` port in the Linux host network namespace; no ports are exposed on all interfaces and daemon settings are unchanged. This requires the daemon's existing loopback-registry support ([Docker daemon documentation](https://docs.docker.com/reference/cli/dockerd/#insecure-registries)). The harness does not enable insecure registries itself. Bind sources are private subdirectories inside an owned volume's Linux host storage; the target containers use genuine `Type=bind` mounts, never user directories.

`integration_crash_worker.py` is a test-only, ownership-scoped entry point with the Docker socket and a private persistent state volume. It runs real `WatcherService` update/recovery code and the real signal setup. A checkpoint pauses immediately after a transaction phase is durably saved, then the parent sends SIGKILL and verifies exit 137. Restarting the same worker container reloads that state. After `backup_renamed`, recovery restores the original. After `replacement_created` or `replacement_started`, recovery starts/verifies the healthy replacement and finishes the update; an unhealthy replacement restores the original. All cases verify a running result, transaction removal and backup cleanup. This remains instrumented fault injection, distinct from the normal process checks below.

### Normal Dockerfile/main.py Process Verification

The entry-point suite builds the repository Dockerfile unchanged and verifies its normal `CMD ["python", "main.py"]`, with no entrypoint override, `UNITTEST_MODE`, application monkey-patching or injected crash worker. The only transport adjustment is `DOCKER_HOST`: it points to `integration_docker_endpoint.py` on a private internal Docker network. No endpoint ports are published. The application has only its own state volume, not the daemon socket.

The endpoint uses the real Docker daemon with a deliberately narrow fixture API subset. It adds the exact owner filter to container lists, verifies owner labels/name prefixes, and forwards mutations using verified immutable IDs. Only the run's loopback-registry repository prefix can be pulled. Global prune, image deletion, exec/attach, unsupported operations, replacement bind mounts, privileges and shared host namespaces are rejected. The endpoint itself has the powerful daemon socket: this is test isolation for controlled fixtures, **not a production security proxy, adversarial security audit, or separate daemon**.

Verified cases: at least two actual interval cycles, real registry update and unhealthy-image rollback through the complete loop/journal, real-pull dry-run without replacement, actual scheduling at the next UTC minute, interruptible scheduled sleep, singleton refusal using two normal processes, and SIGTERM during update health verification followed by SIGTERM during startup-recovery verification. The same application container restarts with its state volume; the final restart verifies the healthy replacement, removes its backup and ends the transaction without a failure cooldown. SIGTERM stops use a 10-second Docker timeout, assert exit 0 and complete in under 8 seconds. State is read even while stopped to verify the pending phase survives both interruptions.

The build context includes a harmless `.env` sentinel (never the user's file); image checks verify that `.env`, tests and all `integration_*.py` helpers are absent. Development caches are excluded as well. Unexpected-loop exception propagation, client closure and shutdown-notification failure handling are covered by mocked unit regressions, not a synthetic crash in the normal-entry-point suite.

**Scope limits:** The normal `main.py` process and full scheduled/interval loop passed through the scoped fixture transport, not direct access to a completely disposable daemon or an actual homelab deployment. SIGKILL checkpoints still use the instrumented worker. VM/host power loss, real external messenger delivery, real registry authentication/TLS/outages and Windows user-directory bind paths were not tested. Client timeouts are deliberately injected; the polling delay in the still-running refusal test is shortened. Live scheduling is UTC; timezone/DST arithmetic is covered separately by unit tests. The remaining release gates require an isolated environment with deployment-specific configuration.

## Safety Guidelines

We highly recommend running these tests on a **separate temporary Linux VM or a completely isolated Docker daemon**. 

On an existing homelab daemon, use only the automated ownership-scoped harness. The manual setup below starts the unmodified application and must run on a disposable daemon. Regex/label filtering is not equivalent to the harness's ownership boundary, particularly during recovery.

For disposable manual environments, adhere to these rules:

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
    container_name: watcher-v17-test-watcher
    build: /path/to/watcher/source
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - watcher-v17-test-data:/app/data
    env_file: .env
    labels:
      - "watcher.self=true"

  watcher-v17-test-app:
    container_name: watcher-v17-test-app
    image: nginx:latest
    networks:
      - watcher-v17-test-net
    labels:
      - "watcher.enable=true"

volumes:
  watcher-v17-test-data:
    name: watcher-v17-test-data

networks:
  watcher-v17-test-net:
    name: watcher-v17-test-net
```

## Release Gate Scenarios

Checked items were verified by the automated harness within the scope above. Unchecked items still require deployment-specific or infrastructure acceptance:

* [x] **Successful private-registry update:** Real push/pull detects the new image, applies it, removes the backup and ends the transaction.
* [x] **Native Docker healthcheck:** Verify Watcher respects native `HEALTHCHECK` instructions.
* [x] **Registry-backed unhealthy replacement and rollback:** A real pushed/pulled broken image fails its native healthcheck and restores the original container ID.
* [x] **Named volume preservation:** Verify data in a named volume is preserved across updates.
* [x] **Linux bind mount preservation:** Private Linux host paths, contents, read-write access and read-only enforcement are preserved.
* [x] **Multiple dynamic networks:** Verify a container attached to multiple user-defined networks is recreated with all networks intact.
* [x] **Fixed tag with local latest alias remains ignored:** Verified using fixture tags `:fixed` and `:latest` on the same image.
* [x] **Explicit static IP is blocked before stop:** Configure a container with `ipv4_address` and verify Watcher raises a `RecreationError` before stopping it.
* [x] **Container network dependent is blocked before stop:** Configure a container with `network_mode: "container:watcher-v17-test-target"` and verify Watcher refuses to update the target.
* [x] **Client ReadTimeout fault injection:** A timeout after an actual daemon stop can finish the update; a timeout with the original still running refuses replacement and retains its identity. The timeout exception itself is simulated.
* [x] **Instrumented process termination after backup rename:** SIGKILL/exit 137 and restart of the same worker restore the original from durable state.
* [x] **Instrumented process termination after replacement creation/start:** SIGKILL/restart keeps a healthy replacement, including one that had not yet started; a broken created replacement rolls back.
* [x] **Second real worker instance is rejected:** Two actual Linux worker processes on the owned scope exercise self-ID detection and singleton refusal; the first remains running.
* [x] **Normal Watcher entry point and scheduled/interval loop via scoped endpoint:** Repository Dockerfile/CMD and `main.py`, native SIGTERM/restart recovery, real UTC scheduling, interval cycles and dry-run passed without application patches.
* [x] **Normal second Watcher instance via scoped endpoint:** Two normal application processes exercise self-ID detection and singleton refusal; the first stays running.
* [ ] **Raw daemon and homelab acceptance:** Verify the deployment configuration directly on a disposable daemon, rather than the restricted fixture transport. Do not run this against production workloads as a shortcut.
* [ ] **Registry/environment acceptance:** Check real homelab registry authentication/TLS, outages and deployment-specific bind paths without risking user data.
* [ ] **Infrastructure restart/power loss:** Container SIGKILL with an intact Docker daemon is not a host/VM/filesystem crash test.

## Cleanup and Verification

The automated harness cleans its exact owned resources and reports cleanup failures. For the manual disposable Compose environment, remove only that Compose project's resources:

```bash
docker compose -p watcher-integration down -v
```

Never delete resources by a shared prefix across runs, and never use global `docker system prune`. Inspect any leftovers and verify the exact project/ownership labels before removal.

**To confirm no production container was modified:**
Check the Watcher test logs and the `journal.json` in the test data volume. Verify that no container names outside of the `watcher-v17-test-` prefix appear in the update history.
