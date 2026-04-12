# Changelog

All notable changes to this project will be documented in this file.

## [1.5.0] - 2026-04-12
### Added
- **Multi-Messenger Notifications:** Pluggable notification backends. Added full support for Slack, Telegram, and Ntfy alongside Discord. 
- **Rich Embeds & Dashboards:** Radically improved notifications. Notifications now provide extensive details including scan modes, version shifts (image hashes), exact duration, dependent restarts, and specific error steps.
- **Fail-Fast Configuration:** Strict startup validation ensures Watcher fails immediately with a clear error if the environment is misconfigured (e.g., invalid intervals or webhook URLs), preventing silent runtime failures.
- **Graceful Shutdown:** Safely handles `SIGTERM` and `SIGINT` signals. Watcher will never exit mid-update, guaranteeing containers are not left in an undefined or broken state if the host restarts.
- **Daily Scheduling:** Added `SCHEDULE_TIME` config variable to allow running Watcher at a specific time once a day, instead of using a fixed interval.
- **Robust Execution Plan (Dry Run):** The dry-run mode has been completely overhauled to not just log, but push a detailed "Execution Plan" to your configured messengers, showing exact targets and dependencies that *would* be touched.

### Changed
- **Architectural Rewrite:** The core logic now uses strict OOP models (`ContainerUpdateInfo`, `UpdateStatus`) instead of primitive types, massively boosting stability.
- **Stop Timeout Resiliency:** The container stop process now reads the target container's specific `StopTimeout` from Docker, falling back to 15s. Additionally, Python client `ReadTimeout` exceptions during heavy stops (like databases or qBittorrent saving state) are now intercepted safely, eliminating false-positive rollback triggers.
- **Global Timeout:** Increased the Docker client timeout to 120s to ensure Watcher does not lose connection during extensive operations.

## [1.4.3] - 2026-04-08
### Added
- **Hardened Self-Protection:** Watcher now identifies itself via container ID, ensuring zero risk of self-updating even if renamed.
- **Healthcheck Visibility:** Added warnings for containers lacking native Docker healthchecks to improve safety for stateful applications.
- **Smart Restart Protection:** Prevented infinite restart loops by deduplicating dependency triggers within the same cycle.

### Changed
- Refined exception handling in `health_monitor.py` and `docker_handler.py` for better production stability.
- Improved logging clarity for container discovery and health verification.

## [1.4.2] - 2026-04-07
### Fixed
- **User-Resolution Fallback:** Containers failing to start with a configured user (e.g. `root`) now perform a single retry without the user configuration. This resolves issues with minimalist/distroless images that lack a passwd file.
- **Container Selection (Discovery):** Fixed a bug where containers with empty local image tags (RepoTags=[]) were skipped. Discovery now correctly prioritizes the container's original `Config.Image` reference.
- **Defensive Rollback:** Hardened the rollback lifecycle against `IndexError` and improved overall diagnostic logging.
- **SDK Stability:** Pruned `None`-values from Docker create arguments to prevent SDK-level inconsistencies.

## [1.4.0] - 2026-04-07
### Added
- **Hardened Recreation:** Support for `Ulimits`, `Sysctls`, `LogConfig`, `ShmSize`, `IpcMode`, and `PidMode`.
- **Advanced Networking:** Support for `NetworkMode: container:<name>` dependencies.
- **Reporting:** Hybrid mode summary report now includes "Reported" containers (updates available but not applied).
- **Discord:** Professional summary report with categorized scan results.

### Changed
- Refactored `WatcherService` for better stability and explicit error handling.
- Optimized Docker recreation plan logic to ensure bit-perfect replicas.

## [1.3.2] - 2026-04-03
### Changed
- Prepared repository for public release.
- Refined documentation and examples.

## [1.3.1] - 2026-04-03
### Fixed
- Corrected fallback logic for containers with `watcher.enable=false`.
- Improved stability in hybrid monitoring mode.

## [1.3.0] - 2026-04-03
### Added
- **Hybrid Update Mode:** Introduction of opt-in/opt-out monitoring strategies.
- Added comprehensive unit tests for hybrid logic.

## [1.2.3] - 2026-04-03
### Changed
- Set default `CHECK_INTERVAL` to 24 hours (86400s) for better performance in production environments.

## [1.1.0] - Initial Development
- Core update logic with Rename-Backup Strategy.
- Basic Discord notifications.
- Health monitor integration.
