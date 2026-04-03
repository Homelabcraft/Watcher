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

## 3. Protecting the Watcher
Always ensure your Watcher container has the `watcher.self=true` label if you are running it alongside the containers it monitors. This guarantees it will never attempt to update or restart itself, preventing a broken state.