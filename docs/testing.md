# Watcher Verification & Testing Guide

Before deploying Watcher into production, it is highly recommended to test the update lifecycle in a safe, controlled environment (Sandbox).

This project includes automated unit tests that verify the critical execution paths without needing a live Docker environment or a real Discord webhook. **However, unit tests do not replace real Docker integration tests!**

## 1. Running Automated Tests
The unit tests cover the core logic:
- Successful updates
- Failed updates triggering a rollback
- Rollbacks recovering from a rename failure
- Safe restoration of renamed backups
- Deduplication of dependent container restarts

**Run the tests using:**
```bash
python -m pip install -r requirements-dev.txt
ruff check .
python -m unittest discover -s tests -p "test_*.py" -v
```
All tests use `unittest.mock` to simulate Docker and HTTP requests, meaning they are completely safe to run anywhere.

### Opt-in Real Docker Lifecycle Checks

On a Linux Docker daemon, explicitly opt in to disposable-resource tests:
```bash
WATCHER_DOCKER_INTEGRATION=1 python integration_test_docker.py -v
```

The harness owns a unique `watcher-v17-test-` namespace, builds Alpine fixture images and removes its own containers, networks, volumes and image tags. The default suite does not start the Watcher background loop or touch existing workloads. It includes real recreation, healthchecks, named volumes, read-write/read-only Linux bind mounts and simulated client stop timeouts. Update-detection pulls in this default suite use locally tagged fixtures. Base images/build caches may remain.

Enable the additional real private-registry and instrumented Linux process checks separately:
```bash
WATCHER_DOCKER_INTEGRATION=1 WATCHER_RELEASE_GATES=1 python integration_test_docker.py -v
```

This starts a disposable `registry:3` on the Linux daemon's loopback interface using host networking, without changing daemon configuration. It builds a Linux Python worker with the Docker socket and a private state volume. Container discovery/creation is ownership-scoped. The worker is paused after a real persisted transaction phase, killed via SIGKILL (exit 137), then restarted with the same state. This exercises production update/recovery methods, **not the unmodified `main.py` entry point or full scheduled loop**. The worker and test harness are excluded from the production image.

To also test the normal Dockerfile/`main.py` process, including scheduled/interval loops and graceful shutdown:
```bash
WATCHER_DOCKER_INTEGRATION=1 WATCHER_RELEASE_GATES=1 WATCHER_ENTRYPOINT_GATES=1 python integration_test_docker.py -v
```

The application image is built with the repository Dockerfile and runs its normal CMD, without monkey-patching application methods or injecting the test worker. Its `DOCKER_HOST` points to a test-only API endpoint in a private internal Docker network. Only that endpoint mounts the daemon socket; the application does not. The endpoint filters container lists by ownership, verifies prefix/labels before metadata or mutations, rewrites mutations to immutable container IDs, permits only the private fixture registry, and rejects unsupported/destructive operations and replacement host mounts/privileges. This is a restricted fixture transport, **not a production security proxy or a separate Docker daemon**. A harmless `.env` sentinel in the temporary build context verifies exclusion without reading user secrets.

In the optional GitHub workflow, select `docker_integration` for the default suite, `release_gates` for registry/SIGKILL checks, or `entrypoint_gates` for all checks. The latter also enables registry/SIGKILL checks. On Docker Desktop, set `DOCKER_HOST` explicitly as documented in the [integration guide](linux-integration-testing.md); the Linux Docker socket must be mountable for the endpoint/worker fixtures.

Use a disposable daemon when possible. See [Linux Integration Testing](linux-integration-testing.md) for details and remaining manual release gates.

## 2. Sandbox Testing (Controlled Live Test)
To verify the watcher with real containers, follow these steps using harmless `:latest` test containers.

**Important Note on Tags:** Watcher *exclusively* tracks and updates containers running a `:latest` tag. Containers with fixed tags (e.g. `nginx:1.24` or `redis:alpine`) or explicit SHAs are completely ignored by design.

1. Create a `docker-compose.yml` for your test:
   ```yaml
   services:
     watcher:
       build: .
       volumes:
         - /var/run/docker.sock:/var/run/docker.sock
       env_file: .env
       labels:
         - "watcher.self=true"
       stop_grace_period: 5m

     web_app:
       image: nginx:latest
       ports:
         - "8080:80"
       labels:
         - "watcher.enable=true"

     db_backend:
       image: redis:latest
       labels:
         - "watcher.depends_on=web_app"
   ```

2. Make sure `.env` is configured for your Sandbox:
   ```env
   DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
   CHECK_INTERVAL=30
   WATCH_BY_LABEL=true
   ```

3. The correct sandbox initialization sequence:
   To ensure the container's `Config.Image` remains exactly `nginx:latest` while running an outdated instance, you must tag the old image locally *before* creating the test application:
   ```bash
   # Pull an older image
   docker pull nginx:1.24
   # Tag it so the local 'latest' is outdated
   docker tag nginx:1.24 nginx:latest
   # Create the application without pulling
   docker compose up -d --pull never web_app
   # Start the watcher
   docker compose up -d watcher
   ```
   This ensures the Docker daemon creates the container with the reference `nginx:latest`, but uses the outdated local image cache, accurately simulating a pending update.

For a comprehensive suite of real Docker environment integration tests, see [Linux Integration Testing](linux-integration-testing.md).

Watcher should detect the change when it pulls the real `nginx:latest`, restart `web_app`, and subsequently restart `db_backend`. Check your Discord channel for the summary report.

## 3. Safety Features Testing

### Dry Run / Execution Plan
Set `DRY_RUN=true` in your `.env`. When you run Watcher, it will generate a detailed **Execution Plan** and send it to Discord.

**Important:** To reliably detect updates, Watcher *will* pull the latest images from the registry, which updates your local image cache. However, it will **not** stop, recreate, or restart any of your running containers. The Execution Plan shows exactly which containers would be updated (including old and new Image Hashes) and which dependents would be restarted if `DRY_RUN` were false.

### Daily Scheduling
Set `SCHEDULE_TIME=14:30` (or any time slightly in the future) in your `.env`. Watcher will calculate the sleep time until this exact moment and execute a run.

### Fail-Fast Configuration
Set an invalid configuration in your `.env` (e.g., `CHECK_INTERVAL=-1` or `CHECK_INTERVAL=5`).
Run `docker compose up -d watcher` and check the logs: `docker logs watcher`.
Watcher should exit immediately with a clear `ConfigurationError` and `Exit Code 1`.

### Graceful Shutdown
While Watcher is in the middle of pulling an image or recreating a container, run `docker compose stop watcher`.
Because of the `SIGTERM` handler and `stop_grace_period: 5m`, Watcher will log `Graceful shutdown initiated`. The shutdown flag does not forcibly abort blocking Docker API calls, but interrupts waits and health polling. An interrupted update is recorded as `INTERRUPTED`, not as an unhealthy replacement: replacement, backup and transaction are retained for startup recovery, without a new failure cooldown. Interrupted recovery/rollback verification also retains its pending transaction. No new container update/recovery/dependent restart begins once shutdown is observed. A genuine API error or health failure can still require rollback. Already verified updates may finish cleanup. Do not treat this as a guarantee against forced termination or power loss.

Unexpected exceptions in the main loop are logged and propagated, producing a nonzero process exit. Shutdown notification and client-close failures are logged separately so they do not hide the original exception.

## 4. Protecting the Watcher
Always ensure your Watcher container has the `watcher.self=true` label if you are running it alongside the containers it monitors. This guarantees it will never attempt to update or restart itself, preventing a broken state.
