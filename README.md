# 🛡️ Watcher

![Python Version](https://img.shields.io/badge/python-3.9%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)
![Version](https://img.shields.io/badge/version-v1.3.1-orange)
![Docker](https://img.shields.io/badge/docker-supported-blue?logo=docker)

**Watcher** is a hardened, production-ready Python alternative to tools like Watchtower. It monitors your Docker containers for image updates and performs bit-perfect recreations with automated health checks, data-loss protection (via Rename-Backups), and Discord notifications.

---

## 🎯 Why Watcher? (The Data-Loss Problem)
Standard auto-updaters often stop and delete your container *before* they verify if the new image actually works. If a new update is broken, your container is gone, and downgrading manually is a nightmare. 

**Watcher solves this using a Rename-Backup-Strategy:**
1. Your running container is paused and safely renamed to `name_backup`.
2. The new container is created and started.
3. Watcher waits and runs a rigorous **Health Check**.
4. ✅ **Success:** The backup is deleted.
5. ❌ **Failure:** The broken update is deleted, your backup is renamed back and instantly started. **Zero data loss. Zero downtime.**

---

## 🚀 Key Features

* **Rename-Backup Rollbacks:** Safest update mechanism for critical stateful apps.
* **Hybrid Update Strategy (Opt-in):** Auto-update safe containers, but only *report* available updates for risky ones (like databases).
* **Dependent Restarts:** Link containers together! If your database updates successfully, Watcher automatically restarts your web app.
* **Config Preservation:** 1:1 reconstruction of Ports, Volumes, ENV vars, and Labels.
* **Discord Integration:** Clean, non-spammy summary reports sent directly to your Discord channel.

---

## ⚙️ How the Hybrid Mode Works
We highly recommend running Watcher in **Opt-In** mode (`WATCH_BY_LABEL=true` in `.env`). 
This gives you total control via standard Docker labels:

| Label | Behavior |
| :--- | :--- |
| **No Label** | Container is monitored. If an update is found, Watcher sends a Discord alert but **does not touch** the container. |
| `watcher.enable=true` | Container is fully auto-updated and rolled back if necessary. |
| `watcher.enable=false` | Force monitor-only (useful if you disable opt-in mode). |
| `watcher.self=true` | Protects the container from being scanned or touched entirely (Mandatory for the Watcher container itself). |
| `watcher.depends_on=app1,app2` | If `app1` or `app2` are auto-updated successfully, this container will be gracefully restarted. |

### ⚠️ A Warning on Databases
Even with automated rollbacks, **never auto-update heavy databases** (like PostgreSQL, InfluxDB, or CouchDB) or apps that perform irreversible schema migrations on startup (like Nextcloud). If a migration fails halfway through, a rollback to the old image might leave your data volumes corrupted. Leave these containers unlabeled so Watcher only *reports* their updates, and update them manually after taking a volume backup.

---

## 🛠️ Installation & Setup

1. **Clone the repository:**
   ```bash
   git clone https://github.com/Homelabcraft/Watcher.git
   cd Watcher
   ```

2. **Configure your environment:**
   ```bash
   cp .env.example .env
   # Edit .env and insert your Discord Webhook URL!
   ```

3. **Start Watcher:**
   ```bash
   docker-compose up -d
   ```

### Example: Protecting Watcher & Enabling an App
Here is how you configure a standard `docker-compose.yml` to work with Watcher:

```yaml
services:
  watcher:
    build: .
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
    env_file: .env
    labels:
      - "watcher.self=true"  # Crucial: Watcher must never update itself!

  sonarr:
    image: lscr.io/linuxserver/sonarr:latest
    labels:
      - "watcher.enable=true" # Safe stateless app -> Auto-update it

  postgres:
    image: postgres:15
    # No label -> Watcher will only notify you when Postgres 16 is available
```

---

## 🧪 Testing (Sandbox)
Before running Watcher on your production homelab, please read our [TEST_GUIDE.md](TEST_GUIDE.md) to understand how to safely simulate updates and verify Discord webhooks using unit tests or a controlled Sandbox.

---

## 📄 License
This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.