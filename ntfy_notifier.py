import logging

import requests

from models import ContainerUpdateInfo, ExecutionPlan

logger = logging.getLogger("Watcher.Ntfy")


class NtfyNotifier:
    """Publish to an ntfy topic (https://ntfy.sh or self-hosted). Set NTFY_URL to the full topic URL."""

    def __init__(self, topic_url: str | None):
        self.topic_url = topic_url

    def _send(self, title: str, body: str, tags: list[str] = None) -> None:
        if not self.topic_url:
            return
        try:
            headers = {"Title": title[:200] if title else "Watcher"}
            if tags:
                headers["Tags"] = ",".join(tags)
            requests.post(
                self.topic_url,
                data=body.encode("utf-8"),
                headers=headers,
                timeout=10,
            ).raise_for_status()
        except requests.exceptions.Timeout:
            logger.warning("Ntfy webhook notification timed out.")
        except requests.exceptions.RequestException as e:
            logger.error(f"ntfy notify failed: {e}")
        except Exception as e:
            logger.error(f"Unexpected error during ntfy notification: {e}")

    def notify_scan_started(self, total_containers: int, run_mode: str) -> None:
        self._send("Scan Started", f"Checking {total_containers} containers.\nMode: {run_mode}", tags=["mag"])

    def notify_update_started(self, name: str, image: str, old_short_id: str, new_short_id: str, current_status: str, dependents: list[str]) -> None:
        msg = f"Target: {image}\nStatus: {current_status}"
        if old_short_id and new_short_id:
            msg += f"\nVersion: {old_short_id} -> {new_short_id}"
        if dependents:
            msg += f"\nDependents: {', '.join(dependents)}"
        self._send(f"Updating: {name}", msg, tags=["arrows_counterclockwise"])

    def notify_update_success(self, info: ContainerUpdateInfo) -> None:
        msg = f"Duration: {info.duration_sec:.1f}s"
        if info.old_image_short_id and info.new_image_short_id:
            msg += f"\nVersion: {info.old_image_short_id} -> {info.new_image_short_id}"
        self._send(f"Update OK: {info.name}", msg, tags=["white_check_mark"])

    def notify_update_failure(self, info: ContainerUpdateInfo) -> None:
        msg = f"Failed Step: {info.error_step}\nError: {info.error_message}"
        if info.rollback_attempted:
            rb_status = "Success" if info.rollback_success else "Failed"
            msg += f"\nRollback: {rb_status} ({info.rollback_details})"
        self._send(f"Update FAILED: {info.name}", msg, tags=["x"])

    def notify_dependents_restarted(
        self,
        restarted: list[str],
        failures: list[tuple[str, str]],
    ) -> None:
        if not restarted and not failures:
            return
        lines = []
        if restarted:
            lines.append(f"Restarted: {', '.join(restarted)}")
        if failures:
            for n, err in failures:
                lines.append(f"Failed {n}: {err}")
        self._send("Dependent restarts", "\n".join(lines), tags=["link"])

    def notify_rollback(self, name: str, status: str, detail: str = "") -> None:
        if status == "started":
            self._send(f"Rollback Initiated: {name}", detail, tags=["warning"])
        elif status == "success":
            self._send(f"Rollback OK: {name}", detail, tags=["white_check_mark"])
        else:
            self._send(f"ROLLBACK FAILED: {name}", detail, tags=["rotating_light"])

    def notify_summary_report(self, summary: dict, infos: list[ContainerUpdateInfo] = None, duration_sec: float = 0.0) -> None:
        if not summary["updated"] and not summary["failed"] and not summary["rolled_back"] and not summary.get("reported"):
            return
            
        def format_info(name):
            if not infos: return name
            for info in infos:
                if info.name == name and info.old_image_short_id and info.new_image_short_id:
                    return f"{name} ({info.old_image_short_id} -> {info.new_image_short_id})"
            return name
            
        lines = []
        if summary.get("reported"):
            lines.append(f"Updates available (not auto-updated): {', '.join([format_info(n) for n in summary['reported']])}")
        if summary["updated"]:
            lines.append(f"Updated: {', '.join([format_info(n) for n in summary['updated']])}")
        if summary["failed"]:
            lines.append(f"Failed: {', '.join(summary['failed'])}")
        if summary["rolled_back"]:
            lines.append(f"Rolled back: {', '.join(summary['rolled_back'])}")
        self._send(f"Watcher Scan Summary ({duration_sec:.1f}s)", "\n".join(lines), tags=["bar_chart"])

    def notify_execution_plan(self, plan: ExecutionPlan, next_run: str = None) -> None:
        if not plan.updates_available and not plan.monitored_only:
            return

        lines = [f"Containers checked: {plan.checked_containers}"]
        
        if plan.updates_available:
            lines.append("\nWould Update:")
            for u in plan.updates_available:
                if u.old_image_short_id and u.new_image_short_id:
                    lines.append(f"- {u.name} ({u.old_image_short_id} -> {u.new_image_short_id})")
                else:
                    lines.append(f"- {u.name} (Update found)")

        if plan.monitored_only:
            lines.append("\nMonitored Only:")
            for u in plan.monitored_only:
                if u.old_image_short_id and u.new_image_short_id:
                    lines.append(f"- {u.name} ({u.old_image_short_id} -> {u.new_image_short_id})")
                else:
                    lines.append(f"- {u.name} (Update found)")

        if plan.dependents_to_restart:
            lines.append(f"\nWould Restart Dependents: {', '.join(plan.dependents_to_restart)}")

        if next_run:
            lines.append(f"\nNext scheduled run: {next_run}")

        self._send("Dry Run Execution Plan", "\n".join(lines), tags=["clipboard"])

    def notify_summary(self, title: str, message: str) -> None:
        self._send(title, message, tags=["information_source"])

    def notify_startup(self, config: dict, hostname: str) -> None:
        iv = config.get("check_interval", 0)
        if iv >= 86400: iv_str = f"{iv // 86400}d"
        elif iv >= 3600: iv_str = f"{iv // 3600}h"
        elif iv >= 60: iv_str = f"{iv // 60}m"
        else: iv_str = f"{iv}s"
        
        mode = "SCHEDULED" if config.get("schedule_time") else "INTERVAL"
        time_info = f"{config.get('schedule_time')}" if config.get("schedule_time") else f"{iv_str} ({iv}s)"
        
        self._send(
            "Watcher started",
            f"Host: {hostname}\nMode: {mode}\nInterval/Time: {time_info}\nDry Run: {'Active' if config.get('dry_run') else 'Disabled'}",
            tags=["rocket"]
        )

    def notify_shutdown(self, reason: str = "Stopped by user (Ctrl+C).") -> None:
        self._send("Watcher stopped", reason, tags=["stop_sign"])
