# Watcher

Lightweight Docker container updater for standalone hosts. Focuses on safe recreation and bit-perfect rollbacks.

## Features
- **Auto-Update**: Pulls and recreates containers with the `:latest` tag.
- **Config Preservation**: Reconstructs containers from runtime configuration (Ports, Volumes, Env, Labels, etc.).
- **Health Verification**: Retries checks post-update to ensure stability.
- **Automated Rollback**: Reverts to the exact previous **Image ID** if the update fails.
- **Discord Alerts**: Informative embed-based notifications.

## Safety
Watcher works exclusively through the Docker API. It does not touch YAML files. Rollbacks use cryptographic Image IDs to guarantee state recovery.
