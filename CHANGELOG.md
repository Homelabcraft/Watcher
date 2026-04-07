# Changelog

All notable changes to this project will be documented in this file.

## [1.4.1] - 2026-04-07
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
