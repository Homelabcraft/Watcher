import logging

import requests

from models import ContainerUpdateInfo, ExecutionPlan

logger = logging.getLogger("Watcher.Slack")


class SlackNotifier:
    """Slack Incoming Webhooks (https://api.slack.com/messaging/webhooks)."""

    def __init__(self, webhook_url: str | None):
        self.webhook_url = webhook_url

    def _send(self, text: str) -> None:
        if not self.webhook_url:
            return
        try:
            requests.post(self.webhook_url, json={"text": text}, timeout=10).raise_for_status()
        except requests.exceptions.Timeout:
            logger.warning("Slack webhook notification timed out.")
        except requests.exceptions.RequestException as e:
            logger.error(f"Slack notify failed: {e}")
        except Exception as e: # noqa: BLE001
            logger.error(f"Unexpected error during Slack notification: {e}")

    def notify_scan_started(self, total_containers: int, run_mode: str) -> None:
        self._send(f"🔍 *Scan Started*\nChecking for updates on {total_containers} containers. Mode: `{run_mode}`")

    def notify_update_started(self, name: str, image: str, old_short_id: str, new_short_id: str, current_status: str, dependents: list[str]) -> None:
        msg = f"🔄 *Updating:* `{name}`\n*Target:* `{image}`\n*Status:* `{current_status}`"
        if old_short_id and new_short_id:
            msg += f"\n*Version:* `{old_short_id}` → `{new_short_id}`"
        if dependents:
            msg += f"\n*Dependents to restart:* {', '.join(dependents)}"
        self._send(msg)

    def notify_update_success(self, info: ContainerUpdateInfo) -> None:
        msg = f"✅ *Update OK:* `{info.name}`\nDuration: `{info.duration_sec:.1f}s`"
        if info.old_image_short_id and info.new_image_short_id:
            msg += f"\n*Version:* `{info.old_image_short_id}` → `{info.new_image_short_id}`"
        self._send(msg)

    def notify_update_failure(self, info: ContainerUpdateInfo) -> None:
        msg = f"❌ *Update FAILED:* `{info.name}`\n*Failed Step:* `{info.error_step}`\n*Error:* ```{info.error_message}```"
        if info.rollback_attempted:
            rb_status = "✅ Success" if info.rollback_success else "❌ Failed"
            msg += f"\n*Rollback Status:* {rb_status} ({info.rollback_details})"
        self._send(msg)

    def notify_dependents_restarted(
        self,
        restarted: list[str],
        failures: list[tuple[str, str]],
    ) -> None:
        if not restarted and not failures:
            return
        lines = ["*Dependent restarts*"]
        if restarted:
            lines.append(f"*Restarted:* {', '.join(restarted)}")
        if failures:
            for n, err in failures:
                lines.append(f"*Failed* `{n}`: {err}")
        self._send("\n".join(lines))

    def notify_rollback(self, name: str, status: str, detail: str = "") -> None:
        if status == "started":
            title = f"⚠️ *Rollback Initiated:* `{name}`"
        elif status == "success":
            title = f"✅ *Rollback OK:* `{name}`"
        else:
            title = f"🚨 *ROLLBACK FAILED:* `{name}`"
        self._send(f"{title}\n{detail}" if detail else title)

    def notify_summary_report(self, summary: dict, infos: list[ContainerUpdateInfo] | None = None, duration_sec: float = 0.0) -> None:
        def format_info(name):
            if not infos: return name
            for info in infos:
                if info.name == name and info.old_image_short_id and info.new_image_short_id:
                    return f"{name} (`{info.old_image_short_id}` → `{info.new_image_short_id}`)"
            return name
            
        lines = [f"📊 *Watcher Scan Summary* (Duration: `{duration_sec:.1f}s`)"]
        if summary.get("reported"):
            reported_formatted = [format_info(n) for n in summary['reported']]
            lines.append("\n*Updates available (not auto-updated):*\n- " + "\n- ".join(reported_formatted))
        if summary["updated"]:
            updated_formatted = [format_info(n) for n in summary['updated']]
            lines.append("\n*Updated:*\n- " + "\n- ".join(updated_formatted))
        if summary["failed"]:
            lines.append(f"\n*Failed:* {', '.join(summary['failed'])}")
        if summary["rolled_back"]:
            lines.append(f"\n*Rolled back:* {', '.join(summary['rolled_back'])}")
            
        if not summary["updated"] and not summary["failed"] and not summary.get("rolled_back") and not summary.get("reported"):
            lines.append("\nℹ️ No updates or errors detected.")

        self._send("\n".join(lines))

    def notify_execution_plan(self, plan: ExecutionPlan, next_run: str | None = None) -> None:
        if not plan.updates_available and not plan.monitored_only:
            return

        lines = ["📋 *Dry Run Execution Plan*"]
        lines.append(f"Containers checked: {plan.checked_containers}")
        
        if plan.updates_available:
            lines.append("\n*Would Update:*")
            for u in plan.updates_available:
                if u.old_image_short_id and u.new_image_short_id:
                    lines.append(f"- *{u.name}* (`{u.old_image_short_id}` → `{u.new_image_short_id}`)")
                else:
                    lines.append(f"- *{u.name}* (Update found)")

        if plan.monitored_only:
            lines.append("\n*Monitored Only (Update Available, Auto-Update Disabled):*")
            for u in plan.monitored_only:
                if u.old_image_short_id and u.new_image_short_id:
                    lines.append(f"- *{u.name}* (`{u.old_image_short_id}` → `{u.new_image_short_id}`)")
                else:
                    lines.append(f"- *{u.name}* (Update found)")

        if plan.dependents_to_restart:
            lines.append("\n*Would Restart Dependents:*")
            lines.append(f"- {', '.join(plan.dependents_to_restart)}")

        if next_run:
            lines.append(f"\n*Next scheduled run:* {next_run}")

        self._send("\n".join(lines))

    def notify_summary(self, title: str, message: str) -> None:
        self._send(f"*{title}*\n{message}")

    def notify_startup(self, config: dict, hostname: str) -> None:
        iv = config.get("check_interval", 0)
        if iv >= 86400: iv_str = f"{iv // 86400}d"
        elif iv >= 3600: iv_str = f"{iv // 3600}h"
        elif iv >= 60: iv_str = f"{iv // 60}m"
        else: iv_str = f"{iv}s"
        
        mode = "SCHEDULED" if config.get("schedule_time") else "INTERVAL"
        time_info = f"`{config.get('schedule_time')}`" if config.get("schedule_time") else f"`{iv_str}` ({iv}s)"
        
        self._send(
            f"🚀 *Watcher started*\nMonitoring containers for image updates.\n"
            f"*Host:* `{hostname}` | *Scan Mode:* `{mode}` | *Interval/Time:* {time_info}\n"
            f"*Dry Run:* `{'Active' if config.get('dry_run') else 'Disabled'}` | "
            f"*Filter Mode:* `{'Label-only' if config.get('watch_by_label') else 'All'}`"
        )

    def notify_shutdown(self, reason: str = "Stopped by user (Ctrl+C).") -> None:
        self._send(f"🛑 *Watcher stopped*\n{reason}")
