# 🛡️ Watcher

[![Python Version](https://img.shields.io/badge/python-3.9%2B-blue?style=flat-square)](https://www.python.org/)
[![Docker](https://img.shields.io/badge/docker-ready-blue?style=flat-square&logo=docker)](https://www.docker.com/)
[![License](https://img.shields.io/badge/license-MIT-green?style=flat-square)](LICENSE)
[![Version](https://img.shields.io/badge/version-v1.5.2-orange?style=flat-square)](https://github.com/Homelabcraft/Watcher/releases)

**Watcher** is a production-grade Docker container auto-updater built for environments where downtime is unacceptable. It automates your image lifecycle while prioritizing **system stability and data persistence** through zero-data-loss rollbacks, multi-stage health validation, and rich multi-messenger notifications.

---

## 💡 Why Watcher?

While tools like Watchtower are great for blindly pulling and restarting containers, they often leave you in the dark when an update breaks your application. **Watcher is different.**

*   **Zero-Downtime Philosophy:** Watcher doesn't just delete your working container. It pauses it, renames it, and keeps it as an instant backup.
*   **Health Validation:** It verifies the health of the newly pulled container. If it crashes or reports unhealthy, Watcher instantly restores the backup.
*   **Rich Notifications:** Instead of generic "Update applied" logs, Watcher sends comprehensive, color-coded execution plans, version shifts (image hashes), and detailed rollback reports to **Discord, Slack, Telegram, or Ntfy**.
*   **Total Control:** Granular opt-in/opt-out labeling, deep dependency restarts, and robust dry-runs ensure you always know exactly what will happen.

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

## 🚀 Key Features (v1.5)

*   **🛡️ Hardened Recreation:** Full support for advanced Docker configurations: `Ulimits`, `Sysctls`, `LogConfig`, `ShmSize`, `IpcMode`, and `PidMode`.
*   **🛑 Graceful Shutdown:** Safely handles `SIGTERM`/`SIGINT`. Watcher will never exit mid-update, guaranteeing containers are not left in an undefined state.
*   **🚫 Fail-Fast Configuration:** Strict startup validation ensures Watcher fails immediately with a clear error if misconfigured.
*   **⚖️ Hybrid Update Strategy:** Opt-in (`watcher.enable=true`) and Opt-out (`watcher.enable=false`) monitoring modes.
*   **🔗 Smart Dependency Management:** Support for `watcher.depends_on` labels and `NetworkMode: container:<name>` linking with automatic restart deduplication.
*   **⏱️ Precision Scheduling:** Run scans on a strict interval (`CHECK_INTERVAL`) or at an exact time daily (`SCHEDULE_TIME`).
*   **📋 Robust Dry Runs:** Safely simulate updates to generate a detailed Execution Plan sent directly to your messengers without altering running containers.

---

## 🏗️ The "Fail-Safe" Lifecycle

Watcher addresses the "Broken Update" problem by ensuring that a functional environment is never deleted until the replacement is verified as stable.

1.  **Detection:** Identifies upstream image changes via SHA-256 digest comparison.
2.  **State Preservation:** The active container is paused and renamed to `${NAME}_backup`, preserving its exact state.
3.  **Hardened Recreation:** A new container is provisioned with 1:1 configuration parity.
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
| `NOTIFY_UPDATES_AVAILABLE` | `true` | If false, suppresses "Updates Available" alerts in summary reports for `watcher.enable=false` containers. |
| `CLEANUP_OLD_IMAGES` | `false` | Automatically prune dangling images after a successful update. |
| `HEALTH_CHECK_RETRIES` | `12` | Number of attempts to verify container health. |
| `HEALTH_CHECK_DELAY` | `10` | Seconds to wait between health checks. |
| `LOG_LEVEL` | `INFO` | Standard output log level. Can be set to `DEBUG` for extensive troubleshooting. |

#### Notifications (Configure at least one)
| Variable | Description |
| :--- | :--- |
| `DISCORD_WEBHOOK_URL` | Your Discord Webhook URL. |
| `SLACK_WEBHOOK_URL`   | Your Slack Incoming Webhook URL. |
| `TELEGRAM_BOT_TOKEN`  | Your Telegram Bot API Token. |
| `TELEGRAM_CHAT_ID`    | Your Telegram target Chat ID. |
| `NTFY_URL`            | Your Ntfy topic URL (e.g., `https://ntfy.sh/mytopic`). |

### Docker Labels

| Label | Value | Description |
| :--- | :--- | :--- |
| `watcher.enable` | `true/false` | Controls the update behavior for this container. |
| `watcher.self` | `true` | **Mandatory:** Protects the Watcher instance from self-updating. |
| `watcher.depends_on` | `app1,app2` | Comma-separated list of containers to restart after this one updates. |

---

## 📄 License & Compliance
This project is licensed under the **MIT License**. It is designed for use in homelabs and production environments where reliability is the primary metric of success.
