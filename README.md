# 🛡️ Watcher

[![Python Version](https://img.shields.io/badge/python-3.9%2B-blue?style=flat-square)](https://www.python.org/)
[![Docker](https://img.shields.io/badge/docker-ready-blue?style=flat-square&logo=docker)](https://www.docker.com/)
[![License](https://img.shields.io/badge/license-MIT-green?style=flat-square)](LICENSE)
[![Version](https://img.shields.io/badge/version-v1.5.0-orange?style=flat-square)](https://github.com/Homelabcraft/Watcher/releases)

**Watcher** is a high-integrity, production-grade container orchestration utility designed for automated image lifecycle management. Unlike standard update tools, Watcher prioritizes **system stability and data persistence** through a sophisticated *Rename-Backup-Strategy*, multi-stage health validation, and rigorous state management.

---

## 🏗️ Architecture & Philosophy

In enterprise environments, a failed container update isn't just an inconvenience—it's downtime. Watcher addresses the "Broken Update" problem by ensuring that a functional environment is never deleted until the replacement is verified as stable.

### The "Fail-Safe" Lifecycle:
1.  **Detection:** Identifying upstream image changes via SHA-256 digest comparison.
2.  **State Preservation:** The active container is paused and renamed to `${NAME}_backup`, preserving its exact state and configuration.
3.  **Hardened Recreation:** A new container is provisioned with 1:1 parity (including Ulimits, Sysctls, and NetworkModes).
4.  **Health Verification:** A multi-stage poll validates the new container's status and internal health checks.
5.  **Atomic Cleanup:** Only upon confirmed health is the backup removed. On failure, an **automated rollback** restores the original container instantly.

---

## 🚀 Enterprise Features (v1.5)

*   **🛡️ Hardened Recreation:** Full support for advanced Docker configurations: `Ulimits`, `Sysctls`, `LogConfig`, `ShmSize`, `IpcMode`, and `PidMode`.
*   **🛑 Graceful Shutdown:** Safely handles `SIGTERM` and `SIGINT` signals. Watcher will never exit mid-update, guaranteeing containers are not left in an undefined or broken state if the host restarts.
*   **🚫 Fail-Fast Configuration:** Strict startup validation ensures Watcher fails immediately with a clear error if the environment is misconfigured (e.g., invalid intervals or webhook URLs), preventing silent failures during runtime.
*   **🔄 Intelligent Rollbacks:** Zero-data-loss recovery if the new image is unhealthy or crashes on startup.
*   **⚖️ Hybrid Update Strategy:** Opt-in/Opt-out modes allowing you to auto-update stateless apps while only *monitoring* mission-critical databases.
*   **🔗 Deep Dependency Management:** Support for `watcher.depends_on` labels and `NetworkMode: container:<name>` linking.
*   **🧠 Smart Restart Protection:** Prevents infinite restart loops by deduplicating dependency triggers within the same cycle.
*   **📊 Summary Reporting:** Professional Discord notifications with categorized results (Updated, Reported, Failed, Rolled Back). Notification failures never crash the orchestration loop.

---

## ⚙️ Configuration Reference

### Environment Variables (`.env`)

| Variable | Default | Description |
| :--- | :--- | :--- |
| `CHECK_INTERVAL` | `86400` | Scan frequency in seconds (Default: 24h). Ignored if `SCHEDULE_TIME` is set. Must be >= 10. |
| `SCHEDULE_TIME`  | `""`    | Optional: Run Watcher once daily at this specific local time (e.g. `03:00`). |
| `WATCH_BY_LABEL` | `true`  | If true, only containers with `watcher.enable=true` are updated. |
| `DRY_RUN`        | `false` | Generates a detailed Execution Plan to Discord. **Note:** Pulls new images to detect changes, but does not stop, recreate, or restart any containers. |
| `CLEANUP_OLD_IMAGES` | `false` | Automatically prune dangling images after a successful update. |
| `HEALTH_CHECK_RETRIES` | `12` | Number of attempts to verify container health. |
| `HEALTH_CHECK_DELAY` | `10` | Seconds to wait between health checks. |

### Docker Labels

| Label | Value | Description |
| :--- | :--- | :--- |
| `watcher.enable` | `true/false` | Controls the update behavior for this container. |
| `watcher.self` | `true` | **Mandatory:** Protects the Watcher instance from self-updating. |
| `watcher.depends_on` | `app1,app2` | Comma-separated list of containers to restart after this one updates. |

---

## 🛠️ Quick Start

### 1. Deployment
```bash
git clone https://github.com/Homelabcraft/Watcher.git
cd Watcher
cp .env.example .env
docker-compose up -d
```

### 2. Example Configuration
```yaml
services:
  database:
    image: postgres:16
    labels:
      - "watcher.enable=false" # Monitor only: Receive alerts, but update manually.

  web-app:
    image: my-app:latest
    labels:
      - "watcher.enable=true"  # Auto-update: Full lifecycle management.
      - "watcher.depends_on=database"
```

---

## 📄 License & Compliance
This project is licensed under the **MIT License**. It is designed for use in homelabs and production environments where reliability is the primary metric of success.
