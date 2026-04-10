"""No-op notifier when no backends are configured."""

class NoopNotifier:
    def notify_recreation_started(self, name: str, image: str) -> None:
        pass

    def notify_update_success(self, name: str, image: str) -> None:
        pass

    def notify_dependents_restarted(
        self,
        restarted: list[str],
        failures: list[tuple[str, str]],
    ) -> None:
        pass

    def notify_failure(self, name: str, reason: str) -> None:
        pass

    def notify_rollback(self, name: str, success: bool, detail: str = "") -> None:
        pass

    def notify_summary_report(self, summary: dict) -> None:
        pass

    def notify_summary(self, title: str, message: str) -> None:
        pass

    def notify_startup(self, check_interval_seconds: int, hostname: str) -> None:
        pass

    def notify_shutdown(self, reason: str = "Stopped by user (Ctrl+C).") -> None:
        pass
