import logging

from models import ContainerUpdateInfo, ExecutionPlan
from notifier_protocol import Notifier

logger = logging.getLogger("Watcher.MultiNotifier")

class MultiNotifier:
    """Fan-out every notification to multiple backends."""

    def __init__(self, backends: list[Notifier]):
        self._backends = backends

    def _fan_out(self, method_name: str, *args, **kwargs):
        for b in self._backends:
            try:
                getattr(b, method_name)(*args, **kwargs)
            except Exception as e: # noqa: BLE001
                logger.error(f"Notifier {b.__class__.__name__} failed in {method_name}: {e}")

    def notify_scan_started(self, total_containers: int, run_mode: str) -> None:
        self._fan_out("notify_scan_started", total_containers, run_mode)

    def notify_update_started(self, name: str, image: str, old_short_id: str, new_short_id: str, current_status: str, dependents: list[str]) -> None:
        self._fan_out("notify_update_started", name, image, old_short_id, new_short_id, current_status, dependents)

    def notify_update_success(self, info: ContainerUpdateInfo) -> None:
        self._fan_out("notify_update_success", info)

    def notify_update_failure(self, info: ContainerUpdateInfo) -> None:
        self._fan_out("notify_update_failure", info)

    def notify_dependents_restarted(
        self,
        restarted: list[str],
        failures: list[tuple[str, str]],
    ) -> None:
        self._fan_out("notify_dependents_restarted", restarted, failures)

    def notify_rollback(self, name: str, status: str, detail: str = "") -> None:
        self._fan_out("notify_rollback", name, status, detail)

    def notify_summary_report(self, summary: dict, infos: list[ContainerUpdateInfo] | None = None, duration_sec: float = 0.0) -> None:
        self._fan_out("notify_summary_report", summary, infos, duration_sec)
            
    def notify_execution_plan(self, plan: ExecutionPlan, next_run: str | None = None) -> None:
        self._fan_out("notify_execution_plan", plan, next_run)

    def notify_summary(self, title: str, message: str) -> None:
        self._fan_out("notify_summary", title, message)

    def notify_startup(self, config: dict, hostname: str) -> None:
        self._fan_out("notify_startup", config, hostname)

    def notify_shutdown(self, reason: str = "Stopped by user (Ctrl+C).") -> None:
        self._fan_out("notify_shutdown", reason)
