# Watcher Verification & Testing Guide

Before deploying Watcher into production, it is highly recommended to test the update lifecycle in a safe, controlled environment (Sandbox). 

This project includes automated unit tests that verify the critical execution paths without needing a live Docker environment or a real Discord webhook.

## 1. Running Automated Tests
The `test_main.py` file covers the core logic:
- Successful updates
- Failed updates triggering a rollback
- Rollbacks recovering from a rename failure
- Safe restoration of renamed backups
- Deduplication of dependent container restarts

**Run the tests using:**
```bash
python -m unittest test_main.py
```
All tests use `unittest.mock` to simulate Docker and HTTP requests, meaning they are completely safe to run anywhere.

## 2. Sandbox Testing (Controlled Live Test)
To verify the watcher with real containers, follow these steps:

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
       stop_grace_period: 2m
         
     web_app:
       image: nginx:1.24
       ports:
         - "8080:80"
       labels:
         - "watcher.enable=true"
         
     db_backend:
       image: redis:alpine
       labels:
         - "watcher.depends_on=web_app"
   ```

2. Make sure `.env` is configured for your Sandbox:
   ```env
   DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
   CHECK_INTERVAL=30
   WATCH_BY_LABEL=true
   ```

3. Start your environment:
   ```bash
   docker-compose up -d
   ```

4. Trigger an update by manually overriding the image tag of `web_app`:
   ```bash
   # Pull the real latest image
   docker pull nginx:latest
   # Tag it so Watcher thinks it needs to update the local 'latest' (which is currently 1.24)
   docker tag nginx:1.24 nginx:latest
   ```
   
Watcher should detect the change, restart `web_app`, and subsequently restart `db_backend`. Check your Discord channel for the summary report.

## 3. Testing v1.5 Safety Features

### Dry Run / Execution Plan
Set `DRY_RUN=true` in your `.env`. When you run Watcher, it will generate a detailed **Execution Plan** and send it to Discord. 

**Important:** To reliably detect updates, Watcher *will* pull the latest images from the registry, which updates your local image cache. However, it will **not** stop, recreate, or restart any of your running containers. The Execution Plan shows exactly which containers would be updated (including old and new Image Hashes) and which dependents would be restarted if `DRY_RUN` were false.

### Daily Scheduling
Set `SCHEDULE_TIME=14:30` (or any time slightly in the future) in your `.env`. Watcher will calculate the sleep time until this exact moment and execute a run.

### Fail-Fast Configuration
Set an invalid configuration in your `.env` (e.g., `CHECK_INTERVAL=-1` or `CHECK_INTERVAL=5`). 
Run `docker-compose up -d watcher` and check the logs: `docker logs watcher`. 
Watcher should exit immediately with a clear `ConfigurationError` and `Exit Code 1`.

### Graceful Shutdown
While Watcher is in the middle of pulling an image or recreating a container, run `docker-compose stop watcher`.
Because of the new `SIGTERM` handler and `stop_grace_period: 2m`, Watcher will log `Graceful shutdown initiated`. It does not forcefully abort blocking Docker API calls, but rather flags the shutdown, finishes the current critical update step (including health checks and rollback if necessary), and then exits safely.

## 4. Protecting the Watcher
Always ensure your Watcher container has the `watcher.self=true` label if you are running it alongside the containers it monitors. This guarantees it will never attempt to update or restart itself, preventing a broken state.
