import logging
import requests
import datetime
from typing import Optional
from models import ExecutionPlan, ContainerUpdateInfo, UpdateStatus

logger = logging.getLogger('Watcher.Discord')

class DiscordNotifier:
    """Sends structured Discord notifications for the update lifecycle."""
    def __init__(self, webhook_url: Optional[str]):
        self.webhook_url = webhook_url

    def _send(self, payload: dict):
        if not self.webhook_url: return
        try:
            requests.post(self.webhook_url, json=payload, timeout=5).raise_for_status()
        except requests.exceptions.Timeout:
            logger.warning("Discord webhook notification timed out.")
        except requests.exceptions.RequestException as e:
            logger.error(f"Discord notify failed: {e}")
        except Exception as e:
            logger.error(f"Unexpected error during Discord notification: {e}")

    def send_event(self, title: str, description: str, color: int = 0x3498db, fields: list = None):
        embed = {
            "title": title,
            "description": description,
            "color": color,
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "fields": fields or []
        }
        self._send({"embeds": [embed]})

    def notify_scan_started(self, total_containers: int, run_mode: str) -> None:
        self.send_event(
            "🔍 Scan Started", 
            f"Checking for updates on {total_containers} containers.\n**Mode:** `{run_mode}`", 
            color=0x34495e
        )

    def notify_update_started(self, name: str, image: str, old_short_id: str, new_short_id: str, current_status: str, dependents: list[str]) -> None:
        fields = [
            {"name": "Current Status", "value": f"`{current_status}`", "inline": True},
            {"name": "Target Image", "value": f"`{image}`", "inline": False},
        ]
        if old_short_id and new_short_id:
            fields.append({"name": "Version Shift", "value": f"`{old_short_id}` → `{new_short_id}`", "inline": False})
        if dependents:
            fields.append({"name": "Dependents to Restart", "value": ", ".join(dependents), "inline": False})
            
        self.send_event(f"🔄 Updating: {name}", "Initiating sequence: Pull -> Stop -> Recreate -> Start -> Health Check", color=0x3498db, fields=fields)

    def notify_update_success(self, info: ContainerUpdateInfo) -> None:
        fields = [
            {"name": "Duration", "value": f"`{info.duration_sec:.1f}s`", "inline": True},
        ]
        if info.old_image_short_id and info.new_image_short_id:
            fields.append({"name": "Version Shift", "value": f"`{info.old_image_short_id}` → `{info.new_image_short_id}`", "inline": False})
        if info.old_id and info.new_id:
            fields.append({"name": "Container IDs", "value": f"`{info.old_id[:12]}` → `{info.new_id[:12]}`", "inline": False})
            
        desc = "Update completed successfully and container is healthy."
        self.send_event(f"✅ Update OK: {info.name}", desc, color=0x2ecc71, fields=fields)

    def notify_update_failure(self, info: ContainerUpdateInfo) -> None:
        fields = [
            {"name": "Failed Step", "value": f"`{info.error_step}`", "inline": True},
            {"name": "Duration before fail", "value": f"`{info.duration_sec:.1f}s`", "inline": True},
            {"name": "Error Details", "value": f"```\n{info.error_message}\n```", "inline": False}
        ]
        
        if info.rollback_attempted:
            rb_status = "✅ Success" if info.rollback_success else "❌ Failed"
            fields.append({"name": "Rollback Status", "value": f"{rb_status}: {info.rollback_details}", "inline": False})
            
        self.send_event(f"❌ Update FAILED: {info.name}", "A critical error occurred during the update process.", color=0xe74c3c, fields=fields)

    def notify_rollback(self, name: str, status: str, detail: str = "") -> None:
        if status == "started":
            self.send_event(f"⚠️ Rollback Initiated: {name}", "Attempting to restore previous container state...", color=0xf39c12)
        elif status == "success":
            self.send_event(f"✅ Rollback OK: {name}", detail, color=0x2ecc71)
        else:
            self.send_event(f"🚨 ROLLBACK FAILED: {name}", detail, color=0xc0392b)

    def notify_summary_report(self, summary: dict, infos: list[ContainerUpdateInfo] = None, duration_sec: float = 0.0) -> None:
        if not summary["updated"] and not summary["failed"] and not summary["rolled_back"] and not summary.get("reported"):
            return

        lines = [f"📊 **Watcher Scan Summary** (Duration: `{duration_sec:.1f}s`)"]
        
        def format_info(name):
            if not infos: return name
            for info in infos:
                if info.name == name and info.old_image_short_id and info.new_image_short_id:
                    return f"{name} (`{info.old_image_short_id}` → `{info.new_image_short_id}`)"
            return name

        if summary.get("reported"):
            reported_formatted = [format_info(n) for n in summary['reported']]
            lines.append(f"\n👀 **Updates Available (Not Auto-Updated):**\n- " + "\n- ".join(reported_formatted))
        if summary["updated"]:
            updated_formatted = [format_info(n) for n in summary['updated']]
            lines.append(f"\n✅ **Updated:**\n- " + "\n- ".join(updated_formatted))
        if summary["failed"]:
            lines.append(f"\n❌ **Failed:** {', '.join(summary['failed'])}")
        if summary.get("rolled_back"):
            lines.append(f"\n⚠️ **Rolled Back:** {', '.join(summary['rolled_back'])}")
        if summary.get("skipped"):
            lines.append(f"\n⏭️ **Skipped (Cooldown):** {', '.join(summary['skipped'])}")

        self.send_event("Cycle Complete", "\n".join(lines), color=0x9b59b6)

    def notify_execution_plan(self, plan: ExecutionPlan, next_run: str = None) -> None:
        if not plan.updates_available and not plan.monitored_only:
            return

        lines = [f"📋 **Dry Run Execution Plan**"]
        lines.append(f"Containers checked: {plan.checked_containers}")
        
        if plan.updates_available:
            lines.append("\n🔄 **Would Update:**")
            for u in plan.updates_available:
                if u.old_image_short_id and u.new_image_short_id:
                    lines.append(f"- **{u.name}** (`{u.old_image_short_id}` → `{u.new_image_short_id}`)")
                else:
                    lines.append(f"- **{u.name}** (Update found)")
                    
        if plan.monitored_only:
            lines.append("\n👀 **Monitored Only (Update Available, Auto-Update Disabled):**")
            for u in plan.monitored_only:
                if u.old_image_short_id and u.new_image_short_id:
                    lines.append(f"- **{u.name}** (`{u.old_image_short_id}` → `{u.new_image_short_id}`)")
                else:
                    lines.append(f"- **{u.name}** (Update found)")

        if plan.dependents_to_restart:
            lines.append(f"\n🔗 **Would Restart Dependents:**")
            lines.append(f"- {', '.join(plan.dependents_to_restart)}")
            
        if next_run:
            lines.append(f"\n⏳ **Next scheduled run:** {next_run}")

        self.send_event("Dry Run Completed", "\n".join(lines), color=0xf1c40f)

    def notify_summary(self, title: str, message: str) -> None:
        self.send_event(title, message, color=0x95a5a6)

    def notify_dependents_restarted(self, restarted: list[str], failures: list[tuple[str, str]]) -> None:
        if not restarted and not failures: return
        lines = []
        if restarted: lines.append(f"**Restarted:** {', '.join(restarted)}")
        if failures:
            for n, err in failures: lines.append(f"**Failed** `{n}`: {err}")
        self.send_event("Dependent Restarts", "\n".join(lines), color=0x3498db)

    def notify_startup(self, config: dict, hostname: str) -> None:
        iv = config.get("check_interval", 0)
        if iv >= 86400: iv_str = f"{iv // 86400}d"
        elif iv >= 3600: iv_str = f"{iv // 3600}h"
        elif iv >= 60: iv_str = f"{iv // 60}m"
        else: iv_str = f"{iv}s"
        
        mode = "SCHEDULED" if config.get("schedule_time") else "INTERVAL"
        time_info = f"`{config.get('schedule_time')}`" if config.get("schedule_time") else f"`{iv_str}` ({iv}s)"
        
        fields = [
            {"name": "Host", "value": f"`{hostname}`", "inline": True},
            {"name": f"Scan Mode ({mode})", "value": time_info, "inline": True},
            {"name": "Dry Run", "value": "`Active`" if config.get("dry_run") else "`Disabled`", "inline": True},
            {"name": "Cleanup Old Images", "value": "`Active`" if config.get("cleanup_old_images") else "`Disabled`", "inline": True},
            {"name": "Filter Mode", "value": "`Label-only`" if config.get("watch_by_label") else "`All Containers`", "inline": True},
            {"name": "Health Check (Retries/Delay)", "value": f"`{config.get('health_check_retries')}x` / `{config.get('health_check_delay')}s`", "inline": True},
        ]
        
        self.send_event("🚀 Watcher Started", "Service is initialized and monitoring containers.", color=0x2c3e50, fields=fields)

    def notify_shutdown(self, reason: str = "Stopped by user (Ctrl+C).") -> None:
        self.send_event("🛑 Watcher Stopped", reason, color=0x7f8c8d)