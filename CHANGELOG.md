# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]
### Added
- **Crash Recovery & Startup Reconciliation (`startup_recovery()`):** Watcher now automatically detects orphaned `_backup` containers on startup resulting from unexpected host reboots or power loss during an active update. It automatically reconciles the state by checking health and safely recovering the primary or backup container. (Note: Automatic recovery depends on the persistent `state.json` accurately recording the transaction. Untracked backups will be reported for manual intervention.)
- **Robust Persistence & Atomicity:** Both `state.json` and `journal.json` are now written using atomic temporary files (`os.replace`) with aggressive disk syncing (`f.flush()`, `os.fsync()`) to prevent JSON corruption during power loss. Added automatic detection and backup of corrupted state files.
- **Enhanced Container Replication:** Drastically improved close configuration parity. Added support for complex mounts (including `tmpfs`), complex device mappings, DNS configurations (`dns`, `dns_search`, `dns_opt`), capabilities (`cap_add`, `cap_drop`), CPU/Memory limits, PID modes, and custom healthcheck start periods (`StartPeriod`).
- **Data Persistence Strategy:** Re-architected storage paths. All operational data (journal, state) is now saved to an explicitly declared `data/` volume mount point, ensuring persistence across Watcher container updates.
- **Fault-Tolerant Multi-Notifier:** The `MultiNotifier` component now strictly isolates failures. If one configured messaging backend (e.g., Ntfy) goes down, it will no longer block or crash the delivery of notifications to other functional backends (e.g., Telegram).
- **Telegram Notification Hardening:** Implemented strict HTML entity escaping for Telegram messages to prevent parsing errors when container names, image tags, or error messages contain sensitive characters (`<`, `>`, `&`).

### Changed
- **User Fallback Policy:** The fallback to `root` during container recreation (for images without explicit users) is now **disabled by default** to prioritize security. Set `ALLOW_USER_FALLBACK=true` to restore the old behavior.
- **Update Rate Limiting Strictness:** `MAX_UPDATES_PER_CYCLE` now strictly counts failed attempts and network timeouts against the limit, preventing Watcher from continuously hammering registries when the limit is reached.
- **Network Resilience (Hard Rollback):** Network disconnects or Docker API availability issues during the recreation phase now trigger an immediate hard rollback to the original container, strictly favoring availability over partial updates.
- **Anonymous Volume Preservation:** Improved handling of Docker anonymous volumes (64-character hex strings). They are now treated explicitly to prevent data loss or detachment during container recreation.

### Fixed
- **Backup Container Deletion:** Fixed a critical bug where Watcher attempted to delete an existing, conflicting `_backup` container automatically. It now aborts the recreation for safety and leaves the conflict for manual resolution or automatic `startup_recovery()`.
- **API Fetching Safety:** Errors during Docker daemon interactions (like `containers.list()`) now cleanly abort the cycle rather than falsely reporting 0 containers, preventing corrupted states or empty reports.


## [1.6.0] - 2026-04-12
### Added
- **Persistent Update Journal:** Added local JSON journaling (`journal.json`) to track every scan cycle, container check, and update result across service restarts. Configurable history rotation via `JOURNAL_MAX_ENTRIES`.
- **Failure Cooldown:** Implemented an intelligent cooldown period (`FAILURE_COOLDOWN_SECONDS`) for containers that repeatedly fail to update, preventing excessive Docker API calls and resource churn.
- **Optional Notifications:** Watcher no longer requires a notification backend to start. It now defaults to a `NoopNotifier` if no webhooks are configured, while maintaining strict "Fail-Fast" validation for partial configurations.
- **Update Rate Limiting:** Added `MAX_UPDATES_PER_CYCLE` to limit the number of containers updated in a single run, preventing potential CPU/Network spikes.
- **Advanced Filtering:** Added `EXCLUDE_CONTAINER_REGEX` support for global, pattern-based container exclusion.
- **Granular Notification Control:**
    - `NOTIFY_SUMMARY_STRATEGY`: Choose when to receive cycle summaries (`always`, `on_change`, or `on_error`).
    - `NOTIFY_ON_UPDATE_START`: Option to suppress "Initiating sequence" alerts for a quieter notification feed.
    - `NOTIFY_ON_STARTUP`: Option to disable the initial "Watcher Started" notification.
- **Operational Toggles:**
    - `RESTART_DEPENDENTS`: Option to globally disable the automatic restart of linked containers.
- **Improved Failure Tracking:** Success or "No Change" results now automatically clear any active cooldowns for a container.
- **Enhanced Configuration Validation:** Strict startup checks for all new 1.6.0 parameters.

### Fixed
- **Journal Robustness:** Improved error handling for filesystem IO errors and JSON corruption during journaling.
- **Code Hygiene:** Eliminated remaining unsanitized exception blocks to prevent interference with system signals.
- **Summary Logic:** Refined the definition of "Change" for the summary report strategy to strictly mean operational actions (Update, Fail, Rollback).

## [1.5.3] - 2026-04-12
### Changed
- **Documentation:** Enhanced the configuration table in `README.md` to include Telegram variables.
- **Verification:** Full synchronization check between local and public repository state.

## [1.5.2] - 2026-04-12
### Fixed
- **Repository Cleanup:** Removed accidentally tracked temporary release notes files from the repository.
- **Configuration Parity:** Synchronized `docker-compose.yml` and `.env.example` to correctly include the new v1.5.0/v1.5.1 configuration variables (`LOG_LEVEL`, `NOTIFY_UPDATES_AVAILABLE`, `SCHEDULE_TIME`, `NTFY_URL`, etc.).
- **Documentation:** Corrected the GitHub repository topics to strictly align with the project's scope.

## [1.5.1] - 2026-04-12
### Added
- **Configurable Notification Verbosity:** Added `NOTIFY_UPDATES_AVAILABLE` environment variable (defaults to `true`). If set to `false`, Watcher will no longer include `watcher.enable=false` containers in the daily summary report, reducing notification spam for containers you explicitly chose not to auto-update.
- **Dynamic Log Levels:** Added `LOG_LEVEL` environment variable (defaults to `INFO`). Can be set to `DEBUG` for deep troubleshooting and visibility into the Docker SDK recreation process.

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
- Optimized Docker recreation plan logic to ensure close configuration parity.

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
