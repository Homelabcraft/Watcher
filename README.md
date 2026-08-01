# 🛡️ Watcher

[![Python Version](https://img.shields.io/badge/python-3.9%2B-blue?style=flat-square)](https://www.python.org/)
[![Docker](https://img.shields.io/badge/docker-ready-blue?style=flat-square&logo=docker)](https://www.docker.com/)
[![License](https://img.shields.io/badge/license-MIT-green?style=flat-square)](LICENSE)
[![Version](https://img.shields.io/badge/version-v1.7.0-orange?style=flat-square)](https://github.com/Homelabcraft/Watcher/releases)

**Watcher** is a Homelab Docker container auto-updater built for environments where short downtimes are acceptable. It automates your image lifecycle while prioritizing **system stability and data persistence** through container rollbacks, multi-stage health validation, and rich multi-messenger notifications.

---

## 💡 Why Watcher?

While tools like Watchtower are great for blindly pulling and restarting containers, they often leave you in the dark when an update breaks your application. **Watcher is different.**

*   **Container Lifecycle:** Watcher stops your working container, renames it, and keeps it as a backup before creating a replacement.
*   **Health Validation:** It verifies the health of the newly pulled container. If it crashes or reports unhealthy, Watcher instantly restores the backup.
*   **Rich Notifications:** Instead of generic "Update applied" logs, Watcher sends comprehensive, color-coded execution plans, version shifts (image hashes), and detailed rollback reports to **Discord, Slack, Telegram, or Ntfy**.
*   **Local Journaling:** Watcher maintains a persistent local JSON history of every scan cycle and update result for auditing and troubleshooting.
*   **Intelligent Cooldown:** Avoids "retry loops" by automatically placing failing containers into a cooldown period.
*   **Total Control:** Granular opt-in/opt-out labeling, deep dependency restarts, regex-based exclusions, and robust dry-runs ensure you always know exactly what will happen.

---

### Operational Policy
* **`:latest` Policy:** Watcher is exclusively designed to update containers tracking the `:latest` tag. Containers with explicit version tags (e.g., `:16`) or digests are ignored to ensure predictability.
* **Rollbacks (Container vs. Data):** Watcher performs *container* rollbacks by restoring the previous container instance if the new one fails health checks. It does **not** perform data rollbacks. If a new image performs an irreversible database migration before failing, the old container may not be able to read the new data format.

### Persistence & Crash Recovery
* **Data Directory:** Watcher persists its state and journal to a mapped `data/` volume.
* **Recovery Limits:** In the event of a hard crash (e.g., power loss) during an update, Watcher attempts to reconcile orphaned backups upon restart based on the persistent state file, but absolute recovery cannot be guaranteed in all edge cases.

---

## 🛠️ Quick Start

Get Watcher running in under 60 seconds.

### 1. Deployment
```bash
git clone https://github.com/Homelabcraft/Watcher.git
cd Watcher
cp .env.example .env
# Edit .env with your Discord/Slack/Telegram/Ntfy webhook URL
docker-compose up -d
```

### 2. Example Configuration
Add these labels to the containers you want to manage:
```yaml
services:
  database:
    image: postgres:16
    labels:
      - "watcher.enable=false" # Monitor only: Receive alerts if updates exist, but don't auto-update.

  web-app:
    image: my-app:latest
    labels:
      - "watcher.enable=true"  # Auto-update: Full lifecycle management.
      - "watcher.depends_on=database" # Restarts the database if web-app is updated.
```

---

## 🚀 Key Features (v1.6)

*   **📔 Update Journal:** Persistent local history of every scan cycle and update result in `journal.json`.
*   **❄️ Failure Cooldown:** Prevents aggressive retries of failing containers via configurable `FAILURE_COOLDOWN_SECONDS`.
*   **🛡️ Hardened Recreation:** Full support for advanced Docker configurations: `Ulimits`, `Sysctls`, `LogConfig`, `ShmSize`, `IpcMode`, and `PidMode`.
*   **🛑 Graceful Shutdown:** Safely handles `SIGTERM`/`SIGINT`. Watcher will safely handle signals, attempting to avoid leaving containers in an undefined state, although this is not guaranteed during hard crashes.
*   **🚫 Fail-Fast Configuration:** Strict startup validation ensures Watcher fails immediately with a clear error if misconfigured.
*   **⚖️ Hybrid Update Strategy:** Opt-in (`watcher.enable=true`) and Opt-out (`watcher.enable=false`) monitoring modes.
*   **🔍 Advanced Filtering:** Exclude containers globally via names (`EXCLUDE_CONTAINER_NAMES`) or Regex (`EXCLUDE_CONTAINER_REGEX`).
*   **⏱️ Precision Scheduling:** Run scans on a strict interval (`CHECK_INTERVAL`) or at an exact time daily (`SCHEDULE_TIME`).
*   **📋 Robust Dry Runs:** Generates a detailed Execution Plan sent directly to your messengers without altering running containers.

---

## 🏗️ The "Fail-Safe" Lifecycle

Watcher addresses the "Broken Update" problem by ensuring that a functional environment is never deleted until the replacement is verified as stable.

1.  **Detection:** Identifies upstream image changes via SHA-256 digest comparison.
2.  **State Preservation:** The active container is stopped and renamed to `${NAME}_backup`, preserving its exact state.
3.  **Hardened Recreation:** A new container is provisioned with close configuration parity.
4.  **Health Verification:** A multi-stage poll validates the new container's status and internal Docker health checks.
5.  **Atomic Cleanup:** Only upon confirmed health is the backup removed. On failure, an **automated rollback** restores the original container instantly.

---

## ⚙️ Configuration Reference

### Environment Variables (`.env`)

| Variable | Default | Description |
| :--- | :--- | :--- |
| `CHECK_INTERVAL` | `86400` | Scan frequency in seconds (Default: 24h). Ignored if `SCHEDULE_TIME` is set. |
| `SCHEDULE_TIME`  | `""`    | Optional: Run Watcher once daily at this specific local time (e.g. `03:00`). |
| `WATCH_BY_LABEL` | `true`  | If true, only containers with `watcher.enable=true` are updated. |
| `DRY_RUN`        | `false` | Generates a detailed Execution Plan to your configured messengers. |
| `STATE_PATH` | `/app/data/state.json` | Path to persistent state file for recovery. |
| `ALLOW_USER_FALLBACK` | `false` | If true, permits recreating a container as `root` if the configured user is missing in the new image. |
| `JOURNAL_ENABLED` | `true` | Enable persistent local JSON history of all scan cycles. |
| `JOURNAL_MAX_ENTRIES` | `100` | Maximum cycle records to keep in the journal. |
| `FAILURE_COOLDOWN_SECONDS` | `3600` | Seconds to wait before retrying a container that failed multiple times. |
| `MAX_UPDATES_PER_CYCLE` | `0` | Limit updates per run (0 = unlimited) to prevent resource spikes. |
| `RESTART_DEPENDENTS` | `true` | If false, disables the automatic restart of linked containers. |
| `NOTIFY_SUMMARY_STRATEGY` | `always` | `always`, `on_change` (action taken), or `on_error`. |
| `NOTIFY_ON_STARTUP` | `true` | Set to false for a quieter startup (no "Watcher Started" alert). |
| `EXCLUDE_CONTAINER_REGEX` | `""` | Optional regex pattern to ignore containers by name. |
| `CLEANUP_OLD_IMAGES` | `false` | Automatically prune dangling images after a successful update. |
| `HEALTH_CHECK_RETRIES` | `12` | Number of attempts to verify container health. |
| `HEALTH_CHECK_DELAY` | `10` | Seconds to wait between health checks. |
| `LOG_LEVEL` | `INFO` | Standard output log level (`DEBUG`, `INFO`, `WARNING`, `ERROR`). |

#### Notifications
| Variable | Description |
| :--- | :--- |
| `DISCORD_WEBHOOK_URL` | Your Discord Webhook URL. |
| `SLACK_WEBHOOK_URL`   | Your Slack Incoming Webhook URL. |
| `TELEGRAM_BOT_TOKEN`  | Your Telegram Bot API Token. |
| `TELEGRAM_CHAT_ID`    | Your Telegram target Chat ID. |
| `NTFY_URL`            | Your Ntfy topic URL. |
| `NOTIFY_UPDATES_AVAILABLE` | If false, suppresses alerts for monitored-only containers. |
| `NOTIFY_ON_UPDATE_START` | If false, suppresses the "Initiating sequence" notifications. |

### Docker Labels

| Label | Value | Description |
| :--- | :--- | :--- |
| `watcher.enable` | `true/false` | Controls the update behavior for this container. |
| `watcher.self` | `true` | **Mandatory:** Protects the Watcher instance from self-updating. |
| `watcher.health.start_period` | `0` | Seconds to wait before starting health checks. |
| `watcher.depends_on` | `app1,app2` | Comma-separated list of containers to restart after this one updates. |

---

## 📄 License & Compliance
This project is licensed under the **MIT License**. It is designed for use in homelab environments where reliability is the primary metric of success.
