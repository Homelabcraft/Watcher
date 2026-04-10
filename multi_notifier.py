from notifier_protocol import Notifier


class MultiNotifier:
    """Fan-out every notification to multiple backends."""

    def __init__(self, backends: list[Notifier]):
        self._backends = backends

    def notify_recreation_started(self, name: str, image: str) -> None:
        for b in self._backends:
            b.notify_recreation_started(name, image)

    def notify_update_success(self, name: str, image: str) -> None:
        for b in self._backends:
            b.notify_update_success(name, image)

    def notify_dependents_restarted(
        self,
        restarted: list[str],
        failures: list[tuple[str, str]],
    ) -> None:
        for b in self._backends:
            b.notify_dependents_restarted(restarted, failures)

    def notify_failure(self, name: str, reason: str) -> None:
        for b in self._backends:
            b.notify_failure(name, reason)

    def notify_rollback(self, name: str, success: bool, detail: str = "") -> None:
        for b in self._backends:
            b.notify_rollback(name, success, detail)

    def notify_summary_report(self, summary: dict) -> None:
        for b in self._backends:
            b.notify_summary_report(summary)

    def notify_summary(self, title: str, message: str) -> None:
        for b in self._backends:
            b.notify_summary(title, message)

    def notify_startup(self, check_interval_seconds: int, hostname: str) -> None:
        for b in self._backends:
            b.notify_startup(check_interval_seconds, hostname)

    def notify_shutdown(self, reason: str = "Stopped by user (Ctrl+C).") -> None:
        for b in self._backends:
            b.notify_shutdown(reason)
