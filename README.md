# 🛡️ Watcher

![Python Version](https://img.shields.io/badge/python-3.9%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)
![Version](https://img.shields.io/badge/version-v0.1.0--alpha-orange)
![Docker](https://img.shields.io/badge/docker-supported-blue?logo=docker)

**Watcher** is a hardened, Python-based alternative to Watchtower. It monitors your Docker containers for image updates and performs bit-perfect recreations with automated health checks and Discord notifications.

## 🚀 Key Features
* **Intelligent Updates:** Only recreates containers when a new `:latest` image is actually pulled.
* **Config Preservation:** 1:1 reconstruction of Ports, Volumes, ENV vars, and Labels.
* **Deterministic Networking:** Uses `networking_config` to prevent IP-address loss.
* **Safety First:** Automated rollbacks if a container becomes `unhealthy` after an update.
* **Discord Integration:** Real-time status embeds for your homelab monitoring.

## ⚙️ Configuration
| Variable | Description | Default |
| :--- | :--- | :--- |
| `CHECK_INTERVAL` | Seconds between update checks | `300` |
| `DISCORD_WEBHOOK_URL` | Your Discord channel webhook | `None` |
| `DRY_RUN` | If `true`, only checks but doesn't restart | `false` |
| `CLEANUP_OLD_IMAGES`| Removes previous images after success | `false` |

## 🛠️ Setup
1. Clone the repo: `git clone https://github.com/Homelabcraft/Watcher.git`
2. Configure your `.env` file.
3. Run with docker-compose: `docker-compose up -d`
