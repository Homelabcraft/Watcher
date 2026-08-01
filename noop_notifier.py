"""No-op notifier when no backends are configured."""

from models import ContainerUpdateInfo, ExecutionPlan


class NoopNotifier:
    def notify_scan_started(self, total_containers: int, run_mode: str) -> None:
        pass

    def notify_update_started(self, name: str, image: str, old_short_id: str, new_short_id: str, current_status: str, dependents: list[str]) -> None:
        pass

    def notify_update_success(self, info: ContainerUpdateInfo) -> None:
        pass

    def notify_update_failure(self, info: ContainerUpdateInfo) -> None:
        pass

    def notify_dependents_restarted(
        self,
        restarted: list[str],
        failures: list[tuple[str, str]],
    ) -> None:
        pass

    def notify_rollback(self, name: str, status: str, detail: str = "") -> None:
        pass

    def notify_summary_report(self, summary: dict, infos: list[ContainerUpdateInfo] = None, duration_sec: float = 0.0) -> None:
        pass
        
    def notify_execution_plan(self, plan: ExecutionPlan, next_run: str = None) -> None:
        pass

    def notify_summary(self, title: str, message: str) -> None:
        pass

    def notify_startup(self, config: dict, hostname: str) -> None:
        pass

    def notify_shutdown(self, reason: str = "Stopped by user (Ctrl+C).") -> None:
        pass
